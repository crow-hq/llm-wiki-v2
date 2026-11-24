# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`crow.fallback_reply_tokens`: the output cap of the routing fallback's reply. Unset, the fallback sends the requests of
before (the cap of a decision) with every provider. Set, that call alone is asked with that cap, below the floor of the
others; a reply cut at it is asked once more, shorter, then on Ollama the classifier's best path decides, and with any other
provider the ingest stops."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from llmw2 import ModelError, StepContext, Wiki, WikiConfig
from llmw2.agents import steps_classic as classic
from llmw2.agents import steps_crow
from llmw2.agents.data import Candidate
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CROW, decision_hash
from llmw2.bundle.tree import Folder
from llmw2.config import CrowConfig
from llmw2.errors import ReplyCut
from llmw2.models.llm import MIN_REPLY_TOKENS, REASONING_ROOM, reply_tokens
from llmw2.models.usage import UsageTracker
from tests.wiki.fakes import FakeClassifier
from tests.wiki.test_reply_cap import CapRecorder, completion, make_llm, sent

ROUTE_FALLBACK, SUMMARIZE = "librarian/route_fallback", "librarian/summarize"
DRAFT = {"title": "Q3 revenue", "summary": "Revenue.", "tags": [], "body": "# Overview\n\nFacts."}
ROUTED = '{"reasoning": "Alpha fits.", "action": "select", "folder": "/alpha"}'
CAND = Candidate("Q3 revenue", "Revenue.", [], "# Overview\n\nFacts.")
DECIDE = reply_tokens(classic.DECIDE_CHARS)
CAP = 400
PROVIDERS = ["ollama", "openrouter", "openai", "gemini", "custom"]
NO_SWITCH = ("openai", "gemini", "custom")  # presets with no reasoning off switch: the model may think


class CutFallback(CapRecorder):
    """Notes the cap of every call; the routing fallback's replies are cut at it."""

    def chat(self, messages: list[dict[str, str]], *, op: str = "", json_mode: bool = False, max_tokens: int | None = None) -> str:  # type: ignore[override]
        if op == ROUTE_FALLBACK:
            self.caps.append((op, max_tokens))
            raise ReplyCut(f"llm {op}: reply cut at {max_tokens} output tokens (finish_reason length)", text='{"reasoning": "')
        return super().chat(messages, op=op, json_mode=json_mode, max_tokens=max_tokens)


def fallback_wiki(bundle: Path, llm: CapRecorder, tracker: UsageTracker, provider: str = "ollama", cap: int | None = None) -> Wiki:
    """A CROW wiki with /alpha and /beta whose classifier is unsure at the root, so the LLM routes the next document."""
    classifier = FakeClassifier(tracker)
    cfg = WikiConfig(
        bundle=bundle, mode="crow", crow={"beam": 1, "fallback_reply_tokens": cap}, strict_new_folders=False,
        llm={"provider": provider, "model": "small", "base_url": "http://localhost:1234/v1"},
    )
    wiki = Wiki(cfg, llm=llm, classifier=classifier, steps=replace(CROW, name="levels", route=steps_crow._route_by_levels))
    wiki.init()
    root = wiki.store.load()
    wiki.store.create_folder(root, "alpha", "Alpha things.")
    wiki.store.create_folder(root, "beta", "Beta things.")
    llm.add(SUMMARIZE, DRAFT)
    classifier.add_choice("route", "alpha", {"alpha": 0.2, "beta": 0.1, "New subfolder": 0.1}, 0.9)  # under tau_path
    for _ in range(3):
        classifier.add_choice("route", "Here", {"Here": 0.9, "New subfolder": 0.05, "None of these": 0.05}, 0.9)
    return wiki


def fallback_caps(llm: CapRecorder) -> list[int | None]:
    return [cap for op, cap in llm.caps if op == ROUTE_FALLBACK]


def bodies(
    tracker: UsageTracker, bundle: Path, provider: str, reasoning: bool, cap: int | None, ask: Callable[[StepContext, Folder], object]
) -> list[dict[str, Any]]:
    """The request bodies a real client on `provider` sends for `ask`, given a step context and the root of a wiki with /alpha."""
    llm, requests = make_llm(tracker, completion(ROUTED), provider=provider, reasoning=reasoning)
    wiki = fallback_wiki(bundle, CapRecorder(tracker), tracker, provider=provider, cap=cap)
    ask(StepContext(llm, None, Prompts(), wiki.cfg, "librarian/system"), wiki.store.load())
    return [sent(r) for r in requests]


def fallback(ctx: StepContext, root: Folder) -> object:
    alpha = root.find("/alpha")
    return steps_crow._route_fallback(ctx, root, [alpha], CAND, 0.2, alpha)


def before(ctx: StepContext, root: Folder) -> object:
    """The fallback's call as it was: the cap of a decision."""
    menu = steps_crow._fallback_menu(ctx, root, [root.find("/alpha")])
    state = steps_crow.routing_state(ctx, CAND)
    return ctx.ask_json(ROUTE_FALLBACK, classic.FallbackRoute, folders=menu, note=state, reply_chars=classic.DECIDE_CHARS)


def cap_sent(body: dict[str, Any]) -> int:
    return body.get("max_tokens", body.get("max_completion_tokens"))


# -- the setting ---------------------------------------------------------------------------------------------------


def test_the_setting_is_unset_by_default_and_read_from_its_variable(tmp_path: Path) -> None:
    assert CrowConfig().fallback_reply_tokens is None
    env = {"OKF_BUNDLE": str(tmp_path), "OKF_MODE": "classic", "OKF_FALLBACK_REPLY_TOKENS": "400"}
    assert WikiConfig.from_env(env).crow.fallback_reply_tokens == CAP


def test_the_setting_is_refused_below_one() -> None:
    with pytest.raises(ValueError, match="fallback_reply_tokens"):
        CrowConfig(fallback_reply_tokens=0)


def test_the_setting_does_not_change_the_decision_hash(tmp_path: Path) -> None:
    def digest(crow: CrowConfig) -> str:
        return decision_hash(CROW, WikiConfig(bundle=tmp_path, mode="crow", crow=crow), Prompts())

    assert digest(CrowConfig(fallback_reply_tokens=CAP)) == digest(CrowConfig())


# -- unset: the requests of before, with every provider -------------------------------------------------------------


@pytest.mark.parametrize("provider", PROVIDERS)
def test_unset_the_route_fallback_keeps_the_cap_of_a_decision(bundle: Path, tracker: UsageTracker, provider: str) -> None:
    llm = CapRecorder(tracker)
    wiki = fallback_wiki(bundle, llm, tracker, provider=provider)
    llm.add(ROUTE_FALLBACK, ROUTED)

    result = wiki.ingest("Revenue grew.")

    assert result.folder == "/alpha"
    assert fallback_caps(llm) == [DECIDE]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("reasoning", [False, True])
def test_unset_the_fallback_sends_the_request_of_before(tmp_path: Path, tracker: UsageTracker, provider: str, reasoning: bool) -> None:
    now = bodies(tracker, tmp_path / "now", provider, reasoning, None, fallback)
    then = bodies(tracker, tmp_path / "then", provider, reasoning, None, before)

    assert now == then
    thinks = reasoning or provider in NO_SWITCH
    assert cap_sent(now[0]) == DECIDE + (REASONING_ROOM if thinks else 0)


# -- set: that call alone, with that cap ----------------------------------------------------------------------------


def test_set_only_the_route_fallback_is_asked_with_it(bundle: Path, tracker: UsageTracker) -> None:
    llm = CapRecorder(tracker)
    wiki = fallback_wiki(bundle, llm, tracker, cap=CAP)
    llm.add(ROUTE_FALLBACK, ROUTED)

    result = wiki.ingest("Revenue grew.")

    assert result.folder == "/alpha"
    assert fallback_caps(llm) == [CAP]
    assert dict(llm.caps)[SUMMARIZE] == reply_tokens(wiki.cfg.effective_note_chars)
    others = [cap for op, cap in llm.caps if op != ROUTE_FALLBACK]
    assert others and all(cap is None or cap >= MIN_REPLY_TOKENS for cap in others)  # the floor still holds for every other call


@pytest.mark.parametrize(
    ("provider", "reasoning", "expected"),
    [
        ("ollama", False, CAP),
        ("ollama", True, CAP + REASONING_ROOM),
        ("openrouter", False, CAP),
        ("openrouter", True, CAP + REASONING_ROOM),
        ("custom", False, CAP + REASONING_ROOM),
    ],
)
def test_set_the_cap_is_sent_with_the_reasoning_room_of_a_model_that_may_think(
    tmp_path: Path, tracker: UsageTracker, provider: str, reasoning: bool, expected: int
) -> None:
    # reasoning off: the cap is sent as it is; a model that may think gets its room on top, so thinking does not eat the reply
    assert [cap_sent(b) for b in bodies(tracker, tmp_path, provider, reasoning, CAP, fallback)] == [expected]


def test_set_a_reply_cut_twice_on_ollama_lets_the_classifiers_best_path_decide(bundle: Path, tracker: UsageTracker) -> None:
    llm = CutFallback(tracker)
    wiki = fallback_wiki(bundle, llm, tracker, cap=CAP)

    result = wiki.ingest("Revenue grew.")

    assert fallback_caps(llm) == [CAP, CAP]  # asked once more, shorter
    assert result.folder == "/alpha"
    assert any(d.step == "route" and d.decider == "code" for d in result.decisions)


def test_set_a_reply_cut_twice_on_another_provider_stops_the_ingest(bundle: Path, tracker: UsageTracker) -> None:
    llm = CutFallback(tracker)
    wiki = fallback_wiki(bundle, llm, tracker, provider="openrouter", cap=CAP)

    with pytest.raises(ModelError, match="finish_reason length"):
        wiki.ingest("Revenue grew.")

    assert fallback_caps(llm) == [CAP, CAP]


def test_a_reply_cut_at_the_cap_is_asked_again_shorter(tracker: UsageTracker) -> None:
    llm, requests = make_llm(tracker, completion('{"reasoning": "on and on', "length"), completion(ROUTED), provider="ollama")

    out = llm.json("sys", "Route it.", classic.FallbackRoute, op=ROUTE_FALLBACK, max_tokens=CAP)

    assert out.folder == "/alpha"
    assert [cap_sent(sent(r)) for r in requests] == [CAP, CAP]
    assert "(400 tokens)" in sent(requests[1])["messages"][-1]["content"]


# -- ask_json -------------------------------------------------------------------------------------------------------


def test_ask_json_takes_reply_chars_or_max_tokens_not_both(tracker: UsageTracker, bundle: Path) -> None:
    llm = CapRecorder(tracker).add(ROUTE_FALLBACK, ROUTED)
    ctx = StepContext(llm, None, Prompts(), WikiConfig(bundle=bundle), "librarian/system")

    with pytest.raises(ValueError, match="not both"):
        ctx.ask_json(ROUTE_FALLBACK, classic.FallbackRoute, folders="", note="", reply_chars=2_000, max_tokens=CAP)

    assert llm.caps == []  # nothing was asked
