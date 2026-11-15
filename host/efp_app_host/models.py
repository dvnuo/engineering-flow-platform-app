"""Shared data shapes of the host (docs/CONTRACTS.md)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")
RESERVED_SLUGS = frozenset(
    {"_efp", "api", "app", "apps", "admin", "assets", "auth", "efp", "health", "login", "logout", "portal", "static", "www"}
)

VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"
VISIBILITY_PLATFORM = "platform"
VISIBILITY_ANYONE = "anyone"
VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, VISIBILITY_PLATFORM, VISIBILITY_ANYONE)

APP_TYPES = ("fastapi", "flask", "streamlit", "gradio", "command", "static")
# Whether the gateway removes "/{slug}" before the request reaches the app.
STRIP_PREFIX_BY_TYPE = {
    "fastapi": True,
    "gradio": True,
    "static": True,
    "flask": False,
    "streamlit": False,
    "command": False,
}

STATE_RUNNING = "running"
STATE_STARTING = "starting"
STATE_STOPPED = "stopped"
STATE_CRASHED = "crashed"
STATE_KILLED = "killed"
STATE_FAILED = "failed"
STATE_UNDEPLOYED = "undeployed"
STATE_DISABLED = "disabled"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def is_valid_slug(slug: str) -> bool:
    return bool(SLUG_RE.match(slug or "")) and slug not in RESERVED_SLUGS


@dataclass
class Release:
    sha: str
    deployed_at: str
    type: str
    entry_dir: str = ""
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        return {"sha": self.sha, "deployed_at": self.deployed_at}


@dataclass
class AppRecord:
    """What the host remembers about one app. Lives in registry.json."""

    id: str
    slug: str
    name: str = ""
    owner_username: str = ""
    visibility: str = VISIBILITY_PRIVATE
    enabled: bool = True
    disabled_reason: str | None = None
    visitor_key: str = ""
    memory_mb: int | None = None
    data_mb: int | None = None
    uid: int = 0
    port: int = 0
    current_sha: str | None = None
    previous_sha: str | None = None
    releases: dict[str, Release] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    @property
    def current(self) -> Release | None:
        return self.releases.get(self.current_sha) if self.current_sha else None

    @property
    def previous(self) -> Release | None:
        return self.releases.get(self.previous_sha) if self.previous_sha else None

    @property
    def type(self) -> str | None:
        release = self.current
        return release.type if release else None

    @property
    def base_path(self) -> str:
        return f"/{self.slug}"

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "AppRecord":
        payload = dict(data)
        releases = {sha: Release(**value) for sha, value in (payload.pop("releases", None) or {}).items()}
        known = {name for name in cls.__dataclass_fields__ if name != "releases"}
        return cls(releases=releases, **{key: value for key, value in payload.items() if key in known})


@dataclass
class Upstream:
    """Where the gateway sends a request for an app."""

    kind: str  # "proxy" or "static"
    strip_prefix: bool
    host: str = "127.0.0.1"
    port: int = 0
    static_root: str | None = None


@dataclass
class AppStatus:
    id: str
    slug: str
    type: str | None
    state: str
    reason: str | None = None
    release: dict[str, Any] | None = None
    previous_release: dict[str, Any] | None = None
    uptime_seconds: int = 0
    restarts: int = 0
    rss_bytes: int = 0
    cpu_percent: float = 0.0
    data_bytes: int = 0
    last_activity_at: str | None = None
    port: int = 0
    last_log_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Visitor:
    """A signed-in visitor, as redeemed from the Portal."""

    id: int
    username: str
    display_name: str = ""
    group_id: int | None = None
    group_name: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Visitor":
        return cls(
            id=int(data["id"]),
            username=str(data.get("username") or ""),
            display_name=str(data.get("display_name") or ""),
            group_id=data.get("group_id"),
            group_name=str(data.get("group_name") or ""),
        )

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
