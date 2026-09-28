# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Configuration: plain pydantic models, filled from `OKF_*` environment variables."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field, model_validator

from okf_wiki import __version__
from okf_wiki.errors import ConfigError


class Provider(NamedTuple):
    """An OpenAI-compatible endpoint: where it is, which variable holds its key, models to suggest."""

    base_url: str
    key_env: str  # "" when the endpoint takes no key
    models: tuple[str, ...]  # suggestions; the first is the default
    reasoning_off: Mapping[str, Any] | None = None  # request fields that turn thinking off; None: no one switch
    reasoning_least: Mapping[str, Any] | None = None  # for models that must think and refuse the off switch with a 400


PROVIDERS: dict[str, Provider] = {
    # Checked against each provider's model list on 2026-09-25. The settings page also
    # asks the provider for its live list (GET {base_url}/models).
    "openrouter": Provider(
        "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        ("google/gemini-3.8-flash", "anthropic/claude-sonnet-5", "openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash"),
        {"reasoning": {"enabled": False}},  # deepseek-v4.1-flash: 35 output tokens to name a folder, 2722 with effort minimal
        {"reasoning": {"effort": "minimal"}},  # gemini-3.8-flash must think: 400 to the off switch, this one it takes
    ),
    "openai": Provider("https://api.openai.com/v1", "OPENAI_API_KEY", ("gpt-6-luna", "gpt-6-sol")),
    "gemini": Provider(
        "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
        ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"),
    ),
    "ollama": Provider("http://localhost:11434/v1", "", ("gemma4", "qwen3.8")),
    "custom": Provider("", "", ()),  # vLLM, LM Studio, llama.cpp, a gateway: set base_url (and api_key if it wants one)
}

JEV = "typesafe/jev-1.13"  # pinned: thresholds are calibrated per model version

# Where the CROW classifier runs, apart from the LLM: every route speaks the System One API.
CLASSIFIER_PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", (JEV,)),
    "typesafe": Provider("https://api.typesafe.ai/v1", "TYPESAFE_API_KEY", (JEV,)),
    "custom": Provider("", "", (JEV,)),  # a local Laya server: set base_url (and api_key if it wants one)
}


class LLMConfig(BaseModel):
    """The writing model, behind any OpenAI-compatible `/chat/completions` (see PROVIDERS)."""

    provider: str = "openrouter"
    base_url: str = ""  # empty: the provider's
    model: str = ""  # empty: the provider's first suggestion
    api_key: str = ""
    temperature: float = Field(0.1, ge=0, le=2)
    timeout: float = Field(120.0, gt=0)
    # Thinking before answering made filing 5-40x slower in our runs (hidden tokens) and no better: off where the provider can.
    reasoning: bool = False
    # Extra JSON fields for every request. OpenRouter example:
    # {"provider": {"order": ["together", "coreweave"]}} to pin faster providers.
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fill_from_provider(self) -> LLMConfig:
        preset = PROVIDERS.get(self.provider)
        if preset is None:
            raise ValueError(f"unknown provider {self.provider!r}: use one of {', '.join(PROVIDERS)}")
        self.base_url = self.base_url or preset.base_url
        self.model = self.model or next(iter(preset.models), "")
        if not self.base_url or not self.model:
            raise ValueError(f"provider {self.provider!r} needs a base_url and a model")
        return self

    @property
    def ready(self) -> bool:
        """False while the provider still wants a key."""
        return bool(self.api_key or not PROVIDERS[self.provider].key_env)


class ClassifierConfig(BaseModel):
    """The CROW typed classifier, reached through the System One API (POST {base_url}/systemone); see CLASSIFIER_PROVIDERS."""

    provider: str = "openrouter"
    base_url: str = ""  # empty: the provider's
    model: str = ""  # empty: the provider's, pinned (thresholds are calibrated per model version)
    api_key: str = ""
    timeout: float = Field(8.0, gt=0)  # it answers in under a second; a slow call is dropped and the LLM decides (paper: 60)
    attempts: int = Field(1, gt=0)  # every caller falls back to the LLM, so a retry only makes the ingest wait (paper: 3)
    state_chars: int = Field(6_000, gt=0)  # compact state: title, summary and opening passage
    request_chars: int = Field(90_000, gt=0)  # state + questions per request (~32k tokens on OpenRouter)

    @model_validator(mode="after")
    def _fill_from_provider(self) -> ClassifierConfig:
        preset = CLASSIFIER_PROVIDERS.get(self.provider)
        if preset is None:
            raise ValueError(f"unknown classifier provider {self.provider!r}: use one of {', '.join(CLASSIFIER_PROVIDERS)}")
        self.base_url = self.base_url or preset.base_url
        self.model = self.model or preset.models[0]
        if not self.base_url:
            raise ValueError(f"classifier provider {self.provider!r} needs a base_url")
        return self

    @property
    def ready(self) -> bool:
        """False while the provider still wants a key."""
        return bool(self.api_key or not CLASSIFIER_PROVIDERS[self.provider].key_env)


class CrowConfig(BaseModel):
    """Thresholds of the CROW paper (§5.3). Calibrate them on a labelled sample of your wiki.

    Ingestion values are the paper's package defaults (never calibrated, Table 2). Retrieval values
    are the paper's calibrated ones (§8.4): at 0.5 most right notes were cut before ranking could act.
    """

    tau_route: float = Field(0.6, ge=0, le=1)  # routing Choice confidence below which the beam splits
    tau_path: float = Field(0.5, ge=0, le=1)  # best path score below which routing falls back to the LLM
    tau_ing: float = Field(0.5, ge=0, le=1)  # note-relevance probability for consolidation candidates
    tau_cons: float = Field(0.6, ge=0, le=1)  # create-or-modify confidence below which a new note is created
    tau_fold: float = Field(0.08, ge=0, le=1)  # retrieval: folder probability to explore it (paper default 0.5)
    tau_ret: float = Field(0.1, ge=0, le=1)  # retrieval: note probability to read it (paper default 0.5)
    tau_link: float = Field(0.6, ge=0, le=1)  # extension: relatedness probability for a See-also link
    beam: int = Field(2, gt=0)  # b at ingestion: paths kept per uncertain routing step
    # b at retrieval: folders explored per level; with low thresholds the beam, not tau_fold, bounds exploration
    retrieval_beam: int = Field(6, gt=0)
    k: int = Field(8, gt=0)  # CROW retrieval: notes passed to the answer (paper default 5)


# WikiConfig fields read from OKF_<NAME>; the nested models are read by prefix (OKF_LLM_*, OKF_CLASSIFIER_*).
_TOP_LEVEL_ENV = (
    "bundle", "mode", "summarize", "max_depth", "max_steps", "max_links", "max_notes", "upload_mb", "usage_log", "prompts_dir",
)


class WikiConfig(BaseModel):
    bundle: Path
    mode: Literal["classic", "crow"] = "crow"
    summarize: bool = True  # CROW step 0; False files the source verbatim
    max_depth: int = Field(4, ge=0)  # deepest folder level the librarian may reach or create
    max_steps: int = Field(12, gt=0)  # classic researcher: folders it may open per question
    max_links: int = Field(5, ge=0)  # See-also links added per ingest
    max_notes: int = Field(8, gt=0)  # classic researcher and CROW's fallback: notes read per question (CROW uses crow.k)
    source_chars: int = Field(100_000, gt=0)  # longest source text sent to the LLM
    upload_mb: int = Field(25, gt=0)  # largest request body the server reads: a big PDF fits, a runaway upload does not fill memory
    actor: str = f"okf_wiki/{__version__}"  # OKF `generated.by`
    usage_log: Path | None = None  # optional JSONL audit of every model call
    prompts_dir: Path | None = None  # optional folder overriding packaged prompts
    llm: LLMConfig = Field(default_factory=LLMConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    crow: CrowConfig = Field(default_factory=CrowConfig)

    @model_validator(mode="after")
    def _share_the_openrouter_key(self) -> WikiConfig:
        """One OpenRouter key serves both models when the classifier has none of its own."""
        openrouter = PROVIDERS["openrouter"].base_url
        # Both at OpenRouter's own address: a key for a gateway named "openrouter" must not travel to the real one.
        on_openrouter = self.llm.provider == "openrouter" and self.llm.base_url == openrouter and self.classifier.base_url == openrouter
        if not self.classifier.api_key and on_openrouter:
            self.classifier.api_key = self.llm.api_key
        return self

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, base: Mapping[str, Any] | None = None, **overrides: Any
    ) -> WikiConfig:
        """Build a config from `OKF_*` variables laid over `base` (e.g. the settings file); keyword overrides win."""
        env = os.environ if env is None else env
        top: dict[str, Any] = {}
        for key in _TOP_LEVEL_ENV:
            if value := env.get(f"OKF_{key.upper()}"):
                top[key] = value
        llm: dict[str, Any] = {
            f: env[f"OKF_LLM_{f.upper()}"] for f in LLMConfig.model_fields if env.get(f"OKF_LLM_{f.upper()}")
        }
        if "extra_body" in llm:
            try:
                llm["extra_body"] = json.loads(llm["extra_body"])
            except ValueError as e:
                raise ConfigError(f"OKF_LLM_EXTRA_BODY must be a JSON object (single-quote it in .env): {e}") from e
        clf = {
            f: env[f"OKF_CLASSIFIER_{f.upper()}"]
            for f in ClassifierConfig.model_fields
            if env.get(f"OKF_CLASSIFIER_{f.upper()}")
        }
        crow = {f: env[f"OKF_{f.upper()}"] for f in CrowConfig.model_fields if env.get(f"OKF_{f.upper()}")}
        data = merge(dict(base or {}), {**top, "llm": llm, "classifier": clf, "crow": crow})
        # Each model's provider variable (OPENROUTER_API_KEY, …) is its key unless OKF_LLM_API_KEY / OKF_CLASSIFIER_API_KEY is set.
        for name, explicit, presets in (("llm", llm, PROVIDERS), ("classifier", clf, CLASSIFIER_PROVIDERS)):
            preset = presets.get(data[name].get("provider", "openrouter"))
            if "api_key" not in explicit and preset and preset.key_env and env.get(preset.key_env):
                data[name]["api_key"] = env[preset.key_env]
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
        return cls.model_validate(data)


def merge(base: dict[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    """`top` laid over `base`, nested mappings merged key by key (a copy; neither is changed)."""
    out = dict(base)
    for key, value in top.items():
        out[key] = merge(out[key], value) if isinstance(value, Mapping) and isinstance(out.get(key), Mapping) else value
    return out
