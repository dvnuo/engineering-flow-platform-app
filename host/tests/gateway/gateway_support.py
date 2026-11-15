"""Helpers shared by the gateway tests (not a test module).

The fixtures ``settings``, ``registry``, ``fake_runtime`` and ``fake_portal``
come from ``tests/conftest.py``.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from efp_app_host.gateway import services as gateway_services
from efp_app_host.gateway.decision import GatewayRequest
from efp_app_host.gateway.services import GatewayServices
from efp_app_host.gateway.sessions import COOKIE_NAME
from efp_app_host.models import Release, Visitor
from efp_app_host.paths import HostPaths
from efp_app_host.state import HostState

NOW = 1_763_195_400.0  # 2025-11-15T08:30:00Z
VISITOR_KEY = base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode("ascii")
OTHER_VISITOR_KEY = base64.urlsafe_b64encode(bytes(range(1, 33))).rstrip(b"=").decode("ascii")
BOB = Visitor(id=12, username="bob", display_name="Bob", group_id=3, group_name="QA")
NAVIGATION = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8", "sec-fetch-mode": "navigate"}
FETCH = {"accept": "application/json", "sec-fetch-mode": "cors"}


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, now: float = NOW):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def make_gateway(settings: Any, registry: Any, runtime: Any, portal: Any, *, clock: Clock | None = None) -> GatewayServices:
    state = HostState(settings=settings, paths=HostPaths.of(settings), registry=registry, runtime=runtime, portal=portal)
    if clock is None:
        state.gateway = gateway_services.build(state)
    else:
        state.gateway = gateway_services.build(state, clock=clock, monotonic=clock)
    return state.gateway


def add_app(
    registry: Any,
    runtime: Any,
    app_id: str = "app-1",
    slug: str = "mindmap",
    *,
    type: str = "fastapi",
    visibility: str = "private",
    deployed: bool = True,
    running: bool = True,
    manifest: dict[str, Any] | None = None,
    **fields: Any,
) -> Any:
    values: dict[str, Any] = dict(name="Mind map", owner_username="alice", visibility=visibility, visitor_key=VISITOR_KEY)
    values.update(fields)
    registry.upsert(app_id, slug=slug, **values)
    if deployed:
        registry.add_release(app_id, Release(sha="s1", deployed_at="2026-11-15T00:00:00Z", type=type, manifest=manifest or {}))
        if running:
            runtime.mark_running(app_id)
    return registry.get(app_id)


def allow(portal: Any, app_id: str = "app-1", visitor: Visitor = BOB) -> None:
    portal.access[(app_id, visitor.id)] = (True, None)
    portal.users[visitor.id] = visitor


def session_cookie(gateway: GatewayServices, visitor: Visitor = BOB, *, now: float | None = None) -> str:
    """A ``Cookie`` header value that carries a session of ``visitor``."""

    return f"{COOKIE_NAME}={gateway.sessions.issue(visitor, now=now)}"


def request(
    path: str = "/mindmap/",
    *,
    slug: str = "mindmap",
    query: str | None = None,
    method: str = "GET",
    cookies: str = "",
    headers: dict[str, str] | None = None,
    source: str = "203.0.113.7",
) -> GatewayRequest:
    return GatewayRequest(
        slug=slug,
        path=path,
        query=query,
        method=method,
        cookies=cookies,
        headers=NAVIGATION if headers is None else headers,
        source=source,
    )
