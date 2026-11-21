"""Running a ``LaunchSpec`` as a child of the manager.

Used by deployments (virtual environment, pip, the health-check candidate)
and by the restore of hidden mode. Every command goes through the launcher,
so it runs as the app's user inside the app's isolation, never as the
manager. Output is handed on in whole lines, as many at once as arrived
together: a process that prints as fast as it can costs the manager one
call per read, not one per line. Output that is kept in memory (``capture``)
has a ceiling. A command that outlives its timeout, or whose caller is
cancelled, is killed with its whole process group.
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
# What ``capture`` keeps at most. The one command whose output is captured
# prints a list of package names; the process that prints it is the app's.
MAX_CAPTURE_BYTES = 1024 * 1024
_LONG_LINE_BYTES = 256 * 1024


class CommandTimeout(Exception):
    """The command did not finish in time and was killed."""


@dataclass
class RunResult:
    code: int
    output: str = ""
    # ``capture``: the command printed more than MAX_CAPTURE_BYTES; ``output`` is only the start.
    truncated: bool = False


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
    """Turns what a process writes into text that ends at a line break.

    Each call of the sink gets every complete line that has arrived, in one
    piece. With ``keep`` the text is collected instead, up to ``limit``
    bytes; ``truncated`` says that more came.
    """

    def __init__(self, sink: LogSink | None, keep: list[str] | None, *, limit: int = MAX_CAPTURE_BYTES):
        self._sink = sink
        self._keep = keep
        self._limit = limit
        self._kept = 0
        self._pending = b""
        self.truncated = False

    def feed(self, data: bytes) -> None:
        self._pending += data
        cut = self._pending.rfind(b"\n")
        if cut >= 0:
            ready, self._pending = self._pending[: cut + 1], self._pending[cut + 1 :]
            self._emit(ready)
        if len(self._pending) > _LONG_LINE_BYTES:
            self._emit(self._pending)
            self._pending = b""

    def close(self) -> None:
        if self._pending:
            self._emit(self._pending)
            self._pending = b""

    def _emit(self, raw: bytes) -> None:
        text = raw.decode("utf-8", "replace").replace("\r\n", "\n")
        if text.endswith("\r"):
            text = text[:-1]
        if not text.endswith("\n"):
            text += "\n"
        if self._keep is not None:
            # Once something was dropped nothing more is kept: what is kept has no hole.
            if self.truncated or self._kept + len(text) > self._limit:
                self.truncated = True
                return
            self._kept += len(text)
            self._keep.append(text)
        elif self._sink is not None:
            self._sink(text)


class _Process:
    def __init__(self, process: asyncio.subprocess.Process, pump: "asyncio.Task[None]", spec_file: Path, buffer: _LineBuffer):
        self._process = process
        self._pump = pump
        self._spec_file = spec_file
        self._buffer = buffer

    @property
    def truncated(self) -> bool:
        return self._buffer.truncated

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
            import signal

            assert process.stdout is not None
            stopped = False
            try:
                while True:
                    data = await process.stdout.read(65536)
                    if not data:
                        break
                    buffer.feed(data)
                    if buffer.truncated and not stopped:
                        # Nothing more of it will be kept: no reason to let it go on printing.
                        stopped = True
                        _signal_group(process.pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            finally:
                buffer.close()

        return _Process(process, asyncio.get_running_loop().create_task(pump()), spec_file, buffer)

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
        return RunResult(code=code, output="".join(keep or []), truncated=process.truncated)

    async def spawn(self, spec: LaunchSpec, *, log: LogSink | None = None) -> Spawned:
        return await self._start(spec, log, None)
