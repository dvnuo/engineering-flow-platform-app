"""The host's own record of its apps: one JSON file on the volume.

The Portal's database is the source of truth for what an app is and who may
open it. This file is what lets the host bring its apps back after a restart
without the Portal: slug, OS uid, port, releases, and which sessions were
revoked. Environment variables (they hold the app token) live next to each
app in ``env.json`` rather than here.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .config import Settings
from .models import AppRecord, Release, iso, utcnow
from .paths import HostPaths

REGISTRY_VERSION = 1


class SlugTaken(Exception):
    """Another app already owns the slug."""


class AppNotFound(KeyError):
    """No app with that id (or slug) is registered."""


def write_json_atomic(path: Path, payload: Any, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    try:
        os.chmod(tmp, mode)
    except OSError:
        pass
    os.replace(tmp, path)


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


class Registry:
    def __init__(self, settings: Settings, paths: HostPaths | None = None):
        self.settings = settings
        self.paths = paths or HostPaths.of(settings)
        self._lock = threading.RLock()
        self._apps: dict[str, AppRecord] = {}
        self._revoked_before: dict[str, float] = {}
        self.load()

    # ---- persistence ------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            data = read_json(self.paths.registry_file, {})
            self._apps = {app_id: AppRecord.from_json(value) for app_id, value in (data.get("apps") or {}).items()}
            self._revoked_before = {str(key): float(value) for key, value in (data.get("revoked_before") or {}).items()}

    def _save(self) -> None:
        write_json_atomic(
            self.paths.registry_file,
            {
                "version": REGISTRY_VERSION,
                "apps": {app_id: record.to_json() for app_id, record in self._apps.items()},
                "revoked_before": self._revoked_before,
            },
        )

    # ---- lookups ----------------------------------------------------------
    def get(self, app_id: str) -> AppRecord:
        with self._lock:
            try:
                return self._apps[app_id]
            except KeyError:
                raise AppNotFound(app_id) from None

    def find(self, app_id: str) -> AppRecord | None:
        with self._lock:
            return self._apps.get(app_id)

    def find_by_slug(self, slug: str) -> AppRecord | None:
        with self._lock:
            for record in self._apps.values():
                if record.slug == slug:
                    return record
            return None

    def find_by_uid(self, uid: int) -> AppRecord | None:
        with self._lock:
            for record in self._apps.values():
                if record.uid == uid:
                    return record
            return None

    def all(self) -> list[AppRecord]:
        with self._lock:
            return sorted(self._apps.values(), key=lambda record: record.slug)

    # ---- changes ----------------------------------------------------------
    def upsert(self, app_id: str, *, slug: str, **fields: Any) -> tuple[AppRecord, bool]:
        """Create or update a record. Returns it and whether it is new.

        The slug of an existing app never changes: a different one is ignored.
        """

        with self._lock:
            owner = self.find_by_slug(slug)
            if owner is not None and owner.id != app_id:
                raise SlugTaken(slug)
            record = self._apps.get(app_id)
            created = record is None
            now = iso(utcnow()) or ""
            if record is None:
                record = AppRecord(id=app_id, slug=slug, created_at=now)
                record.uid = self._next_free(self.settings.uid_range_start, (item.uid for item in self._apps.values()))
                record.port = self._next_free(self.settings.port_range_start, (item.port for item in self._apps.values()))
                self._apps[app_id] = record
            for key, value in fields.items():
                if key in AppRecord.__dataclass_fields__ and key not in {"id", "slug", "uid", "port", "releases", "created_at"}:
                    setattr(record, key, value)
            record.updated_at = now
            self._save()
            return record, created

    def add_release(self, app_id: str, release: Release, *, keep: int = 2) -> list[str]:
        """Make ``release`` current. Returns the shas that fell out of retention."""

        with self._lock:
            record = self.get(app_id)
            if record.current_sha and record.current_sha != release.sha:
                record.previous_sha = record.current_sha
            record.releases[release.sha] = release
            record.current_sha = release.sha
            retained = [sha for sha in (record.current_sha, record.previous_sha) if sha][:keep]
            dropped = [sha for sha in record.releases if sha not in retained]
            for sha in dropped:
                del record.releases[sha]
            if record.previous_sha not in record.releases:
                record.previous_sha = None
            record.updated_at = iso(utcnow()) or ""
            self._save()
            return dropped

    def activate(self, app_id: str, sha: str) -> AppRecord:
        """Switch to a retained release; the one that was current becomes the previous."""

        with self._lock:
            record = self.get(app_id)
            if sha not in record.releases:
                raise KeyError(sha)
            if record.current_sha != sha:
                record.previous_sha, record.current_sha = record.current_sha, sha
                record.updated_at = iso(utcnow()) or ""
                self._save()
            return record

    def remove(self, app_id: str) -> AppRecord | None:
        with self._lock:
            record = self._apps.pop(app_id, None)
            if record is not None:
                self._save()
            return record

    # ---- environment ------------------------------------------------------
    def read_env(self, app_id: str) -> dict[str, str]:
        data = read_json(self.paths.env_file(app_id), {})
        return {str(key): str(value) for key, value in data.items()}

    def write_env(self, app_id: str, env: dict[str, str]) -> bool:
        """Store the environment. Returns whether it differs from what was stored."""

        with self._lock:
            cleaned = {str(key): str(value) for key, value in (env or {}).items()}
            changed = cleaned != self.read_env(app_id)
            if changed or not self.paths.env_file(app_id).exists():
                write_json_atomic(self.paths.env_file(app_id), cleaned, mode=0o600)
            return changed

    # ---- session revocation ----------------------------------------------
    def revoke_user(self, user_id: int, *, at: float | None = None) -> None:
        with self._lock:
            self._revoked_before[str(int(user_id))] = float(time.time() if at is None else at)
            self._save()

    def revoked_before(self, user_id: int) -> float:
        with self._lock:
            return self._revoked_before.get(str(int(user_id)), 0.0)

    # ---- helpers ----------------------------------------------------------
    @staticmethod
    def _next_free(start: int, used: Iterable[int]) -> int:
        taken = set(used)
        candidate = start
        while candidate in taken:
            candidate += 1
        return candidate
