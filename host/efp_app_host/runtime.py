"""PLACEHOLDER - replaced by the runtime half.

``HostRuntime`` implements ``interfaces.AppRuntime`` on top of the registry,
the process supervisor and the isolation mode.
"""

from __future__ import annotations

from typing import Any

from .config import Settings
from .interfaces import PortalApi
from .models import STATE_UNDEPLOYED, AppStatus, Upstream
from .registry import Registry


class HostRuntime:
    def __init__(self, settings: Settings, registry: Registry, *, portal: PortalApi | None = None, controller: Any = None):
        self.settings = settings
        self.registry = registry
        self.portal = portal
        self.controller = controller

    def status(self, app_id: str) -> AppStatus:
        record = self.registry.get(app_id)
        return AppStatus(id=record.id, slug=record.slug, type=record.type, state=STATE_UNDEPLOYED, reason="not_deployed")

    def statuses(self) -> list[AppStatus]:
        return [self.status(record.id) for record in self.registry.all()]

    def upstream(self, app_id: str) -> Upstream | None:
        return None

    async def ensure_running(self, app_id: str) -> AppStatus:
        return self.status(app_id)

    async def stop(self, app_id: str, reason: str) -> AppStatus:
        return self.status(app_id)

    def touch(self, app_id: str) -> None:
        return None

    def host_info(self) -> dict[str, Any]:
        return {}
