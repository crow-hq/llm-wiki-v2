# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The settings file: where it is, how it lies under the environment, what the page may change."""

from __future__ import annotations

import json
import os
import stat
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from llmw2.config import LLMConfig
from llmw2.errors import InputError
from llmw2.models.llm import live_models
from llmw2.settings import Settings, mask, settings_path

KEY = "sk-or-v1-0123456789abcdef"


def written(settings: Settings) -> dict[str, Any]:
    return json.loads(settings.path.read_text(encoding="utf-8"))


def test_the_file_is_under_the_config_home_unless_okf_config_names_one(home: Path, tmp_path: Path) -> None:
    assert settings_path({}) == home / ".config" / "llm-wiki" / "config.json"
    assert settings_path({"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "llm-wiki" / "config.json"
    assert settings_path({"OKF_CONFIG": str(tmp_path / "wiki.json")}) == tmp_path / "wiki.json"


def test_without_a_file_the_defaults_and_the_home_wiki_apply(home: Path) -> None:
    cfg = Settings({}).config()

    assert cfg.bundle == home / "llm-wiki"
    assert (cfg.llm.provider, cfg.llm.model, cfg.llm.api_key) == ("openrouter", "google/gemini-3.8-flash", "")


def test_save_writes_only_what_changed_readable_by_the_owner_only() -> None:
    settings = Settings({})

    cfg = settings.save({"provider": "openrouter", "api_key": KEY, "model": "", "base_url": "", "mode": "crow"})

    assert written(settings) == {"llm": {"provider": "openrouter", "api_key": KEY}, "mode": "crow"}
    assert stat.S_IMODE(settings.path.stat().st_mode) == 0o600
    assert (cfg.mode, cfg.llm.api_key, cfg.classifier.api_key) == ("crow", KEY, KEY)  # one OpenRouter key for both


def test_an_empty_key_keeps_the_saved_one_and_an_empty_field_its_default() -> None:
    settings = Settings({})
    settings.save({"api_key": KEY, "model": "openai/gpt-4.1-mini"})

    cfg = settings.save({"api_key": "", "model": ""})

    assert written(settings) == {"llm": {"api_key": KEY}}
    assert cfg.llm.model == "google/gemini-3.8-flash"


def test_a_key_never_follows_the_wiki_to_another_provider() -> None:
    settings = Settings({})
    settings.save({"api_key": KEY})

    cfg = settings.save({"provider": "custom", "base_url": "http://gpu.lan:8000/v1", "model": "qwen", "api_key": ""})

    assert cfg.llm.api_key == ""  # the OpenRouter key never goes to the GPU server…
    assert cfg.classifier.api_key == KEY  # …and still serves the classifier, on OpenRouter
    assert written(settings) == {
        "llm": {"provider": "custom", "base_url": "http://gpu.lan:8000/v1", "model": "qwen"},
        "remembered": {"llm": {"openrouter": {"api_key": KEY}}},
    }


def test_save_when_the_llm_switches_provider_and_back_then_its_key_and_model_come_back() -> None:
    # ARRANGE
    settings = Settings({})
    settings.save({"api_key": KEY, "model": "deepseek/deepseek-v4.1-flash"})
    settings.save({"provider": "custom", "base_url": "http://127.0.0.1:8080/v1", "model": "bonsai", "api_key": ""})

    # ACT
    cfg = settings.save({"provider": "openrouter", "base_url": ""})

    # ASSERT
    assert (cfg.llm.provider, cfg.llm.model, cfg.llm.api_key) == ("openrouter", "deepseek/deepseek-v4.1-flash", KEY)
    memory = settings.view()["memory"]["llm"]
    assert memory["custom"] == {"model": "bonsai", "base_url": "http://127.0.0.1:8080/v1", "api_key": ""}
    assert memory["openrouter"] == {"model": "deepseek/deepseek-v4.1-flash", "base_url": "", "api_key": "…cdef"}
    assert KEY not in json.dumps(settings.view())


def test_config_when_the_classifier_is_on_openrouter_at_another_address_then_the_openrouter_key_stays_home() -> None:
    # ARRANGE
    Settings({}).save({"api_key": KEY})
    Settings({}).save({"provider": "ollama", "mode": "classic"})

    # ACT
    cfg = Settings({"OKF_CLASSIFIER_BASE_URL": "http://laya.lan/v1"}).config()

    # ASSERT
    assert (cfg.classifier.provider, cfg.classifier.api_key) == ("openrouter", "")


GPU = {"provider": "custom", "base_url": "http://gpu.lan:8000/v1", "model": "qwen", "api_key": KEY, "mode": "classic"}


@pytest.mark.parametrize(
    ("first", "change", "key"),
    [
        (GPU, {"base_url": "http://evil.test/v1", "api_key": ""}, ""),
        (GPU, {"base_url": "http://gpu.lan:8000/v1/", "api_key": ""}, KEY),
        ({"api_key": KEY}, {"base_url": "", "api_key": ""}, KEY),
    ],
    ids=["address-changed", "same-address", "still-the-default-address"],
)
def test_save_when_base_url_changes_then_the_saved_key_is_dropped(first: dict[str, str], change: dict[str, str], key: str) -> None:
    # ARRANGE
    settings = Settings({})
    settings.save(first)

    # ACT
    cfg = settings.save(change)

    # ASSERT
    assert cfg.llm.api_key == key and written(settings)["llm"].get("api_key", "") == key


def test_save_when_a_wide_temp_file_is_left_over_then_the_file_is_still_owner_only() -> None:
    # ARRANGE
    settings = Settings({})
    settings.path.parent.mkdir(parents=True)
    leftover = settings.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    leftover.write_text("{}", encoding="utf-8")
    leftover.chmod(0o644)

    # ACT
    settings.save({"api_key": KEY})

    # ASSERT
    assert not leftover.exists()  # the save went through the leftover file
    assert stat.S_IMODE(settings.path.stat().st_mode) == 0o600


def test_the_environment_and_flags_win_and_their_fields_are_not_saved(tmp_path: Path) -> None:
    settings = Settings({"OKF_LLM_MODEL": "openai/gpt-4.1", "OPENROUTER_API_KEY": "env-key"}, mode="crow", bundle=tmp_path)

    cfg = settings.save({"model": "google/gemini-2.5-pro", "api_key": KEY, "mode": "classic", "provider": "openrouter"})

    assert written(settings) == {"llm": {"provider": "openrouter"}}
    assert (cfg.llm.model, cfg.llm.api_key, cfg.mode, cfg.bundle) == ("openai/gpt-4.1", "env-key", "crow", tmp_path)
    assert settings.managed("openrouter") == ["api_key", "model", "mode", "classifier_api_key", "bundle"]


@pytest.mark.parametrize(("change", "saved", "shown"), [("true", "true", True), ("", None, False)], ids=["on", "reset"])
def test_save_when_reasoning_changes_then_the_view_shows_it_as_a_bool(change: str, saved: str | None, shown: bool) -> None:
    # ARRANGE
    settings = Settings({})
    settings.save({"reasoning": "true", "api_key": KEY})

    # ACT
    cfg = settings.save({"reasoning": change})

    # ASSERT
    assert (written(settings).get("llm", {}).get("reasoning"), cfg.llm.reasoning) == (saved, shown)
    assert settings.view()["values"]["reasoning"] is shown


def test_save_when_okf_llm_reasoning_is_set_then_reasoning_is_managed_and_not_saved() -> None:
    # ARRANGE
    settings = Settings({"OKF_LLM_REASONING": "true"})

    # ACT
    cfg = settings.save({"reasoning": "", "model": "openai/gpt-4.1", "api_key": KEY})

    # ASSERT
    assert "reasoning" in settings.managed("openrouter")
    assert "reasoning" not in written(settings)["llm"]
    assert cfg.llm.reasoning is True and settings.view()["values"]["reasoning"] is True


def test_invalid_settings_raise_and_write_nothing() -> None:
    settings = Settings({})

    with pytest.raises(ValueError, match="needs a base_url and a model"):
        settings.save({"provider": "custom"})
    with pytest.raises(ValueError, match="mode"):
        settings.save({"mode": "hybrid"})
    assert not settings.path.exists()


def test_unknown_fields_are_ignored() -> None:
    settings = Settings({})

    settings.save({"model": "openai/gpt-4.1", "api_key": KEY, "usage_log": "/etc/passwd", "llm": {"base_url": "http://evil.test"}})

    assert written(settings) == {"llm": {"model": "openai/gpt-4.1", "api_key": KEY}}


def test_the_view_masks_keys_and_says_whether_the_wiki_is_ready() -> None:
    settings = Settings({})
    assert settings.view()["ready"] is False

    settings.save({"api_key": KEY})
    view = settings.view()

    assert view["values"]["api_key"] == "…cdef" and KEY not in json.dumps(view)
    assert view["ready"] is True
    assert view["providers"]["ollama"] == {"base_url": "http://localhost:11434/v1", "needs_key": False, "models": ["gemma4", "qwen3.8"]}
    assert view["file"] == "~/.config/llm-wiki/config.json"


def test_view_when_a_classifier_key_is_saved_then_it_is_masked() -> None:
    # ARRANGE
    settings = Settings({})
    settings.save({"provider": "ollama", "classifier_api_key": KEY})

    # ACT
    view = settings.view()

    # ASSERT
    assert view["values"]["classifier_api_key"] == "…cdef" and KEY not in json.dumps(view)


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"provider": "ollama", "mode": "crow"}, "CROW mode needs a key for its classifier on OpenRouter: paste it in Settings → Advanced"),
        ({"provider": "ollama", "classifier_provider": "typesafe"}, "CROW mode needs a key for its classifier on TypeSafe"),
        ({"provider": "openai", "mode": "classic"}, "OpenAI needs an API key: paste it in Settings → API key"),
        ({"mode": "crow"}, "OpenRouter needs an API key \\(it serves the CROW classifier too\\)"),
    ],
    ids=["crow-local-llm", "crow-typesafe", "classic-openai", "crow-openrouter"],
)
def test_save_when_a_needed_key_is_missing_then_it_is_refused_saying_which_and_where(changes: dict[str, str], said: str) -> None:
    # ARRANGE
    settings = Settings({})

    # ACT
    with pytest.raises(InputError, match=said):
        settings.save(changes)

    # ASSERT
    assert not settings.path.exists()


def test_save_when_crow_has_its_classifier_key_then_the_wiki_is_ready() -> None:
    settings = Settings({})

    settings.save({"provider": "ollama", "mode": "crow", "classifier_api_key": KEY})

    assert settings.view()["ready"] is True and settings.view()["missing"] == []


def test_save_when_the_classifier_has_its_own_provider_then_the_llm_keeps_its_own() -> None:
    # ARRANGE
    settings = Settings({})

    # ACT
    cfg = settings.save({"provider": "gemini", "api_key": "g-key-0123456789", "classifier_provider": "typesafe", "classifier_api_key": KEY})

    # ASSERT
    assert (cfg.llm.provider, cfg.llm.api_key) == ("gemini", "g-key-0123456789")
    assert (cfg.classifier.provider, cfg.classifier.base_url, cfg.classifier.api_key) == ("typesafe", "https://api.typesafe.ai/v1", KEY)
    assert settings.view()["ready"] is True


@pytest.mark.parametrize(
    ("change", "key"),
    [
        ({"classifier_provider": "typesafe"}, ""),
        ({"classifier_provider": "custom", "classifier_base_url": "http://laya.lan/v1"}, ""),
        ({"classifier_provider": "openrouter", "classifier_model": "typesafe/jev-1.13"}, KEY),
        ({"provider": "custom", "base_url": "http://gpu.lan/v1", "model": "qwen"}, KEY),
    ],
    ids=["other-provider", "other-address", "same-provider", "only-the-llm-moves"],
)
def test_save_when_the_classifier_moves_then_its_key_stays_with_its_provider(change: dict[str, str], key: str) -> None:
    # ARRANGE
    settings = Settings({})
    settings.save({"provider": "ollama", "mode": "classic", "classifier_api_key": KEY})

    # ACT
    cfg = settings.save({**change, "classifier_api_key": ""})

    # ASSERT
    assert cfg.classifier.api_key == key and written(settings).get("classifier", {}).get("api_key", "") == key


def test_save_when_the_classifier_model_is_empty_then_the_pinned_default_is_not_written() -> None:
    # ARRANGE
    settings = Settings({})
    settings.save({"classifier_model": "jev-1.13", "api_key": KEY})

    # ACT
    cfg = settings.save({"classifier_model": ""})

    # ASSERT
    assert "classifier" not in written(settings) or "model" not in written(settings)["classifier"]
    assert cfg.classifier.model == "typesafe/jev-1.13"


def test_crow_with_a_local_classifier_is_ready_without_a_key_but_needs_its_address() -> None:
    # ARRANGE
    settings = Settings({})

    # ACT
    with pytest.raises(ValueError, match="classifier provider 'custom' needs a base_url"):
        settings.save({"provider": "ollama", "classifier_provider": "custom"})
    settings.save({"provider": "ollama", "classifier_provider": "custom", "classifier_base_url": "http://localhost:9000/v1"})

    # ASSERT
    assert settings.view()["ready"] is True


def test_managed_when_the_classifier_is_on_typesafe_then_its_own_variable_locks_its_key() -> None:
    # ARRANGE
    settings = Settings({"OKF_CLASSIFIER_PROVIDER": "typesafe", "OPENROUTER_API_KEY": "or-key"})

    # ACT
    managed = settings.managed("gemini", "typesafe")

    # ASSERT
    assert managed == ["classifier_provider"]
    assert Settings({"TYPESAFE_API_KEY": "ts-key"}).managed("gemini", "typesafe") == ["classifier_api_key"]


def test_a_broken_file_names_itself() -> None:
    settings = Settings({})
    settings.path.parent.mkdir(parents=True)
    settings.path.write_text("[1, 2]", encoding="utf-8")

    with pytest.raises(ValueError, match="must hold a JSON object"):
        settings.config()


@pytest.mark.parametrize(("secret", "shown"), [("", ""), ("short", "set"), (KEY, "…cdef")])
def test_mask(secret: str, shown: str) -> None:
    assert mask(secret) == shown


def models_client(status: int, body: Any, seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_live_models_lists_the_provider_text_models() -> None:
    seen: list[httpx.Request] = []
    body = {"data": [
        {"id": "google/gemini-3.8-flash", "architecture": {"output_modalities": ["text"]}},
        {"id": "google/lyria-3-pro-preview", "architecture": {"output_modalities": ["audio"]}},
        {"id": "models/gemini-3.5-flash"},  # Gemini's naming, no architecture block
    ]}

    models = live_models(LLMConfig(api_key=KEY), models_client(200, body, seen))

    assert models == ["google/gemini-3.8-flash", "gemini-3.5-flash"]
    assert str(seen[0].url) == "https://openrouter.ai/api/v1/models"
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"


@pytest.mark.parametrize(("status", "body"), [(401, {"error": "no key"}), (200, {"models": []}), (200, [1, 2])])
def test_live_models_is_empty_when_the_provider_does_not_say(status: int, body: Any) -> None:
    assert live_models(LLMConfig(provider="ollama"), models_client(status, body, [])) == []
