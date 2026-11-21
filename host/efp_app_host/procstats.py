"""What the processes and disks are doing: memory, CPU, sizes.

Each app runs as its own OS user, so everything a uid owns is that app:
its server, the workers it forked and anything that detached itself.
"""

from __future__ import annotations

import logging
import os
import shutil
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


@dataclass
class UidUsage:
    rss_bytes: int = 0
    cpu_seconds: float = 0.0
    pids: list[int] = field(default_factory=list)


class ProcessSampler(Protocol):
    def sample(self) -> dict[int, UidUsage]:
        """Resident memory and CPU time of every non-root user's processes, by uid."""

    def kill_uid(self, uid: int) -> int:
        """Kill everything the uid owns, and do not return while any of it lives.

        Returns how many processes were hit.
        """

    def busy_ports(self) -> set[int]:
        """Local TCP ports that have an established connection right now.

        An app listens on its own loopback port, so a connection whose local
        port is that port is one the app accepted: a request in flight or a
        WebSocket. The gateway keeps no idle connections to apps.
        """

    def listener_uids(self, port: int) -> "set[int] | None":
        """The users whose processes would accept a connection to 127.0.0.1:``port``.

        ``None`` when that cannot be told, and where the question has no
        answer: a manager that is not root runs every app as itself.
        """


# The addresses a connection to 127.0.0.1 can arrive at, the most specific
# first. Two users cannot listen on the same one, and the kernel hands a
# connection to the most specific that is taken: a listener on another
# address (127.0.0.2, or IPv6 only) never gets what the gateway sends to an
# app, so it is not looked at.
_LOOPBACK_LISTEN_ORDER = ("127.0.0.1", "::ffff:127.0.0.1", "0.0.0.0", "::")
# The kernel's own list of TCP sockets: one line per socket of the network
# the manager and the apps share, with the uid of whoever made the socket.
# Reading it needs no right over other users' processes. The way through
# /proc/<pid>/fd (what psutil takes to name a socket's process) needs
# SYS_PTRACE, which the host's container does not have: asked that way,
# the manager was never told whose listener it is.
_TCP_TABLES = ("/proc/net/tcp", "/proc/net/tcp6")
_TCP_LISTEN = "0A"
_LOOPBACK_LISTEN_BYTES = {
    b"\x7f\x00\x00\x01": "127.0.0.1",
    b"\x00" * 10 + b"\xff\xff\x7f\x00\x00\x01": "::ffff:127.0.0.1",
    b"\x00" * 4: "0.0.0.0",
    b"\x00" * 16: "::",
}
# One round of kill_uid: a fresh look at what the user owns, then the kill.
KILL_ROUNDS = 40
KILL_SETTLE_SECONDS = 0.05


def kill_all_of(uid: int) -> bool:
    """SIGKILL every process of ``uid`` in one system call. False when that could not be done.

    A child becomes the user and calls ``kill(-1)``: the kernel signals
    everything that user may signal while nothing can be forked, so a
    process that keeps forking to stay ahead of a list of pids is hit too.
    Needs root; the child signals nobody else, because by then it is no
    longer root, and never itself.

    Once the child is the user, that user's processes can signal it (where
    they share the manager's PID namespace: hidden mode, Landlock, bubblewrap
    without a PID namespace). Killing it only costs the round. Stopping it
    would leave the manager waiting for a child that never ends, so a child
    that was stopped is killed and the call reports that nothing was done.
    """

    import signal

    fork = getattr(os, "fork", None)
    if fork is None or uid <= 0 or getattr(os, "geteuid", lambda: 1)() != 0:
        return False
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    try:
        pid = fork()
    except OSError:
        return False
    if pid == 0:
        # The child: nothing but system calls, then gone.
        code = 1
        try:
            # No handler of the manager's runs in here, whatever is sent.
            signal.pthread_sigmask(signal.SIG_BLOCK, signal.valid_signals())
            os.setgroups([])
            os.setgid(uid)
            os.setuid(uid)
            if os.getuid() == uid and os.geteuid() == uid:
                try:
                    os.kill(-1, sigkill)
                except ProcessLookupError:
                    pass  # nothing of that user was running
                code = 0
        except BaseException:  # noqa: BLE001 - whatever it is, this process must not go on as the manager
            code = 1
        finally:
            os._exit(code)
    while True:
        try:
            # WUNTRACED: also come back when the child was stopped.
            _, status = os.waitpid(pid, os.WUNTRACED)
        except OSError:
            return False
        if not os.WIFSTOPPED(status):
            break
        try:
            os.kill(pid, sigkill)
        except OSError:
            pass
    # A process of the user may have killed the child before it could act.
    return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0


def parse_tcp_table(text: str) -> "list[tuple[str, int, int]] | None":
    """The listening sockets in the text of ``/proc/net/tcp`` or ``tcp6``.

    One ``(address, port, uid)`` for every socket that listens on an address
    a connection to 127.0.0.1 can arrive at. ``None`` when a line is not
    what this code knows: then nothing is said, instead of something wrong.
    """

    rows: list[tuple[str, int, int]] = []
    for line in text.splitlines()[1:]:
        fields = line.split()
        if not fields:
            continue
        # sl, local address, remote address, state, queues, timer, retransmits, uid, ...
        if len(fields) < 8:
            return None
        if fields[3] != _TCP_LISTEN:
            continue
        raw, _, port = fields[1].rpartition(":")
        try:
            if len(raw) not in (8, 32):
                raise ValueError(raw)
            # An address is printed as 32-bit words in the machine's own byte order.
            packed = b"".join(struct.pack("=I", int(raw[index : index + 8], 16)) for index in range(0, len(raw), 8))
            address = _LOOPBACK_LISTEN_BYTES.get(packed)
            if address is not None:
                rows.append((address, int(port, 16), int(fields[7], 10)))
        except (ValueError, struct.error):
            return None
    return rows


def proc_listeners() -> "list[tuple[str, int, int]] | None":
    """Every loopback listener the kernel lists; ``None`` where it has no such list."""

    rows: list[tuple[str, int, int]] = []
    for index, path in enumerate(_TCP_TABLES):
        try:
            with open(path, encoding="ascii", errors="replace") as handle:
                text = handle.read()
        except OSError:
            if index == 0:
                return None  # not Linux, or no /proc
            continue  # a kernel without IPv6
        table = parse_tcp_table(text)
        if table is None:
            return None
        rows.extend(table)
    return rows


class PsutilSampler:
    def listener_uids(self, port: int) -> "set[int] | None":
        # Apps have users of their own only under a manager that is root.
        if getattr(os, "geteuid", lambda: 1)() != 0:
            return None
        port = int(port)
        listeners: "list[tuple[str, int, int | None]] | None" = proc_listeners()
        if listeners is None:
            listeners = self._psutil_listeners(port)
        if listeners is None:
            return None
        by_address: dict[str, set[int]] = {}
        unknown = False
        for address, listening_on, uid in listeners:
            if listening_on != port or address not in _LOOPBACK_LISTEN_ORDER:
                continue
            if uid is None:
                unknown = True
                continue
            by_address.setdefault(address, set()).add(uid)
        for address in _LOOPBACK_LISTEN_ORDER:
            if address in by_address:
                return by_address[address]
        return None if unknown else set()

    @staticmethod
    def _psutil_listeners(port: int) -> "list[tuple[str, int, int | None]] | None":
        """The listeners on ``port`` as psutil sees them, where the kernel's list cannot be read.

        A socket whose process cannot be named has ``None`` for its uid:
        not known, rather than "nobody".
        """

        import psutil

        try:
            connections = psutil.net_connections(kind="tcp")
        except Exception:  # noqa: BLE001 - AccessDenied and friends: nothing is known
            return None
        rows: list[tuple[str, int, int | None]] = []
        for connection in connections:
            try:
                if connection.status != psutil.CONN_LISTEN or not connection.laddr or int(connection.laddr.port) != port:
                    continue
                address = str(connection.laddr.ip)
            except (AttributeError, TypeError, ValueError):
                continue
            if not connection.pid:
                rows.append((address, port, None))
                continue
            try:
                rows.append((address, port, int(psutil.Process(connection.pid).uids().real)))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, AttributeError):
                continue
        return rows

    def busy_ports(self) -> set[int]:
        import psutil

        try:
            connections = psutil.net_connections(kind="tcp")
        except Exception:  # noqa: BLE001 - AccessDenied and friends: nothing is known, nothing is assumed
            return set()
        ports: set[int] = set()
        for connection in connections:
            if connection.status != psutil.CONN_ESTABLISHED or not connection.laddr or not connection.raddr:
                continue
            try:
                ports.add(int(connection.laddr.port))
            except (AttributeError, TypeError, ValueError):
                continue
        return ports

    def sample(self) -> dict[int, UidUsage]:
        import psutil

        usage: dict[int, UidUsage] = {}
        for process in psutil.process_iter():
            try:
                uids = getattr(process, "uids", None)
                if uids is None:  # not a POSIX system
                    return {}
                uid = uids().real
                if uid == 0:
                    continue
                entry = usage.setdefault(uid, UidUsage())
                entry.rss_bytes += int(process.memory_info().rss)
                times = process.cpu_times()
                entry.cpu_seconds += float(times.user + times.system)
                entry.pids.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return usage

    def pids_of(self, uid: int) -> list[int]:
        """The processes of ``uid`` that can still run code (a zombie cannot)."""

        import psutil

        found: list[int] = []
        for process in psutil.process_iter():
            try:
                uids = getattr(process, "uids", None)
                if uids is None or uids().real != uid:
                    continue
                if process.status() == psutil.STATUS_ZOMBIE:
                    continue
                found.append(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return found

    def signal_pid(self, pid: int) -> None:
        import signal

        try:
            os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def kill_all(self, uid: int) -> bool:
        return kill_all_of(uid)

    def kill_uid(self, uid: int) -> int:
        """Kill everything the uid owns; come back when a fresh look finds nothing.

        One walk over a list of pids is not enough: a process that forks
        and lets its parent exit has a new pid before the walk reaches the
        old one, and stays. So every round kills what it sees by pid (that
        gets whatever sits still) and then lets the kernel kill everything
        of the user at once (that gets whatever keeps forking), until a
        look finds nothing left.
        """

        if uid <= 0:
            return 0
        hit: set[int] = set()
        for _ in range(KILL_ROUNDS):
            pids = self.pids_of(uid)
            if not pids:
                return len(hit)
            hit.update(pids)
            for pid in pids:
                self.signal_pid(pid)
            self.kill_all(uid)
            time.sleep(KILL_SETTLE_SECONDS)
        remaining = self.pids_of(uid)
        if remaining:
            logger.error("processes of uid %s survived %s rounds of SIGKILL: %s", uid, KILL_ROUNDS, remaining[:20])
        return len(hit)


class CpuMeter:
    """Turns running totals of CPU seconds into a percentage between samples."""

    def __init__(self) -> None:
        self._last: dict[int, tuple[float, float]] = {}

    def percent(self, key: int, cpu_seconds: float, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        previous = self._last.get(key)
        self._last[key] = (now, cpu_seconds)
        if previous is None:
            return 0.0
        elapsed = now - previous[0]
        if elapsed <= 0 or cpu_seconds < previous[1]:
            return 0.0
        return round(100.0 * (cpu_seconds - previous[1]) / elapsed, 1)

    def forget(self, key: int) -> None:
        self._last.pop(key, None)


def dir_size(path: Path) -> int:
    """Bytes held by the files below ``path`` (links are not followed)."""

    total = 0
    stack = [str(path)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        else:
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def disk_usage(path: Path) -> dict[str, int]:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return {"total_bytes": 0, "used_bytes": 0}
    return {"total_bytes": int(usage.total), "used_bytes": int(usage.used)}


def free_bytes(path: Path) -> int | None:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    try:
        with open(path, "r", encoding="ascii") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def memory_usage() -> dict[str, int]:
    """Memory of the container (its cgroup) when that is limited, else of the machine."""

    import psutil

    machine = psutil.virtual_memory()
    total, used = int(machine.total), int(machine.total - machine.available)
    for limit_file, usage_file in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        limit, current = _read_int(limit_file), _read_int(usage_file)
        if limit is not None and current is not None and 0 < limit < total:
            return {"total_bytes": limit, "used_bytes": current}
    return {"total_bytes": total, "used_bytes": used}


def cpu_percent() -> float:
    import psutil

    return float(psutil.cpu_percent(interval=None))


def host_numbers(store: Path, local: Path) -> dict[str, Any]:
    return {
        "memory": memory_usage(),
        "cpu_percent": cpu_percent(),
        "disk": {"store": disk_usage(store), "local": disk_usage(local)},
    }
