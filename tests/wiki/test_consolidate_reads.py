# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`--consolidate-reads source` of benchmarks/stability.py: consolidate reads the source extract plus the note, as routing does."""

from __future__ import annotations

import argparse
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from llmw2 import Wiki, WikiConfig
from llmw2.agents.base import Decision
from llmw2.agents.state import source_state
from llmw2.errors import ModelError
from tests.wiki.fakes import FakeClassifier, FakeLLM
from tests.wiki.test_stability_metrics import load

HERE, NEW = "Here", "New subfolder"
MODIFY, NEW_NOTE = "Modify", "New note"
SUMMARIZE, MERGE = "librarian/summarize", "librarian/merge"
RAW = "RAWMARKER prices went up by ten percent."
LLM_REPLY = {"title": "LLMTITLE", "summary": "LLMSUMMARY", "tags": [], "body": "# Overview\n\nLLMBODY"}
SEPARATOR = "\n\n--- Existing note ---\n"


@pytest.fixture(scope="module")
def stability() -> ModuleType:
    return load()


def ns(**kw: Any) -> argparse.Namespace:
    base = {"route_reads": "source", "merge_reads": "note", "consolidate_reads": "summary"}
    return argparse.Namespace(**{**base, **kw})


def make_wiki(bundle: Path, llm: FakeLLM, classifier: FakeClassifier, steps: Any) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="crow"), llm=llm, classifier=classifier, steps=steps)
    wiki.init()
    fruit, _ = wiki.store.create_folder(wiki.store.load(), "fruit", "Fruit and its prices.")
    source = {"resource": "/raw/s.md", "title": "S"}
    wiki.store.write_note(fruit, title="Apple", summary="About Apple.", body="# Overview\n\nSeeded.", tags=[], source=source)
    return wiki


def prepare(classifier: FakeClassifier, llm: FakeLLM, choice: str = MODIFY, confidence: float = 0.9) -> None:
    llm.add(SUMMARIZE, LLM_REPLY).add(MERGE, LLM_REPLY)
    classifier.add_choice("route", "fruit", {"fruit": 0.9, NEW: 0.1}, 0.9)
    classifier.add_choice("route", HERE, {HERE: 0.9, NEW: 0.05}, 0.9)
    classifier.noul_scores["match"] = {'"Apple"': 0.9}
    classifier.add_choice("consolidate", choice, {choice: confidence}, confidence)


def consolidate_states(classifier: FakeClassifier) -> list[str]:
    return [state for op, state, _ in classifier.calls if op == "consolidate"]


# -- the option -------------------------------------------------------------------------------


def test_option_defaults_to_summary_and_accepts_source(stability: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[argparse.Namespace] = []

    class Stop(Exception):
        pass

    def run_once(scenario: Any, items: Any, labels: Any, variant: Any, args: argparse.Namespace, *rest: Any, **kw: Any) -> None:
        seen.append(args)
        raise Stop

    monkeypatch.setattr(stability, "run_once", run_once)
    for extra, expected in (([], "summary"), (["--consolidate-reads", "source"], "source")):
        with pytest.raises(Stop):
            stability.main(["--dry-run", "--runs", "1", "--out", str(tmp_path / "o.json"), *extra])
        assert seen[-1].consolidate_reads == expected


def test_option_rejects_other_values(stability: ModuleType, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        stability.main(["--dry-run", "--consolidate-reads", "note"])

    assert "consolidate-reads" in capsys.readouterr().err


def test_option_is_recorded_in_the_run_args(stability: ModuleType) -> None:
    args = ns(
        consolidate_reads="source", votes=None, tau_cons=None, prompts=None, base=None, cache=None,
        cache_ops=None, corpus=None, only=None, fallback_menu=None, variant=["base"],
    )  # fmt: skip

    assert stability.defining(args, "base")["consolidate_reads"] == "source"


# -- the steps ----------------------------------------------------------------------------------


def test_summary_changes_nothing(stability: ModuleType) -> None:
    assert stability.steps_for(ns(consolidate_reads="summary"), "crow") is None


def test_source_replaces_consolidate_and_names_the_steps(stability: ModuleType) -> None:
    steps = stability.steps_for(ns(consolidate_reads="source"), "crow")

    assert steps is not None
    assert steps.name != "crow"
    assert "consolidate" in steps.name


@pytest.mark.parametrize(
    ("kw", "words"),
    [({"route_reads": "summary"}, ("consolidate", "summarize")), ({"merge_reads": "source"}, ("consolidate", "merge"))],
)
def test_combined_with_the_other_options_every_change_is_in_the_name(
    stability: ModuleType, kw: dict[str, str], words: tuple[str, ...]
) -> None:
    steps = stability.steps_for(ns(consolidate_reads="source", **kw), "crow")
    alone = stability.steps_for(ns(consolidate_reads="source"), "crow")

    assert steps is not None
    assert all(w in steps.name for w in words)
    assert steps.name != alone.name


# -- the state the classifier reads -----------------------------------------------------------------


def test_state_is_the_source_extract_and_the_note_without_the_llm_candidate(
    stability: ModuleType, bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, stability.steps_for(ns(consolidate_reads="source"), "crow"))
    prepare(classifier, llm)
    note = wiki.store.load().find("/fruit").notes[0]

    wiki.ingest(RAW, title="Memo")

    (state,) = consolidate_states(classifier)
    room = max(classifier.request_chars // 2, 1)
    assert state == source_state("Memo", RAW, wiki.cfg.classifier.state_chars) + SEPARATOR + note.full_text()[:room]
    assert "RAWMARKER" in state
    for llm_text in ("LLMTITLE", "LLMSUMMARY", "LLMBODY"):
        assert llm_text not in state


def test_default_consolidate_state_of_a_short_source_is_the_source_state(
    stability: ModuleType, bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, stability.steps_for(ns(consolidate_reads="summary"), "crow"))
    prepare(classifier, llm)

    wiki.ingest(RAW, title="Memo")

    (state,) = consolidate_states(classifier)
    assert "RAWMARKER" in state  # round 5: a source up to twice note_chars is read as the source state
    assert "LLMTITLE" not in state


def test_a_long_note_is_cut_at_request_chars_over_two(
    stability: ModuleType, bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    wiki = make_wiki(bundle, llm, classifier, stability.steps_for(ns(consolidate_reads="source"), "crow"))
    fruit = wiki.store.load().find("/fruit")
    long_note = wiki.store.write_note(
        fruit, title="Banana", summary="About Banana.", body="# Overview\n\n" + "yellow " * 300 + "ENDMARK", tags=[],
        source={"resource": "/raw/b.md", "title": "B"},
    )  # fmt: skip
    prepare(classifier, llm)
    classifier.noul_scores["match"] = {'"Banana"': 0.9}
    classifier.request_chars = 600

    wiki.ingest(RAW, title="Memo")

    (state,) = consolidate_states(classifier)
    assert state.split(SEPARATOR, 1)[1] == long_note.full_text()[:300]
    assert "ENDMARK" not in state


# -- the decision -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("choice", "confidence", "action", "decided"),
    [(MODIFY, 0.9, "merged", "modify"), (MODIFY, 0.5, "created", "new"), (NEW_NOTE, 0.9, "created", "new")],
)
def test_same_rule_and_record_as_crow(
    stability: ModuleType,
    bundle: Path,
    llm: FakeLLM,
    classifier: FakeClassifier,
    choice: str,
    confidence: float,
    action: str,
    decided: str,
) -> None:
    wiki = make_wiki(bundle, llm, classifier, stability.steps_for(ns(consolidate_reads="source"), "crow"))
    prepare(classifier, llm, choice, confidence)

    result = wiki.ingest(RAW, title="Memo")

    assert result.action == action
    assert Decision("consolidate", "classifier", decided, confidence, scores={choice: confidence}) in result.decisions


def test_tau_cons_is_the_threshold(stability: ModuleType, bundle: Path, llm: FakeLLM, classifier: FakeClassifier) -> None:
    wiki = make_wiki(bundle, llm, classifier, stability.steps_for(ns(consolidate_reads="source"), "crow"))
    wiki.cfg.crow.tau_cons = 0.95
    prepare(classifier, llm, MODIFY, 0.9)

    assert wiki.ingest(RAW, title="Memo").action == "created"


def test_a_classifier_error_falls_back_to_the_llm_consolidate(
    stability: ModuleType, bundle: Path, llm: FakeLLM, classifier: FakeClassifier, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki = make_wiki(bundle, llm, classifier, stability.steps_for(ns(consolidate_reads="source"), "crow"))
    prepare(classifier, llm)
    llm.add("librarian/consolidate", {"decision": "new"})
    ask = classifier.ask

    def broken(state: str, questions: dict[str, dict[str, Any]], *, op: str = "") -> dict[str, dict[str, Any]]:
        if op == "consolidate":
            raise ModelError("classifier unreachable")
        return ask(state, questions, op=op)

    monkeypatch.setattr(classifier, "ask", broken)

    result = wiki.ingest(RAW, title="Memo")

    assert "librarian/consolidate" in llm.ops
    assert result.action == "created"
    assert Decision("consolidate", "classifier", "error", None, fallback=True) in result.decisions


def test_combined_with_summary_routing_and_source_merge(
    stability: ModuleType, bundle: Path, llm: FakeLLM, classifier: FakeClassifier
) -> None:
    args = ns(consolidate_reads="source", route_reads="summary", merge_reads="source")
    wiki = make_wiki(bundle, llm, classifier, stability.steps_for(args, "crow"))
    prepare(classifier, llm)

    result = wiki.ingest(RAW, title="Memo")

    assert result.action == "merged"
    (state,) = consolidate_states(classifier)
    assert state.split(SEPARATOR)[0].startswith("Title: LLMTITLE")  # the spec: ctx.route_state(cand), here the summary state
    assert SEPARATOR in state
    assert "LLMTITLE" in next(s for op, s, _ in classifier.calls if op == "route")  # routing reads the summary
    merge_prompt = next(msgs for op, msgs in llm.calls if op == MERGE)[1]["content"]
    assert "RAWMARKER" in merge_prompt  # the merge reads the source


def test_dry_run_classic_mode_keeps_working_without_a_classifier(stability: ModuleType, bundle: Path, llm: FakeLLM) -> None:
    steps = stability.steps_for(ns(consolidate_reads="source"), "classic")
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic"), llm=llm, steps=steps)
    wiki.init()
    fruit, _ = wiki.store.create_folder(wiki.store.load(), "fruit", "Fruit.")
    source = {"resource": "/raw/s.md", "title": "S"}
    wiki.store.write_note(fruit, title="Apple", summary="About Apple.", body="# Overview\n\nSeeded.", tags=[], source=source)
    llm.add(SUMMARIZE, LLM_REPLY).add("librarian/route", {"action": "descend", "subfolder": "fruit"}, {"action": "here"})
    llm.add("librarian/find_match", {"match": "fruit/apple.md"}).add("librarian/consolidate", {"decision": "new"})
    llm.add("librarian/relate", {"related": []})

    result = wiki.ingest(RAW, title="Memo")

    assert result.action == "created"


def test_merge_no_length_drops_the_length_rule_from_the_merge_prompt_only(stability: ModuleType, tmp_path: Path) -> None:
    # written by the orchestrator (benchmark knob of round 3, not from a blind spec)
    from llmw2 import Source, StepContext
    from llmw2.agents.prompts import Prompts
    from llmw2.agents.steps import LENGTH_RULE

    args = argparse.Namespace(route_reads="source", merge_reads="source", consolidate_reads="summary", merge_no_length=True)
    steps = stability.steps_for(args, "crow")
    seen: dict[str, str] = {}

    class Recorder:
        def json(self, system: str, user: str, schema: Any, *, op: str = "", max_tokens: Any = None) -> Any:
            seen[op] = user
            return schema(title="T", summary="S", tags=[], body="B")

    cfg = WikiConfig(bundle=tmp_path)
    ctx = StepContext(Recorder(), None, Prompts(), cfg, system_prompt="librarian/system")  # type: ignore[arg-type]
    note = type("N", (), {"main_body": lambda self: "old body", "title": "Old", "summary": "s", "tags": []})()
    steps.merge(ctx, note, Source("the source text", title="Src"))
    rule = LENGTH_RULE.format(note_chars=cfg.effective_note_chars).strip()
    assert rule not in seen["librarian/merge"]
    assert "the source text" in seen["librarian/merge"]
    assert "length_rule" not in ctx.__dict__ and "_values" not in ctx.__dict__  # the context is left as it was
