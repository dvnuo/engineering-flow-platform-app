"""How an app process is started: command line, environment, directories.

docs/CONTRACTS.md, sections 8 and 8.1. Everything here is pure: the same
functions serve the deployment's health check and the supervised process.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import Settings
from .manifest import strips_prefix
from .models import AppRecord, Release
from .paths import HostPaths

SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
RESERVED_ENV_NAMES = frozenset({"PORT", "HOME", "TMPDIR", "PATH"})
APP_TOKEN_ENV = "EFP_APP_TOKEN"
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# The directory inside the runtime directory that holds static sites in hidden
# mode. The leading dot keeps it apart from app ids.
STATIC_AREA = ".static"


class LaunchError(ValueError):
    """The release cannot be turned into a command line."""


@dataclass(frozen=True)
class Command:
    argv: list[str]
    env: dict[str, str]


def build_command(manifest: Mapping[str, Any], *, python: str, port: int, base_path: str) -> Command:
    """The command line of docs/CONTRACTS.md, section 8.1.

    ``base_path`` is empty on a developer's machine; the options that carry it
    are then left out.
    """

    app_type = manifest["type"]
    entry = manifest.get("entry") or ""
    port_text = str(port)
    env: dict[str, str] = {}
    if app_type == "fastapi":
        argv = [python, "-m", "uvicorn", entry, "--host", "127.0.0.1", "--port", port_text]
        if base_path:
            argv += ["--root-path", base_path]
        argv += ["--no-server-header"]
    elif app_type == "flask":
        argv = [python, "-m", "gunicorn", entry, "--bind", f"127.0.0.1:{port_text}", "--workers", "1", "--threads", "8", "--timeout", "120"]
        if base_path:
            env["SCRIPT_NAME"] = base_path
    elif app_type == "streamlit":
        argv = [python, "-m", "streamlit", "run", entry, "--server.port", port_text, "--server.address", "127.0.0.1"]
        if base_path:
            argv += ["--server.baseUrlPath", base_path]
        argv += [
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--server.enableCORS",
            "false",
            "--server.enableXsrfProtection",
            "false",
        ]
    elif app_type == "gradio":
        argv = [python, entry]
        env["GRADIO_SERVER_NAME"] = "127.0.0.1"
        env["GRADIO_SERVER_PORT"] = port_text
        if base_path:
            env["GRADIO_ROOT_PATH"] = base_path
    elif app_type == "command":
        command = manifest.get("command") or ""
        if not command.strip():
            raise LaunchError("a command app needs a command")
        argv = ["/bin/sh", "-c", command]
    elif app_type == "static":
        raise LaunchError("a static app has no process")
    else:
        raise LaunchError(f"unknown app type {app_type!r}")
    return Command(argv=argv, env=env)


def owner_environment(stored: Mapping[str, str]) -> dict[str, str]:
    """The owner-defined part of ``env.json``: no ``EFP_`` names, no reserved names."""

    result: dict[str, str] = {}
    for key, value in stored.items():
        if not _ENV_NAME_RE.match(key) or key.startswith("EFP_") or key in RESERVED_ENV_NAMES:
            continue
        if "\x00" in value:
            continue
        result[key] = value
    return result


# ---- directories ---------------------------------------------------------------


@dataclass(frozen=True)
class RunDirs:
    """Where a release runs from. Sandbox: the volume. Hidden: the container's own disk."""

    release: Path
    code: Path
    venv: Path
    tmp: Path
    home: Path
    data: Path | None

    @property
    def python(self) -> str:
        return (self.venv / "bin" / "python").as_posix()


def run_dirs(settings: Settings, paths: HostPaths, app_id: str, sha: str) -> RunDirs:
    tmp = paths.local_tmp_dir(app_id)
    if settings.is_hidden:
        release = paths.local_release_dir(app_id, sha)
        data = None
    else:
        release = paths.release_dir(app_id, sha)
        data = paths.data_dir(app_id)
    return RunDirs(release=release, code=release / "code", venv=release / "venv", tmp=tmp, home=tmp / "home", data=data)


def static_root(settings: Settings, paths: HostPaths, app_id: str, release: Release) -> Path:
    """The directory the gateway serves for a static app."""

    entry = release.manifest.get("entry") or "."
    if settings.is_hidden:
        base = paths.runtime / STATIC_AREA / app_id / release.sha
    else:
        base = paths.code_dir(app_id, release.sha)
    return base if entry in (".", "") else base / entry


# ---- environment ---------------------------------------------------------------


def app_environment(
    settings: Settings,
    paths: HostPaths,
    record: AppRecord,
    release: Release,
    stored_env: Mapping[str, str],
    *,
    port: int | None = None,
    extra: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """The complete environment of an app process (docs/CONTRACTS.md, section 8).

    Nothing of the manager's own environment leaks in: only the proxy and
    certificate variables of ``settings.passthrough_env`` are inherited, never
    the ``PIP_*`` ones (an index URL may carry credentials).
    """

    dirs = run_dirs(settings, paths, record.id, release.sha)
    env: dict[str, str] = {key: value for key, value in settings.passthrough_env.items() if not key.startswith("PIP_")}
    env.update(owner_environment(stored_env))
    env.update(
        {
            "EFP_APP_ID": record.id,
            "EFP_APP_SLUG": record.slug,
            "EFP_APP_TOKEN": stored_env.get(APP_TOKEN_ENV, ""),
            "EFP_APP_BASE_PATH": record.base_path,
            "EFP_APP_PUBLIC_URL": f"{settings.apps_public_url}/{record.slug}/",
            "EFP_PORTAL_URL": settings.portal_internal_url,
            "EFP_VISITOR_KEY": record.visitor_key,
            "PORT": str(record.port if port is None else port),
            "HOME": dirs.home.as_posix(),
            "TMPDIR": dirs.tmp.as_posix(),
            "PATH": f"{(dirs.venv / 'bin').as_posix()}:{SYSTEM_PATH}",
            "VIRTUAL_ENV": dirs.venv.as_posix(),
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "LANG": "C.UTF-8",
        }
    )
    if settings.is_hidden:
        env["EFP_APP_STORAGE_SOCKET"] = paths.storage_socket.as_posix()
    else:
        env["EFP_APP_DATA_DIR"] = paths.data_dir(record.id).as_posix()
    if extra:
        env.update(extra)
    return env


def health_url(record: AppRecord, release: Release, *, port: int | None = None) -> str:
    """``GET`` this; a status below 500 means the app is up (section 8.1)."""

    manifest = release.manifest
    prefix = "" if strips_prefix(manifest) else record.base_path
    path = manifest.get("health_path") or "/"
    return f"http://127.0.0.1:{record.port if port is None else port}{prefix}{path}"
