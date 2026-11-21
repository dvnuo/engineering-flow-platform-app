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
import shutil
import stat
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
# Requests of one app that may be open at the same time. Each one is a
# descriptor of the manager, which every app shares.
MAX_CONNECTIONS_PER_APP = 64
_CHUNK = 1024 * 1024
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)  # Windows, where the tests also run
# Used with fullmatch: "$" would let a key end in a line break.
_KEY_RE = re.compile(r"[A-Za-z0-9._/-]+")
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

    if not isinstance(key, str) or not 1 <= len(key) <= MAX_KEY_LENGTH or not _KEY_RE.fullmatch(key):
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
    if not isinstance(prefix, str) or len(prefix) > MAX_KEY_LENGTH or not _KEY_RE.fullmatch(prefix) or prefix.startswith("/"):
        raise StorageError("invalid_key", "a prefix follows the rules of a key")
    if any(segment == ".." for segment in prefix.split("/")):
        raise StorageError("invalid_key", "a prefix may not hold a '..' segment")
    return prefix


class ObjectStore:
    """The objects of one app: files below ``root``, a size limit and a quota.

    The store follows no symbolic link. In hidden mode only the manager
    writes the volume, but the directory may have been the app's own before:
    in sandbox mode an app writes ``data/`` itself, and a link it left under
    ``objects`` would otherwise be read or written through by the manager,
    as root, once the host runs in hidden mode. A key that leads through a
    link is refused, a link is never an object, and files are opened with
    ``O_NOFOLLOW``.

    The quota is kept under one lock: a put reserves its size before its
    body is received, so puts that overlap cannot each pass the check and
    exceed the quota together. What is in use changes only together with
    the file it counts (the rename of a put, the removal of a delete), by
    what that step really did to the store: requests that overlap on one
    key cannot count the same object off twice.
    """

    def __init__(self, root: Path, *, quota_bytes: int, max_object_bytes: int = MAX_OBJECT_BYTES):
        self.root = Path(root)
        self.quota_bytes = int(quota_bytes)
        self.max_object_bytes = int(max_object_bytes)
        self._lock = threading.Lock()
        self._used: int | None = None
        # Bytes of puts whose body is still on its way.
        self._reserved = 0
        self._uploads_ready = False

    # ---- usage ------------------------------------------------------------
    def _measure(self) -> int:
        # dir_size skips links below the directory it is given, not the directory itself.
        return 0 if os.path.islink(self.root) else dir_size(self.root)

    def used(self) -> int:
        with self._lock:
            if self._used is None:
                self._used = self._measure()
            return self._used

    def set_used(self, value: int | None) -> None:
        with self._lock:
            self._used = value

    def _release(self, size: int, existing: int) -> None:
        with self._lock:
            self._reserved = max(0, self._reserved - max(0, size - existing))

    # ---- paths ------------------------------------------------------------
    def _path(self, key: str) -> Path:
        return self.root.joinpath(*validate_key(key).split("/"))

    def _refuse_links(self, path: Path, *, code: str = "invalid_key", last: bool = True) -> None:
        """Raise when the root or a component of ``path`` below it is a link.

        ``last=False`` leaves the final component alone: a put replaces
        whatever has that name, a link included, without following it.
        """

        parts = path.relative_to(self.root).parts
        current = self.root
        chain = [current]
        for part in parts if last else parts[:-1]:
            current = current / part
            chain.append(current)
        for item in chain:
            try:
                mode = os.lstat(item).st_mode
            except OSError:
                return  # nothing is there, so nothing below it is either
            if stat.S_ISLNK(mode):
                raise StorageError(code, "this key leads through a link; the storage service follows none")

    @staticmethod
    def _mode(path: Path) -> int | None:
        try:
            return os.lstat(path).st_mode
        except OSError:
            return None

    @classmethod
    def _size(cls, path: Path) -> int | None:
        """The size of the object at ``path``; ``None`` when no plain file is there."""

        try:
            info = os.lstat(path)
        except OSError:
            return None
        return int(info.st_size) if stat.S_ISREG(info.st_mode) else None

    # ---- operations -------------------------------------------------------
    def check_put(self, key: str, size: Any) -> tuple[Path, int]:
        """Validate a put before any byte of its body is read, and reserve its size.

        Whoever gets the answer owes the store one of ``commit_upload`` and
        ``cancel_put``.
        """

        path = self._path(key)
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise StorageError("bad_request", "size must be the number of bytes of the body")
        if size > self.max_object_bytes:
            raise StorageError("too_large", f"one object is at most {self.max_object_bytes // (1024 * 1024)} MiB")
        self._refuse_links(path, last=False)
        mode = self._mode(path)
        if mode is not None and stat.S_ISDIR(mode):
            raise StorageError("invalid_key", "this key is the prefix of other objects")
        existing = self._size(path) or 0
        with self._lock:
            if self._used is None:
                self._used = self._measure()
            if self._used + self._reserved + size - existing > self.quota_bytes:
                raise StorageError("quota_exceeded", f"the app's data would exceed its quota of {self.quota_bytes // (1024 * 1024)} MiB")
            self._reserved += max(0, size - existing)
        return path, existing

    def cancel_put(self, size: int, existing: int) -> None:
        """Give back what ``check_put`` reserved for a put that did not happen."""

        self._release(size, existing)

    def _uploads(self) -> Path:
        """The directory bodies are received into, without what a run that ended badly left there."""

        uploads = self.root.parent / UPLOADS_DIR
        with self._lock:
            if not self._uploads_ready:
                if os.path.islink(uploads):
                    os.unlink(uploads)
                shutil.rmtree(uploads, ignore_errors=True)
                self._uploads_ready = True
        uploads.mkdir(parents=True, exist_ok=True)
        if os.path.islink(uploads):
            raise StorageError("bad_request", "the storage of this app is not usable")
        return uploads

    def open_upload(self, path: Path) -> tuple[Path, Any]:
        self._refuse_links(path, last=False)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            raise StorageError("invalid_key", "a prefix of this key is an object") from None
        tmp = self._uploads() / uuid.uuid4().hex
        descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY, 0o600)
        return tmp, os.fdopen(descriptor, "wb")

    def commit_upload(self, tmp: Path, path: Path, size: int, existing: int) -> None:
        """Put a received body in its place and settle the quota.

        ``existing`` is what ``check_put`` found under the key. A request
        that overlapped this one may have replaced or removed that object
        since (another put of the same key, a delete). So what the store
        really grows by is measured here, under the lock, and a put that
        does not fit any more is refused: counting ``size - existing`` let
        every one of such requests count the same object off, and the
        store then held more than it knew of.
        """

        reserved = max(0, size - existing)
        with self._lock:
            replaced = self._size(path) or 0
            if self._used is not None and replaced < existing:
                others = max(0, self._reserved - reserved)
                if self._used + others + size - replaced > self.quota_bytes:
                    raise StorageError("quota_exceeded", f"the app's data would exceed its quota of {self.quota_bytes // (1024 * 1024)} MiB")
            # rename replaces a link of that name, it does not follow it
            os.replace(tmp, path)
            self._reserved = max(0, self._reserved - reserved)
            if self._used is not None:
                self._used = max(0, self._used + size - replaced)

    def put(self, key: str, data: bytes) -> None:
        path, existing = self.check_put(key, len(data))
        try:
            tmp, handle = self.open_upload(path)
            try:
                with handle:
                    handle.write(data)
                self.commit_upload(tmp, path, len(data), existing)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
        except BaseException:
            self.cancel_put(len(data), existing)
            raise

    def locate(self, key: str) -> tuple[Path, int]:
        path = self._path(key)
        self._refuse_links(path, code="not_found")
        size = self._size(path)
        if size is None:
            raise StorageError("not_found", "no object has this key")
        return path, size

    def open_object(self, path: Path) -> Any:
        """Open an object for reading; a link that took its place meanwhile is not followed."""

        try:
            descriptor = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_BINARY)
        except OSError:
            raise StorageError("not_found", "no object has this key") from None
        handle = os.fdopen(descriptor, "rb")
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            handle.close()
            raise StorageError("not_found", "no object has this key")
        return handle

    def get(self, key: str) -> bytes:
        path, _ = self.locate(key)
        with self.open_object(path) as handle:
            return handle.read()

    def delete(self, key: str) -> bool:
        path = self._path(key)
        try:
            self._refuse_links(path)
        except StorageError:
            return False
        # Look, remove and count under one lock: of several deletes of the
        # same key only one finds the object, and only that one counts it off.
        with self._lock:
            size = self._size(path)
            if size is None:
                return False
            try:
                path.unlink()
            except FileNotFoundError:
                return False
            if self._used is not None:
                self._used = max(0, self._used - size)
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
        try:
            self._refuse_links(start)
        except StorageError:
            return []
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
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                keys.append(
                    {
                        "key": key,
                        "size": int(info.st_size),
                        "modified_at": iso(datetime.fromtimestamp(info.st_mtime, timezone.utc)),
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
        # Open connections by the uid at the other end.
        self._open: dict[int, int] = {}

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
        uid = self._identify(writer.get_extra_info("socket"))
        counted = uid if uid is not None else -1
        self._open[counted] = self._open.get(counted, 0) + 1
        try:
            if self._open[counted] > MAX_CONNECTIONS_PER_APP:
                # Not read, not waited for: whoever opens this many at once is answered at once.
                writer.write(_failure("bad_request", "too many storage requests of this app are open at the same time"))
                await writer.drain()
            else:
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
            left = self._open.get(counted, 1) - 1
            if left > 0:
                self._open[counted] = left
            else:
                self._open.pop(counted, None)
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
            # check_put reserves the size against the quota; from here on the
            # reservation is either turned into the object or given back.
            path, existing = await asyncio.to_thread(store.check_put, header.get("key"), header.get("size"))
            size = int(header["size"])
            try:
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
            except BaseException:
                store.cancel_put(size, existing)
                raise
            writer.write(_frame({"ok": True}))
        elif operation == "get":
            path, size = await asyncio.to_thread(store.locate, header.get("key"))
            with await asyncio.to_thread(store.open_object, path) as handle:
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
