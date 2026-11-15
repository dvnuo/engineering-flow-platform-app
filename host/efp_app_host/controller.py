"""Process control: one supervised program per app.

``ProcessController`` is what the runtime needs from a process supervisor.
``SupervisorController`` speaks to supervisord over XML-RPC on its unix
socket; ``FakeController`` keeps everything in memory for tests.

supervisord only starts, stops and reaps. It never restarts an app by itself
(``autorestart=false``): crashes, back-off, health and idle stop are decided
by the runtime, which is the only place that knows why a process should or
should not be running.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

PROC_ABSENT = "absent"  # no such program
PROC_STOPPED = "stopped"
PROC_STARTING = "starting"
PROC_RUNNING = "running"
PROC_STOPPING = "stopping"
PROC_EXITED = "exited"
PROC_FATAL = "fatal"  # could not be spawned
PROC_UNKNOWN = "unknown"

ALIVE_STATES = (PROC_STARTING, PROC_RUNNING)

_SUPERVISOR_STATES = {
    "STOPPED": PROC_STOPPED,
    "STARTING": PROC_STARTING,
    "RUNNING": PROC_RUNNING,
    "BACKOFF": PROC_FATAL,
    "STOPPING": PROC_STOPPING,
    "EXITED": PROC_EXITED,
    "FATAL": PROC_FATAL,
    "UNKNOWN": PROC_UNKNOWN,
}

# supervisor.xmlrpc.Faults
FAULT_BAD_NAME = 10
FAULT_ALREADY_STARTED = 60
FAULT_NOT_RUNNING = 70
FAULT_ALREADY_ADDED = 90
FAULT_STILL_RUNNING = 91


class ControllerError(RuntimeError):
    """The supervisor refused or could not be reached."""


@dataclass
class ProgramSpec:
    name: str
    command: list[str]
    log_file: str
    stop_wait_seconds: int = 10
    log_max_bytes: int = 10 * 1024 * 1024
    log_backups: int = 2


@dataclass
class ProcessInfo:
    name: str
    state: str
    pid: int = 0
    exit_status: int | None = None
    started_at: float = 0.0
    stopped_at: float = 0.0
    detail: str = ""

    @property
    def alive(self) -> bool:
        return self.state in ALIVE_STATES


class ProcessController(Protocol):
    def apply(self, programs: list[ProgramSpec]) -> None:
        """Make the set of programs exactly ``programs`` (none is started)."""

    def add(self, program: ProgramSpec) -> None:
        ...

    def remove(self, name: str) -> None:
        """Stop the program and forget it."""

    def start(self, name: str) -> None:
        """Spawn the program; returns without waiting for it to come up."""

    def stop(self, name: str) -> None:
        """Stop the program and wait until it is gone."""

    def info(self, name: str) -> ProcessInfo:
        ...

    def all(self) -> dict[str, ProcessInfo]:
        ...


def program_name(app_id: str) -> str:
    return f"app-{app_id}"


# ---- supervisord ---------------------------------------------------------------


def _quote(value: str) -> str:
    """One word of a supervisor ``command=`` value."""

    value = value.replace("%", "%%")
    if value and all(ch.isalnum() or ch in "/._-=:+@%" for ch in value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_program(program: ProgramSpec) -> str:
    """The ``[program:x]`` section of one app."""

    lines = [
        f"[program:{program.name}]",
        "command=" + " ".join(_quote(part) for part in program.command),
        "directory=/",
        "autostart=false",
        "autorestart=false",
        "startsecs=0",
        "startretries=0",
        "stopsignal=TERM",
        f"stopwaitsecs={int(program.stop_wait_seconds)}",
        "stopasgroup=true",
        "killasgroup=true",
        "redirect_stderr=true",
        "stdout_logfile=" + program.log_file.replace("%", "%%"),
        f"stdout_logfile_maxbytes={int(program.log_max_bytes)}",
        f"stdout_logfile_backups={int(program.log_backups)}",
        "",
    ]
    return "\n".join(lines)


def to_process_info(name: str, raw: dict) -> ProcessInfo:
    """Map supervisor's ``getProcessInfo`` structure."""

    state = _SUPERVISOR_STATES.get(str(raw.get("statename") or ""), PROC_UNKNOWN)
    exit_status = raw.get("exitstatus")
    return ProcessInfo(
        name=name,
        state=state,
        pid=int(raw.get("pid") or 0),
        exit_status=int(exit_status) if state in (PROC_EXITED, PROC_FATAL) and exit_status is not None else None,
        started_at=float(raw.get("start") or 0),
        stopped_at=float(raw.get("stop") or 0),
        detail=str(raw.get("spawnerr") or raw.get("description") or ""),
    )


class SupervisorController:
    """supervisord over XML-RPC on a unix socket.

    ``conf_dir`` is the directory supervisord includes (``[include] files``);
    one file per program is written there.
    """

    def __init__(self, socket_path: Path, conf_dir: Path, *, timeout: float = 30.0):
        self.socket_path = Path(socket_path)
        self.conf_dir = Path(conf_dir)
        self.timeout = timeout
        self._lock = threading.Lock()

    # -- XML-RPC plumbing ---------------------------------------------------
    def _proxy(self):
        import http.client
        import socket
        import xmlrpc.client

        socket_path = str(self.socket_path)
        timeout = self.timeout

        class UnixConnection(http.client.HTTPConnection):
            def connect(self) -> None:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # type: ignore[attr-defined]
                self.sock.settimeout(timeout)
                self.sock.connect(socket_path)

        class UnixTransport(xmlrpc.client.Transport):
            def make_connection(self, host):  # noqa: ANN001 - signature of the base class
                return UnixConnection("localhost")

        return xmlrpc.client.ServerProxy("http://localhost/RPC2", transport=UnixTransport(), allow_none=True)

    def _call(self, method: str, *args, ignore: tuple[int, ...] = ()):
        import xmlrpc.client

        with self._lock:
            try:
                target = self._proxy()
                for part in method.split("."):
                    target = getattr(target, part)
                return target(*args)
            except xmlrpc.client.Fault as fault:
                if fault.faultCode in ignore:
                    return None
                raise ControllerError(f"supervisor {method}: {fault.faultString}") from None
            except (OSError, AttributeError) as exc:
                # AttributeError: a system without unix sockets.
                raise ControllerError(f"supervisor is not reachable on {self.socket_path}: {exc}") from None

    # -- configuration ------------------------------------------------------
    def _conf_file(self, name: str) -> Path:
        return self.conf_dir / f"{name}.conf"

    def _write(self, program: ProgramSpec) -> None:
        self.conf_dir.mkdir(parents=True, exist_ok=True)
        path = self._conf_file(program.name)
        text = render_program(program)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                if handle.read() == text:
                    return
        except FileNotFoundError:
            pass
        tmp = path.with_name(f".{path.name}.tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)

    def _reload(self) -> None:
        result = self._call("supervisor.reloadConfig")
        added, changed, removed = (result or [[[], [], []]])[0]
        for name in removed:
            self._call("supervisor.stopProcessGroup", name, True, ignore=(FAULT_BAD_NAME, FAULT_NOT_RUNNING))
            self._call("supervisor.removeProcessGroup", name, ignore=(FAULT_BAD_NAME,))
        for name in changed:
            self._call("supervisor.stopProcessGroup", name, True, ignore=(FAULT_BAD_NAME, FAULT_NOT_RUNNING))
            self._call("supervisor.removeProcessGroup", name, ignore=(FAULT_BAD_NAME,))
            self._call("supervisor.addProcessGroup", name, ignore=(FAULT_ALREADY_ADDED,))
        for name in added:
            self._call("supervisor.addProcessGroup", name, ignore=(FAULT_ALREADY_ADDED,))

    def apply(self, programs: list[ProgramSpec]) -> None:
        self.conf_dir.mkdir(parents=True, exist_ok=True)
        wanted = {program.name for program in programs}
        for path in self.conf_dir.glob("app-*.conf"):
            if path.stem not in wanted:
                path.unlink(missing_ok=True)
        for program in programs:
            self._write(program)
        self._reload()

    def add(self, program: ProgramSpec) -> None:
        self._write(program)
        self._reload()

    def remove(self, name: str) -> None:
        self._conf_file(name).unlink(missing_ok=True)
        self._reload()

    # -- processes ----------------------------------------------------------
    def start(self, name: str) -> None:
        self._call("supervisor.startProcess", name, False, ignore=(FAULT_ALREADY_STARTED,))

    def stop(self, name: str) -> None:
        self._call("supervisor.stopProcess", name, True, ignore=(FAULT_NOT_RUNNING, FAULT_BAD_NAME))

    def info(self, name: str) -> ProcessInfo:
        raw = self._call("supervisor.getProcessInfo", name, ignore=(FAULT_BAD_NAME,))
        if raw is None:
            return ProcessInfo(name=name, state=PROC_ABSENT)
        return to_process_info(name, raw)

    def all(self) -> dict[str, ProcessInfo]:
        result: dict[str, ProcessInfo] = {}
        for raw in self._call("supervisor.getAllProcessInfo") or []:
            name = str(raw.get("name") or "")
            if name.startswith("app-"):
                result[name] = to_process_info(name, raw)
        return result


# ---- in memory -----------------------------------------------------------------


@dataclass
class FakeController:
    """A supervisor that only remembers what it was told. For tests."""

    programs: dict[str, ProgramSpec] = field(default_factory=dict)
    infos: dict[str, ProcessInfo] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    fail_start: dict[str, str] = field(default_factory=dict)
    _next_pid: int = 4000

    def apply(self, programs: list[ProgramSpec]) -> None:
        wanted = {program.name: program for program in programs}
        for name in list(self.programs):
            if name not in wanted:
                self.remove(name)
        for program in programs:
            self.add(program)

    def add(self, program: ProgramSpec) -> None:
        self.calls.append(("add", program.name))
        self.programs[program.name] = program
        self.infos.setdefault(program.name, ProcessInfo(name=program.name, state=PROC_STOPPED))

    def remove(self, name: str) -> None:
        self.calls.append(("remove", name))
        self.programs.pop(name, None)
        self.infos.pop(name, None)

    def start(self, name: str) -> None:
        self.calls.append(("start", name))
        if name not in self.programs:
            raise ControllerError(f"supervisor startProcess: BAD_NAME: {name}")
        if name in self.fail_start:
            self.infos[name] = ProcessInfo(name=name, state=PROC_FATAL, detail=self.fail_start[name])
            return
        if self.infos[name].alive:
            return
        self._next_pid += 1
        self.infos[name] = ProcessInfo(name=name, state=PROC_RUNNING, pid=self._next_pid, started_at=time.time())

    def stop(self, name: str) -> None:
        self.calls.append(("stop", name))
        info = self.infos.get(name)
        if info is not None and info.alive:
            self.infos[name] = ProcessInfo(name=name, state=PROC_STOPPED, stopped_at=time.time())

    def info(self, name: str) -> ProcessInfo:
        return self.infos.get(name) or ProcessInfo(name=name, state=PROC_ABSENT)

    def all(self) -> dict[str, ProcessInfo]:
        return dict(self.infos)

    # -- what a test does to a process -------------------------------------
    def exit(self, name: str, code: int = 1) -> None:
        self.infos[name] = ProcessInfo(name=name, state=PROC_EXITED, exit_status=code, stopped_at=time.time())

    def started(self, name: str) -> int:
        return sum(1 for call in self.calls if call == ("start", name))
