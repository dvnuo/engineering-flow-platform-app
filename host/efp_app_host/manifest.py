"""Validation of ``efp-app.json`` (docs/CONTRACTS.md, section 2)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import APP_TYPES, STRIP_PREFIX_BY_TYPE

MANIFEST_NAME = "efp-app.json"
MAX_MANIFEST_BYTES = 64 * 1024
DEFAULT_REQUIREMENTS = "requirements.txt"
DEFAULT_HEALTH_PATH = "/"
DEFAULT_STARTUP_TIMEOUT = 60
MAX_STARTUP_TIMEOUT = 300

_MODULE_ATTR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
_HEALTH_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class ManifestError(ValueError):
    """The manifest is missing, unreadable or breaks a rule."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("; ".join(self.problems))


def is_safe_relative_path(value: Any) -> bool:
    """A path that stays inside the directory it is relative to."""

    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        return False
    if value.startswith("/") or _WINDOWS_DRIVE_RE.match(value):
        return False
    return all(part != ".." for part in value.split("/"))


def normalize_relative_path(value: str) -> str:
    """Drop ``.`` segments and doubled slashes; ``""`` stands for the directory itself."""

    return "/".join(part for part in value.split("/") if part not in ("", "."))


def validate_manifest(data: Any) -> dict[str, Any]:
    """Return the manifest with every default filled in, or raise ``ManifestError``."""

    if not isinstance(data, dict):
        raise ManifestError(f"{MANIFEST_NAME} must be a JSON object")
    problems: list[str] = []

    name = data.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        problems.append("name is required and must be 1-80 characters")
        name = ""
    name = name.strip()

    description = data.get("description")
    if description is None:
        description = ""
    if not isinstance(description, str) or len(description) > 500:
        problems.append("description must be a string of up to 500 characters")
        description = ""

    icon = data.get("icon")
    if icon is not None and not is_safe_relative_path(icon):
        problems.append("icon must be a relative path inside the entry directory")
        icon = None

    app_type = data.get("type")
    if app_type not in APP_TYPES:
        problems.append("type is required and must be one of " + ", ".join(APP_TYPES))
        raise ManifestError(problems)

    entry = data.get("entry")
    command = data.get("command")
    if app_type in ("fastapi", "flask"):
        if not isinstance(entry, str) or not _MODULE_ATTR_RE.match(entry):
            problems.append(f"entry must be module:attribute for a {app_type} app (for example main:app)")
        command = None
    elif app_type in ("streamlit", "gradio"):
        if not is_safe_relative_path(entry) or not normalize_relative_path(entry):
            problems.append(f"entry must be the relative path of the script for a {app_type} app (for example app.py)")
        else:
            entry = normalize_relative_path(entry)
        command = None
    elif app_type == "static":
        if entry is None or entry == "":
            entry = "."
        if not is_safe_relative_path(entry):
            problems.append("entry must be a relative directory for a static app")
        else:
            entry = normalize_relative_path(entry) or "."
        command = None
    else:  # command
        entry = None
        if not isinstance(command, str) or not command.strip():
            problems.append("command is required for a command app")
        elif len(command) > 2000 or any(ch in command for ch in "\x00\r\n"):
            problems.append("command must be a single line of up to 2000 characters")

    requirements = data.get("requirements")
    if requirements is None or requirements == "":
        requirements = DEFAULT_REQUIREMENTS
    if not is_safe_relative_path(requirements) or not normalize_relative_path(requirements):
        problems.append("requirements must be a relative path inside the entry directory")
    else:
        requirements = normalize_relative_path(requirements)

    health_path = data.get("health_path")
    if health_path is None or health_path == "":
        health_path = DEFAULT_HEALTH_PATH
    if not isinstance(health_path, str) or len(health_path) > 200 or not _HEALTH_PATH_RE.match(health_path):
        problems.append("health_path must start with / and hold only URL characters")

    startup_timeout = data.get("startup_timeout")
    if startup_timeout is None:
        startup_timeout = DEFAULT_STARTUP_TIMEOUT
    if isinstance(startup_timeout, bool) or not isinstance(startup_timeout, int) or not 1 <= startup_timeout <= MAX_STARTUP_TIMEOUT:
        problems.append(f"startup_timeout must be a whole number of seconds between 1 and {MAX_STARTUP_TIMEOUT}")

    strip_prefix = data.get("strip_prefix")
    if strip_prefix is not None and not isinstance(strip_prefix, bool):
        problems.append("strip_prefix must be true, false or null")

    owners = data.get("owners")
    if owners is None:
        owners = []
    if not isinstance(owners, list) or not all(isinstance(item, str) and item for item in owners):
        problems.append("owners must be a list of Portal usernames")
        owners = []

    if problems:
        raise ManifestError(problems)
    return {
        "name": name,
        "description": description,
        "icon": normalize_relative_path(icon) if icon else None,
        "type": app_type,
        "entry": entry,
        "command": command,
        "requirements": requirements,
        "health_path": health_path,
        "startup_timeout": startup_timeout,
        "strip_prefix": strip_prefix,
        "owners": list(owners),
    }


def load_manifest(code_dir: Path) -> dict[str, Any]:
    """Read and validate the manifest of an unpacked entry directory, files included."""

    path = code_dir / MANIFEST_NAME
    try:
        size = path.stat().st_size
    except OSError:
        raise ManifestError(f"{MANIFEST_NAME} is missing from the entry directory") from None
    if size > MAX_MANIFEST_BYTES:
        raise ManifestError(f"{MANIFEST_NAME} is larger than {MAX_MANIFEST_BYTES // 1024} KiB")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{MANIFEST_NAME} is not valid JSON: {exc}") from None
    manifest = validate_manifest(data)
    check_files(manifest, code_dir)
    return manifest


def check_files(manifest: dict[str, Any], code_dir: Path) -> None:
    """The files the manifest points at must exist in the entry directory."""

    problems: list[str] = []
    app_type = manifest["type"]
    entry = manifest.get("entry")
    if app_type in ("streamlit", "gradio") and not (code_dir / entry).is_file():
        problems.append(f"entry {entry!r} does not exist in the entry directory")
    if app_type == "static" and not (code_dir / entry).is_dir():
        problems.append(f"entry {entry!r} is not a directory in the entry directory")
    if app_type in ("fastapi", "flask"):
        module = entry.split(":", 1)[0].replace(".", "/")
        if not (code_dir / f"{module}.py").is_file() and not (code_dir / module / "__init__.py").is_file():
            problems.append(f"entry {entry!r}: {module}.py does not exist in the entry directory")
    if manifest.get("icon") and not (code_dir / manifest["icon"]).is_file():
        problems.append(f"icon {manifest['icon']!r} does not exist in the entry directory")
    if problems:
        raise ManifestError(problems)


def strips_prefix(manifest: dict[str, Any]) -> bool:
    """Whether the gateway removes ``/{slug}`` before the request reaches the app."""

    override = manifest.get("strip_prefix")
    if override is not None:
        return bool(override)
    return STRIP_PREFIX_BY_TYPE[manifest["type"]]
