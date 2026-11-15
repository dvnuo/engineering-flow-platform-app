"""The gateway half's shared objects, kept as ``HostState.gateway``."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..config import Settings
from ..interfaces import AppRuntime, PortalApi
from ..paths import HostPaths
from ..registry import Registry
from ..state import HostState
from .access import AccessCache
from .metrics import TrafficStore
from .pages import PageRenderer
from .ratelimit import RateLimiter
from .secret import access_log_key, load_or_create
from .sessions import SessionCodec


@dataclass
class GatewayServices:
    settings: Settings
    paths: HostPaths
    registry: Registry
    runtime: AppRuntime
    portal: PortalApi
    sessions: SessionCodec
    access: AccessCache
    limiter: RateLimiter
    pages: PageRenderer
    metrics: TrafficStore
    # Wall-clock time: sessions, visitor headers and revocations share it.
    clock: Callable[[], float] = time.time
    # What ``background.start`` started (the access-log listener, the loops).
    background: Any = None
    _secret: str | None = None

    @property
    def secret(self) -> str:
        """The secret nginx sends on the internal routes; made on first use."""

        if self._secret is None:
            self._secret = load_or_create(self.paths.gateway_secret_file)
        return self._secret

    @property
    def access_log_key(self) -> str:
        return access_log_key(self.secret)


def build(
    state: HostState,
    *,
    clock: Callable[[], float] = time.time,
    monotonic: Callable[[], float] = time.monotonic,
) -> GatewayServices:
    """Nothing here touches the disk except reading the pages: keys and the
    gateway secret are read (or made) when they are first needed.

    ``clock`` (wall-clock time) and ``monotonic`` (for the access cache and
    the rate limiter) are there for tests.
    """

    settings = state.settings
    paths = state.paths
    registry = state.registry
    services = GatewayServices(
        settings=settings,
        paths=paths,
        registry=registry,
        runtime=state.runtime,
        portal=state.portal,
        sessions=SessionCodec(
            lambda: load_or_create(paths.session_key_file).encode("ascii"),
            settings.session_ttl_hours * 3600,
            clock=clock,
            revoked_before=registry.revoked_before,
        ),
        access=AccessCache(state.portal, settings.access_cache_seconds, clock=monotonic),
        limiter=RateLimiter(settings.anon_rate_per_minute, clock=monotonic),
        pages=PageRenderer(),
        metrics=TrafficStore(
            paths.metrics_state_file,
            known_app=lambda app_id: registry.find(app_id) is not None,
            clock=clock,
        ),
        clock=clock,
    )
    services.metrics.log_key = lambda: services.access_log_key
    return services
