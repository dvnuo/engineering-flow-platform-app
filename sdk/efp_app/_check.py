"""What ``efp-app check`` looks at beyond the manifest's own rules.

- the files the manifest names: the entry, the requirements file, the icon;
- the requirements: things the host cannot install (it takes wheels from its
  package index and nothing else);
- pages, scripts and style sheets: root-absolute URLs such as
  ``src="/static/app.js"``. An app is served under ``/{slug}/``, so a URL
  that starts with ``/`` points outside the app and breaks once deployed,
  although it works on a developer's machine.
"""

from __future__ import annotations

import os
import posixpath
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import manifest as _manifest
from .manifest import ERROR, WARNING, Problem

SCAN_SUFFIXES = frozenset(
    {
        ".html",
        ".htm",
        ".js",
        ".mjs",
        ".cjs",
        ".jsx",
        ".ts",
        ".tsx",
        ".css",
        ".vue",
        ".svelte",
        ".jinja",
        ".jinja2",
        ".j2",
        ".tmpl",
        ".tpl",
    }
)
SKIP_DIRS = frozenset({"node_modules", "__pycache__", "venv", "env", "site-packages"})
MAX_SCAN_BYTES = 2 * 1024 * 1024
MAX_FINDINGS_PER_FILE = 20

_ATTRIBUTE = re.compile(r"""\b(src|href|action|formaction|poster)\s*=\s*(["'])(/(?!/)[^"']*)\2""", re.IGNORECASE)
_CALL = re.compile(r"""\b(fetch|EventSource|axios(?:\.[A-Za-z]+)?)\s*\(\s*(["'`])(/(?!/)[^"'`]*)\2""")
_CSS_URL = re.compile(r"""\burl\(\s*(["']?)(/(?!/)[^"')\s]*)\1\s*\)""", re.IGNORECASE)

_FRAMEWORK_PACKAGE = {"fastapi": "fastapi", "flask": "flask", "streamlit": "streamlit", "gradio": "gradio"}
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_INDEX_OPTIONS = ("-i", "--index-url", "--extra-index-url", "-f", "--find-links", "--trusted-host")


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _relative(url: str) -> str:
    return url.lstrip("/") or "./"


def _from_sheet(url: str, sheet: str) -> str:
    """``url`` as a style sheet writes it, the sheet being served at the path it has in the app.

    A URL in a style sheet is resolved against the sheet's own address, not the page's:
    ``/static/bg.png`` in ``static/style.css`` is ``bg.png``, and ``/img/bg.png`` there is
    ``../img/bg.png``.
    """
    cut = min((url.index(mark) for mark in "?#" if mark in url), default=len(url))
    target, rest = url[:cut].lstrip("/"), url[cut:]
    path = posixpath.relpath(target or ".", posixpath.dirname(sheet) or ".")
    if path == ".":
        path = "./"
    elif url[:cut].endswith("/"):
        path += "/"
    return path + rest


# --- Files the manifest names -------------------------------------------------


def check_files(directory: Path, data: Dict[str, Any]) -> List[Problem]:
    """Problems with the files a valid manifest refers to."""
    problems: List[Problem] = []
    found = _manifest.parse(data)
    name = _manifest.FILE_NAME

    if found.type in _manifest.MODULE_TYPES and found.entry:
        parts = _manifest.split_entry(found.entry)
        if parts:
            module, attribute = parts
            module_path = module.replace(".", "/")
            source = next(
                (
                    candidate
                    for candidate in (directory / (module_path + ".py"), directory / module_path / "__init__.py")
                    if candidate.is_file()
                ),
                None,
            )
            if source is None:
                problems.append(
                    Problem(ERROR, '"entry" is "%s", but %s.py does not exist' % (found.entry, module_path), name)
                )
            else:
                variable = attribute.split(".")[0]
                try:
                    text = source.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                if text and not re.search(r"\b%s\b" % re.escape(variable), text):
                    problems.append(
                        Problem(
                            WARNING,
                            '"entry" is "%s", but %s does not mention "%s"'
                            % (found.entry, source.relative_to(directory).as_posix(), variable),
                            name,
                        )
                    )
    elif found.type in _manifest.SCRIPT_TYPES and found.entry:
        if not (directory / found.entry).is_file():
            problems.append(Problem(ERROR, '"entry" is "%s", which does not exist' % found.entry, name))
    elif found.type == "static":
        root = directory / (found.entry or ".")
        if not root.is_dir():
            problems.append(Problem(ERROR, '"entry" is "%s", which is not a directory' % found.entry, name))
        elif not (root / "index.html").is_file():
            problems.append(
                Problem(WARNING, "there is no index.html in the served directory: the app's address answers 404", name)
            )

    requirements = directory / found.requirements
    if not requirements.is_file():
        if found.requirements_named:
            problems.append(
                Problem(ERROR, '"requirements" is "%s", which does not exist' % found.requirements, name)
            )
        elif found.type in _FRAMEWORK_PACKAGE:
            problems.append(
                Problem(
                    WARNING,
                    "there is no %s: the host installs the app without any dependency" % found.requirements,
                    name,
                )
            )
    elif found.type != "static":
        problems.extend(check_requirements(requirements, found.requirements, found.type))

    if found.icon and not (directory / found.icon).is_file():
        problems.append(Problem(ERROR, '"icon" is "%s", which does not exist' % found.icon, name))
    return problems


def check_requirements(path: Path, shown: str, app_type: str) -> List[Problem]:
    """Lines of a requirements file the host cannot install."""
    problems: List[Problem] = []
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError as exc:
        return [Problem(ERROR, "cannot be read: %s" % (exc.strerror or exc), shown)]
    names = set()
    includes_other_files = False
    for number, raw in enumerate(lines, 1):
        line = raw.split(" #", 1)[0].strip()
        if not line or line.startswith("#"):
            continue
        lowered = line.lower()
        first = lowered.split()[0].split("=")[0]
        if first in ("-r", "--requirement", "-c", "--constraint"):
            includes_other_files = True
            continue
        if first in _INDEX_OPTIONS:
            problems.append(
                Problem(WARNING, "the host uses its own package index; remove this option", shown, number)
            )
            continue
        if (
            first in ("-e", "--editable")
            or lowered.startswith(("git+", "hg+", "svn+", "bzr+", "http://", "https://", "file:", "./", "../", "/"))
            or re.search(r"@\s*(git\+|hg\+|https?://|file:)", lowered)
        ):
            problems.append(
                Problem(
                    WARNING,
                    "the host installs wheels from its package index only: "
                    "a repository, URL or local path cannot be installed",
                    shown,
                    number,
                )
            )
            continue
        match = _REQUIREMENT_NAME.match(line)
        if match:
            names.add(re.sub(r"[-_.]+", "-", match.group(0).lower()))
    package = _FRAMEWORK_PACKAGE.get(app_type)
    if package and package not in names and not includes_other_files:
        problems.append(
            Problem(WARNING, 'does not list "%s", which a %s app needs' % (package, app_type), shown)
        )
    return problems


# --- Root-absolute URLs -------------------------------------------------------


def _scan_files(directory: Path) -> Iterator[Path]:
    for current, folders, names in os.walk(directory):
        folders[:] = sorted(name for name in folders if not name.startswith(".") and name not in SKIP_DIRS)
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() in SCAN_SUFFIXES:
                yield Path(current) / name


def _findings(line: str, app_type: Optional[str], shown: str = "") -> Iterator[Tuple[int, str]]:
    for match in _ATTRIBUTE.finditer(line):
        attribute, url = match.group(1), match.group(3)
        advice = 'use a relative URL: %s="%s"' % (attribute, _short(_relative(url), 40))
        if app_type == "flask":
            advice += " or {{ url_for(...) }}"
        yield match.start(), 'root-absolute URL %s="%s": %s' % (attribute, _short(url), advice)
    for match in _CALL.finditer(line):
        call, url = match.group(1), match.group(3)
        yield match.start(), 'root-absolute URL %s("%s"): use a relative URL: %s("%s")' % (
            call,
            _short(url),
            call,
            _short(_relative(url), 40),
        )
    for match in _CSS_URL.finditer(line):
        url = match.group(2)
        if shown.lower().endswith(".css"):
            # Resolved against the sheet's own address, so the hint starts from where the sheet lies.
            advice = "use a path relative to the style sheet: url(%s)" % _short(_from_sheet(url, shown), 40)
        else:
            # A style written into a page is resolved against the page.
            advice = "use a relative URL: url(%s)" % _short(_relative(url), 40)
        yield match.start(), "root-absolute URL url(%s): %s" % (_short(url), advice)


def scan_urls(directory: Path, app_type: Optional[str] = None, served_from: str = "") -> List[Problem]:
    """Root-absolute URLs in the pages, scripts and style sheets below ``directory``.

    ``served_from`` is the folder whose files are served at the top of the app
    (the ``entry`` of a static app): the hint for a style sheet in it starts
    from where the sheet is served, not from where it lies in the directory.
    """
    problems: List[Problem] = []
    inside = served_from.strip("/") + "/" if served_from.strip("/") else ""
    for path in _scan_files(directory):
        shown = path.relative_to(directory).as_posix()
        served = shown[len(inside) :] if inside and shown.startswith(inside) else shown
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found: List[Tuple[int, int, str]] = []
        for number, line in enumerate(text.splitlines(), 1):
            if "/" not in line:
                continue
            for column, message in _findings(line, app_type, served):
                found.append((number, column, message))
        found.sort()
        for number, _column, message in found[:MAX_FINDINGS_PER_FILE]:
            problems.append(Problem(WARNING, message, shown, number))
        if len(found) > MAX_FINDINGS_PER_FILE:
            problems.append(
                Problem(WARNING, "%d more root-absolute URLs in this file" % (len(found) - MAX_FINDINGS_PER_FILE), shown)
            )
    return problems


# --- Everything ---------------------------------------------------------------


def check_directory(directory: Path) -> List[Problem]:
    """Every finding for an app directory: the manifest, its files, its URLs."""
    directory = Path(directory)
    data, problems = _manifest.read(directory)
    app_type = None
    served_from = ""
    if isinstance(data, dict):
        app_type = data.get("type") if data.get("type") in _manifest.TYPES else None
        if app_type:
            problems = problems + check_files(directory, data)
        entry = data.get("entry")
        if app_type == "static" and isinstance(entry, str):
            folder = posixpath.normpath(entry.replace("\\", "/"))
            served_from = "" if folder in (".", "/") or folder.startswith("..") else folder
    if directory.is_dir():
        problems = problems + scan_urls(directory, app_type, served_from)
    return problems
