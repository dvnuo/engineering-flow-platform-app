"""Data that outlives restarts and deployments (docs/CONTRACTS.md, sections 7 and 7.1).

    from efp_app import storage

    storage.save("notes/alice.txt", "remember the milk")
    storage.load("notes/alice.txt")              # b"remember the milk"
    storage.save_json("settings.json", {"theme": "dark"})
    storage.load_json("settings.json", default={})
    [item.key for item in storage.list("notes/")]
    storage.delete("notes/alice.txt")

The same four operations work in the three places an app runs, and the SDK
picks the place by itself:

- ``EFP_APP_DATA_DIR`` is set (the host isolates apps with a sandbox): files
  under ``$EFP_APP_DATA_DIR/objects/``;
- ``EFP_APP_STORAGE_SOCKET`` is set (the host hides the disk from apps): the
  host's storage service behind that unix socket;
- neither, on a developer's machine: files under ``./.efp-data/objects/``.

A key is 1-512 characters of ``A-Z a-z 0-9 . _ - /``; it does not start with
``/``, has no empty, ``.`` or ``..`` segment and no segment longer than 255
characters. One object is at most 64 MiB. Those rules are checked here, the same way everywhere, so an app that
works locally works on either kind of host.

``data_dir()`` gives a directory for direct file access (SQLite, uploads) in
the first and the third place; on a host that hides the disk it raises
``DataDirUnavailableError``.
"""

from __future__ import annotations

import builtins
import errno
import json
import os
import re
import secrets
import socket
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from . import config, errors

#: The largest object, in bytes (64 MiB).
MAX_OBJECT_BYTES = 64 * 1024 * 1024
MAX_KEY_LENGTH = 512
#: Every place keeps an object as a file named after the key, and a file
#: name is limited to 255 bytes; the limit is applied here so that a key
#: behaves the same everywhere.
MAX_SEGMENT_LENGTH = 255
LOCAL_DIR = ".efp-data"
OBJECTS_DIR = "objects"
#: Seconds one exchange with the storage service may take.
SOCKET_TIMEOUT = 60.0

_MAX_HEADER_BYTES = 64 * 1024 * 1024
_KEY_CHARS = re.compile(r"[A-Za-z0-9._/-]+")
_MISSING: Any = object()


@dataclass(frozen=True)
class StoredObject:
    """One entry of :func:`list`."""

    key: str
    size: int
    modified_at: Optional[datetime] = None


# --- Keys --------------------------------------------------------------------


def check_key(key: Any) -> str:
    """Return ``key`` when it is a valid key, else raise ``InvalidKeyError``."""
    if not isinstance(key, str) or not key:
        raise errors.InvalidKeyError("a key must be a non-empty string")
    if len(key) > MAX_KEY_LENGTH:
        raise errors.InvalidKeyError("a key is at most %d characters" % MAX_KEY_LENGTH)
    if not _KEY_CHARS.fullmatch(key):
        raise errors.InvalidKeyError("a key may only contain A-Z a-z 0-9 . _ - / (got %r)" % key)
    if key.startswith("/"):
        raise errors.InvalidKeyError("a key may not start with '/' (got %r)" % key)
    for segment in key.split("/"):
        if segment in ("", ".", ".."):
            raise errors.InvalidKeyError("a key may not have an empty, '.' or '..' segment (got %r)" % key)
        if len(segment) > MAX_SEGMENT_LENGTH:
            raise errors.InvalidKeyError(
                "one segment of a key (the text between two '/') is at most %d characters" % MAX_SEGMENT_LENGTH
            )
    return key


def check_prefix(prefix: Any) -> str:
    """Return ``prefix`` when it can be the start of a key, else raise ``InvalidKeyError``."""
    if prefix is None or prefix == "":
        return ""
    if not isinstance(prefix, str) or len(prefix) > MAX_KEY_LENGTH or not _KEY_CHARS.fullmatch(prefix):
        raise errors.InvalidKeyError("a prefix may only contain A-Z a-z 0-9 . _ - / (got %r)" % (prefix,))
    if prefix.startswith("/"):
        raise errors.InvalidKeyError("a prefix may not start with '/' (got %r)" % prefix)
    segments = prefix.split("/")
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if segment in (".", "..") or (segment == "" and not last):
            raise errors.InvalidKeyError("a prefix may not have an empty, '.' or '..' segment (got %r)" % prefix)
    return prefix


def _is_key(key: str) -> bool:
    try:
        check_key(key)
    except errors.InvalidKeyError:
        return False
    return True


def _as_bytes(data: Any) -> bytes:
    if isinstance(data, str):
        data = data.encode("utf-8")
    elif isinstance(data, (bytearray, memoryview)):
        data = bytes(data)
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes or str; use save_json() for other values")
    if len(data) > MAX_OBJECT_BYTES:
        raise errors.TooLargeError(
            "one object is at most %d MiB, this one has %d bytes" % (MAX_OBJECT_BYTES // (1024 * 1024), len(data))
        )
    return data


# --- Files: a sandbox host and a developer's machine --------------------------


_WINDOWS_DEVICES = frozenset(
    ["CON", "PRN", "AUX", "NUL"] + ["COM%d" % number for number in range(1, 10)] + ["LPT%d" % number for number in range(1, 10)]
)


def windows_problem(key: str) -> Optional[str]:
    """Why Windows cannot keep a file with this (valid) key, or ``None``.

    Windows drops dots at the end of a name and reserves device names, so
    such a key would silently land on another file. Only a developer's
    machine is affected; the hosts run Linux.
    """
    for segment in key.split("/"):
        if segment.endswith("."):
            return "a name may not end with '.' on Windows"
        if segment.split(".")[0].upper() in _WINDOWS_DEVICES:
            return "%r is a device name on Windows" % segment
    return None


class _Files:
    def __init__(self, root: Path, mode: str) -> None:
        self.root = root
        self.mode = mode

    def _path(self, key: str) -> Path:
        if os.name == "nt":
            problem = windows_problem(key)
            if problem:
                raise errors.InvalidKeyError(
                    "the key %r cannot be stored on this machine: %s. It is a valid key on the host." % (key, problem)
                )
        return self.root.joinpath(*key.split("/"))

    @staticmethod
    def _failure(action: str, key: str, exc: OSError) -> errors.StorageError:
        if exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)):
            return errors.QuotaExceededError("no space left to %s %r" % (action, key))
        if exc.errno == errno.ENAMETOOLONG:
            return errors.InvalidKeyError("a segment of the key %r is too long for the file system" % key)
        return errors.StorageError("could not %s %r: %s" % (action, key, exc.strerror or exc))

    def put(self, key: str, data: bytes) -> None:
        target = self._path(key)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            raise errors.InvalidKeyError(
                "the key %r lies below an existing object; a name cannot be both an object and a folder" % key
            ) from None
        except OSError as exc:
            raise self._failure("save", key, exc) from exc
        if target.is_dir():
            raise errors.InvalidKeyError(
                "other objects are stored below %r; a name cannot be both an object and a folder" % key
            )
        # The temporary name has a character no key can have, so it never
        # collides with an object and never shows up in list().
        temporary = target.parent / ("~" + secrets.token_hex(8) + ".tmp")
        try:
            with open(temporary, "wb") as handle:
                handle.write(data)
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(temporary, target)
        except OSError as exc:
            raise self._failure("save", key, exc) from exc
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass

    def get(self, key: str) -> bytes:
        target = self._path(key)
        if not target.is_file():
            raise errors.StorageNotFoundError("there is no object with the key %r" % key)
        try:
            return target.read_bytes()
        except FileNotFoundError:
            raise errors.StorageNotFoundError("there is no object with the key %r" % key) from None
        except OSError as exc:
            raise self._failure("load", key, exc) from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> bool:
        target = self._path(key)
        if not target.is_file():
            return False
        try:
            os.unlink(target)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise self._failure("delete", key, exc) from exc
        # Empty folders go too, so the name is free for an object again.
        folder = target.parent
        while folder != self.root:
            try:
                os.rmdir(folder)
            except OSError:
                break
            folder = folder.parent
        return True

    def list(self, prefix: str) -> List[StoredObject]:
        start = self.root
        if "/" in prefix:
            start = self.root.joinpath(*prefix.rsplit("/", 1)[0].split("/"))
        if not start.is_dir():
            return []
        found: List[StoredObject] = []
        for directory, _folders, names in os.walk(start):
            relative = Path(directory).relative_to(self.root).as_posix()
            base = "" if relative == "." else relative + "/"
            for name in names:
                key = base + name
                if not key.startswith(prefix) or not _is_key(key):
                    continue
                try:
                    status = os.stat(os.path.join(directory, name))
                except OSError:
                    continue
                found.append(
                    StoredObject(
                        key=key,
                        size=status.st_size,
                        modified_at=datetime.fromtimestamp(status.st_mtime, tz=timezone.utc),
                    )
                )
        found.sort(key=lambda item: item.key)
        return found


# --- The storage service of a host that hides the disk (section 7.1) ----------


def _read_exactly(connection: socket.socket, count: int) -> bytes:
    chunks: List[bytes] = []
    remaining = count
    while remaining > 0:
        chunk = connection.recv(min(remaining, 1024 * 1024))
        if not chunk:
            raise EOFError("the connection closed %d bytes early" % remaining)
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _parse_time(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class _Socket:
    """One request per connection: 4-byte big-endian header length, JSON header, body."""

    mode = "hidden"

    def __init__(self, path: str, connect: Optional[Callable[[], socket.socket]] = None) -> None:
        self.path = path
        self._connect = connect or self._unix

    def _unix(self) -> socket.socket:
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            raise errors.StorageUnavailableError(
                "%s is set, but this system has no unix sockets" % config.APP_STORAGE_SOCKET
            )
        connection = socket.socket(family, socket.SOCK_STREAM)
        try:
            connection.settimeout(SOCKET_TIMEOUT)
            connection.connect(self.path)
        except OSError:
            connection.close()
            raise
        return connection

    def _call(self, header: Dict[str, Any], body: bytes = b"") -> Tuple[Dict[str, Any], bytes]:
        try:
            connection = self._connect()
        except OSError as exc:
            raise errors.StorageUnavailableError(
                "cannot reach the storage service at %s: %s" % (self.path, exc.strerror or exc)
            ) from exc
        try:
            with connection:
                encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
                refused: Optional[OSError] = None
                try:
                    connection.sendall(struct.pack(">I", len(encoded)) + encoded)
                    if body:
                        connection.sendall(body)
                except OSError as exc:
                    # The service may refuse a request (a full quota, say) and
                    # close before it has read the whole body; its answer is
                    # still waiting to be read.
                    refused = exc
                try:
                    (length,) = struct.unpack(">I", _read_exactly(connection, 4))
                    if length > _MAX_HEADER_BYTES:
                        raise ValueError("the answer's header is %d bytes long" % length)
                    raw = _read_exactly(connection, length)
                except (OSError, EOFError):
                    if refused is not None:
                        raise refused from None
                    raise
                answer = json.loads(raw.decode("utf-8"))
                if not isinstance(answer, dict):
                    raise ValueError("the answer's header is not an object")
                if not answer.get("ok"):
                    error = answer.get("error")
                    error = error if isinstance(error, dict) else {}
                    message = error.get("message")
                    raise errors.for_storage_code(
                        str(error.get("code") or ""), message if isinstance(message, str) and message else None
                    )
                content = b""
                if header.get("op") == "get":
                    size = answer.get("size")
                    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_OBJECT_BYTES:
                        raise ValueError("the answer has no usable size")
                    content = _read_exactly(connection, size)
                return answer, content
        except errors.EfpError:
            raise
        except (OSError, EOFError, ValueError, struct.error) as exc:
            raise errors.StorageError("the storage service did not answer properly: %s" % exc) from exc

    def put(self, key: str, data: bytes) -> None:
        self._call({"op": "put", "key": key, "size": len(data)}, data)

    def get(self, key: str) -> bytes:
        return self._call({"op": "get", "key": key})[1]

    def exists(self, key: str) -> bool:
        return any(item.key == key for item in self.list(key))

    def delete(self, key: str) -> bool:
        try:
            answer, _content = self._call({"op": "delete", "key": key})
        except errors.StorageNotFoundError:
            return False
        return bool(answer.get("deleted", True))

    def list(self, prefix: str) -> List[StoredObject]:
        answer, _content = self._call({"op": "list", "prefix": prefix})
        keys = answer.get("keys")
        found = [
            StoredObject(
                key=item["key"],
                size=item.get("size") if isinstance(item.get("size"), int) else 0,
                modified_at=_parse_time(item.get("modified_at")),
            )
            for item in (keys if isinstance(keys, builtins.list) else ())
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        ]
        found.sort(key=lambda item: item.key)
        return found


# --- Choosing the place ------------------------------------------------------


def _backend() -> Union[_Files, _Socket]:
    settings = config.load()
    if settings.data_dir:
        return _Files(Path(settings.data_dir) / OBJECTS_DIR, "sandbox")
    if settings.storage_socket:
        return _Socket(settings.storage_socket)
    if settings.deployed:
        raise errors.StorageUnavailableError(
            "Neither %s nor %s is set; the host sets one of them for a deployed app."
            % (config.APP_DATA_DIR, config.APP_STORAGE_SOCKET)
        )
    return _Files(Path.cwd() / LOCAL_DIR / OBJECTS_DIR, "local")


def mode() -> str:
    """Where the data goes: ``sandbox`` (files of the host), ``hidden`` (the
    host's storage service) or ``local`` (a developer's machine)."""
    return _backend().mode


def data_dir() -> Path:
    """A directory the app may read and write directly; it survives restarts and deployments.

    Use it for what does not fit the key/value calls, an SQLite file for
    example. :func:`save` keeps its objects in the ``objects`` folder of this
    directory, so use other names. On a host that hides the disk from apps
    there is no such directory: ``DataDirUnavailableError``.
    """
    settings = config.load()
    if settings.data_dir:
        path = Path(settings.data_dir)
    elif settings.storage_socket:
        raise errors.DataDirUnavailableError(
            "This host keeps apps away from the disk (isolation mode 'hidden'), so there is no data "
            "directory. Use efp_app.storage.save() and load(): they work on every host."
        )
    elif settings.deployed:
        raise errors.StorageUnavailableError(
            "Neither %s nor %s is set; the host sets one of them for a deployed app."
            % (config.APP_DATA_DIR, config.APP_STORAGE_SOCKET)
        )
    else:
        path = Path.cwd() / LOCAL_DIR
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise errors.StorageError("could not create the data directory %s: %s" % (path, exc.strerror or exc)) from exc
    return path


# --- The four operations -----------------------------------------------------


def save(key: str, data: Union[bytes, bytearray, memoryview, str]) -> None:
    """Store ``data`` (bytes, or text saved as UTF-8) under ``key``, replacing what was there.

    A file is written completely or not at all.
    """
    check_key(key)
    _backend().put(key, _as_bytes(data))


def load(key: str, default: Any = _MISSING) -> Any:
    """The bytes stored under ``key``.

    A missing key raises ``StorageNotFoundError``, or returns ``default`` when one is given.
    """
    check_key(key)
    try:
        return _backend().get(key)
    except errors.StorageNotFoundError:
        if default is _MISSING:
            raise
        return default


def exists(key: str) -> bool:
    """True when something is stored under ``key``."""
    check_key(key)
    return _backend().exists(key)


def delete(key: str) -> bool:
    """Remove ``key``. Returns whether there was something to remove."""
    check_key(key)
    return _backend().delete(key)


def save_json(key: str, value: Any) -> None:
    """Store a JSON-serialisable value under ``key``."""
    save(key, json.dumps(value, ensure_ascii=False).encode("utf-8"))


def load_json(key: str, default: Any = _MISSING) -> Any:
    """The value stored by :func:`save_json`; ``default`` (when given) for a missing key."""
    try:
        content = load(key)
    except errors.StorageNotFoundError:
        if default is _MISSING:
            raise
        return default
    try:
        return json.loads(content.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise errors.StorageError("the object %r is not JSON" % key, code="invalid_json") from None


def list(prefix: str = "") -> List[StoredObject]:  # noqa: A001 - the name is part of the API
    """The stored objects whose key starts with ``prefix``, ordered by key."""
    return _backend().list(check_prefix(prefix))
