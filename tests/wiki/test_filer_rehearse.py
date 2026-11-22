# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The filer asks the route of the next entry while it files one (`Filer.rehearse_next`, `agents.ahead`): the next section takes the
answers kept for the very requests it makes, and asks what differs; what is filed is what the filer files without it."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents import ahead
from llmw2.agents.steps_classic import HERE, MODIFY, NEW, NONE
from llmw2.bundle import journal as J
from llmw2.config import ClassifierConfig
from llmw2.models.classifier import Classifier
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import CLASSIFIER_TOKENS, ContentLLM

DELAY = 0.03


class ContentClassifier(Classifier):
    """A classifier whose answer depends only on the request, as `ContentLLM`: the folder named after the note's group, else a new
    one; a note of the same title matches, and is merged into; notes of the same group are related. Every call takes DELAY seconds."""

    def __init__(self, usage: UsageTracker) -> None:
        super().__init__(ClassifierConfig(model="fake/jev", api_key="fake-key"), usage)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.lock = threading.Lock()

    def ask(self, state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        time.sleep(DELAY)
        with self.lock:
            self.calls.append((op, state, questions))
        self.usage.record("classifier", self.model, *CLASSIFIER_TOKENS, op=op)
        m = re.search(r"About ((\w+)-\d+)", state)
        name, group = (m.group(1), m.group(2)) if m else ("", "")
        out: dict[str, dict[str, Any]] = {}
        for key, q in questions.items():
            if q["type"] == "choice":
                options = list(q["criteria"])
                pick = next((o for o in options if o.strip("/").rsplit("/", 1)[-1] == group), None)
                pick = pick or next((o for o in (MODIFY, NEW, HERE, NONE) if o in options), options[-1])
                rest = [o for o in options if o != pick]
                probs = {pick: 0.96, **{o: round(0.04 / len(rest), 4) for o in rest}} if rest else {pick: 1.0}
                out[key] = {"type": "choice", "choice": pick, "probabilities": probs, "confidence": 0.95}
            else:
                text = q["instructions"]
                same = op == "match" and f"About {name}\n" in text + "\n"
                near = op == "relate" and f"About {group}-" in text
                out[key] = {"type": "noul", "noul": 0.95 if same or near else 0.02}
        return out


TEXTS = [f"topic: {g}-{n}\nThe fact {n} of {g}." for n in (1, 2, 1, 3) for g in ("apple", "pear", "plum")]


def run(bundle: Path, concurrency: int) -> list[tuple[Any, ...]]:
    ut = UsageTracker()
    cfg = WikiConfig(bundle=bundle, mode="crow", concurrency=concurrency)
    w = Wiki(cfg, llm=ContentLLM(ut, jitter=0.05), classifier=ContentClassifier(ut))
    w.init()
    try:
        w._stop_filer()
        for text in TEXTS:
            w.remember(text)
        w._filer_stopped = False
        w.drain(60)
    finally:
        w.close()
    view = J.Journal(bundle, writable=False, shared_hint=False).read()
    filed = [r.meta for r in view.records if r.type == J.T_FILED and "action" in r.meta]
    return [(f["action"], f["note"], f["folder"], f["created_folders"], len(f["related"])) for f in filed]


def test_the_next_section_takes_the_answers_asked_ahead_and_files_what_it_files_without_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[ahead.Answers] = []
    real = ahead.Answers.__init__

    def init(self: ahead.Answers) -> None:
        real(self)
        made.append(self)

    monkeypatch.setattr(ahead.Answers, "__init__", init)
    alone = run(tmp_path / "one", 1)
    assert made == []  # one at a time: nothing is asked ahead
    rehearsed = run(tmp_path / "four", 4)
    assert rehearsed == alone  # the same decisions, notes, folders and links
    assert sum(a.taken for a in made) > 0  # and answers asked ahead were taken by the sections


def test_a_request_the_section_makes_differently_is_asked_again(tmp_path: Path) -> None:
    """An answer kept ahead is taken only by the very request it answered; another goes to the model, and an answer that came as an
    error is asked again."""
    calls: list[str] = []
    answers = ahead.Answers()
    answers.ask(("choice", "route", "s", "i", (("a", "x"),)), lambda: "kept")
    with pytest.raises(RuntimeError):
        answers.ask(("nouls", "match", "s", ()), lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert answers.take(("choice", "route", "s", "i", (("b", "x"),)), lambda: calls.append("b") or "asked") == "asked"
    assert answers.take(("choice", "route", "s", "i", (("a", "x"),)), lambda: calls.append("a") or "asked") == "kept"
    assert answers.take(("choice", "route", "s", "i", (("a", "x"),)), lambda: calls.append("a2") or "again") == "again"  # taken once
    assert answers.take(("nouls", "match", "s", ()), lambda: calls.append("n") or "fresh") == "fresh"
    assert calls == ["b", "a2", "n"]
    answers.claim()
    with pytest.raises(ahead.Late):
        answers.ask(("choice", "route", "t", "i", ()), lambda: "late")


def test_a_request_on_its_way_is_waited_for_not_asked_twice() -> None:
    answers = ahead.Answers()
    started, go = threading.Event(), threading.Event()
    asked: list[str] = []

    def slow() -> str:
        asked.append("ahead")
        started.set()
        go.wait(5)
        return "kept"

    t = threading.Thread(target=answers.ask, args=(("k",), slow))
    t.start()
    started.wait(5)
    threading.Timer(0.2, go.set).start()
    assert answers.take(("k",), lambda: asked.append("section") or "asked") == "kept"
    t.join(5)
    assert asked == ["ahead"]


def test_the_models_go_through_the_answers_by_the_whole_request() -> None:
    """A choice with other options, or a statement asked of another state, is another request: asked again, never taken."""

    class Counting:
        request_chars = 1000

        def __init__(self) -> None:
            self.asked: list[tuple[Any, ...]] = []

        def choice(self, state: str, instructions: str, options: dict[str, str], *, op: str = "") -> Any:
            self.asked.append(("choice", state, tuple(options)))
            return f"{state}:{','.join(options)}"

        def nouls(self, state: str, questions: dict[str, str], *, op: str = "") -> dict[str, float]:
            self.asked.append(("nouls", state, tuple(questions)))
            return {k: float(len(state)) for k in questions}

    inner, answers = Counting(), ahead.Answers()
    early = ahead.AheadClassifier(inner, answers, ahead=True)
    early.choice("s", "i", {"/a": "A", "/b": "B"}, op="route")
    early.nouls("old state", {"q": "same"}, op="match")
    assert early.request_chars == 1000  # what is not a request is the classifier's own
    section = ahead.AheadClassifier(inner, answers, ahead=False)
    assert section.choice("s", "i", {"/a": "A", "/b": "B", "/c": "C"}, op="route") == "s:/a,/b,/c"
    assert section.nouls("new state", {"q": "same"}, op="match") == {"q": 9.0}
    assert section.choice("s", "i", {"/a": "A", "/b": "B"}, op="route") == "s:/a,/b"
    assert len(inner.asked) == 4  # two ahead, two asked again by the section, one taken
