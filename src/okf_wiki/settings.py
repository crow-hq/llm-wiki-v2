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

"""The settings file that `okf-wiki setup` and the web page write.

    ~/.config/llm-wiki/config.json      ($XDG_CONFIG_HOME, or any file named by OKF_CONFIG)

It holds part of a WikiConfig (`{"mode": …, "llm": {"provider": …, "api_key": …}}`) and lies
under the `OKF_*` environment, which lies under command-line flags. The command line and
the server read it; `Wiki.from_env` does not, so a library stays configured by its caller.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from okf_wiki.config import PROVIDERS, LLMConfig, WikiConfig, merge
from okf_wiki.llm import LLM

# The settings people edit: name in the page and in `setup` -> place in WikiConfig.
FIELDS: dict[str, tuple[str, ...]] = {
    "provider": ("llm", "provider"),
    "api_key": ("llm", "api_key"),
    "model": ("llm", "model"),
    "base_url": ("llm", "base_url"),
    "mode": ("mode",),
    "classifier_api_key": ("classifier", "api_key"),
    "bundle": ("bundle",),
}
SECRETS = frozenset({"api_key", "classifier_api_key"})  # never sent back; an empty one keeps what is saved


def settings_path(env: Mapping[str, str]) -> Path:
    if env.get("OKF_CONFIG"):
        return Path(env["OKF_CONFIG"]).expanduser()
    config_home = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return config_home / "llm-wiki" / "config.json"


def default_bundle() -> Path:
    """Where the wiki lives when nothing says otherwise."""
    return Path.home() / "llm-wiki"


def mask(secret: str) -> str:
    return "" if not secret else f"…{secret[-4:]}" if len(secret) > 12 else "set"


class Settings:
    """The settings file under the environment and the command-line flags."""

    def __init__(self, env: Mapping[str, str] | None = None, **flags: Any) -> None:
        self.env = dict(os.environ if env is None else env)
        self.path = settings_path(self.env)
        self.flags = {k: v for k, v in flags.items() if v is not None}

    def saved(self) -> dict[str, Any]:
        """The file as written, {} when there is none."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except ValueError as e:
            raise ValueError(f"{self.path} is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"{self.path} must hold a JSON object")
        return data

    def config(self, saved: Mapping[str, Any] | None = None) -> WikiConfig:
        """The effective config: defaults, the file (or `saved`), the environment, the flags."""
        base = merge({"bundle": str(default_bundle())}, self.saved() if saved is None else saved)
        return WikiConfig.from_env(self.env, base=base, **self.flags)

    def managed(self, provider: str) -> list[str]:
        """Fields fixed by the environment or a flag: shown, not editable."""
        by = {
            "provider": ["OKF_LLM_PROVIDER"],
            "api_key": ["OKF_LLM_API_KEY", PROVIDERS[provider].key_env if provider in PROVIDERS else ""],
            "model": ["OKF_LLM_MODEL"],
            "base_url": ["OKF_LLM_BASE_URL"],
            "mode": ["OKF_MODE"],
            "classifier_api_key": ["OKF_CLASSIFIER_API_KEY", "OPENROUTER_API_KEY"],
            "bundle": ["OKF_BUNDLE"],
        }
        return [f for f, names in by.items() if f in self.flags or any(n and self.env.get(n) for n in names)]

    def view(self) -> dict[str, Any]:
        """What the settings page shows: values with keys masked, and what it may change."""
        cfg = self.config()
        saved_llm = self.saved().get("llm") or {}
        return {
            "values": {
                "provider": cfg.llm.provider,
                "api_key": mask(cfg.llm.api_key),
                "model": cfg.llm.model,
                "base_url": saved_llm.get("base_url") or self.env.get("OKF_LLM_BASE_URL", ""),
                "mode": cfg.mode,
                "classifier_api_key": mask(cfg.classifier.api_key),
                "bundle": str(cfg.bundle),
            },
            "ready": ready(cfg),
            "managed": self.managed(cfg.llm.provider),
            "providers": {
                name: {"base_url": p.base_url, "needs_key": bool(p.key_env), "models": list(p.models)}
                for name, p in PROVIDERS.items()
            },
            "file": str(self.path),
        }

    def apply(self, changes: Mapping[str, Any]) -> tuple[dict[str, Any], WikiConfig]:
        """The file with `changes` in, and the config it gives; nothing is written.

        Unknown and managed fields are ignored, an empty secret keeps the saved one (unless
        the provider changes: a key never goes to another provider), any other empty field
        goes back to its default.
        """
        saved = self.saved()
        before = (saved.get("llm") or {}).get("provider") or "openrouter"
        provider = str(changes.get("provider") or before)
        managed = self.managed(provider)
        if provider != before and "provider" not in managed:
            (saved.get("llm") or {}).pop("api_key", None)
        for name, value in changes.items():
            if name not in FIELDS or name in managed:
                continue
            *parents, leaf = FIELDS[name]
            node = saved
            for key in parents:
                node = node.setdefault(key, {})
            value = str(value or "").strip()
            if value:
                node[leaf] = value
            elif name not in SECRETS:
                node.pop(leaf, None)
        try:
            return saved, self.config(saved)
        except ValidationError as e:
            raise ValueError("; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}".lstrip(": ") for err in e.errors())) from e

    def save(self, changes: Mapping[str, Any]) -> WikiConfig:
        """Apply `changes` and write the file (readable by its owner only)."""
        saved, cfg = self.apply(changes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(saved, f, indent=2)
            f.write("\n")
        tmp.replace(self.path)
        return cfg


def ready(cfg: WikiConfig) -> bool:
    """Every model the mode needs has its key."""
    return cfg.llm.ready and (cfg.mode != "crow" or bool(cfg.classifier.api_key))


def probe(llm: LLM) -> str:
    """One tiny request, to tell a working model from a wrong key or address (ModelError)."""
    return llm.chat([{"role": "user", "content": "Reply with the single word OK."}], op="settings/probe").strip()[:200]


def live_models(llm: LLMConfig, client: httpx.Client | None = None) -> list[str]:
    """The text models the provider offers right now (GET {base_url}/models); [] when it does not say."""
    headers = {"Authorization": f"Bearer {llm.api_key}"} if llm.api_key else {}
    try:
        response = (client or httpx.Client(timeout=10)).get(f"{llm.base_url.rstrip('/')}/models", headers=headers)
        data = response.raise_for_status().json()["data"]
        return [
            str(m["id"]).removeprefix("models/")  # Gemini names them models/<id>
            for m in data
            if m.get("id") and "text" in ((m.get("architecture") or {}).get("output_modalities") or ["text"])
        ]
    except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError):
        return []
