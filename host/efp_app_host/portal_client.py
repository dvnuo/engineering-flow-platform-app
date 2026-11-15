"""The host's calls to the Portal (docs/CONTRACTS.md, section 5)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings
from .models import Visitor, iso, utcnow

logger = logging.getLogger(__name__)

PREFIX = "/api/app-host/v1"


class PortalUnavailable(Exception):
    """The Portal could not be reached or answered with a server error."""


class PortalClient:
    def __init__(self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 10.0):
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.portal_internal_url + PREFIX,
            headers={"Authorization": f"Bearer {settings.api_token}"},
            timeout=timeout,
            transport=transport,
            # The Portal is a cluster service: never send it through a proxy.
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, *, json: Any = None) -> httpx.Response:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise PortalUnavailable(str(exc) or exc.__class__.__name__) from exc
        if response.status_code >= 500:
            raise PortalUnavailable(f"Portal answered {response.status_code}")
        return response

    async def redeem_handoff(self, code: str) -> dict[str, Any] | None:
        response = await self._request("POST", "/handoff/redeem", json={"code": code})
        if response.status_code != 200:
            return None
        data = response.json()
        if not isinstance(data, dict) or "user" not in data or "app_id" not in data:
            return None
        return data

    async def check_access(self, app_id: str, user_id: int) -> tuple[bool, str | None, Visitor | None]:
        response = await self._request("POST", "/access/check", json={"app_id": app_id, "user_id": int(user_id)})
        if response.status_code != 200:
            raise PortalUnavailable(f"access check answered {response.status_code}")
        data = response.json()
        user = data.get("user")
        visitor = Visitor.from_json(user) if isinstance(user, dict) and "id" in user else None
        return bool(data.get("allowed")), data.get("reason"), visitor

    async def desired_apps(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/apps")
        if response.status_code != 200:
            raise PortalUnavailable(f"desired state answered {response.status_code}")
        apps = response.json().get("apps")
        return apps if isinstance(apps, list) else []

    async def push_metrics(self, payload: dict[str, Any]) -> bool:
        try:
            response = await self._request("POST", "/metrics/ingest", json=payload)
        except PortalUnavailable as exc:
            logger.warning("metrics push failed: %s", exc)
            return False
        return response.status_code == 200

    async def post_event(self, app_id: str, kind: str, detail: dict[str, Any] | None = None) -> bool:
        body = {"app_id": app_id, "kind": kind, "detail": detail or {}, "at": iso(utcnow())}
        try:
            response = await self._request("POST", "/events", json=body)
        except PortalUnavailable as exc:
            logger.warning("event %s for %s not delivered: %s", kind, app_id, exc)
            return False
        return response.status_code == 200
