"""Requests per source address, for apps anyone may open without signing in.

A token bucket per address: ``per_minute`` requests may arrive at once, after
that they are let through at ``per_minute`` a minute.
"""

from __future__ import annotations

import ipaddress
import math
import time
from typing import Callable

MAX_BUCKETS = 50_000


def normalize_source(value: str | None) -> str:
    """One key per client: no port, an IPv6 address counts as its /64."""

    text = (value or "").strip()
    if not text:
        return ""
    if text.startswith("["):
        text = text[1:].partition("]")[0]
    elif text.count(":") == 1:
        text = text.partition(":")[0]
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return text[:64].lower()
    if address.version == 6:
        mapped = address.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        prefix = (int(address) >> 64) << 64
        return f"{ipaddress.IPv6Address(prefix)}/64"
    return str(address)


class RateLimiter:
    def __init__(self, per_minute: int, *, clock: Callable[[], float] = time.monotonic):
        self.per_minute = max(1, int(per_minute))
        self._clock = clock
        # address -> (tokens left, when that was true)
        self._buckets: dict[str, tuple[float, float]] = {}

    @property
    def retry_after(self) -> int:
        """Seconds until a client that ran dry may send one more request."""

        return max(1, math.ceil(60 / self.per_minute))

    def allow(self, source: str | None) -> bool:
        key = normalize_source(source)
        now = self._clock()
        capacity = float(self.per_minute)
        tokens, at = self._buckets.get(key, (capacity, now))
        tokens = min(capacity, tokens + (now - at) * capacity / 60.0)
        allowed = tokens >= 1.0
        if allowed:
            tokens -= 1.0
        if key not in self._buckets and len(self._buckets) >= MAX_BUCKETS:
            self._prune(now)
        self._buckets[key] = (tokens, now)
        return allowed

    def _prune(self, now: float) -> None:
        # A bucket nobody touched for a minute is full again: forget it.
        self._buckets = {key: value for key, value in self._buckets.items() if now - value[1] < 60.0}
        if len(self._buckets) >= MAX_BUCKETS:
            self._buckets.clear()
