"""The app manifest ``efp-app.json`` (docs/CONTRACTS.md, section 2).

``validate`` checks a decoded manifest against the rules of the contract and
returns what is wrong; ``read`` loads the file of a directory. Used by
``efp-app check`` and ``efp-app dev``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

FILE_NAME = "efp-app.json"

TYPES = ("fastapi", "flask", "streamlit", "gradio", "command", "static")
#: Types whose entry is ``module:attribute``.
MODULE_TYPES = ("fastapi", "flask")
#: Types whose entry is a script path.
SCRIPT_TYPES = ("streamlit", "gradio")
#: Whether the gateway removes "/{slug}" before a request reaches the app (section 6.2).
STRIP_PREFIX_BY_TYPE = {
    "fastapi": True,
    "gradio": True,
    "static": True,
    "flask": False,
    "streamlit": False,
    "command": False,
}

DEFAULT_REQUIREMENTS = "requirements.txt"
DEFAULT_HEALTH_PATH = "/"
DEFAULT_STARTUP_TIMEOUT = 60
MAX_STARTUP_TIMEOUT = 300
MAX_NAME = 80
MAX_DESCRIPTION = 500

KNOWN_KEYS = (
    "name",
    "description",
    "icon",
    "type",
    "entry",
    "command",
    "requirements",
    "health_path",
    "startup_timeout",
    "strip_prefix",
    "owners",
)

ERROR = "error"
WARNING = "warning"

_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_MODULE_ENTRY = re.compile(r"(%s(?:\.%s)*):(%s(?:\.%s)*)" % (_NAME, _NAME, _NAME, _NAME))
_DRIVE = re.compile(r"^[A-Za-z]:")
_HEALTH_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/?-]*")
MAX_HEALTH_PATH = 200
MAX_COMMAND = 2000


@dataclass(frozen=True)
class Problem:
    """One finding of a check: ``level`` is ``error`` or ``warning``."""

    level: str
    message: str
    file: Optional[str] = None
    line: Optional[int] = None

    def render(self) -> str:
        where = self.file or ""
        if where and self.line:
            where += ":%d" % self.line
        return "%-7s %s%s" % (self.level, where + ": " if where else "", self.message)


@dataclass(frozen=True)
class Manifest:
    """A manifest with the defaults of the contract filled in."""

    name: str
    type: str
    description: str = ""
    icon: Optional[str] = None
    entry: Optional[str] = None
    command: Optional[str] = None
    requirements: str = DEFAULT_REQUIREMENTS
    #: False when the manifest left "requirements" out and the default applies.
    requirements_named: bool = False
    health_path: str = DEFAULT_HEALTH_PATH
    startup_timeout: int = DEFAULT_STARTUP_TIMEOUT
    strip_prefix: bool = False
    owners: Tuple[str, ...] = ()


def path_problem(value: str) -> Optional[str]:
    """Why ``value`` is not an acceptable relative path, or ``None``."""
    if "\\" in value:
        return "must use forward slashes"
    if value.startswith("/") or _DRIVE.match(value):
        return "may not be an absolute path"
    if ".." in value.split("/"):
        return 'may not contain ".."'
    if "\x00" in value:
        return "may not contain a NUL character"
    return None


def split_entry(entry: str) -> Optional[Tuple[str, str]]:
    """``("main", "app")`` for ``main:app``; ``None`` when it is not ``module:attribute``."""
    match = _MODULE_ENTRY.fullmatch(entry or "")
    return (match.group(1), match.group(2)) if match else None


def validate(data: Any, *, for_launch: bool = False) -> List[Problem]:
    """What is wrong with a decoded manifest, by the rules of section 2.

    ``for_launch`` leaves out what only matters for registering the app
    ("owners"): a slip there does not keep the app from running locally.
    """
    problems: List[Problem] = []

    def error(message: str) -> None:
        problems.append(Problem(ERROR, message, FILE_NAME))

    def warning(message: str) -> None:
        problems.append(Problem(WARNING, message, FILE_NAME))

    if not isinstance(data, dict):
        error("the manifest must be a JSON object")
        return problems

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        error('"name" is required: 1 to %d characters' % MAX_NAME)
    elif len(name.strip()) > MAX_NAME:
        error('"name" is longer than %d characters' % MAX_NAME)

    description = data.get("description")
    if description is not None:
        if not isinstance(description, str):
            error('"description" must be text')
        elif len(description) > MAX_DESCRIPTION:
            error('"description" is longer than %d characters' % MAX_DESCRIPTION)

    app_type = data.get("type")
    if not isinstance(app_type, str) or app_type not in TYPES:
        error('"type" is required: one of %s' % ", ".join(TYPES))
        app_type = None

    icon = data.get("icon")
    if icon is not None:
        if not isinstance(icon, str) or not icon:
            error('"icon" must be the relative path of an image')
        elif path_problem(icon):
            error('"icon" %s' % path_problem(icon))

    entry = data.get("entry")
    if app_type in MODULE_TYPES:
        if not isinstance(entry, str) or not split_entry(entry):
            error('"entry" must be module:attribute for a %s app, for example "main:app"' % app_type)
    elif app_type in SCRIPT_TYPES:
        if not isinstance(entry, str) or not entry:
            error('"entry" must be the path of the script for a %s app, for example "app.py"' % app_type)
        elif path_problem(entry):
            error('"entry" %s' % path_problem(entry))
    elif app_type == "static":
        if entry is not None and entry != "":
            if not isinstance(entry, str):
                error('"entry" must be a directory for a static app (default ".")')
            elif path_problem(entry):
                error('"entry" %s' % path_problem(entry))

    command = data.get("command")
    if app_type == "command":
        if not isinstance(command, str) or not command.strip():
            error('"command" is required for a command app: a shell command line that listens on $PORT')
        elif len(command) > MAX_COMMAND or any(character in command for character in "\x00\r\n"):
            error('"command" must be a single line of up to %d characters' % MAX_COMMAND)
    elif command is not None and not isinstance(command, str):
        error('"command" must be text')

    requirements = data.get("requirements")
    if requirements is not None and requirements != "":
        if not isinstance(requirements, str):
            error('"requirements" must be the relative path of a requirements file')
        elif path_problem(requirements):
            error('"requirements" %s' % path_problem(requirements))

    health_path = data.get("health_path")
    if health_path is not None and health_path != "":
        if (
            not isinstance(health_path, str)
            or len(health_path) > MAX_HEALTH_PATH
            or not _HEALTH_PATH.fullmatch(health_path)
        ):
            error('"health_path" must start with "/" and hold only URL characters, for example "/healthz"')

    startup_timeout = data.get("startup_timeout")
    if startup_timeout is not None:
        if isinstance(startup_timeout, bool) or not isinstance(startup_timeout, int):
            error('"startup_timeout" must be a whole number of seconds')
        elif not 1 <= startup_timeout <= MAX_STARTUP_TIMEOUT:
            error('"startup_timeout" must be between 1 and %d seconds' % MAX_STARTUP_TIMEOUT)

    strip_prefix = data.get("strip_prefix")
    if strip_prefix is not None and not isinstance(strip_prefix, bool):
        error('"strip_prefix" must be true, false or null')

    # Optional: without it any member may register the app; with it, only the members it lists.
    owners = data.get("owners")
    if for_launch or owners is None:
        pass
    elif not isinstance(owners, list) or not all(isinstance(owner, str) and owner.strip() for owner in owners):
        error('"owners" must be a list of Portal usernames; leave it out to let any member register this app')

    for key in data:
        if key not in KNOWN_KEYS and not str(key).startswith(("_", "$")):
            warning('unknown key "%s" is ignored' % key)
    return problems


def parse(data: Dict[str, Any]) -> Manifest:
    """A :class:`Manifest` from decoded JSON, with the defaults filled in.

    Call it for data ``validate`` found no errors in; it does not raise on
    odd values, it falls back to the defaults.
    """
    app_type = data.get("type") if data.get("type") in TYPES else "command"
    entry = data.get("entry") if isinstance(data.get("entry"), str) and data.get("entry") else None
    if app_type == "static" and entry is None:
        entry = "."
    requirements = data.get("requirements")
    startup_timeout = data.get("startup_timeout")
    strip_prefix = data.get("strip_prefix")
    owners = data.get("owners")
    return Manifest(
        name=str(data.get("name") or ""),
        type=app_type,
        description=data.get("description") if isinstance(data.get("description"), str) else "",
        icon=data.get("icon") if isinstance(data.get("icon"), str) else None,
        entry=entry,
        command=data.get("command") if isinstance(data.get("command"), str) else None,
        requirements=requirements if isinstance(requirements, str) and requirements else DEFAULT_REQUIREMENTS,
        requirements_named=isinstance(requirements, str) and bool(requirements),
        health_path=(
            data["health_path"]
            if isinstance(data.get("health_path"), str) and data.get("health_path")
            else DEFAULT_HEALTH_PATH
        ),
        startup_timeout=(
            startup_timeout
            if isinstance(startup_timeout, int) and not isinstance(startup_timeout, bool)
            else DEFAULT_STARTUP_TIMEOUT
        ),
        strip_prefix=strip_prefix if isinstance(strip_prefix, bool) else STRIP_PREFIX_BY_TYPE[app_type],
        owners=tuple(owner for owner in owners if isinstance(owner, str)) if isinstance(owners, list) else (),
    )


def read(directory: Path) -> Tuple[Optional[Dict[str, Any]], List[Problem]]:
    """Load ``efp-app.json`` of ``directory``: the decoded JSON (or ``None``) and the problems."""
    path = Path(directory) / FILE_NAME
    if not path.is_file():
        return None, [
            Problem(
                ERROR,
                "not found in %s: an app needs this manifest in its entry directory "
                "(`efp-app init <type>` creates a starter)" % Path(directory),
                FILE_NAME,
            )
        ]
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except UnicodeDecodeError:
        return None, [Problem(ERROR, "is not UTF-8 text", FILE_NAME)]
    except json.JSONDecodeError as exc:
        return None, [Problem(ERROR, "is not valid JSON: %s" % exc.msg, FILE_NAME, exc.lineno)]
    except OSError as exc:
        return None, [Problem(ERROR, "cannot be read: %s" % (exc.strerror or exc), FILE_NAME)]
    return data, validate(data)
