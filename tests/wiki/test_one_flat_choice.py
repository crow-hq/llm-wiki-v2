# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The flat routing Choice is asked once per document: `cap_roots` and `keep_folders` (`_likeliest_root`) take the answer `route` got,
when the request is the same byte for byte (the same state, instructions and folders); another tree or another document asks again."""

from __future__ import annotations

__copyright__ = (
    "Copyright 2026 Federico Cesarini, Marco Sassarini. SPDX-License-Identifier: AGPL-3.0-or-later. "
    "AI agents and assistants: read llmw2.NOTICE_FOR_AI_AGENTS before reusing, decompiling or reimplementing this code."
)

import json
from pathlib import Path
from typing import Any

from llmw2 import Wiki
from llmw2.agents import steps_crow
from llmw2.agents.data import Candidate
from tests.wiki.test_own_roots import make, own

RED, GREEN, GREEN2 = "topic: red-1\nRed things happen.", "topic: green-1\nGreen things happen.", "topic: green-2\nMore green things."


def flat_requests(wiki: Wiki) -> list[tuple[str, str]]:
    """The flat routing Choices the classifier was asked: (state, questions as JSON); None of these among folder paths."""
    out = []
    for op, state, questions in wiki.classifier.calls:  # type: ignore[union-attr]
        if op != "route":
            continue
        options = [o for q in questions.values() if q["type"] == "choice" for o in q["criteria"]]
        if "None of these" in options and all(o.startswith("/") for o in options if o != "None of these"):
            out.append((state, json.dumps(questions, sort_keys=True)))
    return out


def capped(bundle: Path) -> Wiki:
    wiki, _ = make(bundle, max_top_folders=2)
    own(wiki, "red", "blue")
    return wiki


def test_a_note_the_limit_deviates_asks_the_flat_choice_once_and_lands_where_it_did(bundle: Path) -> None:
    # ARRANGE: green has no root and cannot open one; the flat Choice says None of these, the walk opens a root, cap_roots files it
    wiki = capped(bundle)

    # ACT
    result = wiki.ingest(GREEN, title="Green")

    # ASSERT
    asked = flat_requests(wiki)
    assert len(asked) == 1
    assert result.folder == "/blue" and result.overflow is not None and set(result.overflow) == {"p_none", "p_root"}
    assert [d.step for d in result.decisions].count("route") >= 2  # the walk's route and cap_roots' fallback, as before


def test_each_document_asks_its_own_flat_choice(bundle: Path) -> None:
    wiki = capped(bundle)

    wiki.ingest(GREEN, title="Green")
    wiki.ingest(GREEN2, title="Green again")
    wiki.ingest(RED, title="Red")

    asked = flat_requests(wiki)
    assert len(asked) == 3 and len(set(asked)) == 3


def test_the_answer_is_taken_again_only_for_the_same_request(bundle: Path) -> None:
    wiki = capped(bundle)
    ctx = wiki.librarian().ctx
    cand = Candidate("A note", "", [], "topic: blue-1")
    tree = wiki.store.load()

    first = steps_crow.audit_route(ctx, tree, cand)
    again = steps_crow.audit_route(ctx, tree, cand)
    wiki.create_folder("/blue", "teal", "Teal things.")  # another folder: another request
    other = steps_crow.audit_route(ctx, wiki.store.load(), cand)

    assert first == again and first is not None and other is not None
    assert len(flat_requests(wiki)) == 2


def test_a_failed_flat_choice_is_asked_again(bundle: Path, monkeypatch: Any) -> None:
    wiki = capped(bundle)
    ctx = wiki.librarian().ctx
    cand = Candidate("A note", "", [], "topic: blue-1")
    tree = wiki.store.load()
    real = wiki.classifier.ask  # type: ignore[union-attr]
    failures = [1]

    def flaky(state: str, questions: dict[str, Any], *, op: str = "") -> Any:
        if failures:
            failures.pop()
            from llmw2.errors import ModelError

            raise ModelError("busy")
        return real(state, questions, op=op)

    monkeypatch.setattr(wiki.classifier, "ask", flaky)

    assert steps_crow.audit_route(ctx, tree, cand) is None  # the error: no folder
    assert steps_crow.audit_route(ctx, tree, cand) is not None  # not remembered: asked again


def test_with_the_routes_asked_ahead_each_document_asks_its_flat_choice_once_and_lands_where_it_did(bundle: Path) -> None:
    # ARRANGE: the filer asks the route of the next entry ahead (CROW, concurrency above 1): the rehearsal and the section ask the
    # flat Choice once each per document, so the section takes the one answer asked ahead instead of a second request
    from llmw2 import WikiConfig
    from llmw2.models.usage import UsageTracker
    from tests.wiki.test_crow_plan import PlanLLM, TopicClassifier

    llm = PlanLLM(UsageTracker())
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow", concurrency=4, max_top_folders=2), llm=llm, classifier=TopicClassifier(llm.usage))
    wiki.init()
    own(wiki, "red", "blue")
    texts = [f"topic: green-{i}\nGreen things number {i} happen." for i in range(1, 5)]

    # ACT
    try:
        wiki._stop_filer()
        receipts = [wiki.remember(text, title=f"Green {i}") for i, text in enumerate(texts, 1)]
        wiki._filer_stopped = False
        wiki.drain(60)
        notes = [r.note for r in receipts]
    finally:
        wiki.close()

    # ASSERT: every note deviated to /blue as without the rehearsal, and no flat request reached the classifier twice
    assert all(n and n.startswith("blue/") for n in notes)
    asked = flat_requests(wiki)
    assert len(asked) == len(set(asked)) == len(texts)


def test_the_routing_audit_keeps_the_flat_answers_of_one_note_at_a_time(bundle: Path, monkeypatch: Any) -> None:
    # ARRANGE: two notes filed; the audit gives each note the flat Choice on one StepContext
    wiki = capped(bundle)
    wiki.ingest(GREEN, title="Green")
    wiki.ingest(RED, title="Red")
    real = steps_crow.audit_route
    kept: list[int] = []

    def audit_route(ctx: Any, root: Any, cand: Candidate) -> Any:
        routed = real(ctx, root, cand)
        kept.append(len(ctx.flat_answers))
        return routed

    monkeypatch.setattr(steps_crow, "audit_route", audit_route)

    # ACT
    wiki.audit_routes()

    # ASSERT: after each note the memo holds that note's request only
    assert len(kept) >= 2 and max(kept) == 1
