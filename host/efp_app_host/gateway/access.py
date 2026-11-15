"""Who may open which app: the Portal decides, the gateway remembers briefly.

Every request of a signed-in visitor needs an answer to "may this user open
this app?". The Portal is asked at most once per ``access_cache_seconds`` for
each (app, user). When the Portal cannot be reached, an answer younger than
five minutes is reused; without one the caller refuses the request (fail
closed).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Callable

from ..interfaces import PortalApi
from ..models import Visitor

logger = logging.getLogger(__name__)

STALE_SECONDS = 300.0
# After a failed call the Portal is left alone for this long, so that a
# Portal outage does not make every request wait for a timeout.
BACKOFF_SECONDS = 5.0
MAX_ENTRIES = 50_000


class AccessUnavailable(Exception):
    """The Portal could not be asked and nothing recent is remembered."""


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None
    visitor: Visitor | None
    at: float
    stale: bool = False


class AccessCache:
    def __init__(self, portal: PortalApi, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self._portal = portal
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._entries: dict[tuple[str, int], AccessDecision] = {}
        self._pending: dict[tuple[str, int], asyncio.Future] = {}
        self._portal_down_until = 0.0

    def remember(self, app_id: str, user_id: int, allowed: bool, reason: str | None, visitor: Visitor | None) -> AccessDecision:
        decision = AccessDecision(allowed=bool(allowed), reason=reason, visitor=visitor, at=self._clock())
        if len(self._entries) >= MAX_ENTRIES:
            self._prune()
        self._entries[(app_id, int(user_id))] = decision
        return decision

    def _prune(self) -> None:
        horizon = self._clock() - STALE_SECONDS
        self._entries = {key: value for key, value in self._entries.items() if value.at >= horizon}
        if len(self._entries) >= MAX_ENTRIES:
            self._entries.clear()

    async def check(self, app_id: str, user_id: int) -> AccessDecision:
        """The decision for (app, user). Raises ``AccessUnavailable`` when there is none."""

        key = (app_id, int(user_id))
        cached = self._entries.get(key)
        now = self._clock()
        if cached is not None and now - cached.at < self.ttl_seconds:
            return cached
        if now < self._portal_down_until:
            return self._fallback(key)

        pending = self._pending.get(key)
        if pending is None:
            # Requests of one page arrive together: they share one call.
            pending = asyncio.ensure_future(self._ask(key))
            self._pending[key] = pending
            pending.add_done_callback(lambda _done, key=key: self._pending.pop(key, None))
        decision = await asyncio.shield(pending)
        if decision is None:
            return self._fallback(key)
        return decision

    async def _ask(self, key: tuple[str, int]) -> AccessDecision | None:
        try:
            allowed, reason, visitor = await self._portal.check_access(key[0], key[1])
        except Exception as exc:  # PortalUnavailable, or anything a broken answer raises
            self._portal_down_until = self._clock() + BACKOFF_SECONDS
            logger.warning("access check failed, using what is remembered: %s", exc.__class__.__name__)
            return None
        return self.remember(key[0], key[1], allowed, reason, visitor)

    def _fallback(self, key: tuple[str, int]) -> AccessDecision:
        cached = self._entries.get(key)
        if cached is not None and self._clock() - cached.at < STALE_SECONDS:
            return AccessDecision(allowed=cached.allowed, reason=cached.reason, visitor=cached.visitor, at=cached.at, stale=True)
        raise AccessUnavailable(f"no access decision for app {key[0]}")
