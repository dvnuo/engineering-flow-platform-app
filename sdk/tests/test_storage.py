"""Storage in its three places (sections 7 and 7.1).

``StorageServer`` below is a small implementation of the storage service of
section 7.1, written from the contract: one request per connection,

    request : 4-byte big-endian header length, JSON header, body bytes
    response: 4-byte big-endian header length, JSON header, body bytes

It listens on a unix socket where the system has them and on a loopback TCP
socket everywhere, so the framing is tested on Windows as well.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from efp_app import errors, storage

HAS_UNIX_SOCKETS = sys.platform != "win32" and hasattr(socket, "AF_UNIX")


# --- A storage service written from section 7.1 -----------------------------------------


def read_exactly(connection, count):
    data = b""
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            raise EOFError("closed")
        data += chunk
    return data


def frame(header):
    raw = json.dumps(header).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


class StorageServer:
    def __init__(self, listener, *, quota=None, refuse_before_body=False):
        self.listener = listener
        self.quota = quota
        self.refuse_before_body = refuse_before_body
        self.objects = {}
        self.headers = []
        self.connections = 0
        self.misbehave = None
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                connection, _address = self.listener.accept()
            except OSError:
                return
            self.connections += 1
            try:
                with connection:
                    connection.settimeout(10)
                    self.handle(connection)
            except (OSError, EOFError):
                pass

    def fail(self, connection, code, message="refused"):
        connection.sendall(frame({"ok": False, "error": {"code": code, "message": message}}))

    def handle(self, connection):
        (length,) = struct.unpack(">I", read_exactly(connection, 4))
        header = json.loads(read_exactly(connection, length).decode("utf-8"))
        self.headers.append(header)
        if self.misbehave == "hang up":
            return
        if self.misbehave == "garbage":
            connection.sendall(struct.pack(">I", 5) + b"hello")
            return
        if self.misbehave == "bad_request":
            return self.fail(connection, "bad_request", "the caller is not an app of this host")
        operation = header.get("op")
        if operation == "put":
            size = header["size"]
            used = sum(len(value) for name, value in self.objects.items() if name != header["key"])
            over = self.quota is not None and used + size > self.quota
            if over and self.refuse_before_body:
                return self.fail(connection, "quota_exceeded", "the app's data would exceed its quota")
            body = read_exactly(connection, size)
            if over:
                return self.fail(connection, "quota_exceeded", "the app's data would exceed its quota")
            if any(name.startswith(header["key"] + "/") for name in self.objects):
                return self.fail(connection, "invalid_key", "this key is the prefix of other objects")
            if any(header["key"].startswith(name + "/") for name in self.objects):
                return self.fail(connection, "invalid_key", "a prefix of this key is an object")
            self.objects[header["key"]] = body
            connection.sendall(frame({"ok": True}))
        elif operation == "get":
            if header["key"] not in self.objects:
                return self.fail(connection, "not_found", "no object has this key")
            body = self.objects[header["key"]]
            connection.sendall(frame({"ok": True, "size": len(body)}) + body)
        elif operation == "delete":
            deleted = self.objects.pop(header["key"], None) is not None
            connection.sendall(frame({"ok": True, "deleted": deleted}))
        elif operation == "list":
            keys = [
                {"key": name, "size": len(value), "modified_at": "2026-11-15T08:30:00Z"}
                for name, value in sorted(self.objects.items())
                if name.startswith(header.get("prefix") or "")
            ]
            connection.sendall(frame({"ok": True, "keys": keys}))
        else:
            self.fail(connection, "bad_request", "op must be put, get, delete or list")

    def close(self):
        self.listener.close()


@pytest.fixture
def tcp_service(monkeypatch):
    """The storage service on a loopback TCP socket; the SDK's framing code is unchanged."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    server = StorageServer(listener)
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    monkeypatch.setenv("EFP_APP_STORAGE_SOCKET", "/run/efp-host/storage.sock")
    monkeypatch.setattr(storage._Socket, "_unix", lambda self: socket.create_connection(("127.0.0.1", port), timeout=10))
    yield server
    server.close()


@pytest.fixture
def unix_service(monkeypatch):
    """The storage service on a real unix socket, as on a host in hidden mode."""
    if not HAS_UNIX_SOCKETS:
        pytest.skip("unix sockets are needed")
    directory = tempfile.mkdtemp(prefix="efp")  # short: a socket path has a length limit
    path = os.path.join(directory, "storage.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(8)
    server = StorageServer(listener)
    server.path = path
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    monkeypatch.setenv("EFP_APP_STORAGE_SOCKET", path)
    yield server
    server.close()
    shutil.rmtree(directory, ignore_errors=True)


BACKENDS = ["local", "sandbox", "tcp-socket", pytest.param("unix-socket", marks=pytest.mark.skipif(not HAS_UNIX_SOCKETS, reason="unix sockets are needed"))]


@pytest.fixture(params=BACKENDS)
def place(request, monkeypatch, tmp_path):
    """Each of the places an app's data can live in; yields its name."""
    if request.param == "local":
        yield "local"
    elif request.param == "sandbox":
        monkeypatch.setenv("EFP_APP_ID", "app-1")
        monkeypatch.setenv("EFP_APP_DATA_DIR", str(tmp_path / "data"))
        yield "sandbox"
    elif request.param == "tcp-socket":
        request.getfixturevalue("tcp_service")
        yield "hidden"
    else:
        request.getfixturevalue("unix_service")
        yield "hidden"


# --- The same behaviour everywhere --------------------------------------------------------


def test_the_place_is_chosen_from_the_environment(place):
    assert storage.mode() == place


def test_save_and_load_bytes_and_text(place):
    storage.save("a/b.bin", b"\x00\x01\xff")
    storage.save("note.txt", "caf\u00e9")
    storage.save("view", memoryview(b"abc"))
    storage.save("array", bytearray(b"xyz"))
    assert storage.load("a/b.bin") == b"\x00\x01\xff"
    assert storage.load("note.txt") == "caf\u00e9".encode("utf-8")
    assert storage.load("view") == b"abc"
    assert storage.load("array") == b"xyz"


def test_save_replaces(place):
    storage.save("k", b"one")
    storage.save("k", b"two, longer")
    assert storage.load("k") == b"two, longer"
    storage.save("k", b"")
    assert storage.load("k") == b""


def test_load_of_a_missing_key(place):
    with pytest.raises(errors.StorageNotFoundError) as caught:
        storage.load("missing")
    assert caught.value.code == "not_found"
    assert isinstance(caught.value, errors.StorageError)
    assert storage.load("missing", default=None) is None
    assert storage.load("missing", default=b"") == b""


def test_exists_and_delete(place):
    assert storage.exists("a/b") is False
    storage.save("a/b", b"x")
    assert storage.exists("a/b") is True
    assert storage.exists("a") is False
    assert storage.delete("a/b") is True
    assert storage.delete("a/b") is False
    assert storage.exists("a/b") is False
    with pytest.raises(errors.StorageNotFoundError):
        storage.load("a/b")


def test_list_by_prefix_in_key_order(place):
    for key, data in [("notes/b.txt", b"22"), ("notes/a.txt", b"1"), ("notes/deep/c.txt", b"333"), ("other.txt", b"4444"), ("notes2", b"5")]:
        storage.save(key, data)
    everything = storage.list()
    assert [item.key for item in everything] == ["notes/a.txt", "notes/b.txt", "notes/deep/c.txt", "notes2", "other.txt"]
    assert [item.size for item in everything] == [1, 2, 3, 1, 4]
    assert all(isinstance(item.modified_at, datetime) and item.modified_at.tzinfo is not None for item in everything)
    assert [item.key for item in storage.list("notes/")] == ["notes/a.txt", "notes/b.txt", "notes/deep/c.txt"]
    assert [item.key for item in storage.list("notes")] == ["notes/a.txt", "notes/b.txt", "notes/deep/c.txt", "notes2"]
    assert [item.key for item in storage.list("notes/de")] == ["notes/deep/c.txt"]
    assert [item.key for item in storage.list("notes/a.txt")] == ["notes/a.txt"]
    assert storage.list("nothing/") == []
    assert storage.list("zzz") == []


def test_json(place):
    storage.save_json("state.json", {"count": 1, "names": ["a", "\u5f20"], "nested": {"ok": True}})
    assert storage.load_json("state.json") == {"count": 1, "names": ["a", "\u5f20"], "nested": {"ok": True}}
    assert storage.load("state.json").decode("utf-8").startswith("{")
    assert storage.load_json("missing.json", default={}) == {}
    with pytest.raises(errors.StorageNotFoundError):
        storage.load_json("missing.json")
    storage.save("broken.json", b"{not json")
    with pytest.raises(errors.StorageError) as caught:
        storage.load_json("broken.json", default={})
    assert caught.value.code == "invalid_json"


BAD_KEYS = [
    "",
    "/absolute",
    "a//b",
    "a/../b",
    "..",
    ".",
    "a/./b",
    "trailing/",
    "sp ace",
    "uni\u00e9",
    "back\\slash",
    "new\nline",
    "tilde~",
    "x" * 513,
    "x" * 256,
    "ok/" + "x" * 256 + "/file",
    None,
    7,
    b"bytes",
]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_the_key_rules_are_the_same_everywhere(place, key):
    for call in (lambda: storage.save(key, b"x"), lambda: storage.load(key), lambda: storage.delete(key), lambda: storage.exists(key), lambda: storage.load(key, default=None)):
        with pytest.raises(errors.InvalidKeyError) as caught:
            call()
        assert caught.value.code == "invalid_key"


LONGEST_KEY = "/".join(["x" * 127, "y" * 127, "z" * 127, "w" * 128])


@pytest.mark.parametrize("key", ["a", "A-Z_a.z-0/9", "x" * 255, LONGEST_KEY, "a/b/c/d.json", ".hidden", "a.b..c", "..."])
def test_keys_the_contract_allows(place, key):
    assert len(LONGEST_KEY) == storage.MAX_KEY_LENGTH == 512
    storage.check_key(key)
    if place != "hidden" and len(key) > 200:
        pytest.skip("the whole path may be longer than this machine's file system takes")
    if place != "hidden" and os.name == "nt" and storage.windows_problem(key):
        with pytest.raises(errors.InvalidKeyError) as caught:
            storage.save(key, b"x")
        assert "valid key on the host" in caught.value.message
        return
    storage.save(key, b"x")
    assert storage.load(key) == b"x"
    assert storage.delete(key) is True


@pytest.mark.parametrize("prefix", ["/a", "a/../", "..", "a//b", "sp ace", "x" * 513, 5])
def test_prefix_rules(place, prefix):
    with pytest.raises(errors.InvalidKeyError):
        storage.list(prefix)


def test_the_size_limit_is_the_same_everywhere(place, monkeypatch):
    assert storage.MAX_OBJECT_BYTES == 64 * 1024 * 1024
    monkeypatch.setattr(storage, "MAX_OBJECT_BYTES", 10)
    storage.save("fits", b"0123456789")
    with pytest.raises(errors.TooLargeError) as caught:
        storage.save("too-big", b"0123456789A")
    assert caught.value.code == "too_large"
    with pytest.raises(errors.TooLargeError):
        storage.save_json("too-big.json", {"text": "0123456789"})
    assert storage.exists("too-big") is False


def test_only_bytes_and_text_can_be_saved(place):
    with pytest.raises(TypeError):
        storage.save("k", {"a": 1})
    with pytest.raises(TypeError):
        storage.save("k", 12)


def test_an_object_cannot_also_be_a_folder(place):
    storage.save("a", b"object")
    with pytest.raises(errors.InvalidKeyError):
        storage.save("a/b", b"below an object")
    storage.delete("a")
    storage.save("a/b", b"now a folder")
    with pytest.raises(errors.InvalidKeyError):
        storage.save("a", b"above other objects")
    assert storage.delete("a/b") is True
    storage.save("a", b"free again")
    assert storage.load("a") == b"free again"


# --- Files: a sandbox host and a developer's machine -----------------------------------------


def test_local_files_are_under_dot_efp_data(tmp_path):
    storage.save("notes/a.txt", b"hello")
    assert (tmp_path / ".efp-data" / "objects" / "notes" / "a.txt").read_bytes() == b"hello"
    assert storage.data_dir() == tmp_path / ".efp-data"
    assert storage.data_dir().is_dir()


def test_sandbox_files_are_under_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    monkeypatch.setenv("EFP_APP_DATA_DIR", str(tmp_path / "data"))
    storage.save("notes/a.txt", b"hello")
    assert (tmp_path / "data" / "objects" / "notes" / "a.txt").read_bytes() == b"hello"
    assert not (tmp_path / ".efp-data").exists()
    assert storage.data_dir() == tmp_path / "data"


def test_the_data_dir_takes_other_files_without_listing_them(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_APP_DATA_DIR", str(tmp_path / "data"))
    database = storage.data_dir() / "app.sqlite3"
    database.write_bytes(b"SQLite format 3")
    storage.save("k", b"v")
    assert [item.key for item in storage.list()] == ["k"]
    assert database.read_bytes() == b"SQLite format 3"


def test_a_write_leaves_no_temporary_file(tmp_path):
    storage.save("a/b", b"x" * 1000)
    names = sorted(path.name for path in (tmp_path / ".efp-data" / "objects").rglob("*"))
    assert names == ["a", "b"]


def test_a_failed_write_keeps_the_old_content(tmp_path, monkeypatch):
    storage.save("k", b"old content")

    def failing(source, target):
        raise OSError(errno.EIO, "disk trouble")

    real = os.replace
    monkeypatch.setattr(storage.os, "replace", failing)
    with pytest.raises(errors.StorageError) as caught:
        storage.save("k", b"new content")
    assert not isinstance(caught.value, errors.QuotaExceededError)
    monkeypatch.setattr(storage.os, "replace", real)
    assert storage.load("k") == b"old content"
    assert [path.name for path in (tmp_path / ".efp-data" / "objects").iterdir()] == ["k"]


def test_the_write_goes_through_a_temporary_file_in_the_same_folder(tmp_path, monkeypatch):
    seen = []
    real = os.replace

    def spy(source, target):
        seen.append((Path(source), Path(target), Path(source).read_bytes()))
        real(source, target)

    monkeypatch.setattr(storage.os, "replace", spy)
    storage.save("a/b.txt", b"whole")
    source, target, content = seen[0]
    assert target == tmp_path / ".efp-data" / "objects" / "a" / "b.txt"
    assert source.parent == target.parent
    assert content == b"whole"  # complete before it takes the object's place
    assert not storage._is_key(source.name)


def test_a_full_disk_is_quota_exceeded(monkeypatch):
    def full(source, target):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(storage.os, "replace", full)
    with pytest.raises(errors.QuotaExceededError):
        storage.save("k", b"x")


def test_leftover_temporary_files_and_strange_names_are_not_listed(tmp_path):
    storage.save("k", b"v")
    objects = tmp_path / ".efp-data" / "objects"
    (objects / "~0123456789abcdef.tmp").write_bytes(b"left behind")
    (objects / "has space.txt").write_bytes(b"not a key")
    assert [item.key for item in storage.list()] == ["k"]


def test_deleting_removes_empty_folders(tmp_path):
    storage.save("a/b/c", b"x")
    storage.save("a/keep", b"y")
    storage.delete("a/b/c")
    objects = tmp_path / ".efp-data" / "objects"
    assert not (objects / "a" / "b").exists()
    assert (objects / "a" / "keep").is_file()
    storage.delete("a/keep")
    assert objects.is_dir() and list(objects.iterdir()) == []


def test_list_times_come_from_the_file(tmp_path):
    storage.save("k", b"v")
    path = tmp_path / ".efp-data" / "objects" / "k"
    os.utime(path, (1763195400, 1763195400))
    assert storage.list()[0].modified_at == datetime(2025, 11, 15, 8, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "key, problem",
    [("a.", True), ("dir./file", True), ("...", True), ("NUL", True), ("logs/com1.txt", True), ("a.b", False), (".hidden", False), ("nullable", False)],
)
def test_keys_windows_cannot_keep(key, problem):
    assert bool(storage.windows_problem(key)) is problem


def test_a_deployed_app_without_either_setting_gets_a_clear_error(monkeypatch):
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    for call in (lambda: storage.save("k", b"v"), lambda: storage.load("k"), storage.list, storage.data_dir, storage.mode):
        with pytest.raises(errors.StorageUnavailableError) as caught:
            call()
        assert "EFP_APP_DATA_DIR" in caught.value.message and "EFP_APP_STORAGE_SOCKET" in caught.value.message


# --- The storage service (hidden mode) -------------------------------------------------------


def test_the_frames_of_section_7_1(tcp_service):
    storage.save("a/b.json", b"12345")
    assert storage.load("a/b.json") == b"12345"
    assert [item.key for item in storage.list("a/")] == ["a/b.json"]
    assert storage.delete("a/b.json") is True
    assert tcp_service.headers == [
        {"op": "put", "key": "a/b.json", "size": 5},
        {"op": "get", "key": "a/b.json"},
        {"op": "list", "prefix": "a/"},
        {"op": "delete", "key": "a/b.json"},
    ]
    assert tcp_service.connections == 4  # one request per connection


def test_list_parses_sizes_and_times(tcp_service):
    storage.save("k", b"abc")
    assert storage.list() == [storage.StoredObject(key="k", size=3, modified_at=datetime(2026, 11, 15, 8, 30, tzinfo=timezone.utc))]


def test_a_large_object_crosses_the_socket_intact(tcp_service):
    data = os.urandom(3 * 1024 * 1024 + 17)
    storage.save("big.bin", data)
    assert storage.load("big.bin") == data


def test_service_errors_are_typed(tcp_service):
    tcp_service.quota = 10
    storage.save("fits", b"0123456789")
    with pytest.raises(errors.QuotaExceededError) as caught:
        storage.save("more", b"x")
    assert caught.value.code == "quota_exceeded"
    assert caught.value.message == "the app's data would exceed its quota"
    assert caught.value.user_message == "This app has run out of storage space."

    tcp_service.misbehave = "bad_request"
    with pytest.raises(errors.StorageError) as caught:
        storage.load("fits")
    assert caught.value.code == "bad_request"
    assert caught.value.message == "the caller is not an app of this host"


def test_a_service_that_hangs_up_or_talks_nonsense(tcp_service):
    tcp_service.misbehave = "hang up"
    with pytest.raises(errors.StorageError) as caught:
        storage.load("k")
    assert caught.value.code == "storage_error"
    tcp_service.misbehave = "garbage"
    with pytest.raises(errors.StorageError):
        storage.list()


def test_there_is_no_data_dir_in_hidden_mode(tcp_service):
    with pytest.raises(errors.DataDirUnavailableError) as caught:
        storage.data_dir()
    assert "storage.save" in caught.value.message
    assert isinstance(caught.value, errors.StorageError)


def test_a_missing_socket_is_storage_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    monkeypatch.setenv("EFP_APP_STORAGE_SOCKET", str(tmp_path / "nothing.sock"))
    with pytest.raises(errors.StorageUnavailableError):
        storage.save("k", b"v")


def test_a_real_unix_socket(unix_service):
    storage.save("a/b.json", b"12345")
    assert storage.load("a/b.json") == b"12345"
    assert storage.delete("a/b.json") is True
    assert storage.delete("a/b.json") is False
    assert unix_service.headers[0] == {"op": "put", "key": "a/b.json", "size": 5}
    assert unix_service.connections == 4


def test_a_refusal_before_the_body_was_read_still_arrives(unix_service):
    unix_service.quota = 10
    unix_service.refuse_before_body = True
    with pytest.raises(errors.QuotaExceededError):
        storage.save("big", os.urandom(8 * 1024 * 1024))
    with pytest.raises(errors.QuotaExceededError):
        storage.save("small", b"01234567890")
