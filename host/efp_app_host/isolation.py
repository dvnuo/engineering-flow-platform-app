"""How an app process is kept apart from the others (docs/CONTRACTS.md, section 7).

The volume is EFS behind an access point that performs every file operation
as one user, so file ownership on the volume separates nothing. Two modes:

``sandbox``
    The process gets a filesystem view that holds the system directories
    (read-only), its own release (read-only), its own data and a private tmp
    (read-write), each at its real path. Three backends build that view:

    ``bwrap``         bubblewrap run as root (needs ``CAP_SYS_ADMIN``); inside
                      it ``setpriv`` drops to the app's uid with
                      ``no_new_privs``.
    ``bwrap-userns``  ``setpriv`` drops to the app's uid first, then an
                      unprivileged bubblewrap builds the view in a user
                      namespace.
    ``landlock``      the launcher drops to the app's uid, sets
                      ``no_new_privs`` and restricts itself with Landlock.

``hidden``
    The volume sits below a directory only root can enter. The launcher
    drops to the app's uid, sets ``no_new_privs`` and starts the app
    (backend ``plain``); what it runs lives on the container's own disk,
    readable by that uid alone.

Whose environment a stage gets: an owner chooses the variables of their app,
and the dynamic loader obeys some of them (``LD_PRELOAD`` ...) in any program
that is started with them. So bubblewrap and ``setpriv`` - root, or the
app's user but still outside the app's view - are started with a fixed
environment (``STAGE_ENVIRONMENT``). The app's own environment travels in a
descriptor the launcher leaves open, never on a command line (other apps can
read those where there is no PID namespace), and is installed by the last
stage, which already is the app's user inside the app's view
(``ENV_STAGE_CODE``). Where the launcher itself is the last stage (Landlock,
plain) it hands the environment to the app's ``exec`` directly.

``LaunchSpec`` is the complete description of one process; the manager writes
it to a root-only file and ``efp_app_host.launcher`` turns it into an
``exec``. ``detect`` finds out at boot which backend works, by starting a
real probe process through the same launcher. When nothing works the host
refuses to start apps.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import ISOLATION_HIDDEN, ISOLATION_SANDBOX, Settings
from .landlock import LandlockPlan
from .paths import HostPaths
from .pyenv import base_python

BACKEND_BWRAP = "bwrap"
BACKEND_BWRAP_USERNS = "bwrap-userns"
BACKEND_LANDLOCK = "landlock"
BACKEND_PLAIN = "plain"
SANDBOX_ORDER = (BACKEND_BWRAP, BACKEND_BWRAP_USERNS, BACKEND_LANDLOCK)

DEFAULT_NOFILE = 4096
PROBE_TIMEOUT_SECONDS = 30
# Size of /dev/shm in a bubblewrap view (what Docker gives a container).
SHM_BYTES = 64 * 1024 * 1024

# What bubblewrap and setpriv are started with: nothing an owner chose.
STAGE_ENVIRONMENT = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
# Stands in an ``ExecPlan`` for the number of the descriptor that holds the
# app's environment; the launcher knows the number only when it has opened it.
ENV_DESCRIPTOR = "{env-descriptor}"
# The last stage of a bubblewrap chain, run by the host's own interpreter
# (``-I -S``: nothing of the directory it is started in, no site-packages).
# By now the process is the app's user inside the app's view. It reads the
# app's environment (JSON) from the descriptor, gives SIGTERM its default
# meaning back (the wrappers before it ignore the signal so that the app,
# which gets it through the process group, can shut down by itself) and
# becomes the app.
ENV_STAGE_CODE = "\n".join(
    [
        "import json, os, signal, sys",
        "descriptor = int(sys.argv[1])",
        "parts = []",
        "while True:",
        "    part = os.read(descriptor, 65536)",
        "    if not part:",
        "        break",
        "    parts.append(part)",
        "os.close(descriptor)",
        "signal.signal(signal.SIGTERM, signal.SIG_DFL)",
        "os.execve(sys.argv[2], sys.argv[2:], json.loads(b''.join(parts)))",
    ]
)

# Directories below /etc that hold the host's own configuration: an app sees
# them empty (bubblewrap) or not at all (Landlock).
_ETC_MASKS = ("/etc/nginx", "/etc/supervisor")
_ETC_NEVER = ("/etc/shadow", "/etc/shadow-", "/etc/gshadow", "/etc/gshadow-")
_SYSTEM_DIRS = ("usr", "bin", "sbin", "lib", "lib32", "lib64", "libx32")
_OPTIONAL_READ_ONLY = ("/opt/efp", "/sys/devices/system/cpu")


class IsolationError(RuntimeError):
    """No way to isolate apps is available; nothing may be started."""


@dataclass
class LaunchSpec:
    """One process to start: what, as whom, and what it may see."""

    argv: list[str]
    env: dict[str, str]
    cwd: str
    uid: int
    gid: int
    backend: str
    options: dict[str, Any] = field(default_factory=dict)
    ro_paths: list[str] = field(default_factory=list)
    rw_paths: list[str] = field(default_factory=list)
    tmp_dir: str = ""
    nproc: int = 256
    nofile: int = DEFAULT_NOFILE
    label: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "LaunchSpec":
        known = cls.__dataclass_fields__
        return cls(**{key: value for key, value in data.items() if key in known})


@dataclass
class SystemLayout:
    """The parts of the container's own filesystem every app may read."""

    dirs: list[str] = field(default_factory=lambda: ["/usr"])
    links: list[tuple[str, str]] = field(default_factory=list)  # (path, link target)
    etc_masks: list[str] = field(default_factory=list)
    etc_entries: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=lambda: list(_OPTIONAL_READ_ONLY))
    bwrap: str = "/usr/bin/bwrap"
    setpriv: str = "/usr/bin/setpriv"
    # The interpreter of the last stage of a bubblewrap chain (``ENV_STAGE_CODE``).
    python: str = "/usr/local/bin/python3"

    @classmethod
    def detect(cls) -> "SystemLayout":
        dirs: list[str] = []
        links: list[tuple[str, str]] = []
        for name in _SYSTEM_DIRS:
            path = "/" + name
            if os.path.islink(path):
                links.append((path, os.readlink(path)))
            elif os.path.isdir(path):
                dirs.append(path)
        # An interpreter outside the usual directories (a developer's machine).
        prefix = getattr(sys, "base_prefix", sys.prefix)
        if os.path.isdir(prefix) and not any(prefix == item or prefix.startswith(item + "/") for item in dirs):
            dirs.append(prefix)
        masks = [path for path in _ETC_MASKS if os.path.isdir(path)]
        try:
            entries = sorted("/etc/" + name for name in os.listdir("/etc"))
        except OSError:
            entries = []
        return cls(
            dirs=dirs,
            links=links,
            etc_masks=masks,
            etc_entries=[path for path in entries if path not in masks and path not in _ETC_NEVER],
            bwrap=shutil.which("bwrap") or "/usr/bin/bwrap",
            setpriv=shutil.which("setpriv") or "/usr/bin/setpriv",
            python=base_python(),
        )


@dataclass
class ExecPlan:
    """What the launcher does with a ``LaunchSpec``."""

    argv: list[str]
    # bubblewrap's own processes must outlive the stop signal; the last stage
    # gives the signal its meaning back (ENV_STAGE_CODE).
    ignore_signals: bool = False
    # The launcher itself switches to the app's user before the exec.
    drop_in_process: bool = False
    # The launcher itself sets no_new_privs (no setpriv in the chain does).
    no_new_privs: bool = False
    landlock: LandlockPlan | None = None
    chdir: str | None = None
    # ``argv`` starts with stages that must not see the app's environment:
    # they get STAGE_ENVIRONMENT, and the app's travels in a descriptor whose
    # number replaces ENV_DESCRIPTOR in ``argv``. False: ``argv`` is the app
    # itself and the launcher gives it its environment directly.
    env_by_descriptor: bool = False


def _setpriv(spec: LaunchSpec, layout: SystemLayout, current_uid: int) -> list[str]:
    if current_uid == spec.uid:
        # Already the app's user (a manager that does not run as root): there
        # is nothing to switch, only privileges to give up.
        return [layout.setpriv, "--no-new-privs", "--"]
    return [
        layout.setpriv,
        "--reuid",
        str(spec.uid),
        "--regid",
        str(spec.gid),
        "--clear-groups",
        "--no-new-privs",
        "--inh-caps=-all",
        "--",
    ]


def _env_stage(layout: SystemLayout) -> list[str]:
    """The last stage of a bubblewrap chain; the app's command line follows it."""

    return [layout.python, "-I", "-S", "-c", ENV_STAGE_CODE, ENV_DESCRIPTOR]


def _ancestors(path: str) -> list[str]:
    """Every directory above ``path``, from the top down, without ``/``."""

    parts = [part for part in path.split("/") if part]
    return ["/" + "/".join(parts[:count]) for count in range(1, len(parts))]


def _bwrap(spec: LaunchSpec, layout: SystemLayout, *, user_namespace: bool) -> list[str]:
    args = [layout.bwrap, "--die-with-parent", "--unshare-ipc"]
    if user_namespace:
        args += ["--unshare-user"]
    if spec.options.get("proc", "new") == "new":
        args += ["--unshare-pid", "--proc", "/proc"]
    else:
        # A fresh /proc cannot be mounted from a user namespace when the
        # container's own /proc is partly masked; show the existing one.
        args += ["--ro-bind", "/proc", "/proc"]
    # bubblewrap's /dev/shm is a plain directory of its own user: unusable for
    # the app when bubblewrap runs as root, unbounded when it runs as the app.
    # A tmpfs of its own, open like the usual one and with a ceiling.
    args += ["--dev", "/dev", "--perms", "1777", "--size", str(SHM_BYTES), "--tmpfs", "/dev/shm"]

    made: set[str] = set()  # directories of the view that exist by now
    mounted: list[str] = []  # places whose content comes from outside the view

    def lead_to(path: str) -> list[str]:
        """``--dir`` for every directory above ``path`` that the view lacks.

        bubblewrap makes the missing parents of a mount point by itself, but
        closed to everybody except its own user (mode 0700 for a bind mount).
        That user is root when bubblewrap runs as root: the app, which is
        somebody else by then, could not walk down to its own directory.
        ``--dir`` makes them 0755.
        """

        out: list[str] = []
        for directory in _ancestors(path):
            if directory in made or any(directory == mount or directory.startswith(mount + "/") for mount in mounted):
                continue
            made.add(directory)
            out += ["--dir", directory]
        return out

    def bind(option: str, source: str, target: str) -> list[str]:
        out = lead_to(target) + [option, source, target]
        mounted.append(target)
        return out

    for path in layout.dirs:
        args += bind("--ro-bind", path, path)
    for path, target in layout.links:
        args += lead_to(path) + ["--symlink", target, path]
    args += bind("--ro-bind", "/etc", "/etc")
    for path in layout.etc_masks:
        args += ["--tmpfs", path]
    for path in layout.optional:
        args += bind("--ro-bind-try", path, path)
    for path in ("/run", "/var"):
        if path not in made:
            made.add(path)
            args += ["--dir", path]
    if spec.tmp_dir:
        args += bind("--bind", spec.tmp_dir, "/tmp") + bind("--bind", spec.tmp_dir, spec.tmp_dir)
    else:
        args += ["--perms", "1777", "--tmpfs", "/tmp"]
    for path in spec.ro_paths:
        args += bind("--ro-bind", path, path)
    for path in spec.rw_paths:
        args += bind("--bind", path, path)
    # Everything is in place: close what bubblewrap made itself. The root of
    # the view, the directories that lead to the app's own and /dev are
    # tmpfs that belong to whoever runs bubblewrap - in a user namespace that
    # is the app, which could otherwise write there (into memory nobody
    # counts). Not recursive: data, tmp and /dev/shm stay writable.
    for path in layout.etc_masks:
        args += ["--remount-ro", path]
    args += ["--remount-ro", "/dev", "--remount-ro", "/"]
    args += ["--chdir", spec.cwd, "--"]
    return args


def build_exec_plan(spec: LaunchSpec, layout: SystemLayout, *, current_uid: int = 0) -> ExecPlan:
    """Turn a spec into the command the launcher ``exec``s.

    ``current_uid`` is the user the launcher runs as: root in the container.
    """

    if not spec.argv:
        raise IsolationError("nothing to run")
    if spec.uid <= 0 or spec.gid <= 0:
        raise IsolationError("an app never runs as root")
    if spec.backend == BACKEND_BWRAP:
        if current_uid != 0:
            raise IsolationError("the bwrap backend needs the launcher to run as root")
        argv = _bwrap(spec, layout, user_namespace=False) + _setpriv(spec, layout, current_uid) + _env_stage(layout) + list(spec.argv)
        return ExecPlan(argv=argv, ignore_signals=True, env_by_descriptor=True)
    if spec.backend == BACKEND_BWRAP_USERNS:
        argv = _setpriv(spec, layout, current_uid) + _bwrap(spec, layout, user_namespace=True) + _env_stage(layout) + list(spec.argv)
        return ExecPlan(argv=argv, ignore_signals=True, env_by_descriptor=True)
    if spec.backend == BACKEND_LANDLOCK:
        plan = LandlockPlan(
            read_exec=list(layout.dirs) + list(layout.etc_entries) + list(layout.optional) + ["/proc"] + list(spec.ro_paths),
            read_write=list(spec.rw_paths) + ([spec.tmp_dir] if spec.tmp_dir else []) + ["/dev/shm"],
            devices=["/dev"],
        )
        return ExecPlan(argv=list(spec.argv), drop_in_process=current_uid != spec.uid, no_new_privs=True, landlock=plan, chdir=spec.cwd)
    if spec.backend == BACKEND_PLAIN:
        # No setpriv: it would be root with the app's environment for a
        # moment. The launcher drops to the app's user itself, as it does
        # for Landlock, and the app is the first program to see the
        # environment.
        return ExecPlan(argv=list(spec.argv), drop_in_process=current_uid != spec.uid, no_new_privs=True, chdir=spec.cwd)
    raise IsolationError(f"unknown isolation backend {spec.backend!r}")


# ---- detection -----------------------------------------------------------------


@dataclass
class IsolationStatus:
    """The outcome of ``detect``: the backend every launch uses, or why there is none."""

    mode: str
    backend: str | None = None
    options: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    attempts: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.backend is not None and self.error is None

    @property
    def sandbox_backend(self) -> str | None:
        """What ``GET /v1/host`` reports: only sandbox mode has a backend."""

        return self.backend if self.mode == ISOLATION_SANDBOX and self.ok else None


# The probe: runs as the probe uid inside the isolation under test. It must be
# that uid, read its own directory, write its tmp, and neither read nor list
# the directory that stands for another app.
PROBE_SCRIPT = r"""
set -u
inside="$1"; outside="$2"; want_uid="$3"
uid="$(id -u)"
if [ "$uid" != "$want_uid" ]; then echo "runs as uid $uid, expected $want_uid"; exit 40; fi
if ! cat "$inside/marker" >/dev/null; then echo "cannot read its own directory"; exit 41; fi
if cat "$outside/marker" >/dev/null 2>&1; then echo "can read a file outside its directory"; exit 42; fi
if ls "$outside" >/dev/null 2>&1; then echo "can list a directory outside its directory"; exit 43; fi
if ! touch "$TMPDIR/probe"; then echo "cannot write its private tmp"; exit 44; fi
if [ -n "${4:-}" ] && ! touch "$4/probe"; then echo "cannot write its data directory"; exit 45; fi
echo "probe ok"
"""

Runner = Callable[[LaunchSpec, float], "tuple[int, str]"]

# bubblewrap run by root keeps root's capabilities for what it starts, and
# setpriv inside the view needs CAP_SETUID to become the app's user. Where
# that fails although the container has the capability, something outside
# the container took it away: on Ubuntu 24.04 and later the node's AppArmor
# policy attaches a profile to /usr/bin/bwrap whose children may use no
# capability at all (docs/OPERATIONS.md, "Nodes with AppArmor").
APPARMOR_HINT = (
    " (root in the view may not use its capabilities: on nodes with AppArmor the node's own profile for"
    " bwrap does this to an unconfined container; give the container the profile k8s/apparmor/efp-app-host)"
)


def launcher_command(spec_file: str) -> list[str]:
    return [sys.executable, "-m", "efp_app_host.launcher", spec_file]


def run_spec(spec: LaunchSpec, timeout: float, *, spec_dir: Path) -> tuple[int, str]:
    """Run a spec to completion through the launcher; returns (exit code, output)."""

    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = spec_dir / f"probe-{os.getpid()}-{spec.backend}.json"
    fd = os.open(spec_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(spec.to_json(), handle)
    try:
        done = subprocess.run(
            launcher_command(str(spec_file)),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONPATH": os.pathsep.join(sys.path)},
            start_new_session=True,
        )
        return done.returncode, done.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        return 124, ((exc.stdout or b"").decode("utf-8", "replace") + "\nthe probe timed out").strip()
    except OSError as exc:
        return 127, f"the launcher could not be started: {exc}"
    finally:
        try:
            os.unlink(spec_file)
        except OSError:
            pass


def _chown(path: Path, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        # EFS behind an access point owns every file itself.
        pass


def _variants(backend: str) -> list[dict[str, Any]]:
    if backend == BACKEND_BWRAP_USERNS:
        return [{"proc": "new"}, {"proc": "bind"}]
    if backend == BACKEND_BWRAP:
        return [{"proc": "new"}]
    return [{}]


def _summary(output: str) -> str:
    lines = [line.strip() for line in output.strip().splitlines() if line.strip()]
    return " | ".join(lines[-3:])[:400] if lines else "no output"


def detect(settings: Settings, paths: HostPaths, *, runner: Runner | None = None, platform: str | None = None) -> IsolationStatus:
    """Find the isolation to use. Never raises: a failure is in ``error``."""

    mode = settings.isolation_mode
    status = IsolationStatus(mode=mode)
    if (platform or sys.platform) != "linux":
        status.error = "app isolation needs Linux"
        return status
    if runner is None:
        def runner(spec: LaunchSpec, timeout: float) -> tuple[int, str]:
            return run_spec(spec, timeout, spec_dir=paths.run / "launch")

    probe_uid = max(settings.uid_range_start - 1, 1)
    store_probe = paths.store / f".probe-{os.getpid()}"
    local_probe = paths.runtime / f".probe-{os.getpid()}"
    try:
        for base in (store_probe, local_probe):
            shutil.rmtree(base, ignore_errors=True)
        inside = (local_probe if mode == ISOLATION_HIDDEN else store_probe) / "inside"
        outside = store_probe / "outside"
        data = store_probe / "data"
        tmp = local_probe / "tmp"
        for directory in (inside, outside, data, tmp):
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o755)
        for directory in (inside, outside):
            with open(directory / "marker", "w", encoding="utf-8") as handle:
                handle.write("efp\n")
            os.chmod(directory / "marker", 0o644)
        for directory in (tmp, data):
            _chown(directory, probe_uid, probe_uid)
            os.chmod(directory, 0o700)

        def attempt(backend: str, options: dict[str, Any]) -> tuple[bool, str]:
            sandboxed = backend != BACKEND_PLAIN
            argv = ["/bin/sh", "-c", PROBE_SCRIPT, "probe", str(inside), str(outside), str(probe_uid)]
            if sandboxed:
                argv.append(str(data))
            spec = LaunchSpec(
                argv=argv,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "TMPDIR": str(tmp), "HOME": str(tmp)},
                cwd=str(inside),
                uid=probe_uid,
                gid=probe_uid,
                backend=backend,
                options=dict(options),
                ro_paths=[str(inside)] if sandboxed else [],
                rw_paths=[str(data)] if sandboxed else [],
                tmp_dir=str(tmp),
                nproc=64,
                label="probe",
            )
            code, output = runner(spec, PROBE_TIMEOUT_SECONDS)
            message = _summary(output) if code else "ok"
            if code and backend == BACKEND_BWRAP and "setresuid failed" in output:
                message += APPARMOR_HINT
            return code == 0, message

        if mode == ISOLATION_HIDDEN:
            ok, message = attempt(BACKEND_PLAIN, {})
            status.attempts[BACKEND_PLAIN] = message
            if ok:
                status.backend = BACKEND_PLAIN
            else:
                status.error = (
                    "hidden mode is not safe here: " + message + ". The volume must sit below a directory "
                    "that only root can enter (the parent of EFP_HOST_STORE_DIR)."
                )
            return status

        wanted = SANDBOX_ORDER if settings.sandbox_backend == "auto" else (settings.sandbox_backend,)
        for backend in wanted:
            for options in _variants(backend):
                ok, message = attempt(backend, options)
                status.attempts[backend] = message
                if ok:
                    status.backend = backend
                    status.options = dict(options)
                    return status
        tried = "; ".join(f"{name}: {message}" for name, message in status.attempts.items())
        status.error = f"no sandbox backend works ({tried})"
        return status
    except OSError as exc:
        status.error = f"the isolation probe could not run: {exc}"
        return status
    finally:
        for base in (store_probe, local_probe):
            shutil.rmtree(base, ignore_errors=True)
