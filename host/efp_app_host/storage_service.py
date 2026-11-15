"""The storage service of hidden mode (docs/CONTRACTS.md, section 7.1).

In hidden mode an app cannot see the volume, so it keeps its data through
this service: a unix stream socket, one request per connection. The caller is
identified by the OS uid of the peer (``SO_PEERCRED``), which the kernel
vouches for; an app can therefore only ever reach its own objects.

    request : 4-byte big-endian header length, JSON header, body bytes
    response: 4-byte big-endian header length, JSON header, body bytes

``ObjectStore`` is the file side of it (keys, sizes, the quota) and has no
socket in it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import struct
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import AppRecord, iso
from .procstats import dir_size

logger = logging.getLogger(__name__)

MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_KEY_LENGTH = 512
MAX_SEGMENT_LENGTH = 255  # as the SDK checks before it sends a key
MAX_LIST_KEYS = 10_000
IO_TIMEOUT_SECONDS = 120.0
_CHUNK = 1024 * 1024
_KEY_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
# Bodies are received into this directory beside the objects and renamed into
# place once complete, so a half-written object is never visible.
UPLOADS_DIR = ".uploads"


class StorageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def validate_key(key: Any) -> str:
    """A key is 1-512 characters of ``A-Z a-z 0-9 . _ - /`` that name a file below the store."""

    if not isinstance(key, str) or not 1 <= len(key) <= MAX_KEY_LENGTH or not _KEY_RE.match(key):
        raise StorageError("invalid_key", "a key is 1-512 characters of A-Z a-z 0-9 . _ - /")
    if key.startswith("/"):
        raise StorageError("invalid_key", "a key may not start with /")
    for segment in key.split("/"):
        if segment in ("", ".", ".."):
            raise StorageError("invalid_key", "a key may not hold an empty, '.' or '..' segment")
        if len(segment) > MAX_SEGMENT_LENGTH:
            # A segment is a file or directory name on the volume.
            raise StorageError("invalid_key", f"one segment of a key (the text between two '/') is at most {MAX_SEGMENT_LENGTH} characters")
    return key


def validate_prefix(prefix: Any) -> str:
    if prefix is None or prefix == "":
        return ""
    if not isinstance(prefix, str) or len(prefix) > MAX_KEY_LENGTH or not _KEY_RE.match(prefix) or prefix.startswith("/"):
        raise StorageError("invalid_key", "a prefix follows the rules of a key")
    if any(segment == ".." for segment in prefix.split("/")):
        raise StorageError("invalid_key", "a prefix may not hold a '..' segment")
    return prefix


class ObjectStore:
    """The objects of one app: files below ``root``, a size limit and a quota."""

    def __init__(self, root: Path, *, quota_bytes: int, max_object_bytes: int = MAX_OBJECT_BYTES):
        self.root = Path(root)
        self.quota_bytes = int(quota_bytes)
        self.max_object_bytes = int(max_object_bytes)
        self._lock = threading.Lock()
        self._used: int | None = None

    # ---- usage ------------------------------------------------------------
    def used(self) -> int:
        with self._lock:
            if self._used is None:
                self._used = dir_size(self.root)
            return self._used

    def set_used(self, value: int | None) -> None:
        with self._lock:
            self._used = value

    def _adjust(self, delta: int) -> None:
        with self._lock:
            if self._used is not None:
                self._used = max(0, self._used + delta)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*validate_key(key).split("/"))

    @staticmethod
    def _size(path: Path) -> int | None:
        try:
            return path.stat().st_size if path.is_file() else None
        except OSError:
            return None

    # ---- operations -------------------------------------------------------
    def check_put(self, key: str, size: Any) -> tuple[Path, int]:
        """Validate a put before any byte of its body is read."""

        path = self._path(key)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise StorageError("bad_request", "size must be the number of bytes of the body")
        if size > self.max_object_bytes:
            raise StorageError("too_large", f"one object is at most {self.max_object_bytes // (1024 * 1024)} MiB")
        if path.is_dir():
            raise StorageError("invalid_key", "this key is the prefix of other objects")
        existing = self._size(path) or 0
        if self.used() - existing + size > self.quota_bytes:
            raise StorageError("quota_exceeded", f"the app's data would exceed its quota of {self.quota_bytes // (1024 * 1024)} MiB")
        return path, existing

    def open_upload(self, path: Path) -> tuple[Path, Any]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            raise StorageError("invalid_key", "a prefix of this key is an object") from None
        uploads = self.root.parent / UPLOADS_DIR
        uploads.mkdir(parents=True, exist_ok=True)
        tmp = uploads / uuid.uuid4().hex
        return tmp, open(tmp, "wb")

    def commit_upload(self, tmp: Path, path: Path, size: int, existing: int) -> None:
        os.replace(tmp, path)
        self._adjust(size - existing)

    def put(self, key: str, data: bytes) -> None:
        path, existing = self.check_put(key, len(data))
        tmp, handle = self.open_upload(path)
        try:
            with handle:
                handle.write(data)
            self.commit_upload(tmp, path, len(data), existing)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def locate(self, key: str) -> tuple[Path, int]:
        path = self._path(key)
        size = self._size(path)
        if size is None:
            raise StorageError("not_found", "no object has this key")
        return path, size

    def get(self, key: str) -> bytes:
        path, _ = self.locate(key)
        with open(path, "rb") as handle:
            return handle.read()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        size = self._size(path)
        if size is None:
            return False
        path.unlink(missing_ok=True)
        self._adjust(-size)
        # Empty directories are not objects: drop them.
        parent = path.parent
        while parent != self.root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return True

    def list(self, prefix: str = "") -> list[dict[str, Any]]:
        prefix = validate_prefix(prefix)
        keys: list[dict[str, Any]] = []
        start = self.root
        directory = prefix.rsplit("/", 1)[0] if "/" in prefix else ""
        if directory:
            start = self.root.joinpath(*[part for part in directory.split("/") if part not in ("", ".")])
        stack = [start]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(os.scandir(current), key=lambda entry: entry.name)
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink():
                    continue
                key = Path(entry.path).relative_to(self.root).as_posix()
                if entry.is_dir(follow_symlinks=False):
                    if key.startswith(prefix) or prefix.startswith(key + "/"):
                        stack.append(Path(entry.path))
                    continue
                if not key.startswith(prefix):
                    continue
                try:
                    stat = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                keys.append(
                    {
                        "key": key,
                        "size": int(stat.st_size),
                        "modified_at": iso(datetime.fromtimestamp(stat.st_mtime, timezone.utc)),
                    }
                )
                if len(keys) >= MAX_LIST_KEYS:
                    return sorted(keys, key=lambda item: item["key"])
        return sorted(keys, key=lambda item: item["key"])


def peer_uid(sock: Any) -> int | None:
    """The uid of the process at the other end of a unix socket (Linux)."""

    import socket

    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
    except OSError:
        return None
    _pid, uid, _gid = struct.unpack("3i", raw)
    return int(uid)


def _frame(header: dict[str, Any]) -> bytes:
    raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def _failure(code: str, message: str) -> bytes:
    return _frame({"ok": False, "error": {"code": code, "message": message}})


class StorageService:
    """The unix socket server. ``find_app`` maps a peer uid to its app."""

    def __init__(
        self,
        socket_path: Path,
        *,
        find_app: Callable[[int], AppRecord | None],
        store_for: Callable[[AppRecord], ObjectStore],
        identify: Callable[[Any], int | None] = peer_uid,
    ):
        self.socket_path = Path(socket_path)
        self._find_app = find_app
        self._store_for = store_for
        self._identify = identify
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(self._serve, path=str(self.socket_path))  # type: ignore[attr-defined]
        # Any app user may connect; who it is comes from the kernel, not from the file mode.
        os.chmod(self.socket_path, 0o666)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        try:
            self.socket_path.unlink()
        except OSError:
            pass

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(self._handle(reader, writer), IO_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            pass
        except Exception:  # noqa: BLE001 - one bad connection must not hurt the others
            logger.exception("storage request failed")
            try:
                writer.write(_failure("bad_request", "the storage service could not handle the request"))
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        uid = self._identify(writer.get_extra_info("socket"))
        record = self._find_app(uid) if uid is not None and uid > 0 else None
        (length,) = struct.unpack(">I", await reader.readexactly(4))
        if length > MAX_HEADER_BYTES:
            writer.write(_failure("bad_request", "the request header is too long"))
            await writer.drain()
            return
        try:
            header = json.loads((await reader.readexactly(length)).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            header = None
        if record is None:
            writer.write(_failure("bad_request", "the caller is not an app of this host"))
            await writer.drain()
            return
        if not isinstance(header, dict):
            writer.write(_failure("bad_request", "the request header must be a JSON object"))
            await writer.drain()
            return
        store = self._store_for(record)
        try:
            await self._dispatch(store, header, reader, writer)
        except StorageError as exc:
            writer.write(_failure(exc.code, exc.message))
        await writer.drain()

    async def _dispatch(self, store: ObjectStore, header: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        operation = header.get("op")
        if operation == "put":
            path, existing = await asyncio.to_thread(store.check_put, header.get("key"), header.get("size"))
            size = int(header["size"])
            tmp, handle = await asyncio.to_thread(store.open_upload, path)
            try:
                with handle:
                    remaining = size
                    while remaining > 0:
                        chunk = await reader.read(min(_CHUNK, remaining))
                        if not chunk:
                            raise StorageError("bad_request", "the body is shorter than the announced size")
                        await asyncio.to_thread(handle.write, chunk)
                        remaining -= len(chunk)
                await asyncio.to_thread(store.commit_upload, tmp, path, size, existing)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
            writer.write(_frame({"ok": True}))
        elif operation == "get":
            path, size = await asyncio.to_thread(store.locate, header.get("key"))
            with open(path, "rb") as handle:
                size = os.fstat(handle.fileno()).st_size
                writer.write(_frame({"ok": True, "size": size}))
                remaining = size
                while remaining > 0:
                    chunk = await asyncio.to_thread(handle.read, min(_CHUNK, remaining))
                    if not chunk:
                        break
                    writer.write(chunk)
                    await writer.drain()
                    remaining -= len(chunk)
        elif operation == "delete":
            deleted = await asyncio.to_thread(store.delete, header.get("key"))
            writer.write(_frame({"ok": True, "deleted": bool(deleted)}))
        elif operation == "list":
            keys = await asyncio.to_thread(store.list, header.get("prefix") or "")
            writer.write(_frame({"ok": True, "keys": keys}))
        else:
            raise StorageError("bad_request", "op must be put, get, delete or list")
