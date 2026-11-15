"""Running a ``LaunchSpec`` as a child of the manager.

Used by deployments (virtual environment, pip, the health-check candidate)
and by the restore of hidden mode. Every command goes through the launcher,
so it runs as the app's user inside the app's isolation, never as the
manager. Output is handed on line by line; a command that outlives its
timeout, or whose caller is cancelled, is killed with its whole process
group.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .isolation import LaunchSpec, launcher_command

LogSink = Callable[[str], None]
KILL_GRACE_SECONDS = 5.0


class CommandTimeout(Exception):
    """The command did not finish in time and was killed."""


@dataclass
class RunResult:
    code: int
    output: str = ""


class Spawned(Protocol):
    @property
    def returncode(self) -> int | None:
        """``None`` while the process lives."""

    async def stop(self) -> None:
        ...


class CommandRunner(Protocol):
    async def run(self, spec: LaunchSpec, *, log: LogSink | None = None, timeout: float | None = None, capture: bool = False) -> RunResult:
        """Run to completion. With ``capture`` the output is returned instead of logged."""

    async def spawn(self, spec: LaunchSpec, *, log: LogSink | None = None) -> Spawned:
        """Start and return at once; the caller stops it."""


class _LineBuffer:
    def __init__(self, sink: LogSink | None, keep: list[str] | None):
        self._sink = sink
        self._keep = keep
        self._pending = b""

    def feed(self, data: bytes) -> None:
        self._pending += data
        while b"\n" in self._pending:
            raw, self._pending = self._pending.split(b"\n", 1)
            self._emit(raw)
        if len(self._pending) > 256 * 1024:
            self._emit(self._pending)
            self._pending = b""

    def close(self) -> None:
        if self._pending:
            self._emit(self._pending)
            self._pending = b""

    def _emit(self, raw: bytes) -> None:
        line = raw.decode("utf-8", "replace").rstrip("\r") + "\n"
        if self._keep is not None:
            self._keep.append(line)
        elif self._sink is not None:
            self._sink(line)


class _Process:
    def __init__(self, process: asyncio.subprocess.Process, pump: "asyncio.Task[None]", spec_file: Path):
        self._process = process
        self._pump = pump
        self._spec_file = spec_file

    def _forget_spec(self) -> None:
        # The launcher removes the file as soon as it has read it; this only
        # matters when the launcher never got that far.
        try:
            self._spec_file.unlink()
        except OSError:
            pass

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def pid(self) -> int:
        return self._process.pid

    async def wait(self) -> int:
        code = await self._process.wait()
        await asyncio.gather(self._pump, return_exceptions=True)
        self._forget_spec()
        return code

    async def stop(self) -> None:
        import signal

        if self._process.returncode is None:
            _signal_group(self._process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self._process.wait(), KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                _signal_group(self._process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
                await self._process.wait()
        self._pump.cancel()
        await asyncio.gather(self._pump, return_exceptions=True)
        self._forget_spec()


def _signal_group(pid: int, sig: int) -> None:
    try:
        os.killpg(pid, sig)  # type: ignore[attr-defined]
    except (ProcessLookupError, PermissionError, AttributeError):
        pass


class LauncherRunner:
    def __init__(self, spec_dir: Path):
        self.spec_dir = Path(spec_dir)

    def _write_spec(self, spec: LaunchSpec) -> Path:
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        path = self.spec_dir / f"job-{uuid.uuid4().hex}.json"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(spec.to_json(), handle)
        return path

    async def _start(self, spec: LaunchSpec, log: LogSink | None, keep: list[str] | None) -> _Process:
        import sys

        spec_file = await asyncio.to_thread(self._write_spec, spec)
        try:
            process = await asyncio.create_subprocess_exec(
                *launcher_command(str(spec_file)),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "PYTHONPATH": os.pathsep.join(sys.path)},
                start_new_session=True,
            )
        except BaseException:
            spec_file.unlink(missing_ok=True)
            raise
        buffer = _LineBuffer(log, keep)

        async def pump() -> None:
            assert process.stdout is not None
            try:
                while True:
                    data = await process.stdout.read(65536)
                    if not data:
                        break
                    buffer.feed(data)
            finally:
                buffer.close()

        return _Process(process, asyncio.get_running_loop().create_task(pump()), spec_file)

    async def run(self, spec: LaunchSpec, *, log: LogSink | None = None, timeout: float | None = None, capture: bool = False) -> RunResult:
        keep: list[str] | None = [] if capture else None
        process = await self._start(spec, log, keep)
        try:
            code = await asyncio.wait_for(process.wait(), timeout)
        except asyncio.TimeoutError:
            await process.stop()
            raise CommandTimeout(f"the command did not finish within {int(timeout or 0)} seconds") from None
        except BaseException:
            await asyncio.shield(process.stop())
            raise
        return RunResult(code=code, output="".join(keep or []))

    async def spawn(self, spec: LaunchSpec, *, log: LogSink | None = None) -> Spawned:
        return await self._start(spec, log, None)
