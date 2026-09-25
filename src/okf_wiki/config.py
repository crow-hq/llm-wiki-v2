# Copyright 2026 Federico Cesarini
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
from typing import Any, Literal

from pydantic import BaseModel, Field

from okf_wiki import __version__


class LLMConfig(BaseModel):
    """The writing model, reached through Bifrost's OpenAI-compatible API."""

    base_url: str = "http://localhost:8080/v1"
    # Bifrost model names are "<provider>/<model>": openrouter/…, gemini/…, vertex/…
    model: str = "openrouter/google/gemini-2.5-flash"
    api_key: str = ""
    temperature: float = 0.1
    timeout: float = 120.0
    # Extra JSON fields for every request, forwarded by Bifrost to the provider. OpenRouter example:
    # {"provider": {"order": ["together", "coreweave"]}} to pin faster providers.
    extra_body: dict[str, Any] = Field(default_factory=dict)


class ClassifierConfig(BaseModel):
    """The CROW typed classifier, reached through the System One API (POST {base_url}/systemone).

    Today OpenRouter is called directly; point `base_url` at
    http://bifrost:8080/typesafe/v1 to route it through Bifrost instead.
    """

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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: Any) -> WikiConfig:
        """Build a config from `OKF_*` variables; keyword overrides win."""
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
        if "base_url" not in llm and env.get("BIFROST_PORT"):  # host run next to the Docker stack
            llm["base_url"] = f"http://localhost:{env['BIFROST_PORT']}/v1"
        clf = {
            f: env[f"OKF_CLASSIFIER_{f.upper()}"]
            for f in ClassifierConfig.model_fields
            if env.get(f"OKF_CLASSIFIER_{f.upper()}")
        }
        clf.setdefault("api_key", env.get("OPENROUTER_API_KEY", ""))
        crow = {f: env[f"OKF_{f.upper()}"] for f in CrowConfig.model_fields if env.get(f"OKF_{f.upper()}")}
        data: dict[str, Any] = {**top, "llm": llm, "classifier": clf, "crow": crow}
        for key, value in overrides.items():
            if value is not None:
                data[key] = value
        return cls.model_validate(data)
