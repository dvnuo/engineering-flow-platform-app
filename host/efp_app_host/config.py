"""Host settings, read once from the environment (docs/CONTRACTS.md, section 3)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

ISOLATION_SANDBOX = "sandbox"
ISOLATION_HIDDEN = "hidden"
ISOLATION_MODES = (ISOLATION_SANDBOX, ISOLATION_HIDDEN)
SANDBOX_BACKENDS = ("auto", "bwrap", "bwrap-userns", "landlock")


class SettingsError(ValueError):
    """A required setting is missing or malformed."""


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int = 0) -> int:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise SettingsError(f"{name} must be at least {minimum}, got {value}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (env.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _url(env: Mapping[str, str], name: str, *, required: bool) -> str:
    value = (env.get(name) or "").strip().rstrip("/")
    if not value and required:
        raise SettingsError(f"{name} is required")
    if value and not value.startswith(("http://", "https://")):
        raise SettingsError(f"{name} must start with http:// or https://")
    return value


@dataclass(frozen=True)
class Settings:
    api_token: str
    portal_internal_url: str
    portal_public_url: str
    apps_public_url: str
    isolation_mode: str = ISOLATION_SANDBOX
    sandbox_backend: str = "auto"
    store_dir: Path = Path("/var/lib/efp-host/store")
    runtime_dir: Path = Path("/srv/efp-apps")
    run_dir: Path = Path("/run/efp-host")
    admin_port: int = 9000
    gateway_port: int = 8080
    port_range_start: int = 20000
    uid_range_start: int = 20000
    idle_stop_minutes: int = 30
    app_memory_mb: int = 1024
    app_data_mb: int = 1024
    app_nproc: int = 256
    session_ttl_hours: int = 168
    access_cache_seconds: int = 30
    anon_rate_per_minute: int = 120
    metrics_push_seconds: int = 300
    install_concurrency: int = 1
    deploy_timeout_seconds: int = 900
    pip_allow_sdist: bool = False
    # The environment pip and the launcher inherit (PIP_INDEX_URL and friends).
    passthrough_env: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, *, require_urls: bool = True) -> "Settings":
        env = os.environ if env is None else env
        token = (env.get("EFP_HOST_API_TOKEN") or "").strip()
        if not token:
            raise SettingsError("EFP_HOST_API_TOKEN is required")
        mode = (env.get("EFP_HOST_ISOLATION_MODE") or ISOLATION_SANDBOX).strip().lower()
        if mode not in ISOLATION_MODES:
            raise SettingsError(f"EFP_HOST_ISOLATION_MODE must be one of {ISOLATION_MODES}, got {mode!r}")
        backend = (env.get("EFP_HOST_SANDBOX_BACKEND") or "auto").strip().lower()
        if backend not in SANDBOX_BACKENDS:
            raise SettingsError(f"EFP_HOST_SANDBOX_BACKEND must be one of {SANDBOX_BACKENDS}, got {backend!r}")
        passthrough = {key: value for key, value in env.items() if key.startswith("PIP_") or key in _PROXY_KEYS}
        return cls(
            api_token=token,
            portal_internal_url=_url(env, "EFP_PORTAL_INTERNAL_URL", required=require_urls),
            portal_public_url=_url(env, "EFP_PORTAL_PUBLIC_URL", required=require_urls),
            apps_public_url=_url(env, "EFP_APPS_PUBLIC_URL", required=require_urls),
            isolation_mode=mode,
            sandbox_backend=backend,
            store_dir=Path(env.get("EFP_HOST_STORE_DIR") or "/var/lib/efp-host/store"),
            runtime_dir=Path(env.get("EFP_HOST_RUNTIME_DIR") or "/srv/efp-apps"),
            run_dir=Path(env.get("EFP_HOST_RUN_DIR") or "/run/efp-host"),
            admin_port=_int(env, "EFP_HOST_ADMIN_PORT", 9000, minimum=1),
            gateway_port=_int(env, "EFP_HOST_GATEWAY_PORT", 8080, minimum=1),
            port_range_start=_int(env, "EFP_HOST_PORT_RANGE_START", 20000, minimum=1024),
            uid_range_start=_int(env, "EFP_HOST_UID_RANGE_START", 20000, minimum=1000),
            idle_stop_minutes=_int(env, "EFP_HOST_IDLE_STOP_MINUTES", 30),
            app_memory_mb=_int(env, "EFP_HOST_APP_MEMORY_MB", 1024, minimum=64),
            app_data_mb=_int(env, "EFP_HOST_APP_DATA_MB", 1024, minimum=1),
            app_nproc=_int(env, "EFP_HOST_APP_NPROC", 256, minimum=16),
            session_ttl_hours=_int(env, "EFP_HOST_SESSION_TTL_HOURS", 168, minimum=1),
            access_cache_seconds=_int(env, "EFP_HOST_ACCESS_CACHE_SECONDS", 30),
            anon_rate_per_minute=_int(env, "EFP_HOST_ANON_RATE_PER_MINUTE", 120, minimum=1),
            metrics_push_seconds=_int(env, "EFP_HOST_METRICS_PUSH_SECONDS", 300, minimum=5),
            install_concurrency=_int(env, "EFP_HOST_INSTALL_CONCURRENCY", 1, minimum=1),
            deploy_timeout_seconds=_int(env, "EFP_HOST_DEPLOY_TIMEOUT_SECONDS", 900, minimum=30),
            pip_allow_sdist=_bool(env, "EFP_HOST_PIP_ALLOW_SDIST", False),
            passthrough_env=passthrough,
        )

    @property
    def is_sandbox(self) -> bool:
        return self.isolation_mode == ISOLATION_SANDBOX

    @property
    def is_hidden(self) -> bool:
        return self.isolation_mode == ISOLATION_HIDDEN


_PROXY_KEYS = frozenset(
    {"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
)
