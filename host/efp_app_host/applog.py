"""Reading an app's log: the last lines, and following it as it grows."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import AsyncIterator, Callable

_BLOCK = 64 * 1024
MAX_TAIL_LINES = 5000
MAX_LINE_CHARS = 8000
KEEPALIVE_SECONDS = 15.0


def _decode(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace").rstrip("\r")
    return text if len(text) <= MAX_LINE_CHARS else text[:MAX_LINE_CHARS] + " ..."


def tail_lines(path: Path, count: int) -> tuple[list[str], bool]:
    """The last ``count`` lines of the file and whether older lines exist."""

    count = max(0, min(int(count), MAX_TAIL_LINES))
    if count == 0:
        return [], False
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            position = handle.tell()
            chunks: list[bytes] = []
            newlines = 0
            while position > 0 and newlines <= count:
                step = min(_BLOCK, position)
                position -= step
                handle.seek(position)
                chunk = handle.read(step)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
    except OSError:
        return [], False
    data = b"".join(reversed(chunks))
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    truncated = position > 0 or len(lines) > count
    return [_decode(line) for line in lines[-count:]], truncated


def append_note(path: Path, message: str) -> None:
    """Write a line of the host's own into the app's log."""

    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"[efp-host {stamp}Z] {message}\n")
    except OSError:
        pass


async def follow(
    path: Path,
    *,
    tail: int = 0,
    poll_seconds: float = 0.5,
    keepalive_seconds: float = KEEPALIVE_SECONDS,
    stop: Callable[[], bool] | None = None,
) -> AsyncIterator[str | None]:
    """Yield new lines as they are written; ``None`` stands for a keepalive.

    Starts with the last ``tail`` lines. Survives rotation and truncation: a
    file that shrank or was replaced is read again from its start.
    """

    if tail > 0:
        lines, _ = await asyncio.to_thread(tail_lines, path, tail)
        for line in lines:
            yield line
    try:
        stat = os.stat(path)
        position, inode = stat.st_size, stat.st_ino
    except OSError:
        position, inode = 0, None
    pending = b""
    last_sent = time.monotonic()
    while stop is None or not stop():
        try:
            stat = os.stat(path)
        except OSError:
            stat = None
        if stat is not None:
            if stat.st_ino != inode or stat.st_size < position:
                position, inode, pending = 0, stat.st_ino, b""
            if stat.st_size > position:
                data, position = await asyncio.to_thread(_read_from, path, position)
                pending += data
                while b"\n" in pending:
                    raw, pending = pending.split(b"\n", 1)
                    last_sent = time.monotonic()
                    yield _decode(raw)
                if len(pending) > MAX_LINE_CHARS * 4:
                    last_sent = time.monotonic()
                    yield _decode(pending)
                    pending = b""
                continue
        if time.monotonic() - last_sent >= keepalive_seconds:
            last_sent = time.monotonic()
            yield None
        await asyncio.sleep(poll_seconds)


def _read_from(path: Path, position: int) -> tuple[bytes, int]:
    try:
        with open(path, "rb") as handle:
            handle.seek(position)
            data = handle.read(1024 * 1024)
            return data, position + len(data)
    except OSError:
        return b"", position
