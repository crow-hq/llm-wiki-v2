# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from operator import attrgetter
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from okf_wiki.config import WikiConfig


def env_for(bundle: Path, **variables: str) -> dict[str, str]:
    return {"OKF_BUNDLE": str(bundle), **variables}


@pytest.mark.parametrize(
    ("variable", "value", "field", "expected"),
    [
        ("OKF_SUMMARIZE", "false", "summarize", False),
        ("OKF_MAX_DEPTH", "2", "max_depth", 2),
        ("OKF_LLM_MODEL", "Qwen/Qwen2.5-7B-Instruct", "llm.model", "Qwen/Qwen2.5-7B-Instruct"),
        ("OKF_LLM_TEMPERATURE", "0.7", "llm.temperature", 0.7),
        ("OKF_LLM_REASONING", "true", "llm.reasoning", True),
        ("OKF_LLM_REASONING", "false", "llm.reasoning", False),
        ("OKF_CLASSIFIER_STATE_CHARS", "1000", "classifier.state_chars", 1000),
        ("OKF_TAU_ROUTE", "0.8", "crow.tau_route", 0.8),
        ("OKF_BEAM", "3", "crow.beam", 3),
        ("OKF_RETRIEVAL_BEAM", "4", "crow.retrieval_beam", 4),
        ("OKF_CLASSIFIER_ATTEMPTS", "3", "classifier.attempts", 3),
        ("OKF_UPLOAD_MB", "5", "upload_mb", 5),
    ],
)
def test_from_env_when_a_variable_is_set_then_its_field_takes_the_value(
    tmp_path: Path, variable: str, value: str, field: str, expected: object
) -> None:
    # ARRANGE
    env = env_for(tmp_path, **{variable: value})

    # ACT
    config = WikiConfig.from_env(env)

    # ASSERT
    assert attrgetter(field)(config) == expected


def test_from_env_ignores_empty_variables(tmp_path: Path) -> None:
    config = WikiConfig.from_env(env_for(tmp_path, OKF_MODE="", OKF_LLM_MODEL="", OKF_BEAM=""))

    assert config.mode == "crow"
    assert config.llm.model == "google/gemini-3.8-flash"
    assert config.crow.beam == 2


def test_from_env_when_nothing_is_set_then_retrieval_uses_the_calibrated_defaults(tmp_path: Path) -> None:
    # ACT
    config = WikiConfig.from_env(env_for(tmp_path))

    # ASSERT
    crow = config.crow
    assert (crow.tau_fold, crow.tau_ret, crow.retrieval_beam, crow.k, crow.beam) == (0.08, 0.1, 6, 8, 2)
    assert (config.max_notes, config.classifier.attempts, config.classifier.timeout) == (8, 1, 8.0)


def test_a_passed_mapping_replaces_the_process_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKF_MODE", "classic")

    assert WikiConfig.from_env(env_for(tmp_path)).mode == "crow"


def test_without_a_mapping_the_process_environment_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKF_BUNDLE", str(tmp_path))
    monkeypatch.setenv("OKF_MODE", "crow")

    config = WikiConfig.from_env()

    assert (config.bundle, config.mode) == (tmp_path, "crow")


def test_openrouter_api_key_serves_the_llm_and_the_classifier(tmp_path: Path) -> None:
    config = WikiConfig.from_env(env_for(tmp_path, OPENROUTER_API_KEY="or-key"))

    assert (config.llm.api_key, config.classifier.api_key) == ("or-key", "or-key")


def test_the_llm_key_is_shared_only_with_an_openrouter_classifier(tmp_path: Path) -> None:
    config = WikiConfig(bundle=tmp_path, llm={"api_key": "or-key"})
    assert config.classifier.api_key == "or-key"

    config = WikiConfig(bundle=tmp_path, llm={"provider": "gemini", "api_key": "g-key"})
    assert config.classifier.api_key == ""


def test_config_when_the_llm_is_openrouter_at_another_address_then_the_classifier_gets_no_key(tmp_path: Path) -> None:
    # ACT
    config = WikiConfig(bundle=tmp_path, llm={"api_key": "or-key", "base_url": "http://gateway.lan/v1"})

    # ASSERT
    assert config.classifier.api_key == ""


@pytest.mark.parametrize(
    ("variables", "key"),
    [({"OPENROUTER_API_KEY": "or-key"}, ""), ({"TYPESAFE_API_KEY": "ts-key", "OPENROUTER_API_KEY": "or-key"}, "ts-key")],
    ids=["openrouter-key-stays-home", "own-variable"],
)
def test_from_env_when_the_classifier_is_on_typesafe_then_only_its_own_key_goes_there(
    tmp_path: Path, variables: dict[str, str], key: str
) -> None:
    # ACT
    config = WikiConfig.from_env(env_for(tmp_path, OKF_CLASSIFIER_PROVIDER="typesafe", **variables))

    # ASSERT
    assert (config.classifier.base_url, config.classifier.api_key) == ("https://api.typesafe.ai/v1", key)
    assert config.llm.api_key == variables["OPENROUTER_API_KEY"]


def test_config_when_the_classifier_provider_is_unknown_then_it_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown classifier provider 'laya'"):
        WikiConfig(bundle=tmp_path, classifier={"provider": "laya"})


def test_the_provider_fills_base_url_model_and_key(tmp_path: Path) -> None:
    env = env_for(tmp_path, OKF_LLM_PROVIDER="gemini", GEMINI_API_KEY="g-key", OPENROUTER_API_KEY="or-key")

    llm = WikiConfig.from_env(env).llm

    assert llm.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert (llm.model, llm.api_key) == ("gemini-3.8-flash", "g-key")


@pytest.mark.parametrize(("variable", "model"), [("OKF_LLM_API_KEY", "llm"), ("OKF_CLASSIFIER_API_KEY", "classifier")])
def test_from_env_when_an_okf_key_is_set_then_it_wins_over_openrouter_api_key(
    tmp_path: Path, variable: str, model: str
) -> None:
    # ARRANGE
    env = env_for(tmp_path, OPENROUTER_API_KEY="or-key", **{variable: "okf-key"})

    # ACT
    config = WikiConfig.from_env(env)

    # ASSERT
    assert getattr(config, model).api_key == "okf-key"


@pytest.mark.parametrize(
    ("llm", "message"),
    [({"provider": "vertex"}, "unknown provider 'vertex'"), ({"provider": "custom"}, "needs a base_url and a model")],
)
def test_bad_provider_settings_raise(tmp_path: Path, llm: dict[str, str], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        WikiConfig(bundle=tmp_path, llm=llm)


def test_ready_means_the_provider_has_the_key_it_needs(tmp_path: Path) -> None:
    assert not WikiConfig(bundle=tmp_path).llm.ready
    assert WikiConfig(bundle=tmp_path, llm={"api_key": "k"}).llm.ready
    assert WikiConfig(bundle=tmp_path, llm={"provider": "ollama"}).llm.ready


def test_the_environment_is_laid_over_base_and_overrides_over_both(tmp_path: Path) -> None:
    base = {"mode": "crow", "max_depth": 3, "llm": {"provider": "gemini", "api_key": "file-key", "model": "gemini-2.5-pro"}}
    env = env_for(tmp_path, OKF_LLM_MODEL="gemini-2.5-flash", OKF_MAX_DEPTH="5")

    config = WikiConfig.from_env(env, base=base, max_depth=6)

    assert (config.mode, config.max_depth) == ("crow", 6)
    assert (config.llm.provider, config.llm.model, config.llm.api_key) == ("gemini", "gemini-2.5-flash", "file-key")
    assert WikiConfig.from_env({**env, "GEMINI_API_KEY": "env-key"}, base=base).llm.api_key == "env-key"


def test_none_overrides_are_ignored(tmp_path: Path) -> None:
    env = env_for(tmp_path, OKF_MODE="crow")

    config = WikiConfig.from_env(env, bundle=None, mode=None)

    assert (config.bundle, config.mode) == (tmp_path, "crow")


def test_invalid_mode_raises(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="mode"):
        WikiConfig.from_env(env_for(tmp_path, OKF_MODE="hybrid"))


def test_missing_bundle_raises() -> None:
    with pytest.raises(ValidationError, match="bundle"):
        WikiConfig.from_env({"OKF_MODE": "crow"})


def test_extra_body_is_read_as_json(tmp_path: Path) -> None:
    env = {"OKF_BUNDLE": str(tmp_path), "OKF_LLM_EXTRA_BODY": '{"provider": {"order": ["together", "coreweave"]}}'}
    assert WikiConfig.from_env(env).llm.extra_body == {"provider": {"order": ["together", "coreweave"]}}


def test_invalid_extra_body_names_the_variable(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="OKF_LLM_EXTRA_BODY"):
        WikiConfig.from_env({"OKF_BUNDLE": str(tmp_path), "OKF_LLM_EXTRA_BODY": "{provider:{order:[together]}}"})


@pytest.mark.parametrize(
    "fields",
    [{"crow": {"tau_route": 1.5}}, {"crow": {"tau_ret": -0.1}}, {"crow": {"beam": 0}}, {"crow": {"k": 0}}, {"upload_mb": 0}],
    ids=["tau-above-1", "tau-below-0", "beam-0", "k-0", "upload-0"],
)
def test_config_when_a_limit_is_out_of_range_then_raises(tmp_path: Path, fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        WikiConfig(bundle=tmp_path, **fields)


def test_config_when_max_depth_and_max_links_are_zero_then_accepted(tmp_path: Path) -> None:
    # ACT
    config = WikiConfig(bundle=tmp_path, max_depth=0, max_links=0)

    # ASSERT
    assert (config.max_depth, config.max_links) == (0, 0)
