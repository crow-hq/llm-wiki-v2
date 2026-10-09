# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tracking what decides a note: seed and pinned provider, provider in the usage log, the decision hash."""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, get_args

import httpx
import pytest

from llmw2 import Wiki
from llmw2.agents.prompts import Prompts
from llmw2.agents.steps import CLASSIC, CROW, STEP_NAMES, Steps, decision_hash
from llmw2.bundle.document import OKFDocument
from llmw2.config import ClassifierConfig, CrowConfig, LLMConfig, WikiConfig
from llmw2.models.client import HttpModel
from llmw2.models.llm import LLM
from llmw2.models.usage import UsageTracker
from llmw2.settings import Settings
from tests.wiki.fakes import FakeLLM
from tests.wiki.test_llm import BASE_URL, MODEL, completion, sent, serve

KEY = "sk-or-v1-0123456789abcdef"
PIN = {"order": ["DeepInfra", "Novita"], "allow_fallbacks": False, "require_parameters": True}


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(HttpModel, "backoff", 0)


def make(tracker: UsageTracker, *replies: httpx.Response, **cfg: Any) -> tuple[LLM, list[httpx.Request]]:
    client, requests = serve(*replies)
    config = LLMConfig(**{"provider": "openrouter", "base_url": BASE_URL, "model": MODEL, "api_key": "k", **cfg})
    return LLM(config, tracker, client=client), requests


def with_provider(provider: str | None) -> httpx.Response:
    body: dict[str, Any] = {"choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}]}
    if provider is not None:
        body["provider"] = provider
    return httpx.Response(200, json=body)


# -- seed and pinned provider in the request ------------------------------------------------


def test_defaults_when_nothing_is_set_then_no_seed_no_pin_and_temperature_stays_0_1() -> None:
    cfg = LLMConfig()

    assert (cfg.seed, cfg.pin_provider, cfg.temperature) == (None, [], 0.1)


def test_chat_when_no_seed_and_no_pin_then_the_body_has_neither(tracker: UsageTracker) -> None:
    llm, requests = make(tracker, completion("ok"))

    llm.complete("s", "u")

    body = sent(requests[0])
    assert "seed" not in body and "provider" not in body and body["temperature"] == 0.1


@pytest.mark.parametrize("seed", [42, 0])
def test_chat_when_a_seed_is_set_then_it_is_sent_even_if_zero(tracker: UsageTracker, seed: int) -> None:
    llm, requests = make(tracker, completion("ok"), seed=seed)

    llm.complete("s", "u")

    assert sent(requests[0])["seed"] == seed


def test_chat_when_pinned_on_openrouter_then_the_provider_block_forbids_fallbacks(tracker: UsageTracker) -> None:
    llm, requests = make(tracker, completion("ok"), pin_provider=["DeepInfra", "Novita"])

    llm.complete("s", "u")

    assert sent(requests[0])["provider"] == PIN


def test_chat_when_pinned_on_another_provider_then_no_provider_block_is_sent_and_a_warning_is_logged(
    tracker: UsageTracker, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        llm, requests = make(tracker, completion("ok"), provider="ollama", pin_provider=["DeepInfra"])
        llm.complete("s", "u")

    assert "provider" not in sent(requests[0])
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_chat_when_extra_body_names_a_provider_then_it_wins_over_the_pin(tracker: UsageTracker) -> None:
    mine = {"order": ["Mine"]}
    llm, requests = make(tracker, completion("ok"), pin_provider=["DeepInfra"], extra_body={"provider": mine})

    llm.complete("s", "u")

    assert sent(requests[0])["provider"] == mine


def test_chat_when_seed_and_pin_are_set_then_both_travel_with_the_other_fields(tracker: UsageTracker) -> None:
    llm, requests = make(tracker, completion("ok"), seed=7, pin_provider=["DeepInfra", "Novita"], temperature=0)

    llm.complete("s", "u")

    body = sent(requests[0])
    assert (body["seed"], body["provider"], body["temperature"], body["model"]) == (7, PIN, 0, MODEL)


# -- environment and settings page ---------------------------------------------------------------


def test_from_env_reads_the_seed_and_the_comma_separated_pin() -> None:
    cfg = WikiConfig.from_env({"OKF_LLM_SEED": "42", "OKF_LLM_PIN_PROVIDER": "DeepInfra,Novita"}, bundle="/tmp/w")

    assert (cfg.llm.seed, cfg.llm.pin_provider) == (42, ["DeepInfra", "Novita"])


def test_from_env_when_the_pin_has_spaces_around_the_commas_then_they_are_stripped() -> None:
    cfg = WikiConfig.from_env({"OKF_LLM_PIN_PROVIDER": " DeepInfra , Novita "}, bundle="/tmp/w")

    assert cfg.llm.pin_provider == ["DeepInfra", "Novita"]


def test_from_env_without_the_variables_then_no_seed_and_no_pin() -> None:
    cfg = WikiConfig.from_env({}, bundle="/tmp/w")

    assert (cfg.llm.seed, cfg.llm.pin_provider) == (None, [])


def test_settings_save_stores_the_seed_and_the_pin_and_an_empty_value_clears_them() -> None:
    settings = Settings({})

    cfg = settings.save({"api_key": KEY, "seed": "42", "pin_provider": "DeepInfra,Novita"})

    assert (cfg.llm.seed, cfg.llm.pin_provider) == (42, ["DeepInfra", "Novita"])
    assert Settings({}).config().llm.pin_provider == ["DeepInfra", "Novita"]
    values = settings.view()["values"]
    assert str(values["seed"]) == "42"
    assert [p.strip() for p in values["pin_provider"].split(",")] == ["DeepInfra", "Novita"]

    cleared = settings.save({"seed": "", "pin_provider": ""})

    assert (cleared.llm.seed, cleared.llm.pin_provider) == (None, [])
    assert Settings({}).config().llm.seed is None


def test_settings_view_by_default_shows_no_seed_and_no_pin() -> None:
    values = Settings({}).view()["values"]

    assert values["seed"] in (None, "") and values["pin_provider"] in ("", [], None)
    assert values["temperature"] == 0.1


def test_settings_when_the_environment_sets_the_seed_then_the_page_marks_it_managed() -> None:
    settings = Settings({"OKF_LLM_SEED": "5"})

    assert "seed" in settings.managed("openrouter") and settings.config().llm.seed == 5


# -- usage log -------------------------------------------------------------------------------


def logged(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_record_writes_provider_temperature_and_seed_in_the_log_line(tmp_path: Path) -> None:
    tracker = UsageTracker(tmp_path / "usage.jsonl")

    tracker.record("llm", "m", 10, 2, op="x", provider="DeepInfra", temperature=0.1, seed=7)

    [line] = logged(tmp_path / "usage.jsonl")
    assert (line["provider"], line["temperature"], line["seed"]) == ("DeepInfra", 0.1, 7)


def test_record_without_them_logs_the_keys_as_null(tmp_path: Path) -> None:
    tracker = UsageTracker(tmp_path / "usage.jsonl")

    tracker.record("classifier", "jev", 10, 0)

    [line] = logged(tmp_path / "usage.jsonl")
    assert {"provider", "temperature", "seed"} <= line.keys()
    assert (line["provider"], line["temperature"], line["seed"]) == (None, None, None)


def test_chat_logs_the_provider_of_the_response_with_its_temperature_and_seed(tmp_path: Path) -> None:
    tracker = UsageTracker(tmp_path / "usage.jsonl")
    llm, _ = make(tracker, with_provider("DeepInfra"), seed=7, temperature=0.2)

    llm.complete("s", "u", op="librarian/summarize")

    [line] = logged(tmp_path / "usage.jsonl")
    assert (line["provider"], line["temperature"], line["seed"], line["kind"]) == ("DeepInfra", 0.2, 7, "llm")


def test_chat_when_the_response_names_no_provider_then_it_logs_null_and_no_seed(tmp_path: Path) -> None:
    tracker = UsageTracker(tmp_path / "usage.jsonl")
    llm, _ = make(tracker, with_provider(None))

    llm.complete("s", "u")

    [line] = logged(tmp_path / "usage.jsonl")
    assert (line["provider"], line["seed"], line["temperature"]) == (None, None, 0.1)


# -- the decision hash --------------------------------------------------------------------------


def cfg_for(tmp_path: Path, **top: Any) -> WikiConfig:
    return WikiConfig(**{"bundle": tmp_path / "wiki", "mode": "crow", **top})


def digest(tmp_path: Path, *, steps: Steps = CROW, prompts: Prompts | None = None, **top: Any) -> str:
    return decision_hash(steps, cfg_for(tmp_path, **top), prompts or Prompts())


def test_decision_hash_is_a_string_and_equal_for_equal_inputs(tmp_path: Path) -> None:
    first, second = digest(tmp_path), digest(tmp_path)

    assert isinstance(first, str) and first and first == second


def test_decision_hash_is_the_same_in_another_process(tmp_path: Path) -> None:
    code = (
        "from pathlib import Path\n"
        "from llmw2 import WikiConfig\n"
        "from llmw2.agents.prompts import Prompts\n"
        "from llmw2.agents.steps import CROW, decision_hash\n"
        "print(decision_hash(CROW, WikiConfig(bundle=Path('/x/wiki'), mode='crow'), Prompts()))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60).stdout.strip()

    assert out == decision_hash(CROW, WikiConfig(bundle=Path("/x/wiki"), mode="crow"), Prompts())


def other_summarize(ctx: Any, source: Any) -> Any:
    return CROW.summarize(ctx, source)


def test_decision_hash_changes_with_the_step_code(tmp_path: Path) -> None:
    custom = replace(CROW, summarize=other_summarize)

    assert digest(tmp_path, steps=custom) != digest(tmp_path)
    assert digest(tmp_path, steps=CLASSIC) != digest(tmp_path, steps=CROW)


def test_decision_hash_when_a_step_is_a_partial_it_follows_the_wrapped_function(tmp_path: Path) -> None:
    one = replace(CROW, summarize=functools.partial(other_summarize))

    assert digest(tmp_path, steps=one) == digest(tmp_path, steps=replace(CROW, summarize=other_summarize))


@pytest.mark.parametrize("name", Prompts.names())
def test_decision_hash_changes_when_any_prompt_template_changes(tmp_path: Path, name: str) -> None:
    override = tmp_path / "prompts"
    target = override / f"{name}.md"
    target.parent.mkdir(parents=True)
    target.write_text(Prompts().load(name) + "\nOne more rule.\n", encoding="utf-8")

    assert decision_hash(CROW, cfg_for(tmp_path), Prompts(override)) != digest(tmp_path)


def test_prompts_fingerprint_is_stable_and_follows_the_prompts_in_use(tmp_path: Path) -> None:
    override = tmp_path / "prompts"
    (override / "librarian").mkdir(parents=True)
    (override / "librarian" / "summarize.md").write_text("Summarize {{text}}.", encoding="utf-8")

    plain, changed = Prompts().fingerprint(), Prompts(override).fingerprint()

    assert isinstance(plain, str) and plain == Prompts().fingerprint()
    assert changed != plain and changed == Prompts(override).fingerprint()


def test_decision_hash_when_the_override_dir_holds_the_packaged_text_then_it_is_unchanged(tmp_path: Path) -> None:
    override = tmp_path / "prompts"
    (override / "librarian").mkdir(parents=True)
    (override / "librarian" / "merge.md").write_text(Prompts().load("librarian/merge"), encoding="utf-8")

    assert decision_hash(CROW, cfg_for(tmp_path), Prompts(override)) == digest(tmp_path)


def decide(**changes: Any) -> dict[str, Any]:
    return changes


DECIDING = {
    "librarian-model": {"llm": LLMConfig(model="other/writer")},
    "classifier-model": {"classifier": ClassifierConfig(model="other/classifier")},
    "temperature": {"llm": LLMConfig(temperature=0.7)},
    "seed": {"llm": LLMConfig(seed=7)},
    "pin": {"llm": LLMConfig(pin_provider=["DeepInfra"])},
    "max_depth": {"max_depth": 2},
    "max_links": {"max_links": 1},
    "mode": {"mode": "classic"},
    "summarize": {"summarize": False},
}


@pytest.mark.parametrize("change", DECIDING.values(), ids=DECIDING.keys())
def test_decision_hash_changes_with_what_decides(tmp_path: Path, change: dict[str, Any]) -> None:
    assert digest(tmp_path, **change) != digest(tmp_path)


def test_decision_hash_tells_two_seeds_and_two_pins_apart(tmp_path: Path) -> None:
    seeds = {digest(tmp_path, llm=LLMConfig(seed=s)) for s in (None, 0, 1, 2)}
    pins = {digest(tmp_path, llm=LLMConfig(pin_provider=p)) for p in ([], ["A"], ["B"], ["A", "B"])}

    assert len(seeds) == 4 and len(pins) == 4


def crow_variant(name: str) -> CrowConfig:
    default = CrowConfig.model_fields[name].default
    if isinstance(default, str):  # a choice: the other one
        return CrowConfig(**{name: next(c for c in get_args(CrowConfig.model_fields[name].annotation) if c != default)})
    return CrowConfig(**{name: default + 1 if isinstance(default, int) else round(default / 2, 4)})


@pytest.mark.parametrize("name", list(CrowConfig.model_fields))
def test_decision_hash_changes_with_each_crow_threshold_beam_and_k(tmp_path: Path, name: str) -> None:
    assert digest(tmp_path, crow=crow_variant(name)) != digest(tmp_path)


IRRELEVANT = {
    "llm-api-key": {"llm": LLMConfig(api_key="another-secret")},
    "llm-base-url": {"llm": LLMConfig(base_url="https://gateway.test/v1")},
    "llm-timeout": {"llm": LLMConfig(timeout=7)},
    "classifier-api-key": {"classifier": ClassifierConfig(api_key="another-secret")},
    "classifier-base-url": {"classifier": ClassifierConfig(base_url="https://clf.test/v1")},
    "classifier-timeout": {"classifier": ClassifierConfig(timeout=3)},
    "classifier-attempts": {"classifier": ClassifierConfig(attempts=3)},
    "upload_mb": {"upload_mb": 5},
    "concurrency": {"concurrency": 2},
    "usage_log": {"usage_log": Path("/tmp/usage.jsonl")},
}


@pytest.mark.parametrize("change", IRRELEVANT.values(), ids=IRRELEVANT.keys())
def test_decision_hash_ignores_what_does_not_decide(tmp_path: Path, change: dict[str, Any]) -> None:
    assert digest(tmp_path, **change) == digest(tmp_path)


def test_decision_hash_ignores_where_the_bundle_lives(tmp_path: Path) -> None:
    a = decision_hash(CROW, WikiConfig(bundle=tmp_path / "a", mode="crow"), Prompts())
    b = decision_hash(CROW, WikiConfig(bundle=tmp_path / "b", mode="crow"), Prompts())

    assert a == b


def test_steps_hash_is_still_over_the_ten_functions_only() -> None:
    parts = []
    for name in STEP_NAMES:
        fn = getattr(CROW, name)
        while isinstance(fn, functools.partial):
            fn = fn.func
        parts.append(inspect.getsource(fn))
    expected = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:8]

    assert CROW.hash == expected


# -- provenance and log -----------------------------------------------------------------------


def script(llm: FakeLLM) -> None:
    llm.add("librarian/summarize", {"title": "Revenue", "summary": "About revenue.", "body": "Body."})
    llm.add("librarian/route", {"action": "new"}).add("librarian/name_folder", {"name": "finance", "description": "Money."})


def classic_wiki_with(bundle: Path, llm: FakeLLM, **llm_cfg: Any) -> Wiki:
    wiki = Wiki(WikiConfig(bundle=bundle, mode="classic", llm=LLMConfig(model="fake/llm", **llm_cfg)), llm=llm)
    wiki.init()
    return wiki


def log_line(bundle: Path) -> str:
    [line] = [x for x in (bundle / "log.md").read_text(encoding="utf-8").splitlines() if "from " in x and x.startswith("* ")]
    return line


def test_ingest_when_not_pinned_then_the_note_and_the_log_carry_the_decision_hash(bundle: Path, llm: FakeLLM) -> None:
    wiki = classic_wiki_with(bundle, llm)
    script(llm)

    result = wiki.ingest("Booked on delivery.")

    expected = decision_hash(wiki.steps, wiki.cfg, wiki.prompts)
    [source] = OKFDocument.parse((bundle / result.note).read_text(encoding="utf-8")).frontmatter["sources"]
    assert source["steps"] == {"name": "classic", "hash": expected}
    assert expected != wiki.steps.hash
    line = log_line(bundle)
    assert f"(classic {expected}, fake/llm)" in line and "via" not in line


def test_ingest_when_pinned_then_the_log_line_names_the_providers(bundle: Path, llm: FakeLLM) -> None:
    wiki = classic_wiki_with(bundle, llm, pin_provider=["DeepInfra", "Novita"])
    script(llm)

    wiki.ingest("Booked on delivery.")

    expected = decision_hash(wiki.steps, wiki.cfg, wiki.prompts)
    line = log_line(bundle)
    assert f"(classic {expected}, fake/llm)" in line or f"(classic {expected}, fake/llm" in line
    assert line.rstrip().endswith("via DeepInfra,Novita") or ", via DeepInfra,Novita" in line


def test_ingest_hash_in_the_note_changes_when_the_seed_changes(tmp_path: Path) -> None:
    hashes = []
    for i, seed in enumerate((None, 3)):
        llm = FakeLLM(UsageTracker())
        wiki = classic_wiki_with(tmp_path / f"w{i}", llm, seed=seed)
        script(llm)
        result = wiki.ingest("Booked on delivery.")
        [source] = OKFDocument.parse((tmp_path / f"w{i}" / result.note).read_text(encoding="utf-8")).frontmatter["sources"]
        hashes.append(source["steps"]["hash"])

    assert hashes[0] != hashes[1]
