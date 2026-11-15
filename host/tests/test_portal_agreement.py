"""What running the host against the real Portal brought up.

Each test pins one place where the two had drifted apart.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import API_TOKEN, FakePortal
from efp_app_host.app import create_app
from efp_app_host.gateway.access import AccessCache
from efp_app_host.manifest import load_manifest
from efp_app_host.models import Visitor
from efp_app_host.portal_client import PortalClient, PortalUnavailable
from runtime_helpers import Harness


def test_a_manifest_with_a_byte_order_mark_is_read(tmp_path):
    code = tmp_path / "code"
    code.mkdir()
    (code / "index.html").write_text("<h1>hi</h1>", encoding="utf-8")
    manifest = {"name": "Site", "type": "static", "owners": ["alice"]}
    (code / "efp-app.json").write_bytes(b"\xef\xbb\xbf" + json.dumps(manifest).encode("utf-8"))

    loaded = load_manifest(code)

    assert loaded["name"] == "Site"
    assert loaded["type"] == "static"


def test_forgetting_an_app_makes_the_next_check_ask_the_portal_again():
    async def scenario() -> None:
        portal = FakePortal()
        portal.users[7] = Visitor(id=7, username="bob")
        portal.access[("app-1", 7)] = (True, None)
        portal.access[("app-2", 7)] = (True, None)
        cache = AccessCache(portal, ttl_seconds=30)

        assert (await cache.check("app-1", 7)).allowed is True
        assert (await cache.check("app-2", 7)).allowed is True
        assert portal.access_calls == 2

        # The owner narrows the audience of app-1.
        portal.access[("app-1", 7)] = (False, "no_access")
        assert (await cache.check("app-1", 7)).allowed is True, "still remembered"
        assert cache.forget_app("app-1") == 1

        refused = await cache.check("app-1", 7)
        assert (refused.allowed, refused.reason) == (False, "no_access")
        assert (await cache.check("app-2", 7)).allowed is True
        assert portal.access_calls == 3, "app-2 was not asked again"

    asyncio.run(scenario())


def test_updating_an_app_record_drops_the_remembered_access_decisions(tmp_path):
    harness = Harness(tmp_path)
    app = create_app(harness.settings, registry=harness.registry, runtime=harness.runtime, portal=harness.portal, run_background=False)
    access = app.state.host.gateway.access
    access.remember("app-1", 7, True, None, Visitor(id=7, username="bob"))
    access.remember("app-2", 7, True, None, Visitor(id=7, username="bob"))

    with TestClient(app) as client:
        response = client.put(
            "/v1/apps/app-1",
            headers={"Authorization": f"Bearer {API_TOKEN}"},
            json={"slug": "mindmap", "visibility": "private", "visitor_key": "k", "env": {}},
        )

    assert response.status_code == 200, response.text
    assert ("app-1", 7) not in access._entries
    assert ("app-2", 7) in access._entries


def _client(settings, handler) -> PortalClient:
    return PortalClient(settings, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_portal_that_refuses_the_host_is_unavailable_not_a_bad_code(settings, status, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {API_TOKEN}"
        return httpx.Response(status, json={"error": {"code": "unauthorized", "message": "no"}})

    async def scenario() -> None:
        client = _client(settings, handler)
        try:
            with pytest.raises(PortalUnavailable):
                await client.redeem_handoff("some-code")
        finally:
            await client.aclose()

    with caplog.at_level(logging.ERROR, logger="efp_app_host.portal_client"):
        asyncio.run(scenario())
    assert "EFP_HOST_API_TOKEN" in caplog.text
    assert "some-code" not in caplog.text


def test_a_code_the_portal_does_not_know_is_a_bad_code(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": "invalid_code", "message": "no"}})

    async def scenario() -> None:
        client = _client(settings, handler)
        try:
            assert await client.redeem_handoff("stale") is None
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_a_refused_metrics_push_or_event_is_logged(settings, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "unauthorized", "message": "no"}})

    async def scenario() -> None:
        client = _client(settings, handler)
        try:
            assert await client.push_metrics({"traffic": []}) is False
            assert await client.post_event("app-1", "crashed", {"exit_code": 1}) is False
        finally:
            await client.aclose()

    with caplog.at_level(logging.WARNING, logger="efp_app_host.portal_client"):
        asyncio.run(scenario())
    assert "refused the metrics push with 401" in caplog.text
    assert "refused event crashed for app-1 with 401" in caplog.text
