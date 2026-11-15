"""What the processes and disks are doing: memory, CPU, sizes.

Each app runs as its own OS user, so everything a uid owns is that app:
its server, the workers it forked and anything that detached itself.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class UidUsage:
    rss_bytes: int = 0
    cpu_seconds: float = 0.0
    pids: list[int] = field(default_factory=list)


class ProcessSampler(Protocol):
    def sample(self) -> dict[int, UidUsage]:
        """Resident memory and CPU time of every non-root user's processes, by uid."""

    def kill_uid(self, uid: int) -> int:
        """Kill everything the uid owns. Returns how many processes were hit."""

    def busy_ports(self) -> set[int]:
        """Local TCP ports that have an established connection right now.

        An app listens on its own loopback port, so a connection whose local
        port is that port is one the app accepted: a request in flight or a
        WebSocket. The gateway keeps no idle connections to apps.
        """


class PsutilSampler:
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

    def kill_uid(self, uid: int) -> int:
        import psutil
        import signal

        if uid <= 0:
            return 0
        hit = 0
        for process in psutil.process_iter():
            try:
                uids = getattr(process, "uids", None)
                if uids is None or uids().real != uid:
                    continue
                os.kill(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                hit += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, ProcessLookupError, PermissionError):
                continue
        return hit


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
