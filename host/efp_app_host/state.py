"""Everything one manager process shares, reachable as ``request.app.state.host``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .interfaces import AppRuntime, PortalApi
from .paths import HostPaths
from .registry import Registry


@dataclass
class HostState:
    settings: Settings
    paths: HostPaths
    registry: Registry
    runtime: AppRuntime
    portal: PortalApi
    # Owned by the gateway half (sessions, access cache, metrics ...).
    gateway: Any = None
    # Owned by the runtime half (job queue, watchdog tasks ...).
    extras: dict[str, Any] = field(default_factory=dict)
