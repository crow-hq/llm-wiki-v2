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

"""Configuration: plain pydantic models, filled from `OKF_*` environment variables."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field, model_validator

from okf_wiki import __version__


class Provider(NamedTuple):
    """An OpenAI-compatible endpoint: where it is, which variable holds its key, models to suggest."""

    base_url: str
    key_env: str  # "" when the endpoint takes no key
    models: tuple[str, ...]  # suggestions; the first is the default


PROVIDERS: dict[str, Provider] = {
    # Checked against each provider's model list on 2026-09-25. The settings page also
    # asks the provider for its live list (GET {base_url}/models).
    "openrouter": Provider(
        "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
        ("google/gemini-3.8-flash", "anthropic/claude-sonnet-5", "openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash"),
    ),
    "openai": Provider("https://api.openai.com/v1", "OPENAI_API_KEY", ("gpt-6-luna", "gpt-6-sol")),
    "gemini": Provider(
        "https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY",
        ("gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.5-flash-lite"),
    ),
    "ollama": Provider("http://localhost:11434/v1", "", ("gemma4", "qwen3.8")),
    "custom": Provider("", "", ()),  # vLLM, LM Studio, llama.cpp, a gateway: set base_url (and api_key if it wants one)
}


class LLMConfig(BaseModel):
    """The writing model, behind any OpenAI-compatible `/chat/completions` (see PROVIDERS)."""

    provider: str = "openrouter"
    base_url: str = ""  # empty: the provider's
    model: str = ""  # empty: the provider's first suggestion
    api_key: str = ""
    temperature: float = 0.1
    timeout: float = 120.0
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
    """The CROW typed classifier, reached through the System One API (POST {base_url}/systemone)."""

    base_url: str = "https://openrouter.ai/api/v1"
    model: str = "typesafe/jev-1.13"  # pinned: thresholds are calibrated per model version
    api_key: str = ""
    timeout: float = 60.0
    state_chars: int = 6_000  # compact state: title, summary and opening passage
    request_chars: int = 90_000  # state + questions per request (~32k tokens on OpenRouter)


class CrowConfig(BaseModel):
    """Thresholds of the CROW paper (§5.3). Calibrate them on a labelled sample of your wiki."""

    tau_route: float = 0.6  # routing Choice confidence below which the beam splits
    tau_path: float = 0.5  # best path score below which routing falls back to the LLM
    tau_ing: float = 0.5  # note-relevance probability for consolidation candidates
    tau_cons: float = 0.6  # create-or-modify confidence below which a new note is created
    tau_fold: float = 0.5  # retrieval: folder probability to explore it
    tau_ret: float = 0.5  # retrieval: note probability to read it
    tau_link: float = 0.6  # extension: relatedness probability for a See-also link
    beam: int = 2  # b: paths kept per uncertain routing step, folders explored per level
    k: int = 5  # CROW retrieval: notes passed to the answer


class WikiConfig(BaseModel):
    bundle: Path
    mode: Literal["classic", "crow"] = "classic"
    summarize: bool = True  # CROW step 0; False files the source verbatim
    max_depth: int = 4  # deepest folder level the librarian may reach or create
    max_steps: int = 12  # classic researcher: folders it may open per question
    max_links: int = 5  # See-also links added per ingest
    max_notes: int = 5  # classic researcher: notes read per question (CROW uses crow.k)
    source_chars: int = 100_000  # longest source text sent to the LLM
    actor: str = f"okf_wiki/{__version__}"  # OKF `generated.by`
    usage_log: Path | None = None  # optional JSONL audit of every model call
    prompts_dir: Path | None = None  # optional folder overriding packaged prompts
    llm: LLMConfig = Field(default_factory=LLMConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    crow: CrowConfig = Field(default_factory=CrowConfig)

    @model_validator(mode="after")
    def _share_the_openrouter_key(self) -> WikiConfig:
        """One OpenRouter key serves both models when the classifier has none of its own."""
        if not self.classifier.api_key and self.llm.provider == "openrouter" and self.classifier.base_url == PROVIDERS["openrouter"].base_url:
            self.classifier.api_key = self.llm.api_key
        return self

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, base: Mapping[str, Any] | None = None, **overrides: Any
    ) -> WikiConfig:
        """Build a config from `OKF_*` variables laid over `base` (e.g. the settings file); keyword overrides win."""
        env = os.environ if env is None else env
        top: dict[str, Any] = {}
        for key in ("bundle", "mode", "summarize", "max_depth", "max_steps", "max_links", "max_notes", "usage_log", "prompts_dir"):
            if value := env.get(f"OKF_{key.upper()}"):
                top[key] = value
        llm: dict[str, Any] = {
            f: env[f"OKF_LLM_{f.upper()}"] for f in LLMConfig.model_fields if env.get(f"OKF_LLM_{f.upper()}")
        }
        if "extra_body" in llm:
            try:
                llm["extra_body"] = json.loads(llm["extra_body"])
            except ValueError as e:
                raise ValueError(f"OKF_LLM_EXTRA_BODY must be a JSON object (single-quote it in .env): {e}") from e
        clf = {
            f: env[f"OKF_CLASSIFIER_{f.upper()}"]
            for f in ClassifierConfig.model_fields
            if env.get(f"OKF_CLASSIFIER_{f.upper()}")
        }
        crow = {f: env[f"OKF_{f.upper()}"] for f in CrowConfig.model_fields if env.get(f"OKF_{f.upper()}")}
        data = merge(dict(base or {}), {**top, "llm": llm, "classifier": clf, "crow": crow})
        # The provider's own variable (OPENROUTER_API_KEY, …) is the key unless OKF_LLM_API_KEY is set.
        preset = PROVIDERS.get(data["llm"].get("provider", "openrouter"))
        if "api_key" not in llm and preset and preset.key_env and env.get(preset.key_env):
            data["llm"]["api_key"] = env[preset.key_env]
        if "api_key" not in clf and env.get("OPENROUTER_API_KEY"):
            data["classifier"]["api_key"] = env["OPENROUTER_API_KEY"]
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
