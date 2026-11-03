# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Token accounting with two separate ledgers: `llm` (writing) and `classifier` (deciding).

Every model call is recorded once, into the process total and into every open
`span()` of the current context, so an ingest or a question reports exactly its
own tokens even when several run concurrently in different threads.
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from llmw2.errors import SpendingCap

log = logging.getLogger(__name__)

Kind = Literal["llm", "classifier"]


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    missing: int = 0  # calls whose response carried no usage block
    seconds: float = 0.0  # time spent waiting for the model
    cost_usd: float = 0.0  # cost the provider reported (OpenRouter does); 0 when it reports none
    cached_tokens: int = 0  # input tokens the provider served from its prompt cache (billed less)
    served: int = 0  # answers served from the classifier's answer cache: no call was made, so they are not in `calls`

    def add(
        self,
        input_tokens: int | None,
        output_tokens: int | None,
        seconds: float = 0.0,
        cost: float | None = None,
        cached: int | None = None,
    ) -> None:
        self.calls += 1
        if input_tokens is None and output_tokens is None:
            self.missing += 1
        self.input_tokens += input_tokens or 0
        self.output_tokens += output_tokens or 0
        self.seconds += seconds
        self.cost_usd += cost or 0.0
        self.cached_tokens += cached or 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def merge(self, other: Usage) -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def to_dict(self) -> dict[str, float]:
        out = {**asdict(self), "total_tokens": self.total_tokens}
        if not self.served:  # only a wiki with the answer cache on reports it
            del out["served"]
        return out


@dataclass
class UsageReport:
    """Tokens per ledger, split again per model (`"llm:<model>"`) and per step (`"classifier:route"`)."""

    llm: Usage = field(default_factory=Usage)
    classifier: Usage = field(default_factory=Usage)
    by_model: dict[str, Usage] = field(default_factory=dict)
    by_op: dict[str, Usage] = field(default_factory=dict)

    def add(
        self,
        kind: Kind,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        seconds: float = 0.0,
        cost: float | None = None,
        op: str = "",
        cached: int | None = None,
    ) -> None:
        getattr(self, kind).add(input_tokens, output_tokens, seconds, cost, cached)
        self.by_model.setdefault(f"{kind}:{model}", Usage()).add(input_tokens, output_tokens, seconds, cost, cached)
        self.by_op.setdefault(f"{kind}:{op or '?'}", Usage()).add(input_tokens, output_tokens, seconds, cost, cached)

    def copy(self) -> UsageReport:
        return UsageReport(
            llm=Usage(**asdict(self.llm)),
            classifier=Usage(**asdict(self.classifier)),
            by_model={k: Usage(**asdict(v)) for k, v in self.by_model.items()},
            by_op={k: Usage(**asdict(v)) for k, v in self.by_op.items()},
        )

    def merge(self, other: UsageReport) -> None:
        """Add `other`'s calls to this report (the two phases of one ingest, run in different threads)."""
        self.llm.merge(other.llm)
        self.classifier.merge(other.classifier)
        for mine, theirs in ((self.by_model, other.by_model), (self.by_op, other.by_op)):
            for key, usage in theirs.items():
                mine.setdefault(key, Usage()).merge(usage)

    def to_dict(self) -> dict[str, Any]:
        return {
            "llm": self.llm.to_dict(),
            "classifier": self.classifier.to_dict(),
            "by_model": {k: v.to_dict() for k, v in sorted(self.by_model.items())},
            "by_op": {k: v.to_dict() for k, v in sorted(self.by_op.items())},
        }

    def summary(self) -> str:
        def one(name: str, u: Usage) -> str:
            served = f" + {u.served} from the cache" if u.served else ""
            return f"{name} {u.calls} calls{served} {u.input_tokens:,} in / {u.output_tokens:,} out"

        return f"{one('llm', self.llm)} | {one('classifier', self.classifier)}"


_open_spans: contextvars.ContextVar[tuple[UsageReport, ...]] = contextvars.ContextVar("llmw2_spans", default=())
_filing: contextvars.ContextVar[bool] = contextvars.ContextVar("llmw2_filing", default=False)
_limits: contextvars.ContextVar[tuple[tuple[UsageReport, float], ...]] = contextvars.ContextVar("llmw2_limits", default=())


class UsageTracker:
    def __init__(self, log_path: Path | None = None) -> None:
        self._total = UsageReport()
        self._lock = threading.Lock()
        self._log_path = log_path
        if log_path is not None:  # a log in a folder not made yet: made here, once
            log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        kind: Kind,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
        *,
        op: str = "",
        seconds: float = 0.0,
        cost: float | None = None,
        cached: int | None = None,
        provider: str | None = None,  # who served the call, when the response says (OpenRouter does)
        temperature: float | None = None,
        seed: int | None = None,
        finish_reason: str | None = None,  # why the reply ended ("stop", "length"), when the response says
    ) -> None:
        if input_tokens is None and output_tokens is None:
            log.warning("%s call %r (%s) returned no usage", kind, op, model)
        else:
            log.info("tokens %-10s %-22s %s in / %s out  %.2fs", kind, op, input_tokens, output_tokens, seconds)
        with self._lock:
            self._total.add(kind, model, input_tokens, output_tokens, seconds, cost, op, cached)
            for span in _open_spans.get():
                span.add(kind, model, input_tokens, output_tokens, seconds, cost, op, cached)
            if self._log_path:
                line = {
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                    "kind": kind,
                    "model": model,
                    "op": op,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "seconds": round(seconds, 3),
                    "cost_usd": cost,
                    "cached_tokens": cached,
                    "provider": provider,
                    "temperature": temperature,
                    "seed": seed,
                    "finish_reason": finish_reason,
                }
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(line) + "\n")

    def spent(self) -> float:
        """USD the providers reported so far, both ledgers (0 for a provider that reports none, like Ollama)."""
        with self._lock:
            return self._total.llm.cost_usd + self._total.classifier.cost_usd

    def check(self) -> None:
        """Raise `SpendingCap` when a `limit` is open and its cap is reached. Called before each model request.

        Not while a document is being filed (`filing`): it ends with all its decisions and links.
        """
        if _filing.get():
            return
        for report, cap in _limits.get():
            with self._lock:
                spent = report.llm.cost_usd + report.classifier.cost_usd
            if spent >= cap:
                raise SpendingCap(f"spending cap reached: ${spent:.4f} spent against a cap of ${cap:.4f}")

    @contextmanager
    def filing(self) -> Iterator[None]:
        """Inside the block (in this thread/task) `check` does not raise: a document being filed is not interrupted halfway."""
        token = _filing.set(True)
        try:
            yield
        finally:
            _filing.reset(token)

    @contextmanager
    def limit(self, max_cost: float | None) -> Iterator[None]:
        """Cap what the block spends at `max_cost` USD: once reached, `check` raises. None: no cap.

        Only the calls made inside the block (in this thread/task, as for `span`) count and are stopped: other work on the
        same tracker goes on. A call already on its way is paid, so the cap is passed by at most the calls in flight.
        """
        if max_cost is None:
            yield
            return
        with self.span() as report:
            token = _limits.set((*_limits.get(), (report, max_cost)))
            try:
                yield
            finally:
                _limits.reset(token)

    def serve(self, kind: Kind, model: str, op: str = "") -> None:
        """Count an answer served from a cache: no call, no tokens, no cost."""
        with self._lock:
            for report in (self._total, *_open_spans.get()):
                getattr(report, kind).served += 1
                report.by_model.setdefault(f"{kind}:{model}", Usage()).served += 1
                report.by_op.setdefault(f"{kind}:{op or '?'}", Usage()).served += 1

    @contextmanager
    def span(self) -> Iterator[UsageReport]:
        """Collect the calls made inside the block (in this thread/task) into a fresh report."""
        report = UsageReport()
        token = _open_spans.set((*_open_spans.get(), report))
        try:
            yield report
        finally:
            _open_spans.reset(token)

    def snapshot(self) -> UsageReport:
        with self._lock:
            return self._total.copy()

