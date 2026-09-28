# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The settings file that `okf-wiki setup` and the web page write.

    ~/.config/llm-wiki/config.json      ($XDG_CONFIG_HOME, or any file named by OKF_CONFIG)

It holds part of a WikiConfig (`{"mode": …, "llm": {"provider": …, "api_key": …}}`) and lies
under the `OKF_*` environment, which lies under command-line flags. The command line and
the server read it; `Wiki.from_env` does not, so a library stays configured by its caller.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from okf_wiki.config import CLASSIFIER_PROVIDERS, PROVIDERS, Provider, WikiConfig, merge
from okf_wiki.errors import ConfigError, InputError

# The settings people edit: name in the page and in `setup` -> place in WikiConfig.
FIELDS: dict[str, tuple[str, ...]] = {
    "provider": ("llm", "provider"),
    "api_key": ("llm", "api_key"),
    "model": ("llm", "model"),
    "base_url": ("llm", "base_url"),
    "mode": ("mode",),
    "reasoning": ("llm", "reasoning"),
    "classifier_provider": ("classifier", "provider"),
    "classifier_base_url": ("classifier", "base_url"),
    "classifier_model": ("classifier", "model"),
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


def tilde(path: Path) -> str:
    """The path as people write it: ~/… under the home folder."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


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
            raise ConfigError(f"{self.path} is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise ConfigError(f"{self.path} must hold a JSON object")
        return data

    def config(self, saved: Mapping[str, Any] | None = None) -> WikiConfig:
        """The effective config: defaults, the file (or `saved`), the environment, the flags."""
        base = merge({"bundle": str(default_bundle())}, self.saved() if saved is None else saved)
        return WikiConfig.from_env(self.env, base=base, **self.flags)

    def managed(self, provider: str, classifier_provider: str = "openrouter") -> list[str]:
        """Fields fixed by the environment or a flag: shown, not editable."""
        by = {
            "provider": ["OKF_LLM_PROVIDER"],
            "api_key": ["OKF_LLM_API_KEY", key_env(PROVIDERS, provider)],
            "model": ["OKF_LLM_MODEL"],
            "base_url": ["OKF_LLM_BASE_URL"],
            "mode": ["OKF_MODE"],
            "reasoning": ["OKF_LLM_REASONING"],
            "classifier_provider": ["OKF_CLASSIFIER_PROVIDER"],
            "classifier_base_url": ["OKF_CLASSIFIER_BASE_URL"],
            "classifier_model": ["OKF_CLASSIFIER_MODEL"],
            "classifier_api_key": ["OKF_CLASSIFIER_API_KEY", key_env(CLASSIFIER_PROVIDERS, classifier_provider)],
            "bundle": ["OKF_BUNDLE"],
        }
        return [f for f, names in by.items() if f in self.flags or any(n and self.env.get(n) for n in names)]

    def view(self) -> dict[str, Any]:
        """What the settings page shows: values with keys masked, and what it may change."""
        cfg = self.config()
        saved = self.saved()
        saved_llm, saved_clf = saved.get("llm") or {}, saved.get("classifier") or {}
        return {
            "values": {
                "provider": cfg.llm.provider,
                "api_key": mask(cfg.llm.api_key),
                "model": cfg.llm.model,
                "base_url": saved_llm.get("base_url") or self.env.get("OKF_LLM_BASE_URL", ""),
                "mode": cfg.mode,
                "reasoning": cfg.llm.reasoning,
                "classifier_provider": cfg.classifier.provider,
                "classifier_base_url": saved_clf.get("base_url") or self.env.get("OKF_CLASSIFIER_BASE_URL", ""),
                "classifier_model": cfg.classifier.model,
                "classifier_api_key": mask(cfg.classifier.api_key),
                "bundle": str(cfg.bundle),
            },
            "ready": ready(cfg),
            "managed": self.managed(cfg.llm.provider, cfg.classifier.provider),
            "providers": presets(PROVIDERS),
            "classifier_providers": presets(CLASSIFIER_PROVIDERS),
            "file": tilde(self.path),
        }

    def apply(self, changes: Mapping[str, Any]) -> tuple[dict[str, Any], WikiConfig]:
        """The file with `changes` in, and the config it gives; nothing is written.

        Unknown and managed fields are ignored, an empty secret keeps the saved one (unless
        the model's provider or its address changes: a key never goes to another endpoint), any
        other empty field goes back to its default.
        """
        saved = self.saved()
        llm, clf = saved.get("llm") or {}, saved.get("classifier") or {}
        provider = str(changes.get("provider") or llm.get("provider") or "openrouter")
        clf_provider = str(changes.get("classifier_provider") or clf.get("provider") or "openrouter")
        managed = self.managed(provider, clf_provider)
        for section, prefix in ((llm, ""), (clf, "classifier_")):
            if moves(section, changes, prefix, managed):
                section.pop("api_key", None)
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
            raise InputError("; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}".lstrip(": ") for err in e.errors())) from e

    def save(self, changes: Mapping[str, Any]) -> WikiConfig:
        """Apply `changes` and write the file (readable by its owner only)."""
        saved, cfg = self.apply(changes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)  # O_CREAT's mode applies only to a new file, not to a leftover one
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(saved, f, indent=2)
                f.write("\n")
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)
        return cfg


def key_env(providers: Mapping[str, Provider], name: str) -> str:
    return providers[name].key_env if name in providers else ""


def presets(providers: Mapping[str, Provider]) -> dict[str, dict[str, Any]]:
    """What the page needs to know of each provider."""
    return {name: {"base_url": p.base_url, "needs_key": bool(p.key_env), "models": list(p.models)} for name, p in providers.items()}


def moves(saved: Mapping[str, Any], changes: Mapping[str, Any], prefix: str, managed: Collection[str]) -> bool:
    """True when `changes` send a model (its `saved` section) to another provider or address."""

    def after(field: str, before: str) -> str:
        name = prefix + field
        return before if name not in changes or name in managed else str(changes[name] or "").strip()

    provider = str(saved.get("provider") or "openrouter")
    url = str(saved.get("base_url") or "").strip().rstrip("/")
    return (after("provider", provider) or "openrouter") != provider or after("base_url", url).rstrip("/") != url


def ready(cfg: WikiConfig) -> bool:
    """Every model the mode needs has its key."""
    return cfg.llm.ready and (cfg.mode != "crow" or cfg.classifier.ready)
