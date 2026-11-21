"""Virtual environments and pip: which commands a deployment runs.

Pure command building; the deployment pipeline runs the commands through the
launcher, as the app's own user and inside its isolation.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

SDK_DISTRIBUTION = "efp-app-sdk"
BUNDLED_WHEELS_DIR = Path("/opt/efp/wheels")
# The package that provides the server of docs/CONTRACTS.md, section 8.1.
RUNNER_BY_TYPE = {"fastapi": "uvicorn", "flask": "gunicorn"}
# Environment variables that name a file pip must be able to read.
PIP_FILE_VARIABLES = ("PIP_CERT", "PIP_CLIENT_CERT", "PIP_CONFIG_FILE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE")

_NAME_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
_INCLUDE_RE = re.compile(r"^(?:-r|--requirement)(?:\s+|=)(\S+)")
_EGG_RE = re.compile(r"[#&]egg=([A-Za-z0-9][A-Za-z0-9._-]*)")
# What the first part of a wheel's file name (its distribution) is made of.
_WHEEL_NAME_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._")
_WHEEL_SUFFIX = ".whl"
_MAX_INCLUDE_DEPTH = 5
# The manager reads the requirements itself (as root, into memory) to see
# what it has to add: a list of packages is a few kilobytes.
MAX_REQUIREMENTS_BYTES = 1024 * 1024


class RequirementsError(ValueError):
    """The requirements file is not something the host reads."""


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _without_comment(line: str) -> str:
    """``line`` up to a ``#`` that follows white space (where pip starts a comment)."""

    position = line.find("#", 1)
    while position > 0:
        if line[position - 1].isspace():
            return line[:position].rstrip()
        position = line.find("#", position + 1)
    return line


def _wheel_distribution(line: str) -> str | None:
    """The distribution of the first wheel a line names by path or URL.

    A wheel's file name is ``{distribution}-{version}-...whl``: in a part of
    a path that ends in ``.whl``, the name is what stands before a hyphen.
    Every part is walked once, from hyphen to hyphen.
    """

    if _WHEEL_SUFFIX not in line:
        return None
    for word in line.split():
        for part in word.replace("\\", "/").split("/"):
            # ".whl" must leave room for a hyphen and at least one character of a version before it.
            latest = part.rfind(_WHEEL_SUFFIX) - 2
            hyphen = part.find("-")
            while 0 <= hyphen <= latest:
                if part[hyphen + 1] != "-":
                    start = hyphen
                    while start > 0 and part[start - 1] in _WHEEL_NAME_CHARACTERS:
                        start -= 1
                    # A name starts with a letter or a digit.
                    name = part[start:hyphen].lstrip("._")
                    if name:
                        return normalize_name(name)
                hyphen = part.find("-", hyphen + 1)
    return None


def requirement_names(text: str) -> tuple[set[str], list[str]]:
    """The distributions a requirements file names, and the files it includes.

    The manager runs this itself on a file a member wrote, and a pattern at
    work holds every other thread of the manager up. So a line is taken
    apart in one pass: the comment and the wheel names used to be found by
    patterns that tried every way of cutting a line at its blanks or its
    hyphens, which took minutes on one requirement with a dozen ``--hash``
    options (what ``pip-compile --generate-hashes`` writes for every
    package) and did not end on a longer one.
    """

    names: set[str] = set()
    includes: list[str] = []
    logical = text.replace("\\\r\n", " ").replace("\\\n", " ")
    for raw in logical.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = _without_comment(line)
        include = _INCLUDE_RE.match(line)
        if include:
            includes.append(include.group(1))
            continue
        egg = _EGG_RE.search(line)
        if egg:
            names.add(normalize_name(egg.group(1)))
        if line.startswith("-"):
            continue
        match = _NAME_RE.match(line)
        if match and "://" not in line.split("@", 1)[0]:
            names.add(normalize_name(match.group(1)))
        # A wheel given by path or URL: the distribution is the file name's first part.
        wheel = _wheel_distribution(line)
        if wheel:
            names.add(wheel)
    return names, includes


def collect_requirement_names(code_dir: Path, requirements: str) -> set[str] | None:
    """Every distribution named by the requirements file and what it includes.

    ``None`` when the file does not exist (the app has no dependencies).
    """

    root = code_dir.resolve()
    start = (code_dir / requirements).resolve()
    if not start.is_file():
        return None
    names: set[str] = set()
    seen: set[Path] = set()
    queue: list[tuple[Path, int]] = [(start, 0)]
    while queue:
        path, depth = queue.pop()
        if path in seen or depth > _MAX_INCLUDE_DEPTH:
            continue
        seen.add(path)
        if root != path and root not in path.parents:
            continue
        try:
            if path.stat().st_size > MAX_REQUIREMENTS_BYTES:
                raise RequirementsError(f"{path.name} is larger than {MAX_REQUIREMENTS_BYTES // 1024} KiB: not a list of packages the host can read")
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                found, includes = requirement_names(handle.read(MAX_REQUIREMENTS_BYTES + 1))
        except OSError:
            continue
        names |= found
        for include in includes:
            queue.append(((path.parent / include).resolve(), depth + 1))
    return names


def bundled_sdk_wheel(wheels_dir: Path = BUNDLED_WHEELS_DIR) -> Path | None:
    try:
        wheels = sorted(wheels_dir.glob("efp_app_sdk-*.whl"))
    except OSError:
        return None
    return wheels[-1] if wheels else None


@dataclass
class InstallPlan:
    """What pip is asked to install for one release."""

    requirements_file: Path | None
    extras: list[str] = field(default_factory=list)
    find_links: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.requirements_file is None and not self.extras


def plan_install(code_dir: Path, manifest: Mapping[str, object], *, wheels_dir: Path = BUNDLED_WHEELS_DIR) -> InstallPlan:
    """Requirements of the app plus what the host adds by itself.

    The runner of the app's type and the SDK are added when the requirements
    do not name them (docs/CONTRACTS.md, section 8.1).
    """

    requirements = str(manifest.get("requirements") or "requirements.txt")
    names = collect_requirement_names(code_dir, requirements)
    plan = InstallPlan(requirements_file=(code_dir / requirements) if names is not None else None)
    if names is None:
        plan.notes.append(f"{requirements} not found: the app has no dependencies of its own")
        names = set()
    runner = RUNNER_BY_TYPE.get(str(manifest.get("type")))
    if runner and runner not in names:
        plan.extras.append(runner)
        plan.notes.append(f"adding {runner}: the requirements do not name it")
    if normalize_name(SDK_DISTRIBUTION) not in names:
        wheel = bundled_sdk_wheel(wheels_dir)
        if wheel is not None:
            plan.extras.append(SDK_DISTRIBUTION)
            plan.find_links.append(wheels_dir.as_posix())
            plan.notes.append(f"adding the bundled {SDK_DISTRIBUTION} ({wheel.name})")
        else:
            plan.notes.append(f"no bundled {SDK_DISTRIBUTION} wheel in {wheels_dir.as_posix()}: the SDK is not installed")
    return plan


def base_python() -> str:
    """The interpreter new virtual environments are made from."""

    return getattr(sys, "_base_executable", None) or sys.executable


def venv_command(venv_dir: Path, *, python: str | None = None) -> list[str]:
    return [python or base_python(), "-m", "venv", venv_dir.as_posix()]


def _pip(python: str) -> list[str]:
    return [python, "-m", "pip", "--disable-pip-version-check", "--no-input", "--no-cache-dir"]


def _targets(plan: InstallPlan) -> list[str]:
    args: list[str] = []
    if plan.requirements_file is not None:
        args += ["-r", plan.requirements_file.as_posix()]
    return args + list(plan.extras)


def install_command(python: str, plan: InstallPlan, *, allow_sdist: bool) -> list[str]:
    """``pip install`` from the index into the virtual environment (sandbox mode)."""

    args = _pip(python) + ["install"]
    if not allow_sdist:
        args += ["--only-binary", ":all:"]
    for link in plan.find_links:
        args += ["--find-links", link]
    return args + _targets(plan)


def wheel_command(python: str, plan: InstallPlan, wheels_dir: Path, *, allow_sdist: bool) -> list[str]:
    """``pip wheel``: collect every wheel the release needs (hidden mode's cache)."""

    args = _pip(python) + ["wheel", "--wheel-dir", wheels_dir.as_posix()]
    if not allow_sdist:
        args += ["--only-binary", ":all:"]
    for link in plan.find_links:
        args += ["--find-links", link]
    return args + _targets(plan)


def offline_install_command(python: str, wheels: list[Path]) -> list[str]:
    """``pip install`` of exactly the wheels of a cache, never from the network.

    ``pip wheel`` wrote the complete, resolved set of a release into its
    cache, so installing every file of it rebuilds the same environment.
    """

    return _pip(python) + ["install", "--no-index", "--no-deps"] + [wheel.as_posix() for wheel in wheels]


def cached_wheels(wheels_dir: Path) -> list[Path]:
    try:
        return sorted(wheels_dir.glob("*.whl"))
    except OSError:
        return []


def freeze_command(python: str) -> list[str]:
    return _pip(python) + ["freeze"]


def pip_environment(passthrough: Mapping[str, str], *, home: str, tmp: str, venv_bin: str | None = None, index: bool = True) -> dict[str, str]:
    """The environment of a pip run: the host's pip settings and nothing else.

    ``index=False`` is for the commands that never ask the package index
    (the virtual environment, an install from local wheels, ``pip freeze``):
    they get none of the host's ``PIP_*`` settings. Those may hold the
    password of the index, and code of the app runs inside every one of
    these commands.

    The cache is switched off on the command line only (``--no-cache-dir``):
    pip reads ``PIP_NO_CACHE_DIR`` in a way that differs between versions.
    """

    env = {key: value for key, value in passthrough.items() if index or not key.startswith("PIP_")}
    path = "/usr/local/bin:/usr/bin:/bin"
    env.update(
        {
            "HOME": home,
            "TMPDIR": tmp,
            "PATH": f"{venv_bin}:{path}" if venv_bin else path,
            "LANG": "C.UTF-8",
            "PYTHONUNBUFFERED": "1",
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INPUT": "1",
        }
    )
    return env


def pip_readable_files(passthrough: Mapping[str, str], *, index: bool = True) -> list[str]:
    """Absolute files the pip settings name (certificates, a config file).

    None for a command that does not ask the index (``index=False``).
    """

    files: list[str] = []
    if not index:
        return files
    for key in PIP_FILE_VARIABLES:
        value = passthrough.get(key) or ""
        if value.startswith("/") and value not in files:
            files.append(value)
    return files
