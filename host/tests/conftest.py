"""Shared fixtures: settings on a temporary directory, a registry, and fakes
for the two seams of ``efp_app_host.interfaces``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from efp_app_host.config import Settings  # noqa: E402
from efp_app_host.models import (  # noqa: E402
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_UNDEPLOYED,
    STRIP_PREFIX_BY_TYPE,
    AppStatus,
    Upstream,
    Visitor,
)
from efp_app_host.registry import Registry  # noqa: E402

API_TOKEN = "test-host-token"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = dict(
        api_token=API_TOKEN,
        portal_internal_url="http://portal.internal",
        portal_public_url="https://efp.example.com",
        apps_public_url="https://app.example.com",
        store_dir=tmp_path / "store",
        runtime_dir=tmp_path / "runtime",
        run_dir=tmp_path / "run",
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def registry(settings: Settings) -> Registry:
    return Registry(settings)


class FakeRuntime:
    """An ``AppRuntime`` whose state the test sets by hand."""

    def __init__(self, registry: Registry):
        self.registry = registry
        self.states: dict[str, str] = {}
        self.reasons: dict[str, str | None] = {}
        self.touched: list[str] = []
        self.started: list[str] = []
        self.stopped: list[tuple[str, str]] = []
        self.static_roots: dict[str, str] = {}

    def set_state(self, app_id: str, state: str, reason: str | None = None) -> None:
        self.states[app_id] = state
        self.reasons[app_id] = reason

    def status(self, app_id: str) -> AppStatus:
        record = self.registry.get(app_id)
        default = STATE_STOPPED if record.current_sha else STATE_UNDEPLOYED
        return AppStatus(
            id=record.id,
            slug=record.slug,
            type=record.type,
            state=self.states.get(app_id, default),
            reason=self.reasons.get(app_id),
            release=record.current.to_public() if record.current else None,
            port=record.port,
        )

    def statuses(self) -> list[AppStatus]:
        return [self.status(record.id) for record in self.registry.all()]

    def upstream(self, app_id: str) -> Upstream | None:
        record = self.registry.get(app_id)
        release = record.current
        if release is None:
            return None
        strip = release.manifest.get("strip_prefix")
        if strip is None:
            strip = STRIP_PREFIX_BY_TYPE[release.type]
        if release.type == "static":
            return Upstream(kind="static", strip_prefix=True, static_root=self.static_roots.get(app_id, "/static-root"))
        return Upstream(kind="proxy", strip_prefix=bool(strip), port=record.port)

    async def ensure_running(self, app_id: str) -> AppStatus:
        status = self.status(app_id)
        if status.state == STATE_STOPPED:
            self.started.append(app_id)
            self.states[app_id] = STATE_STARTING
            return self.status(app_id)
        return status

    async def stop(self, app_id: str, reason: str) -> AppStatus:
        self.stopped.append((app_id, reason))
        self.set_state(app_id, STATE_STOPPED, reason)
        return self.status(app_id)

    def touch(self, app_id: str) -> None:
        self.touched.append(app_id)

    def host_info(self) -> dict[str, Any]:
        return {"version": "test", "isolation_mode": "sandbox", "apps_total": len(self.registry.all()), "apps_running": 0}

    def mark_running(self, app_id: str) -> None:
        self.set_state(app_id, STATE_RUNNING)


class FakePortal:
    """A ``PortalApi`` with canned answers and a record of what was sent."""

    def __init__(self) -> None:
        self.codes: dict[str, dict[str, Any]] = {}
        self.access: dict[tuple[str, int], tuple[bool, str | None]] = {}
        self.users: dict[int, Visitor] = {}
        self.desired: list[dict[str, Any]] = []
        self.metrics: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.access_calls = 0
        self.fail = False

    async def redeem_handoff(self, code: str) -> dict[str, Any] | None:
        return self.codes.pop(code, None)

    async def check_access(self, app_id: str, user_id: int) -> tuple[bool, str | None, Visitor | None]:
        from efp_app_host.portal_client import PortalUnavailable

        self.access_calls += 1
        if self.fail:
            raise PortalUnavailable("portal down")
        allowed, reason = self.access.get((app_id, user_id), (False, "no_access"))
        return allowed, reason, self.users.get(user_id)

    async def desired_apps(self) -> list[dict[str, Any]]:
        return list(self.desired)

    async def push_metrics(self, payload: dict[str, Any]) -> bool:
        self.metrics.append(payload)
        return True

    async def post_event(self, app_id: str, kind: str, detail: dict[str, Any] | None = None) -> bool:
        self.events.append({"app_id": app_id, "kind": kind, "detail": detail or {}})
        return True


@pytest.fixture
def fake_runtime(registry: Registry) -> FakeRuntime:
    return FakeRuntime(registry)


@pytest.fixture
def fake_portal() -> FakePortal:
    return FakePortal()
