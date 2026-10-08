# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Configuration: plain pydantic models, filled from `OKF_*` environment variables."""

from __future__ import annotations

import json
import os
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field, field_validator, model_validator

from llmw2 import __version__
from llmw2.errors import ConfigError


class Provider(NamedTuple):
    """An OpenAI-compatible endpoint: where it is, which variable holds its key, models to suggest."""

    base_url: str
    key_env: str  # "" when the endpoint takes no key
    models: tuple[str, ...]  # suggestions; the first is the default
    reasoning_off: Mapping[str, Any] | None = None  # request fields that turn thinking off; None: no one switch
    reasoning_least: Mapping[str, Any] | None = None  # for models that must think and refuse the off switch with a 400
    read_chars: int = 16_000  # characters one prompt may hold, when the user does not say (a context window of ~4k tokens, safe anywhere)
    max_tokens_field: str = "max_tokens"  # the request field that caps the output; OpenAI's reasoning models reject "max_tokens"


PROVIDERS: dict[str, Provider] = {
    # Checked against each provider's model list on 2026-09-25. The settings page also
    # asks the provider for its live list (GET {base_url}/models).
    "openrouter": Provider(
        "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        ("google/gemini-3.8-flash", "anthropic/claude-sonnet-5", "openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash"),
        {"reasoning": {"enabled": False}},  # deepseek-v4.1-flash: 35 output tokens to name a folder, 2722 with effort minimal
        {"reasoning": {"effort": "minimal"}},  # gemini-3.8-flash must think: 400 to the off switch, this one it takes
        read_chars=100_000,
    ),
    "openai": Provider(
        "https://api.openai.com/v1", "OPENAI_API_KEY", ("gpt-6-luna", "gpt-6-sol"),
        read_chars=100_000, max_tokens_field="max_completion_tokens",
    ),
    "gemini": Provider(
        "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
        ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"),
        read_chars=100_000,
    ),
    "ollama": Provider("http://localhost:11434/v1", "", ("gemma4", "qwen3.8")),
    "custom": Provider("", "", ()),  # vLLM, LM Studio, llama.cpp, a gateway: set base_url (and api_key if it wants one)
}

# Characters a prompt takes beyond the text it carries (rules, instructions, titles), and the usual longest note body.
PROMPT_ROOM = 6_000
NOTE_CAP = 10_000
NOTE_RATIO = 4  # a proportional note is about this many times shorter than its source
NOTE_MAX = 45_000  # the longest note a proportional length asks for, when a call holds that much

JEV = "typesafe/jev-1.13"  # pinned: thresholds are calibrated per model version

# Where the CROW classifier runs, apart from the LLM: every route speaks the System One API.
CLASSIFIER_PROVIDERS: dict[str, Provider] = {
    "openrouter": Provider("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", (JEV,)),
    "typesafe": Provider("https://api.typesafe.ai/v1", "TYPESAFE_API_KEY", (JEV,)),
    # Any System One server, such as Laya on this computer (`LAYA_PORT=8001 laya-serve`: the wiki takes 8000): set
    # base_url, and api_key if it wants one. It starts from the Laya thresholds preset and from "typed-decisions", the
    # Laya checkpoint that preset was measured on ("auto" lets Laya pick its English or multilingual one per note).
    "custom": Provider("", "", ("typed-decisions", "auto", "english", "multilingual")),
}

# How the settings page and the errors name each provider.
NAMES = {
    "openrouter": "OpenRouter", "openai": "OpenAI", "gemini": "Gemini", "ollama": "Ollama", "typesafe": "TypeSafe", "custom": "your server",
}


class LLMConfig(BaseModel):
    """The writing model, behind any OpenAI-compatible `/chat/completions` (see PROVIDERS)."""

    provider: str = "openrouter"
    base_url: str = ""  # empty: the provider's
    model: str = ""  # empty: the provider's first suggestion
    api_key: str = ""
    temperature: float = Field(0.1, ge=0, le=2)
    seed: int | None = None  # sent with every request when set; providers that support it answer the same prompt the same way
    # OpenRouter only: providers allowed to answer, in order, with no fallback to any other (a down one fails the call).
    # Sent as {"provider": {"order": ..., "allow_fallbacks": false, "require_parameters": true}}; extra_body["provider"] wins.
    pin_provider: list[str] = Field(default_factory=list)
    timeout: float = Field(120.0, gt=0)
    # Characters one prompt may hold, text and rules together; None: the provider's (PROVIDERS). Longer sources are read in parts.
    read_chars: int | None = Field(None, ge=8_000)
    # Thinking before answering made filing 5-40x slower in our runs (hidden tokens) and no better: off where the provider can.
    reasoning: bool = False
    # Extra JSON fields for every request. OpenRouter example:
    # {"provider": {"order": ["together", "coreweave"]}} to pin faster providers.
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("pin_provider", mode="before")
    @classmethod
    def _split_providers(cls, value: Any) -> Any:
        """A comma-separated string (environment, settings file) is a list."""
        return [p.strip() for p in value.split(",") if p.strip()] if isinstance(value, str) else value

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
    votes: int = Field(1, ge=1, le=9)  # classifier calls averaged per borderline routing decision (1: one call, no averaging)
    vote_band: float = Field(0.05, ge=0, le=1)  # how close to tau_route, tau_path or a tie counts as borderline
    # extension: the folders the routing fallback shows the LLM: those the classifier visited, or those and the rest of the tree
    fallback_menu: Literal["visited", "tree"] = "visited"
    beam: int = Field(2, gt=0)  # b at ingestion: paths kept per uncertain routing step
    # b at retrieval: folders explored per level; with low thresholds the beam, not tau_fold, bounds exploration
    retrieval_beam: int = Field(6, gt=0)
    k: int = Field(8, gt=0)  # CROW retrieval: notes passed to the answer (paper default 5)


# CROW thresholds per classifier: Jev's (OpenRouter, TypeSafe) are CrowConfig's defaults, the paper's; a custom
# classifier starts from Laya's.
# Laya's typed-decisions checkpoint answers with flatter probabilities; measured on 25 sample notes (2026-09-28):
# a routing choice it gets wrong scores at most 0.61 and it never picks "New subfolder", so below 0.65 the LLM routes;
# its merge confidence is 0.04-0.12 for a real follow-up and at most 0.044 for a related but distinct note; linked
# notes score 0.5 or more, unrelated ones at most 0.49; the folder and the note that answer a question score at least
# 0.17 and 0.21, the others mostly under 0.18 and 0.20.
CROW_PRESETS: dict[str, dict[str, float | int]] = {
    "jev": {},
    "laya": {"tau_path": 0.65, "tau_ing": 0.45, "tau_cons": 0.05, "tau_fold": 0.15, "tau_ret": 0.2, "tau_link": 0.5, "k": 5},
}


def crow_preset(classifier_provider: str) -> str:
    """The name of the thresholds preset for a classifier provider: Laya's for a custom one, Jev's for the others."""
    return "laya" if classifier_provider == "custom" else "jev"


# WikiConfig fields read from OKF_<NAME>; the nested models are read by prefix (OKF_LLM_*, OKF_CLASSIFIER_*).
_TOP_LEVEL_ENV = (
    "bundle", "mode", "summarize", "max_depth", "max_steps", "max_links", "max_notes", "upload_mb", "concurrency", "usage_log",
    "prompts_dir", "note_chars", "note_length",
)


class WikiConfig(BaseModel):
    bundle: Path
    mode: Literal["classic", "crow"] = "crow"
    summarize: bool = True  # CROW step 0; False files the source verbatim
    max_depth: int = Field(4, ge=0)  # deepest folder level the librarian may reach or create
    max_steps: int = Field(12, gt=0)  # classic researcher: folders it may open per question
    max_links: int = Field(5, ge=0)  # See-also links added per ingest
    max_notes: int = Field(8, gt=0)  # classic researcher and CROW's fallback: notes read per question (CROW uses crow.k)
    # Deprecated: the characters one prompt may hold, when `llm.read_chars` is not set (see `effective_read_chars`).
    source_chars: int = Field(100_000, gt=0)
    note_chars: int | None = Field(None, ge=1_000)  # about how long a note body may be; None: automatic (see `effective_note_chars`)
    note_length: Literal["fixed", "proportional"] = "fixed"  # a note about `note_chars` long, or in proportion to its source
    upload_mb: int = Field(25, gt=0)  # largest request body the server reads: a big PDF fits, a runaway upload does not fill memory
    concurrency: int = Field(4, ge=1, le=16)  # documents prepared (extracted, summarized) at once by a batch or the server
    actor: str = f"llmw2/{__version__}"  # OKF `generated.by`
    usage_log: Path | None = None  # optional JSONL audit of every model call
    prompts_dir: Path | None = None  # optional folder overriding packaged prompts
    llm: LLMConfig = Field(default_factory=LLMConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    crow: CrowConfig = Field(default_factory=CrowConfig)

    @model_validator(mode="after")
    def _warn_source_chars(self) -> WikiConfig:
        if "source_chars" in self.model_fields_set:
            warnings.warn("source_chars is deprecated: use llm.read_chars (OKF_LLM_READ_CHARS)", DeprecationWarning, stacklevel=2)
        return self

    @model_validator(mode="after")
    def _crow_preset(self) -> WikiConfig:
        """The thresholds not set explicitly (environment, settings, caller) come from the classifier's preset."""
        for name, value in CROW_PRESETS[crow_preset(self.classifier.provider)].items():
            if name not in self.crow.model_fields_set:
                setattr(self.crow, name, value)
        return self

    @model_validator(mode="after")
    def _share_the_openrouter_key(self) -> WikiConfig:
        """One OpenRouter key serves both models when the classifier has none of its own."""
        openrouter = PROVIDERS["openrouter"].base_url
        # Both at OpenRouter's own address: a key for a gateway named "openrouter" must not travel to the real one.
        on_openrouter = self.llm.provider == "openrouter" and self.llm.base_url == openrouter and self.classifier.base_url == openrouter
        if not self.classifier.api_key and on_openrouter:
            self.classifier.api_key = self.llm.api_key
        return self

    @property
    def effective_read_chars(self) -> int:
        """Characters one prompt may hold: `llm.read_chars`, else an explicit `source_chars`, else the provider's."""
        if self.llm.read_chars is not None:
            return self.llm.read_chars
        if "source_chars" in self.model_fields_set:
            return self.source_chars
        return PROVIDERS[self.llm.provider].read_chars

    @property
    def effective_note_chars(self) -> int:
        """About how long a note body may be: `note_chars` or NOTE_CAP, and never more than half the text of a prompt."""
        return max(1_000, min(self.note_chars or NOTE_CAP, (self.effective_read_chars - PROMPT_ROOM) // 2))

    @property
    def note_ceiling(self) -> int:
        """The longest note a call can hold: `effective_note_chars` when `note_length` is fixed, else up to NOTE_MAX."""
        if self.note_length == "fixed":
            return self.effective_note_chars
        return max(self.effective_note_chars, min(NOTE_MAX, (self.effective_read_chars - PROMPT_ROOM) // 2))

    def target_note_chars(self, source_chars: int, existing_chars: int = 0) -> int:
        """About how long the note being written should be: for a merge, the existing note and the share the source adds."""
        if self.note_length == "fixed":
            return self.effective_note_chars
        own = min(self.note_ceiling, max(self.effective_note_chars, source_chars // NOTE_RATIO))
        return min(self.note_ceiling, existing_chars + own)

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


def missing_keys(cfg: WikiConfig) -> list[str]:
    """What stops the wiki from working, in the words of the settings page; [] when every model the mode needs has its key."""
    problems: list[str] = []
    shared = cfg.llm.provider == cfg.classifier.provider == "openrouter"
    if not cfg.llm.ready:
        too = " (it serves the CROW classifier too)" if shared and cfg.mode == "crow" else ""
        problems.append(f"{NAMES[cfg.llm.provider]} needs an API key{too}: paste it in Settings → API key, or run llmwiki2 setup.")
    if cfg.mode == "crow" and not cfg.classifier.ready and not (shared and not cfg.llm.ready):
        problems.append(
            f"CROW mode needs a key for its classifier on {NAMES[cfg.classifier.provider]}: "
            "paste it in Settings → Advanced → Classifier key, or choose classic mode there."
        )
    return problems


def merge(base: dict[str, Any], top: Mapping[str, Any]) -> dict[str, Any]:
    """`top` laid over `base`, nested mappings merged key by key (a copy; neither is changed)."""
    out = dict(base)
    for key, value in top.items():
        out[key] = merge(out[key], value) if isinstance(value, Mapping) and isinstance(out.get(key), Mapping) else value
    return out
