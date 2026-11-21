"""The ``efp-app`` command.

    efp-app init <fastapi|flask|streamlit|gradio|static> [directory]
        copy a starter app into the directory (default: the current one)

    efp-app dev [directory] [--port N] [--prefix NAME]
        start the app of efp-app.json the way the host starts it
        (docs/CONTRACTS.md, section 8.1), without a path prefix; the token
        and the Portal address come from the environment or from .env.
        With --prefix the app runs under /NAME/ behind a local stand-in for
        the gateway, which is how it is addressed once deployed

    efp-app check [directory] [--strict]
        check the manifest, the files it names and the pages' URLs before
        registering or deploying; exit code 1 when something is wrong
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import _check, _devgateway, config
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
    # Only what lies below the starter decides: the package itself is often installed
    # under a directory named .venv, which must not hide every file of the starter.
    found = (path.relative_to(source) for path in source.rglob("*") if path.is_file())
    return sorted(
        relative
        for relative in found
        if not _SKIP_COPY_DIRS.intersection(relative.parts) and relative.suffix != ".pyc"
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
    every option that carries the prefix, and with ``/NAME`` for
    ``--prefix NAME``. ``platform`` is ``sys.platform``.
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
            note = "gunicorn needs a POSIX system, so on Windows the app runs on Flask's own server"
            if not base:
                return Launch([python, "-m", "flask", "--app", entry, "run", "--port", number], needs="flask", note=note)
            # That server ignores SCRIPT_NAME; _devgateway.flask_app applies it the way gunicorn does.
            wrapped = "efp_app._devgateway:flask_app('%s')" % entry
            return Launch(
                [python, "-m", "flask", "--app", wrapped, "run", "--port", number],
                env={"SCRIPT_NAME": base},
                needs="flask",
                note=note + ", and efp_app applies SCRIPT_NAME for it",
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


def child_environment(
    base: Mapping[str, str],
    env_file: Mapping[str, str],
    port: int,
    *,
    system_proxy: Optional[Tuple[Mapping[str, str], Sequence[str]]] = None,
) -> Dict[str, str]:
    """The environment of the app process: the caller's, then ``.env``, then what section 8 sets.

    ``system_proxy`` is what ``system_proxy_settings()`` found, for a machine
    whose proxy is in the operating system's settings and not in the
    environment; ``efp-app dev`` passes it, tests leave it out.
    """
    env = dict(base)
    for name, value in env_file.items():
        if not env.get(name):
            env[name] = value
    for name in _BASE_PATH_VARIABLES:
        env.pop(name, None)
    env[config.PORT] = str(port)
    env["PYTHONUNBUFFERED"] = "1"
    if system_proxy is not None and not any(env.get(name) for name in _PROXY_VARIABLES):
        proxies, bypass = system_proxy
        for scheme, address in proxies.items():
            env[scheme.upper() + "_PROXY"] = address
        if proxies and bypass and not (env.get("NO_PROXY") or env.get("no_proxy")):
            env["NO_PROXY"] = ",".join(bypass)
    exempt_loopback_from_proxy(env)
    if sys.platform == "win32":
        keep_paths_from_msys(env)
    return env


def system_proxy_settings() -> Tuple[Dict[str, str], List[str]]:
    """The proxy of the operating system's own settings (Windows, macOS): ``({"http": ..., "https": ...}, [hosts that bypass it])``.

    Why ``efp-app dev`` hands it to the app as environment variables: with no
    proxy variable set, Python's HTTP libraries read these settings by
    themselves, and httpx (which the SDK and Gradio use) then ignores the
    list of hosts that are not to be proxied. The app's own port on 127.0.0.1
    would go to the company proxy. Written into the environment, the same
    proxy is used and ``NO_PROXY`` is honoured.
    """
    try:
        import urllib.request

        found = urllib.request.getproxies()
    except Exception:  # a broken setting must not keep the app from starting
        return {}, []
    proxies = {}
    for scheme in ("http", "https"):
        address = found.get(scheme)
        if address:
            proxies[scheme] = address if "://" in address else "http://" + address
    return proxies, (_windows_proxy_bypass() if proxies and sys.platform == "win32" else [])


def _windows_proxy_bypass() -> List[str]:
    """The "do not use the proxy for" list of the Windows settings, as far as ``NO_PROXY`` can say it."""
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings") as key:
            value = str(winreg.QueryValueEx(key, "ProxyOverride")[0])
    except Exception:
        return []
    hosts: List[str] = []
    for item in value.split(";"):
        item = item.strip()
        if not item or item == "<local>":
            continue
        if item.startswith("*."):
            item = item[1:]  # "*.corp.example" is ".corp.example" in NO_PROXY
        if "*" in item or "," in item:
            continue  # "10.*" and the like have no form in NO_PROXY
        hosts.append(item)
    return hosts


_PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def exempt_loopback_from_proxy(env: Dict[str, str]) -> None:
    """With a proxy in the environment, name the loopback addresses in ``NO_PROXY``, as the host does.

    The app listens on 127.0.0.1, and some frameworks ask their own port
    while they start (Gradio's ``launch()`` does). Sent to a company proxy,
    that request fails and the app never comes up.
    """
    if not any(env.get(name) for name in _PROXY_VARIABLES):
        return
    for name in ("NO_PROXY", "no_proxy"):
        other = "no_proxy" if name == "NO_PROXY" else "NO_PROXY"
        entries = [item.strip() for item in (env.get(name) or env.get(other) or "").split(",") if item.strip()]
        if "*" in entries:
            continue
        known = {item.lower() for item in entries}
        entries += [host for host in _LOOPBACK_HOSTS if host not in known]
        env[name] = ",".join(entries)


def keep_paths_from_msys(env: Dict[str, str]) -> None:
    """Git Bash on Windows: do not let its ``sh`` rewrite the path prefix into a Windows path.

    An MSYS program that starts a Windows one turns every variable that looks
    like a POSIX path into a Windows path, so ``EFP_APP_BASE_PATH=/my-app``
    reaches a ``command`` app (started through ``sh -c``) as
    ``C:/Program Files/Git/my-app``. ``MSYS2_ENV_CONV_EXCL`` names the
    variables to leave alone. It does the same to command-line arguments
    (``python server.py $EFP_APP_BASE_PATH``); ``MSYS_NO_PATHCONV`` (Git for
    Windows) and ``MSYS2_ARG_CONV_EXCL`` (MSYS2) switch that off, which is
    how the host behaves: there nothing is rewritten.
    """
    env.setdefault("MSYS_NO_PATHCONV", "1")
    env.setdefault("MSYS2_ARG_CONV_EXCL", "*")
    names = [item for item in (env.get("MSYS2_ENV_CONV_EXCL") or "").split(";") if item]
    if "*" in names:
        return
    for name in _BASE_PATH_VARIABLES:
        if name not in names:
            names.append(name)
    env["MSYS2_ENV_CONV_EXCL"] = ";".join(names)


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
    # With --prefix the gateway listens on the port that was asked for, and the app on a free one behind it.
    slug = args.prefix
    base = "/" + slug if slug else ""
    app_port = port
    while slug and app_port == port:
        app_port = _devgateway.free_port()
    env = child_environment(os.environ, env_file, app_port, system_proxy=system_proxy_settings())
    launch = build_launch(found, app_port, base_path=base)
    sets = dict(launch.env)
    if slug:
        # What section 8 says about the address. EFP_APP_ID stays unset: it alone makes the SDK treat the
        # process as deployed (config.Settings.deployed), and identity and storage are to stay local.
        sets[config.APP_BASE_PATH] = base
        sets[config.APP_SLUG] = slug
        sets[config.APP_PUBLIC_URL] = "http://%s:%d%s/" % (HOST, port, base)
    env.update(sets)

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
    if slug:
        _say("  address    http://%s:%d%s/  (under %s, as the app is addressed once deployed)" % (HOST, port, base, base))
        _say(
            "  gateway    %s:%d -> the app on %s:%d, with %s %s the path as the host does for this app"
            % (HOST, port, HOST, app_port, base, "removed from" if found.strip_prefix else "kept in")
        )
        _say("             a request outside %s/ gets 404 and a line here: it would not reach the app once deployed" % base)
        _say("             open the address above, not the one the app's own server prints: that port has no gateway")
    else:
        _say("  address    http://%s:%d/" % (HOST, port))
    _say("  command    %s" % " ".join(_quote(argument) for argument in launch.argv))
    for name in sorted(sets):
        _say("  sets       %s=%s" % (name, sets[name]))
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
        elif settings.visitor_key:
            # Only a host sets the key, so the SDK takes the app to stand behind a gateway (visitor.current).
            _say(
                "  warning    %s is set, so a request without a signed header has no visitor (%s is not used)."
                % (config.VISITOR_KEY, config.DEV_VISITOR)
            )
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
        if slug:
            gateway = _devgateway.Gateway(
                slug,
                app_port,
                strip_prefix=found.strip_prefix,
                say=_report,
                startup_timeout=found.startup_timeout,
                local_file_server=found.type == "static",
            )
            return _run_behind_gateway(gateway, port, launch.argv, directory, env)
        return subprocess.call(launch.argv, cwd=str(directory), env=env)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        _fail("efp-app dev: could not start %s: %s" % (launch.argv[0], exc.strerror or exc))
        return 1


def _report(text: str) -> None:
    """A line of the gateway, out at once even when the output is a file or a pipe."""
    _say(text)
    try:
        sys.stdout.flush()
    except Exception:
        pass


def _run_behind_gateway(
    gateway: _devgateway.Gateway, port: int, argv: List[str], directory: Path, env: Mapping[str, str]
) -> int:
    """Run the app with the gateway in front of it, until the app's process ends: its exit code.

    Ctrl+C reaches the app as well (it shares the console), so it is given a
    moment to leave by itself; ``KeyboardInterrupt`` and a command that
    cannot be started (``OSError``) are the caller's to report. SIGTERM
    reaches this process alone, so it is passed on: ``kill`` of the command
    then ends the app too, and the command with it.
    """
    started: List[Any] = []

    def pass_on(signum: int, frame: Any) -> None:
        for process in started:
            process.terminate()

    async def serve() -> int:
        try:
            server = await gateway.start(port)  # first: with the port taken, the app is not started
        except OSError as exc:
            _fail("efp-app dev: could not listen on %s:%d: %s" % (HOST, port, exc.strerror or exc))
            return 1
        try:
            process = subprocess.Popen(argv, cwd=str(directory), env=dict(env))
            started.append(process)
            while process.poll() is None:
                await asyncio.sleep(0.2)
            return int(process.returncode)
        finally:
            server.close()

    previous: Any = None
    try:
        previous = signal.signal(signal.SIGTERM, pass_on)
    except ValueError:
        pass  # not the main thread: signals are not this function's to handle
    try:
        return asyncio.run(serve())
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
        for process in started:
            _stop(process)


def _stop(process: Any, grace: float = 5.0) -> None:
    """Make sure the app's process is gone: wait, then ask, then insist."""
    for ask in (None, process.terminate, process.kill):
        try:
            if ask is not None:
                ask()
            process.wait(timeout=grace)
            return
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            continue  # too slow, or Ctrl+C again
        except OSError:
            return


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


def _prefix(text: str) -> str:
    """The value of ``--prefix`` as a slug; argparse reports what is wrong with it."""
    try:
        return _devgateway.parse_prefix(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="efp-app", description="Create, run and check EFP apps.")
    parser.add_argument("--version", action="version", version="efp-app " + __version__)
    commands = parser.add_subparsers(dest="command", metavar="<command>")

    init = commands.add_parser("init", help="copy a starter app", description="Copy a starter app into a directory.")
    init.add_argument("type", choices=TEMPLATE_TYPES, help="the kind of app")
    init.add_argument("directory", nargs="?", default=None, help="where to put it (default: the current directory)")
    init.add_argument(
        "--owner",
        action="append",
        metavar="USERNAME",
        help='a Portal username for "owners": only the members listed may register the app (repeat for more; default: any member)',
    )
    init.add_argument("--name", help="the app's name")
    init.add_argument("--force", action="store_true", help="overwrite files that already exist")
    init.set_defaults(handler=cmd_init)

    dev = commands.add_parser(
        "dev",
        help="run the app locally, the way the host runs it",
        description="Start the app of efp-app.json the way the host starts it: without a path prefix, "
        "or with --prefix under /NAME/ behind a local stand-in for the gateway.",
    )
    dev.add_argument("directory", nargs="?", default=None, help="the app's directory (default: the current one)")
    dev.add_argument("--port", type=int, default=None, help="the port to listen on (default: $PORT, else 8000)")
    dev.add_argument(
        "--prefix",
        type=_prefix,
        default=None,
        metavar="NAME",
        help="serve the app under /NAME/ as it is addressed once deployed (NAME is a slug such as my-app); "
        "a request outside /NAME/ gets 404 and a line on the console",
    )
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
