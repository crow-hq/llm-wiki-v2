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

"""The writing model: any provider behind Bifrost's OpenAI-compatible `/chat/completions`."""

from __future__ import annotations

import json
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from okf_wiki.client import HttpModel, ModelError, reported_cost
from okf_wiki.config import LLMConfig
from okf_wiki.usage import UsageTracker

T = TypeVar("T", bound=BaseModel)

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json(text: str) -> str:
    """The first JSON object inside a reply, tolerating code fences and chatter (even with braces) around it."""
    text = _FENCE.sub("", text.strip())
    decoder = json.JSONDecoder()
    for start in (i for i, ch in enumerate(text) if ch == "{"):
        try:
            obj, end = decoder.raw_decode(text, start)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return text[start:end]
    return text


class LLM(HttpModel):
    kind = "llm"

    def __init__(self, cfg: LLMConfig, usage: UsageTracker, *, client: httpx.Client | None = None) -> None:
        super().__init__(cfg.base_url, cfg.model, usage, api_key=cfg.api_key, timeout=cfg.timeout, client=client)
        self.temperature = cfg.temperature
        self.extra_body = dict(cfg.extra_body)
        if self.extra_body:  # Bifrost drops unknown fields unless asked to forward them
            self.headers["x-bf-passthrough-extra-params"] = "true"

    def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
        start = time.perf_counter()
        data = self._post(
            "/chat/completions",
            {**self.extra_body, "model": self.model, "messages": messages, "temperature": self.temperature},
        )
        usage = data.get("usage") or {}
        self.usage.record(
            "llm",
            self.model,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            op=op,
            seconds=time.perf_counter() - start,
            cost=reported_cost(usage),
            cached=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
        )
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"llm reply without content: {str(data)[:300]}") from e

    def complete(self, system: str, user: str, *, op: str = "") -> str:
        return self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], op=op)

    def json(self, system: str, user: str, schema: type[T], *, op: str = "") -> T:
        """Ask for one JSON object and validate it; one corrective retry, then ModelError."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": f"{user}\n\nReply with a single JSON object and nothing else."},
        ]
        error: Exception | None = None
        for _ in range(2):
            reply = self.chat(messages, op=op)
            try:
                return schema.model_validate(json.loads(extract_json(reply)))
            except (ValueError, ValidationError) as e:
                error = e
                messages += [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content": f"That reply was not valid: {_short(e)}. Reply again with only the JSON object."},
                ]
        raise ModelError(f"llm {op or 'json'} reply invalid after retry: {_short(error)}")


def _short(e: Any) -> str:
    return str(e).replace("\n", " ")[:300]
