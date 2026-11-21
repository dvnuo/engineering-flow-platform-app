"""The seams between the two halves of the manager.

``runtime`` (deployments, processes, isolation, the admin API) implements
``AppRuntime``. ``gateway`` (access checks, sessions, pages, metrics) only
talks to the runtime through it, and to the Portal through ``PortalApi``.
Tests replace either side with a fake.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .models import AppStatus, Upstream, Visitor


@runtime_checkable
class AppRuntime(Protocol):
    def status(self, app_id: str) -> AppStatus:
        """The app's state right now. Never raises for a registered app."""

    def statuses(self) -> list[AppStatus]:
        """Every registered app."""

    def upstream(self, app_id: str) -> Upstream | None:
        """Where requests go, or ``None`` when the app has no release."""

    async def ensure_running(self, app_id: str) -> AppStatus:
        """Start the app when it is stopped and return at once.

        The returned state is ``running`` when the app can take the request
        now, ``starting`` while it comes up, and anything else when it cannot
        be started (``undeployed``, ``disabled``, ``crashed`` ...). Static
        apps are always ``running``.
        """

    async def stop(self, app_id: str, reason: str) -> AppStatus:
        """Stop the app's process; ``reason`` is what its status reports."""

    def touch(self, app_id: str) -> None:
        """Note that the app just served a request (drives idle stop)."""

    def host_info(self) -> dict[str, Any]:
        """The host object of docs/CONTRACTS.md, section 4.6."""


class PortalApi(Protocol):
    """docs/CONTRACTS.md, section 5."""

    async def redeem_handoff(self, code: str) -> dict[str, Any] | None:
        """``{"app_id", "slug", "user"}``, or ``None`` for a bad code.

        ``app_id`` and ``slug`` are ``None`` for a code of the app center.
        """

    async def check_access(self, app_id: str, user_id: int) -> tuple[bool, str | None, Visitor | None]:
        """Whether the user may open the app, why not, and who they are."""

    async def desired_apps(self) -> list[dict[str, Any]]:
        ...

    async def catalog(self, user_id: int) -> dict[str, Any]:
        """What the app center shows this member (section 5.6), as the Portal sent it."""

    async def app_icon(self, app_id: str) -> tuple[bytes, str] | None:
        """``(bytes, content type)`` of an app's icon (section 5.7), or ``None`` when it has none."""

    async def push_metrics(self, payload: dict[str, Any]) -> bool:
        ...

    async def post_event(self, app_id: str, kind: str, detail: dict[str, Any] | None = None) -> bool:
        ...
