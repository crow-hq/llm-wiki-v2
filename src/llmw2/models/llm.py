# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The writing model: any provider behind an OpenAI-compatible `/chat/completions`."""

from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from llmw2.config import PROVIDERS, LLMConfig, WikiConfig
from llmw2.errors import ModelError, ReplyCut
from llmw2.models.client import HttpModel, reported_cost
from llmw2.models.usage import UsageTracker

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

# The output cap of a call. Not in decision_hash: a reply under the cap is the same with or without it (a reply over it fails).
CHARS_PER_TOKEN = 3.5  # Latin-script text; CJK, or JSON that escapes non-ASCII, takes more tokens per character
REPLY_FACTOR = 3  # a call may write this many times the length asked for
MIN_REPLY_TOKENS = 1_024  # no cap is ever lower
REASONING_ROOM = 32_000  # tokens added to the cap when the model may think: they count against it

_CAP_REFUSED = ("max_tokens", "max_completion_tokens", "context length", "maximum context", "max_model_len")


def reply_tokens(chars: int) -> int:
    """The output cap, in tokens, for a reply that should take about `chars` characters."""
    return max(MIN_REPLY_TOKENS, math.ceil(REPLY_FACTOR * chars / CHARS_PER_TOKEN))


def extract_json(text: str) -> str:
    """The first JSON object inside a reply, tolerating code fences and chatter (even with braces) around it."""
    text = _FENCE.sub("", text.strip())
    decoder = json.JSONDecoder()
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return text[start:end]
    return text


class LLM(HttpModel):
    kind = "llm"

    def __init__(self, cfg: LLMConfig, usage: UsageTracker, *, client: httpx.Client | None = None) -> None:
        super().__init__(
            cfg.base_url, cfg.model, usage, api_key=cfg.api_key, needs_key=not cfg.ready, timeout=cfg.timeout, client=client
        )
        self.temperature = cfg.temperature
        self.seed = cfg.seed
        self.pin = cfg.pin_provider if cfg.provider == "openrouter" else []
        self._openrouter = cfg.provider == "openrouter"
        self._json_mode = True  # retries ask for response_format json_object, until the provider refuses it
        self._cap = True  # requests carry the output cap, until the provider refuses it
        if cfg.pin_provider and not self.pin:
            log.warning("pin_provider only works on OpenRouter: ignored for provider %r", cfg.provider)
        preset = PROVIDERS[cfg.provider]
        # No switch when reasoning is wanted, or when the caller's extra_body already says how much.
        off = preset.reasoning_off if not cfg.reasoning and not (preset.reasoning_off or {}).keys() & cfg.extra_body.keys() else None
        self.extra_body = {**(off or {}), **cfg.extra_body}  # the caller's extra_body wins, reasoning included
        self._least = preset.reasoning_least if off else None  # tried once, if the model refuses the off switch
        self._cap_field = preset.max_tokens_field
        self._thinks = not off  # known not to think only while the off switch is applied
        self._least_applied = False  # True once the switch to the least reasoning happened
        self._switch_lock = threading.Lock()

    @property
    def may_think(self) -> bool:
        """Whether thinking tokens can eat into the output cap (they do, on OpenRouter, OpenAI and Ollama)."""
        return self._thinks

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:
        start = time.perf_counter()
        reasoning_resent = False  # each request resends at most once for reasoning
        while True:
            built_switched = self._least_applied  # whether this request's body carries the least-reasoning fields
            try:
                data = self._post("/chat/completions", self._body(messages, json_mode, max_tokens))
                break
            except ModelError as e:
                text = str(e).lower()
                if e.status == 400 and "reasoning" in text and not reasoning_resent and self._switch_reasoning(built_switched):
                    reasoning_resent = True
                elif max_tokens is not None and self._cap and e.status in (400, 404) and any(w in text for w in _CAP_REFUSED):
                    log.info("%s refuses the output cap: sending requests without it from now on", self.model)
                    self._cap = False
                else:
                    raise
        usage = data.get("usage") or {}
        try:
            finish = data["choices"][0].get("finish_reason")
        except (KeyError, IndexError, TypeError, AttributeError):
            finish = None
        finish = finish if isinstance(finish, str) else None
        self.usage.record(
            "llm",
            self.model,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            op=op,
            seconds=time.perf_counter() - start,
            cost=reported_cost(usage),
            cached=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            provider=data["provider"] if isinstance(data.get("provider"), str) else None,
            temperature=self.temperature,
            seed=self.seed,
            finish_reason=finish,
        )
        if finish == "length":
            out = usage.get("completion_tokens")
            cut = f"cut at {out} output tokens" if out is not None else "cut"
            try:
                written = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                written = None
            raise ReplyCut(f"llm {op or 'chat'}: reply {cut} (finish_reason length)", text=written if isinstance(written, str) else "")
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"llm reply without content: {str(data)[:300]}") from e

    def _switch_reasoning(self, built_switched: bool) -> bool:
        """Whether to resend after a 400 on reasoning: switches once to the least reasoning (thread-safe).

        A request built before another thread's switch is resent too, with a body built now.
        """
        with self._switch_lock:
            if not self._least_applied and self._least:
                log.info("%s must think: asking for the least reasoning from now on", self.model)
                self.extra_body, self._least, self._thinks = {**self.extra_body, **self._least}, None, True
                self._least_applied = True
                return True
            return self._least_applied and not built_switched

    def _body(self, messages: list[dict[str, str]], json_mode: bool = False, max_tokens: int | None = None) -> dict[str, Any]:
        pin = {"provider": {"order": self.pin, "allow_fallbacks": False, "require_parameters": True}} if self.pin else {}
        seed = {"seed": self.seed} if self.seed is not None else {}
        body = {**pin, **seed, **self.extra_body, "model": self.model, "messages": messages, "temperature": self.temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
            if self._openrouter:
                body["provider"] = {**body.get("provider", {}), "require_parameters": True}
        if max_tokens is not None and self._cap and not {"max_tokens", "max_completion_tokens"} & body.keys():  # the caller's cap wins
            body[self._cap_field] = max_tokens + (REASONING_ROOM if self._thinks else 0)
        return body

    def complete(
        self, system: str, user: str, *, op: str = "", max_tokens: int | None = None, keep_cut: bool = False, cut: list[str] | None = None
    ) -> str:
        """The reply as text. With `max_tokens`, a reply cut at the limit is asked once more, shorter, then ModelError.

        With `keep_cut`, a reply cut at the limit is not asked again: what was written is returned, with a warning,
        and appended to `cut` if given (so the caller knows the reply is unfinished).
        """
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            return self.chat(messages, op=op, max_tokens=max_tokens)
        except ReplyCut as e:
            if keep_cut:
                log.warning("%s: reply cut at the output cap: kept the %d characters written", op, len(e.text))
                if cut is not None:
                    cut.append(e.text)
                return e.text
            if max_tokens is None:
                raise
        messages = [messages[0], {"role": "user", "content": user + _cut_note(max_tokens)}]
        return self.chat(messages, op=op, max_tokens=max_tokens)

    def json(self, system: str, user: str, schema: type[T], *, op: str = "", max_tokens: int | None = None) -> T:
        """Ask for one JSON object and validate it; one retry that asks for JSON mode, then ModelError.

        With `max_tokens`, one more retry when the reply is cut at the limit; three requests at most.
        """
        user = f"{user}\n\nReply with a single JSON object and nothing else."
        notes = ""  # what the retries add to the user message
        cut_retried = invalid_retried = False
        while True:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user + notes}]
            try:
                reply = self._retry(messages, op, max_tokens) if invalid_retried else self.chat(messages, op=op, max_tokens=max_tokens)
            except ReplyCut:
                if max_tokens is None or cut_retried:
                    raise
                cut_retried = True
                notes += _cut_note(max_tokens)
                continue
            try:
                return schema.model_validate(json.loads(extract_json(reply)))
            except (ValueError, ValidationError) as e:
                if invalid_retried:
                    raise ModelError(f"llm {op or 'json'} reply invalid after retry: {_short(e)}") from e
                invalid_retried = True
                notes += f"\n\nA previous reply to this request was not valid: {_short(e)} Reply with only the JSON object."

    def _retry(self, messages: list[dict[str, str]], op: str, max_tokens: int | None = None) -> str:
        """The retry, in JSON mode; asked once more without it if the provider refuses response_format."""
        try:
            return self.chat(messages, op=op, json_mode=self._json_mode, max_tokens=max_tokens)
        except ModelError as e:
            text = str(e).lower()
            refused = any(w in text for w in ("response_format", "json_object", "no endpoints"))
            if not (self._json_mode and e.status in (400, 404) and refused):
                raise
            log.info("%s refuses response_format: retrying without it from now on", self.model)
            self._json_mode = False
            return self.chat(messages, op=op, max_tokens=max_tokens)


def _cut_note(max_tokens: int) -> str:
    return (
        f"\n\nYour previous reply to this request was cut off at the length limit ({max_tokens} tokens). "
        "Reply again, complete and much shorter; do not repeat yourself."
    )


def _short(e: Any) -> str:
    return str(e).replace("\n", " ")[:300]


def probe(llm: LLM) -> str:
    """One tiny request, to tell a working model from a wrong key or address (ModelError)."""
    return llm.chat([{"role": "user", "content": "Reply with the single word OK."}], op="settings/probe").strip()[:200]


def context_warning(cfg: WikiConfig, client: httpx.Client | None = None) -> str | None:
    """For Ollama: a warning when its context window (`num_ctx`) is smaller than a prompt can be; else None. Never raises."""
    if cfg.llm.provider != "ollama":
        return None
    own = client is None
    http = client or httpx.Client(timeout=10)
    try:
        url = cfg.llm.base_url.rstrip("/").removesuffix("/v1")
        info = http.post(f"{url}/api/show", json={"model": cfg.llm.model}).raise_for_status().json()
        params = info["parameters"]
        lines = params.splitlines() if isinstance(params, str) else [f"{k} {v}" for k, v in params.items()]
        num_ctx = next(int(line.split()[1]) for line in lines if line.split()[:1] == ["num_ctx"])
    except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError, IndexError, StopIteration):
        return None  # no answer, or no num_ctx set: nothing to say
    finally:
        if own:
            http.close()
    need = cfg.effective_read_chars + 1.2 * cfg.effective_note_chars  # a prompt and the note the model writes, in characters
    if need / CHARS_PER_TOKEN <= num_ctx:
        return None
    return (
        f"{cfg.llm.model} has a context of {num_ctx} tokens in Ollama, and a call can take about {round(need / CHARS_PER_TOKEN)}: "
        "Ollama would silently cut the start of long prompts. Raise num_ctx in the model's Modelfile, or lower Characters per call."
    )


def live_models(llm: LLMConfig, client: httpx.Client | None = None) -> list[str]:
    """The text models the provider offers right now (GET {base_url}/models); [] when it does not say."""
    headers = {"Authorization": f"Bearer {llm.api_key}"} if llm.api_key else {}
    own = client is None
    http = client or httpx.Client(timeout=10)
    try:
        response = http.get(f"{llm.base_url.rstrip('/')}/models", headers=headers)
        data = response.raise_for_status().json()["data"]
        return [
            str(m["id"]).removeprefix("models/")  # Gemini names them models/<id>
            for m in data
            if m.get("id") and "text" in ((m.get("architecture") or {}).get("output_modalities") or ["text"])
        ]
    except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError):
        return []
    finally:
        if own:
            http.close()
