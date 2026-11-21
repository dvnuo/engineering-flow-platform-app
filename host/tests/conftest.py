"""Shared fixtures: settings on a temporary directory, a registry, and fakes
for the two seams of ``efp_app_host.interfaces``."""

from __future__ import annotations

import os
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


class Links:
    """Symbolic links for a test: real ones on Linux, a model of them elsewhere.

    Where links cannot be made (Windows without the privilege) ``os.symlink``
    and the calls ``unpack_bundle`` uses to look at links are replaced by a
    table with POSIX semantics; the model's ``realpath`` is the standard
    library's own ``posixpath.realpath``, fed from the table. Everything else
    stays the real code on the real disk.
    """

    def __init__(self, monkeypatch):
        self.table: dict[str, str] = {}
        self.real = sys.platform == "linux"
        if self.real:
            return
        import ntpath
        import posixpath
        import stat
        import types

        lstat, readlink, unlink = os.lstat, getattr(os, "readlink", None), os.unlink
        lexists, islink = os.path.lexists, os.path.islink

        def key(path) -> str:
            return "/" + ntpath.abspath(str(path)).replace("\\", "/")

        def back(path: str) -> str:
            return path[1:].replace("/", "\\")

        def model_lstat(path):
            if path in self.table:
                return types.SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
            return lstat(back(path))

        def realpath(path, **_kwargs):
            os.lstat, os.readlink = model_lstat, (lambda item: self.table[item])
            try:
                return back(posixpath.realpath(key(path)))
            finally:
                os.lstat = lstat
                if readlink is not None:
                    os.readlink = readlink

        def model_unlink(path, *args, **kwargs):
            if self.table.pop(key(path), None) is None:
                unlink(path, *args, **kwargs)

        self._key = key
        monkeypatch.setattr(os, "symlink", lambda source, target, *a, **k: self.table.__setitem__(key(target), source))
        monkeypatch.setattr(os, "unlink", model_unlink)
        monkeypatch.setattr(os.path, "realpath", realpath)
        monkeypatch.setattr(os.path, "lexists", lambda path: key(path) in self.table or lexists(path))
        monkeypatch.setattr(os.path, "islink", lambda path: key(path) in self.table or islink(path))

    def is_link(self, path) -> bool:
        return os.path.islink(path)

    def make(self, target, source: str) -> None:
        os.symlink(source, target)


@pytest.fixture
def links(monkeypatch) -> Links:
    return Links(monkeypatch)


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
        self.catalogs: dict[int, dict[str, Any]] = {}
        self.icons: dict[str, tuple[bytes, str]] = {}
        self.access_calls = 0
        self.catalog_calls = 0
        self.icon_calls = 0
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

    async def catalog(self, user_id: int) -> dict[str, Any]:
        from efp_app_host.portal_client import PortalUnavailable

        self.catalog_calls += 1
        if self.fail:
            raise PortalUnavailable("portal down")
        return self.catalogs.get(user_id, {"allowed": False, "reason": "user_inactive", "apps": []})

    async def app_icon(self, app_id: str) -> tuple[bytes, str] | None:
        from efp_app_host.portal_client import PortalUnavailable

        self.icon_calls += 1
        if self.fail:
            raise PortalUnavailable("portal down")
        return self.icons.get(app_id)

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
