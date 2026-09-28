# Copyright 2026 Federico Cesarini, Marco Sassarini
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The small JSON-over-HTTP base shared by the LLM and the classifier clients."""

from __future__ import annotations

import random
import time
from typing import Any, Self

import httpx

from okf_wiki.errors import ModelError
from okf_wiki.models.usage import UsageTracker


def reported_cost(usage: dict[str, Any]) -> float | None:
    """USD cost in a usage block, when the provider reports it (OpenRouter does)."""
    cost = usage.get("cost")
    return float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None


class HttpModel:
    """POST JSON to `{base_url}{path}` with bearer auth and retries on transient errors."""

    kind = "llm"
    retry_status = frozenset({408, 429, 500, 502, 503, 504, 529})
    attempts = 3
    backoff = 0.5  # seconds, doubled on each retry, plus up to as much again at random so clients don't retry in step
    max_retry_after = 30.0  # a provider's Retry-After is honoured up to this; longer waits fail instead of hanging a request

    def __init__(
        self,
        base_url: str,
        model: str,
        usage: UsageTracker,
        *,
        api_key: str = "",
        needs_key: bool = False,
        timeout: float = 60.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.usage = usage
        self.api_key = api_key
        self.needs_key = needs_key
        self.headers: dict[str, str] = {}
        self._owns_client = client is None  # a client passed in belongs to the caller, who closes it
        self.client = client or httpx.Client(timeout=timeout)

    def close(self) -> None:
        """Release the connection pool; a no-op for a client passed in."""
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if self.needs_key and not self.api_key:  # fail here, not with the provider's 401 after a round trip
            raise ModelError(f"no API key for the {self.kind}: add one in the settings (the gear) or run okf-wiki setup")
        headers = {**self.headers, **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})}
        url = f"{self.base_url}{path}"
        for attempt in range(1, self.attempts + 1):
            backoff = wait = self.backoff * 2 ** (attempt - 1)
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
                    raise ModelError(f"{self.kind} {response.status_code} from {url}: {response.text[:300]}", status=response.status_code)
                wait = max(wait, _retry_after(response))
                if wait > self.max_retry_after:
                    raise ModelError(f"{self.kind} {response.status_code} from {url}: busy for {wait:.0f}s, try again later")
            time.sleep(wait + random.uniform(0, backoff))  # the jitter spreads retries, never a provider's Retry-After
        raise AssertionError("unreachable")


def _retry_after(response: httpx.Response) -> float:
    """Seconds the provider asks to wait (Retry-After in seconds), 0 when it does not say."""
    try:
        return max(float(response.headers.get("retry-after", 0)), 0.0)
    except ValueError:  # the HTTP-date form: rare for APIs, the exponential backoff covers it
        return 0.0
