# Copyright 2026 Federico Cesarini, Marco Sassarini
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from okf_wiki.config import WikiConfig


def env_for(bundle: Path, **variables: str) -> dict[str, str]:
    return {"OKF_BUNDLE": str(bundle), **variables}


def test_defaults(tmp_path: Path) -> None:
    config = WikiConfig(bundle=tmp_path)

    assert config.mode == "classic"
    assert config.summarize is True
    assert (config.llm.provider, config.llm.base_url) == ("openrouter", "https://openrouter.ai/api/v1")
    assert config.llm.model == "google/gemini-3.8-flash"
    assert config.classifier.base_url == "https://openrouter.ai/api/v1"
    assert config.classifier.model == "typesafe/jev-1.13"


def test_from_env_reads_top_level_settings(tmp_path: Path) -> None:
    env = env_for(tmp_path, OKF_MODE="crow", OKF_SUMMARIZE="false", OKF_MAX_DEPTH="2")

    config = WikiConfig.from_env(env)

    assert config.bundle == tmp_path
    assert config.mode == "crow"
    assert config.summarize is False
    assert config.max_depth == 2


def test_from_env_reads_llm_settings(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        OKF_LLM_PROVIDER="custom",
        OKF_LLM_BASE_URL="http://vllm.test:8000/v1",
        OKF_LLM_MODEL="Qwen/Qwen2.5-7B-Instruct",
        OKF_LLM_API_KEY="llm-key",
        OKF_LLM_TEMPERATURE="0.7",
        OKF_LLM_TIMEOUT="30",
    )

    llm = WikiConfig.from_env(env).llm

    assert (llm.provider, llm.base_url) == ("custom", "http://vllm.test:8000/v1")
    assert llm.model == "Qwen/Qwen2.5-7B-Instruct"
    assert llm.api_key == "llm-key"
    assert llm.temperature == 0.7
    assert llm.timeout == 30.0


def test_from_env_reads_classifier_settings(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        OKF_CLASSIFIER_BASE_URL="http://laya.test/v1",
        OKF_CLASSIFIER_MODEL="typesafe/jev-2.0",
        OKF_CLASSIFIER_TIMEOUT="5",
        OKF_CLASSIFIER_STATE_CHARS="1000",
    )

    classifier = WikiConfig.from_env(env).classifier

    assert classifier.base_url == "http://laya.test/v1"
    assert classifier.model == "typesafe/jev-2.0"
    assert classifier.timeout == 5.0
    assert classifier.state_chars == 1000


def test_from_env_reads_crow_thresholds_beam_and_k(tmp_path: Path) -> None:
    env = env_for(
        tmp_path,
        OKF_TAU_ROUTE="0.8",
        OKF_TAU_PATH="0.4",
        OKF_TAU_ING="0.55",
        OKF_TAU_CONS="0.65",
        OKF_TAU_FOLD="0.3",
        OKF_TAU_RET="0.35",
        OKF_TAU_LINK="0.9",
        OKF_BEAM="3",
        OKF_K="7",
    )

    crow = WikiConfig.from_env(env).crow

    assert (crow.tau_route, crow.tau_path, crow.tau_ing, crow.tau_cons) == (0.8, 0.4, 0.55, 0.65)
    assert (crow.tau_fold, crow.tau_ret, crow.tau_link) == (0.3, 0.35, 0.9)
    assert (crow.beam, crow.k) == (3, 7)


def test_from_env_ignores_empty_variables(tmp_path: Path) -> None:
    config = WikiConfig.from_env(env_for(tmp_path, OKF_MODE="", OKF_LLM_MODEL="", OKF_BEAM=""))

    assert config.mode == "classic"
    assert config.llm.model == "google/gemini-3.8-flash"
    assert config.crow.beam == 2


def test_a_passed_mapping_replaces_the_process_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OKF_MODE", "crow")

    assert WikiConfig.from_env(env_for(tmp_path)).mode == "classic"


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


def test_the_provider_fills_base_url_model_and_key(tmp_path: Path) -> None:
    env = env_for(tmp_path, OKF_LLM_PROVIDER="gemini", GEMINI_API_KEY="g-key", OPENROUTER_API_KEY="or-key")

    llm = WikiConfig.from_env(env).llm

    assert llm.base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert (llm.model, llm.api_key) == ("gemini-3.8-flash", "g-key")


def test_okf_llm_api_key_wins_over_the_provider_variable(tmp_path: Path) -> None:
    env = env_for(tmp_path, OPENROUTER_API_KEY="or-key", OKF_LLM_API_KEY="okf-key")

    assert WikiConfig.from_env(env).llm.api_key == "okf-key"


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


def test_okf_classifier_api_key_wins_over_openrouter_api_key(tmp_path: Path) -> None:
    env = env_for(tmp_path, OPENROUTER_API_KEY="or-key", OKF_CLASSIFIER_API_KEY="okf-key")

    assert WikiConfig.from_env(env).classifier.api_key == "okf-key"


def test_keyword_overrides_win_over_the_environment(tmp_path: Path) -> None:
    env = env_for(tmp_path / "from-env", OKF_MODE="classic", OKF_MAX_DEPTH="2")

    config = WikiConfig.from_env(env, bundle=tmp_path / "from-arg", mode="crow", max_depth=6)

    assert config.bundle == tmp_path / "from-arg"
    assert config.mode == "crow"
    assert config.max_depth == 6


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
