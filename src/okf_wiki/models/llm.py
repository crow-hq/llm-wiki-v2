# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The writing model: any provider behind an OpenAI-compatible `/chat/completions`."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from okf_wiki.config import PROVIDERS, LLMConfig
from okf_wiki.errors import ModelError
from okf_wiki.models.client import HttpModel, reported_cost
from okf_wiki.models.usage import UsageTracker

log = logging.getLogger(__name__)

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
        super().__init__(
            cfg.base_url, cfg.model, usage, api_key=cfg.api_key, needs_key=not cfg.ready, timeout=cfg.timeout, client=client
        )
        self.temperature = cfg.temperature
        preset = PROVIDERS[cfg.provider]
        # No switch when reasoning is wanted, or when the caller's extra_body already says how much.
        off = preset.reasoning_off if not cfg.reasoning and not (preset.reasoning_off or {}).keys() & cfg.extra_body.keys() else None
        self.extra_body = {**(off or {}), **cfg.extra_body}  # the caller's extra_body wins, reasoning included
        self._least = preset.reasoning_least if off else None  # tried once, if the model refuses the off switch

    def chat(self, messages: list[dict[str, str]], *, op: str = "") -> str:
        start = time.perf_counter()
        try:
            data = self._post("/chat/completions", self._body(messages))
        except ModelError as e:
            if not (self._least and e.status == 400 and "reasoning" in str(e).lower()):
                raise
            log.info("%s must think: asking for the least reasoning from now on", self.model)
            self.extra_body, self._least = {**self.extra_body, **self._least}, None
            data = self._post("/chat/completions", self._body(messages))
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

    def _body(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        return {**self.extra_body, "model": self.model, "messages": messages, "temperature": self.temperature}

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


def probe(llm: LLM) -> str:
    """One tiny request, to tell a working model from a wrong key or address (ModelError)."""
    return llm.chat([{"role": "user", "content": "Reply with the single word OK."}], op="settings/probe").strip()[:200]


def live_models(llm: LLMConfig, client: httpx.Client | None = None) -> list[str]:
    """The text models the provider offers right now (GET {base_url}/models); [] when it does not say."""
    headers = {"Authorization": f"Bearer {llm.api_key}"} if llm.api_key else {}
    own = client is None
    http = client or httpx.Client(timeout=10)
    try:
        response = http.get(f"{llm.base_url.rstrip('/')}/models", headers=headers)
        data = response.raise_for_status().json()["data"]
        return [
            str(m["id"]).removeprefix("models/")  # Gemini names them models/<id>
            for m in data
            if m.get("id") and "text" in ((m.get("architecture") or {}).get("output_modalities") or ["text"])
        ]
    except (httpx.HTTPError, KeyError, TypeError, ValueError, AttributeError):
        return []
    finally:
        if own:
            http.close()
