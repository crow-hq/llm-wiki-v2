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

"""The small JSON-over-HTTP base shared by the LLM and the classifier clients."""

from __future__ import annotations

import time
from typing import Any

import httpx

from okf_wiki.usage import UsageTracker


class ModelError(RuntimeError):
    """A model endpoint failed or answered something unusable."""


def reported_cost(usage: dict[str, Any]) -> float | None:
    """USD cost in a usage block: OpenRouter sends a number, Bifrost {"total_cost": …}."""
    cost = usage.get("cost")
    if isinstance(cost, dict):
        cost = cost.get("total_cost")
    return float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


class HttpModel:
    """POST JSON to `{base_url}{path}` with bearer auth and retries on transient errors."""

    kind = "llm"
    retry_status = frozenset({408, 429, 500, 502, 503, 504, 529})
    attempts = 3
    backoff = 0.5  # seconds, doubled on each retry

    def __init__(
        self,
        base_url: str,
        model: str,
        usage: UsageTracker,
        *,
        api_key: str = "",
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.usage = usage
        self.api_key = api_key
        self.headers: dict[str, str] = {}
        self.client = client or httpx.Client(timeout=timeout)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {**self.headers, **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})}
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.attempts + 1):
            try:
                response = self.client.post(url, json=payload, headers=headers)
            except httpx.TransportError as e:
                if attempt == self.attempts:
                    raise ModelError(f"{self.kind} request to {url} failed: {e}") from e
            else:
                if response.status_code < 400:
                    try:
                        data = response.json()
                    except ValueError as e:
                        raise ModelError(f"{self.kind} answered non-JSON from {url}") from e
                    if not isinstance(data, dict):
                        raise ModelError(f"{self.kind} answered a non-object from {url}")
                    return data
                if response.status_code not in self.retry_status or attempt == self.attempts:
                    raise ModelError(f"{self.kind} {response.status_code} from {url}: {response.text[:300]}")
            time.sleep(self.backoff * 2 ** (attempt - 1))
        raise AssertionError("unreachable")
