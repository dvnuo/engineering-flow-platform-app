"""The ``efp-app`` command.

    efp-app init <fastapi|flask|streamlit|gradio|static> [directory]
        copy a starter app into the directory (default: the current one)

    efp-app dev [directory] [--port N]
        start the app of efp-app.json the way the host starts it
        (docs/CONTRACTS.md, section 8.1), without a path prefix; the token
        and the Portal address come from the environment or from .env

    efp-app check [directory] [--strict]
        check the manifest, the files it names and the pages' URLs before
        registering or deploying; exit code 1 when something is wrong
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import _check, config
from . import manifest as _manifest
from ._version import __version__

TEMPLATE_TYPES = ("fastapi", "flask", "streamlit", "gradio", "static")
DEFAULT_PORT = 8000
ENV_FILE = ".env"
HOST = "127.0.0.1"

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SKIP_COPY_DIRS = {"__pycache__", ".efp-data", ".venv"}
# What section 8.1 passes the base path in; all of it is left out when the path is empty.
_BASE_PATH_VARIABLES = (config.APP_BASE_PATH, "SCRIPT_NAME", "GRADIO_ROOT_PATH")


def _write(stream: Any, text: str) -> None:
    """Print a line; a console that cannot show a character gets it escaped."""
    try:
        stream.write(text + "\n")
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, "backslashreplace").decode(encoding) + "\n")


def _say(text: str = "") -> None:
    _write(sys.stdout, text)


def _fail(text: str) -> None:
    _write(sys.stderr, text)


# --- init ---------------------------------------------------------------------


def templates_dir() -> Path:
    """Where the starter templates are: inside the package, or in a checkout."""
    here = Path(__file__).resolve().parent
    packaged = here / "_templates"
    if packaged.is_dir():
        return packaged
    checkout = here.parent.parent / "templates"
    if checkout.is_dir():
        return checkout
    raise FileNotFoundError("the starter templates are missing from this installation of efp-app-sdk")


def template_files(app_type: str) -> List[Path]:
    """The files of a starter, relative to its directory."""
    source = templates_dir() / app_type
    return sorted(
        path.relative_to(source)
        for path in source.rglob("*")
        if path.is_file() and not _SKIP_COPY_DIRS.intersection(path.parts) and path.suffix != ".pyc"
    )


def cmd_init(args: argparse.Namespace) -> int:
    try:
        source = templates_dir() / args.type
        files = template_files(args.type)
    except FileNotFoundError as exc:
        _fail("efp-app init: %s" % exc)
        return 1
    target = Path(args.directory or ".")
    if target.exists() and not target.is_dir():
        _fail("efp-app init: %s exists and is not a directory" % target)
        return 1
    existing = [relative.as_posix() for relative in files if (target / relative).exists()]
    if existing and not args.force:
        _fail("efp-app init: these files already exist in %s:" % target)
        for name in existing:
            _fail("  " + name)
        _fail("Nothing was written. Choose another directory, or pass --force to overwrite them.")
        return 1
    for relative in files:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)

    manifest_path = target / _manifest.FILE_NAME
    if (args.owner or args.name) and manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if args.owner:
            data["owners"] = list(args.owner)
        if args.name:
            data["name"] = args.name
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

    _say("Created a %s app in %s:" % (args.type, target))
    for relative in files:
        _say("  " + relative.as_posix())
    _say()
    _say("Next:")
    step = 1
    if str(target) != ".":
        _say("  %d. cd %s" % (step, target))
        step += 1
    if args.type != "static":
        _say("  %d. pip install -r requirements.txt" % step)
        step += 1
        _say("  %d. copy .env.example to .env and fill in the app token and the Portal address" % step)
        step += 1
    _say("  %d. efp-app dev" % step)
    step += 1
    if not args.owner:
        _say('  %d. put your Portal username in "owners" of %s' % (step, _manifest.FILE_NAME))
        step += 1
    _say("  %d. efp-app check, then push the code and register the app in the Portal" % step)
    return 0


# --- dev ----------------------------------------------------------------------


@dataclass
class Launch:
    """How an app is started: the command line and what it adds to the environment."""

    argv: List[str]
    env: Dict[str, str] = field(default_factory=dict)
    #: The module the command needs in the environment (the runner), if any.
    needs: Optional[str] = None
    note: Optional[str] = None


def build_launch(
    found: _manifest.Manifest,
    port: int,
    *,
    base_path: str = "",
    python: Optional[str] = None,
    platform: Optional[str] = None,
) -> Launch:
    """The command line of section 8.1 for this manifest.

    ``efp-app dev`` calls it with an empty ``base_path``, which leaves out
    every option that carries the prefix. ``platform`` is ``sys.platform``.
    """
    python = python or sys.executable
    platform = sys.platform if platform is None else platform
    base = base_path.rstrip("/")
    entry = found.entry or ""
    number = str(port)

    if found.type == "fastapi":
        argv = [python, "-m", "uvicorn", entry, "--host", HOST, "--port", number]
        if base:
            argv += ["--root-path", base]
        argv.append("--no-server-header")
        return Launch(argv, needs="uvicorn")

    if found.type == "flask":
        if platform == "win32":
            return Launch(
                [python, "-m", "flask", "--app", entry, "run", "--port", number],
                needs="flask",
                note="gunicorn needs a POSIX system, so on Windows the app runs on Flask's own server",
            )
        argv = [python, "-m", "gunicorn", entry, "--bind", "%s:%s" % (HOST, number)]
        argv += ["--workers", "1", "--threads", "8", "--timeout", "120"]
        return Launch(argv, env={"SCRIPT_NAME": base} if base else {}, needs="gunicorn")

    if found.type == "streamlit":
        argv = [python, "-m", "streamlit", "run", entry, "--server.port", number, "--server.address", HOST]
        if base:
            argv += ["--server.baseUrlPath", base]
        argv += ["--server.headless", "true", "--browser.gatherUsageStats", "false"]
        argv += ["--server.enableCORS", "false", "--server.enableXsrfProtection", "false"]
        return Launch(argv, needs="streamlit")

    if found.type == "gradio":
        env = {"GRADIO_SERVER_NAME": HOST, "GRADIO_SERVER_PORT": number}
        if base:
            env["GRADIO_ROOT_PATH"] = base
        return Launch([python, entry], env=env, needs="gradio")

    if found.type == "static":
        return Launch(
            [python, "-m", "http.server", number, "--bind", HOST, "--directory", entry or "."],
            note="a static app has no process on the host (the gateway serves the files); this is a local preview",
        )

    return Launch(["sh", "-c", found.command or ""])


def read_env_file(path: Path) -> Dict[str, str]:
    """``NAME=value`` lines of a ``.env`` file; comments and blank lines are skipped."""
    values: Dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def child_environment(base: Mapping[str, str], env_file: Mapping[str, str], port: int) -> Dict[str, str]:
    """The environment of the app process: the caller's, then ``.env``, then what section 8 sets."""
    env = dict(base)
    for name, value in env_file.items():
        if not env.get(name):
            env[name] = value
    for name in _BASE_PATH_VARIABLES:
        env.pop(name, None)
    env[config.PORT] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def _quote(argument: str) -> str:
    return '"%s"' % argument if (" " in argument or not argument) else argument


def cmd_dev(args: argparse.Namespace) -> int:
    directory = Path(args.directory or ".").resolve()
    data, problems = _manifest.read(directory)
    blocking = [
        problem
        for problem in (problems if data is None else _manifest.validate(data, for_launch=True))
        if problem.level == _manifest.ERROR
    ]
    if blocking or not isinstance(data, dict):
        for problem in blocking:
            _fail("efp-app dev: " + problem.render())
        return 1
    found = _manifest.parse(data)

    env_file = read_env_file(directory / ENV_FILE)
    port = args.port
    if port is None:
        port = config.load(os.environ).port or config.load(env_file).port or DEFAULT_PORT
    env = child_environment(os.environ, env_file, port)
    launch = build_launch(found, port)
    env.update(launch.env)

    if launch.needs and importlib.util.find_spec(launch.needs) is None:
        _fail(
            "efp-app dev: %s is not installed in this Python environment (%s).\n"
            "Install the app's dependencies first: pip install -r %s%s"
            % (
                launch.needs,
                sys.executable,
                found.requirements,
                "" if launch.needs in ("streamlit", "gradio") else "  (and: pip install %s)" % launch.needs,
            )
        )
        return 1
    if found.type == "command" and shutil.which("sh") is None:
        _fail("efp-app dev: a command app runs under `sh -c`, and there is no sh on this machine (try WSL or Git Bash).")
        return 1
    if found.type in _manifest.SCRIPT_TYPES and not (directory / (found.entry or "")).is_file():
        _fail('efp-app dev: "entry" is "%s", which does not exist in %s' % (found.entry, directory))
        return 1

    settings = config.load(env)
    _say("efp-app dev: %s (%s)" % (found.name, found.type))
    _say("  directory  %s" % directory)
    _say("  address    http://%s:%d/" % (HOST, port))
    _say("  command    %s" % " ".join(_quote(argument) for argument in launch.argv))
    for name in sorted(launch.env):
        _say("  sets       %s=%s" % (name, launch.env[name]))
    if launch.note:
        _say("  note       %s" % launch.note)
    if found.type != "static":
        _say("  Portal     %s" % (settings.portal_url or "not set"))
        _say("  token      %s" % ("set" if settings.token else "not set"))
        if not settings.token or not settings.portal_url:
            missing = [
                name
                for name, value in ((config.APP_TOKEN, settings.token), (config.PORTAL_URL, settings.portal_url))
                if not value
            ]
            _say(
                "  warning    %s not set: LLM and connector calls will fail. Put %s in %s "
                "(the token is on the app's page in the Portal)."
                % (" and ".join(missing), "them" if len(missing) > 1 else "it", directory / ENV_FILE)
            )
        if settings.deployed:
            _say("  warning    %s is set, so the SDK behaves as on the host (no local visitor)." % config.APP_ID)
    if args.dry_run:
        return 0
    _say()
    # The app writes to the same output; ours has to be out before it starts.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        return subprocess.call(launch.argv, cwd=str(directory), env=env)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        _fail("efp-app dev: could not start %s: %s" % (launch.argv[0], exc.strerror or exc))
        return 1


# --- check --------------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    directory = Path(args.directory or ".")
    if not directory.is_dir():
        _fail("efp-app check: %s is not a directory" % directory)
        return 1
    problems = _check.check_directory(directory)
    errors = [problem for problem in problems if problem.level == _manifest.ERROR]
    warnings = [problem for problem in problems if problem.level == _manifest.WARNING]
    for problem in errors + warnings:
        _say(problem.render())
    if not problems:
        _say("efp-app check: no problems found in %s" % directory)
        return 0
    _say(
        "efp-app check: %d error%s, %d warning%s"
        % (len(errors), "" if len(errors) == 1 else "s", len(warnings), "" if len(warnings) == 1 else "s")
    )
    if errors or (warnings and args.strict):
        return 1
    return 0


# --- The command line ---------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="efp-app", description="Create, run and check EFP apps.")
    parser.add_argument("--version", action="version", version="efp-app " + __version__)
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    init = commands.add_parser("init", help="copy a starter app", description="Copy a starter app into a directory.")
    init.add_argument("type", choices=TEMPLATE_TYPES, help="the kind of app")
    init.add_argument("directory", nargs="?", default=None, help="where to put it (default: the current directory)")
    init.add_argument("--owner", action="append", metavar="USERNAME", help='your Portal username, for "owners"')
    init.add_argument("--name", help="the app's name")
    init.add_argument("--force", action="store_true", help="overwrite files that already exist")
    init.set_defaults(handler=cmd_init)

    dev = commands.add_parser(
        "dev",
        help="run the app locally, the way the host runs it",
        description="Start the app of efp-app.json the way the host starts it, without a path prefix.",
    )
    dev.add_argument("directory", nargs="?", default=None, help="the app's directory (default: the current one)")
    dev.add_argument("--port", type=int, default=None, help="the port to listen on (default: $PORT, else 8000)")
    dev.add_argument("--dry-run", action="store_true", help="show the command and stop")
    dev.set_defaults(handler=cmd_dev)

    check = commands.add_parser(
        "check",
        help="check the app before registering or deploying it",
        description="Check the manifest, the files it names and the URLs in pages, scripts and style sheets.",
    )
    check.add_argument("directory", nargs="?", default=None, help="the app's directory (default: the current one)")
    check.add_argument("--strict", action="store_true", help="exit with 1 on warnings too")
    check.set_defaults(handler=cmd_check)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 2
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
