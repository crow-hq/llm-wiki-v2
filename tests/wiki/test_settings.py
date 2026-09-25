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

"""The settings file: where it is, how it lies under the environment, what the page may change."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from okf_wiki.config import LLMConfig
from okf_wiki.settings import Settings, live_models, mask, settings_path

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

    assert cfg.llm.api_key == ""
    assert written(settings) == {"llm": {"provider": "custom", "base_url": "http://gpu.lan:8000/v1", "model": "qwen"}}


def test_the_environment_and_flags_win_and_their_fields_are_not_saved(tmp_path: Path) -> None:
    settings = Settings({"OKF_LLM_MODEL": "openai/gpt-4.1", "OPENROUTER_API_KEY": "env-key"}, mode="crow", bundle=tmp_path)

    cfg = settings.save({"model": "google/gemini-2.5-pro", "api_key": KEY, "mode": "classic", "provider": "openrouter"})

    assert written(settings) == {"llm": {"provider": "openrouter"}}
    assert (cfg.llm.model, cfg.llm.api_key, cfg.mode, cfg.bundle) == ("openai/gpt-4.1", "env-key", "crow", tmp_path)
    assert settings.managed("openrouter") == ["api_key", "model", "mode", "classifier_api_key", "bundle"]


def test_invalid_settings_raise_and_write_nothing() -> None:
    settings = Settings({})

    with pytest.raises(ValueError, match="needs a base_url and a model"):
        settings.save({"provider": "custom"})
    with pytest.raises(ValueError, match="mode"):
        settings.save({"mode": "hybrid"})
    assert not settings.path.exists()


def test_unknown_fields_are_ignored() -> None:
    settings = Settings({})

    settings.save({"model": "openai/gpt-4.1", "usage_log": "/etc/passwd", "llm": {"base_url": "http://evil.test"}})

    assert written(settings) == {"llm": {"model": "openai/gpt-4.1"}}


def test_the_view_masks_keys_and_says_whether_the_wiki_is_ready() -> None:
    settings = Settings({})
    assert settings.view()["ready"] is False

    settings.save({"api_key": KEY})
    view = settings.view()

    assert view["values"]["api_key"] == "…cdef" and KEY not in json.dumps(view)
    assert view["ready"] is True
    assert view["providers"]["ollama"] == {"base_url": "http://localhost:11434/v1", "needs_key": False, "models": ["gemma4", "qwen3.8"]}
    assert view["file"] == "~/.config/llm-wiki/config.json"


def test_crow_is_ready_only_with_a_classifier_key() -> None:
    settings = Settings({})
    settings.save({"provider": "ollama", "mode": "crow"})
    assert settings.view()["ready"] is False

    settings.save({"classifier_api_key": KEY})
    assert settings.view()["ready"] is True


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
