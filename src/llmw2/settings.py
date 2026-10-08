# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The settings file that `llmwiki2 setup` and the web page write.

    ~/.config/llm-wiki/config.json      ($XDG_CONFIG_HOME, or any file named by OKF_CONFIG)

It holds part of a WikiConfig (`{"mode": …, "llm": {"provider": …, "api_key": …}}`) and lies
under the `OKF_*` environment, which lies under command-line flags. The command line and
the server read it; `Wiki.from_env` does not, so a library stays configured by its caller.

Under "remembered" it keeps, per model and provider, the key, model and address of the
providers not in use: switching to another provider and back loses nothing, and a key only
ever goes to the provider (and address) it was saved for. An OpenRouter key saved for either
model serves both, at OpenRouter's own address.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from llmw2.config import (
    CLASSIFIER_PROVIDERS,
    CROW_PRESETS,
    PROVIDERS,
    ClassifierConfig,
    CrowConfig,
    LLMConfig,
    Provider,
    WikiConfig,
    crow_preset,
    merge,
    missing_keys,
)
from llmw2.errors import ConfigError, InputError

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
    "concurrency": ("concurrency",),
    # CROW thresholds live with the classifier, so each classifier provider keeps its own (see "remembered").
    **{f"crow_{name}": ("classifier", "crow", name) for name in CrowConfig.model_fields},
    # How each model is called; kept per provider too, like its key and model.
    "temperature": ("llm", "temperature"),
    "seed": ("llm", "seed"),
    "pin_provider": ("llm", "pin_provider"),  # comma-separated
    "timeout": ("llm", "timeout"),
    "read_chars": ("llm", "read_chars"),  # characters one prompt may hold; kept per provider, empty: automatic
    "note_chars": ("note_chars",),  # about how long a note may be; empty: automatic
    "classifier_timeout": ("classifier", "timeout"),
    "classifier_attempts": ("classifier", "attempts"),
    "classifier_state_chars": ("classifier", "state_chars"),
    "classifier_request_chars": ("classifier", "request_chars"),
}
# The parameters of each model the page edits (a number each), with the default a new provider starts from.
PARAMS = {
    "llm": {
        **{name: LLMConfig.model_fields[name].default for name in ("temperature", "timeout")},
        "seed": "",  # not set; "pin_provider" and "read_chars" likewise: text in the page, "" clears
        "pin_provider": "",
        "read_chars": "",
    },
    "classifier": {name: ClassifierConfig.model_fields[name].default for name in ("timeout", "attempts", "state_chars", "request_chars")},
}
SECRETS = frozenset({"api_key", "classifier_api_key"})  # never sent back; an empty one keeps what is saved
REMEMBERED = "remembered"  # {"llm": {provider: {"api_key", "model", "base_url"}}, "classifier": {…}}
PER_PROVIDER = ("api_key", "model", "base_url", "crow", *PARAMS["llm"], *PARAMS["classifier"])  # what a model keeps per provider


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
        data = self.saved() if saved is None else saved
        base = merge({"bundle": str(default_bundle())}, {k: v for k, v in data.items() if k != REMEMBERED})
        clf = base.get("classifier")
        if isinstance(clf, dict) and "crow" in clf:  # the thresholds saved with the classifier are WikiConfig's `crow`
            base = {**base, "classifier": {k: v for k, v in clf.items() if k != "crow"}, "crow": clf["crow"]}
        cfg = WikiConfig.from_env(self.env, base=base, **self.flags)
        if key := openrouter_key(data):  # at OpenRouter's own address only: a gateway named "openrouter" never gets it
            home = PROVIDERS["openrouter"].base_url
            for model in (cfg.llm, cfg.classifier):
                if model.provider == "openrouter" and model.base_url == home and not model.api_key:
                    model.api_key = key
        return cfg

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
            "concurrency": ["OKF_CONCURRENCY"],
            "note_chars": ["OKF_NOTE_CHARS"],
            **{f"crow_{name}": [f"OKF_{name.upper()}"] for name in CrowConfig.model_fields},
            **{name: [f"OKF_LLM_{name.upper()}"] for name in PARAMS["llm"]},
            **{f"classifier_{name}": [f"OKF_CLASSIFIER_{name.upper()}"] for name in PARAMS["classifier"]},
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
                "concurrency": cfg.concurrency,
                "note_chars": "" if cfg.note_chars is None else cfg.note_chars,
                **{f"crow_{name}": value for name, value in cfg.crow.model_dump().items()},
                **{name: param(cfg.llm, name) for name in PARAMS["llm"]},
                **{f"classifier_{name}": getattr(cfg.classifier, name) for name in PARAMS["classifier"]},
            },
            "ready": ready(cfg),
            "missing": missing_keys(cfg),
            "memory": self.memory(saved, cfg),
            "managed": self.managed(cfg.llm.provider, cfg.classifier.provider),
            "providers": presets(PROVIDERS),
            "classifier_providers": {
                name: {**view, "crow_preset": crow_preset(name)} for name, view in presets(CLASSIFIER_PROVIDERS).items()
            },
            "crow_presets": {name: thresholds(name) for name in CROW_PRESETS},
            "params": PARAMS,
            "file": tilde(self.path),
        }

    def memory(self, saved: Mapping[str, Any], cfg: WikiConfig) -> dict[str, dict[str, dict[str, Any]]]:
        """Per model and provider, what choosing that provider brings back: model, address and key (masked)."""
        shared, remembered = openrouter_key(saved), saved.get(REMEMBERED) or {}
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for section, providers in (("llm", PROVIDERS), ("classifier", CLASSIFIER_PROVIDERS)):
            kept = remembered.get(section) or {}
            out[section] = {}
            for name, preset in providers.items():
                node = kept.get(name) or {}
                key = (self.env.get(preset.key_env) if preset.key_env else "") or node.get("api_key")
                key = key or (shared if name == "openrouter" and not node.get("base_url") else "")
                out[section][name] = {"model": node.get("model", ""), "base_url": node.get("base_url", ""), "api_key": mask(str(key or ""))}
                out[section][name].update({k: node.get(k, default) for k, default in PARAMS[section].items()})
                if section == "classifier":  # its preset, with what was changed for it
                    out[section][name]["crow"] = {**thresholds(crow_preset(name)), **(node.get("crow") or {})}
            active = cfg.llm if section == "llm" else cfg.classifier
            base_url = (saved.get(section) or {}).get("base_url", "")
            out[section][active.provider] = {"model": active.model, "base_url": base_url, "api_key": mask(active.api_key)}
            out[section][active.provider].update({k: param(active, k) for k in PARAMS[section]})
        out["classifier"][cfg.classifier.provider]["crow"] = cfg.crow.model_dump()
        return out

    def apply(self, changes: Mapping[str, Any]) -> tuple[dict[str, Any], WikiConfig]:
        """The file with `changes` in, and the config it gives; nothing is written.

        Unknown and managed fields are ignored, an empty secret keeps the saved one, any other
        empty field goes back to its default. A model that changes provider keeps what it had
        under "remembered" and gets back what it had there for the new one; a key whose address
        changes is dropped, so it never goes to another endpoint.
        """
        saved = self.saved()
        llm, clf = saved.get("llm") or {}, saved.get("classifier") or {}
        provider = str(changes.get("provider") or llm.get("provider") or "openrouter")
        clf_provider = str(changes.get("classifier_provider") or clf.get("provider") or "openrouter")
        managed = self.managed(provider, clf_provider)
        for section, prefix in (("llm", ""), ("classifier", "classifier_")):
            switch(saved, section, prefix, changes, managed)
        for name, value in changes.items():
            if name not in FIELDS or name in managed:
                continue
            *parents, leaf = FIELDS[name]
            node = saved
            for key in parents:
                node = node.setdefault(key, {})
            value = str(value or "").strip()
            if value and is_default(name, value, clf_provider):
                value = ""  # a default or preset value is not written: a new one still reaches this wiki
            if value:
                node[leaf] = value
            elif name not in SECRETS:
                node.pop(leaf, None)
        prune(saved)
        try:
            return saved, self.config(saved)
        except ValidationError as e:
            raise InputError("; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}".lstrip(": ") for err in e.errors())) from e

    def save(self, changes: Mapping[str, Any]) -> WikiConfig:
        """Apply `changes` and write the file (readable by its owner only); InputError, saying which key, when one is missing."""
        saved, cfg = self.apply(changes)
        if problems := missing_keys(cfg):
            raise InputError(" ".join(problems))
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


def param(model: LLMConfig | ClassifierConfig, name: str) -> Any:
    """A model parameter as the page edits it: unset is "", a list of providers is comma-separated text."""
    value = getattr(model, name)
    return "" if value is None else ",".join(value) if isinstance(value, list) else value


def key_env(providers: Mapping[str, Provider], name: str) -> str:
    return providers[name].key_env if name in providers else ""


def presets(providers: Mapping[str, Provider]) -> dict[str, dict[str, Any]]:
    """What the page needs to know of each provider."""
    return {name: {"base_url": p.base_url, "needs_key": bool(p.key_env), "models": list(p.models)} for name, p in providers.items()}


def switch(saved: dict[str, Any], section: str, prefix: str, changes: Mapping[str, Any], managed: Collection[str]) -> None:
    """Move one model (`saved[section]`) to the provider and address `changes` ask for.

    What it had goes under "remembered" for its old provider, and what it had there for the new
    one comes back. A key stays with its provider and address: a new address drops it.
    """
    node = saved.get(section) or {}
    before = str(node.get("provider") or "openrouter")
    name = prefix + "provider"
    after = before if name not in changes or name in managed else (str(changes[name] or "").strip() or "openrouter")
    if after != before:
        kept = saved.setdefault(REMEMBERED, {}).setdefault(section, {})
        kept[before] = {k: node.pop(k) for k in PER_PROVIDER if k in node}
        node.update(kept.pop(after, {}))
        saved[section] = node
    name = prefix + "base_url"
    url = str(node.get("base_url") or "").strip().rstrip("/")
    if name in changes and name not in managed and str(changes[name] or "").strip().rstrip("/") != url:
        node.pop("api_key", None)


def thresholds(preset: str) -> dict[str, Any]:
    """Every CROW threshold of a preset: CrowConfig's defaults with the preset's own values over them."""
    return {**CrowConfig().model_dump(), **CROW_PRESETS[preset]}


def is_default(field: str, value: str, classifier_provider: str) -> bool:
    """True when `value` is what the field has anyway: a threshold's preset value, a parameter's default."""
    section, leaf = FIELDS[field][0], FIELDS[field][-1]
    presets = thresholds(crow_preset(classifier_provider)) if field.startswith("crow_") else PARAMS.get(section, {})
    default = presets.get(leaf)
    try:
        return default is not None and math.isclose(float(value), float(default))
    except ValueError:
        return value == default  # a choice such as fallback_menu


def openrouter_key(saved: Mapping[str, Any]) -> str:
    """An OpenRouter key saved for either model, in use or remembered, at OpenRouter's own address."""
    remembered = saved.get(REMEMBERED) or {}
    for section in ("llm", "classifier"):
        active = saved.get(section) or {}
        nodes = [active] if (active.get("provider") or "openrouter") == "openrouter" else []
        nodes.append((remembered.get(section) or {}).get("openrouter") or {})
        for node in nodes:
            if node.get("api_key") and not node.get("base_url"):
                return str(node["api_key"])
    return ""


def prune(node: dict[str, Any]) -> dict[str, Any]:
    """Drop the empty objects a switch leaves behind, so the file holds only what was set."""
    for key in [k for k, v in node.items() if isinstance(v, dict) and not prune(v)]:
        del node[key]
    return node


def ready(cfg: WikiConfig) -> bool:
    """Every model the mode needs has its key."""
    return not missing_keys(cfg)
