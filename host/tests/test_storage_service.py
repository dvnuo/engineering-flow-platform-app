from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import tempfile

import pytest
from conftest import make_settings

from efp_app_host.registry import Registry
from efp_app_host.storage_service import ObjectStore, StorageError, StorageService, validate_key

MIB = 1024 * 1024


@pytest.mark.parametrize("key", ["a", "a/b.json", "A-Z_0.9/x", "x" * 512, "notes/2026-11-15.txt"])
def test_valid_keys(key):
    assert validate_key(key) == key


@pytest.mark.parametrize("key", ["", "/a", "a//b", "a/", "../a", "a/../b", "a/./b", ".", "a b", "a\\b", "caf\u00e9", "x" * 513, None, 5, "a\x00b"])
def test_invalid_keys(key):
    with pytest.raises(StorageError) as caught:
        validate_key(key)
    assert caught.value.code == "invalid_key"


def test_put_get_list_delete(tmp_path):
    store = ObjectStore(tmp_path / "data" / "objects", quota_bytes=MIB)
    store.put("notes/a.txt", b"alpha")
    store.put("notes/deep/b.txt", b"beta!")
    store.put("other.bin", b"\x00\x01")
    assert store.get("notes/a.txt") == b"alpha"
    assert store.used() == 12

    listed = store.list("")
    assert [item["key"] for item in listed] == ["notes/a.txt", "notes/deep/b.txt", "other.bin"]
    assert listed[0]["size"] == 5 and listed[0]["modified_at"].endswith("Z")
    assert [item["key"] for item in store.list("notes/")] == ["notes/a.txt", "notes/deep/b.txt"]
    assert [item["key"] for item in store.list("notes/d")] == ["notes/deep/b.txt"]
    assert [item["key"] for item in store.list("o")] == ["other.bin"]
    assert store.list("nothing/") == []

    store.put("notes/a.txt", b"alpha-2")
    assert store.get("notes/a.txt") == b"alpha-2" and store.used() == 14

    assert store.delete("notes/deep/b.txt") is True
    assert store.delete("notes/deep/b.txt") is False
    assert not (tmp_path / "data" / "objects" / "notes" / "deep").exists()
    assert store.used() == 9
    with pytest.raises(StorageError) as caught:
        store.get("notes/deep/b.txt")
    assert caught.value.code == "not_found"
    # Nothing half-written is left beside the objects.
    assert list((tmp_path / "data" / ".uploads").iterdir()) == []


def test_limits(tmp_path):
    store = ObjectStore(tmp_path / "objects", quota_bytes=100, max_object_bytes=60)
    with pytest.raises(StorageError) as caught:
        store.put("big", b"x" * 61)
    assert caught.value.code == "too_large"
    store.put("a", b"x" * 60)
    with pytest.raises(StorageError) as caught:
        store.put("b", b"x" * 41)
    assert caught.value.code == "quota_exceeded"
    # Replacing an object counts the difference only.
    store.put("a", b"y" * 50)
    store.put("b", b"x" * 50)
    assert store.used() == 100
    with pytest.raises(StorageError) as caught:
        store.check_put("c", "12")
    assert caught.value.code == "bad_request"


def test_an_object_and_a_prefix_cannot_share_a_name(tmp_path):
    store = ObjectStore(tmp_path / "objects", quota_bytes=MIB)
    store.put("a/b", b"1")
    with pytest.raises(StorageError) as caught:
        store.put("a", b"2")
    assert caught.value.code == "invalid_key"
    with pytest.raises(StorageError) as caught:
        store.put("a/b/c", b"3")
    assert caught.value.code == "invalid_key"
    with pytest.raises(StorageError) as caught:
        store.list("../")
    assert caught.value.code == "invalid_key"


def test_usage_is_counted_once_and_can_be_reset(tmp_path):
    root = tmp_path / "objects"
    root.mkdir()
    (root / "old.bin").write_bytes(b"x" * 10)
    store = ObjectStore(root, quota_bytes=MIB)
    assert store.used() == 10
    (root / "sneaked.bin").write_bytes(b"x" * 5)
    assert store.used() == 10
    store.set_used(None)
    assert store.used() == 15


# ---- over the socket -------------------------------------------------------------------


async def _call(path: str, header: dict, body: bytes = b"") -> tuple[dict, bytes]:
    reader, writer = await asyncio.open_unix_connection(path)
    raw = json.dumps(header).encode("utf-8")
    writer.write(struct.pack(">I", len(raw)) + raw + body)
    await writer.drain()
    (length,) = struct.unpack(">I", await reader.readexactly(4))
    answer = json.loads(await reader.readexactly(length))
    payload = await reader.readexactly(answer["size"]) if answer.get("ok") and "size" in answer else b""
    writer.close()
    return answer, payload


@pytest.mark.skipif(sys.platform != "linux" or os.getuid() == 0, reason="unix sockets with SO_PEERCRED are Linux; root is never an app")
def test_the_service_serves_the_app_the_kernel_names(tmp_path):
    settings = make_settings(tmp_path, isolation_mode="hidden", uid_range_start=os.getuid(), app_data_mb=1)
    registry = Registry(settings)
    record, _ = registry.upsert("app-1", slug="mindmap")
    assert record.uid == os.getuid()
    stores: dict[str, ObjectStore] = {}

    def store_for(app) -> ObjectStore:
        return stores.setdefault(app.id, ObjectStore(registry.paths.objects_dir(app.id), quota_bytes=MIB))

    short = tempfile.mkdtemp(prefix="efp-st-")
    socket_path = os.path.join(short, "storage.sock")

    async def scenario() -> None:
        service = StorageService(socket_path, find_app=registry.find_by_uid, store_for=store_for)
        await service.start()
        try:
            assert os.stat(socket_path).st_mode & 0o777 == 0o666
            answer, _ = await _call(socket_path, {"op": "put", "key": "a/b.json", "size": 7}, b'{"a":1}')
            assert answer == {"ok": True}
            answer, payload = await _call(socket_path, {"op": "get", "key": "a/b.json"})
            assert answer == {"ok": True, "size": 7} and payload == b'{"a":1}'
            big = os.urandom(300_000)
            answer, _ = await _call(socket_path, {"op": "put", "key": "big.bin", "size": len(big)}, big)
            assert answer == {"ok": True}
            answer, payload = await _call(socket_path, {"op": "get", "key": "big.bin"})
            assert payload == big
            answer, _ = await _call(socket_path, {"op": "list", "prefix": "a/"})
            assert answer["ok"] is True and [item["key"] for item in answer["keys"]] == ["a/b.json"]
            assert set(answer["keys"][0]) == {"key", "size", "modified_at"}
            answer, _ = await _call(socket_path, {"op": "delete", "key": "a/b.json"})
            assert answer == {"ok": True, "deleted": True}
            answer, _ = await _call(socket_path, {"op": "delete", "key": "a/b.json"})
            assert answer == {"ok": True, "deleted": False}

            for header, code in (
                ({"op": "get", "key": "a/b.json"}, "not_found"),
                ({"op": "get", "key": "../registry.json"}, "invalid_key"),
                ({"op": "put", "key": "x", "size": 65 * MIB}, "too_large"),
                ({"op": "put", "key": "x", "size": 900_000}, "quota_exceeded"),
                ({"op": "rename", "key": "x"}, "bad_request"),
                ({"op": "put", "key": "x"}, "bad_request"),
            ):
                answer, _ = await _call(socket_path, header)
                assert answer["ok"] is False and answer["error"]["code"] == code, (header, answer)
                assert answer["error"]["message"]

            # The objects are the app's own, on the volume, where the contract puts them.
            assert (registry.paths.objects_dir("app-1") / "big.bin").read_bytes() == big

            # A caller whose uid is not an app's gets nothing.
            registry.remove("app-1")
            answer, _ = await _call(socket_path, {"op": "list", "prefix": ""})
            assert answer["ok"] is False and answer["error"]["code"] == "bad_request"
        finally:
            await service.stop()
        assert not os.path.exists(socket_path)

    try:
        asyncio.run(scenario())
    finally:
        import shutil

        shutil.rmtree(short, ignore_errors=True)


def test_an_unknown_caller_is_refused_without_a_socket(tmp_path):
    """The same decision on any system: the peer's uid picks the app, nothing in the request does."""

    settings = make_settings(tmp_path, isolation_mode="hidden")
    registry = Registry(settings)
    first, _ = registry.upsert("app-1", slug="one")
    second, _ = registry.upsert("app-2", slug="two")
    stores = {record.id: ObjectStore(registry.paths.objects_dir(record.id), quota_bytes=MIB) for record in (first, second)}
    stores["app-1"].put("secret.txt", b"one's secret")

    class Writer:
        def __init__(self, uid):
            self.uid = uid
            self.sent = b""

        def get_extra_info(self, name):
            return self.uid

        def write(self, data):
            self.sent += data

        async def drain(self):
            return None

    async def ask(uid, header) -> dict:
        raw = json.dumps(header).encode("utf-8")
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack(">I", len(raw)) + raw)
        reader.feed_eof()
        writer = Writer(uid)
        service = StorageService(tmp_path / "unused.sock", find_app=registry.find_by_uid, store_for=lambda record: stores[record.id], identify=lambda uid: uid)
        await service._handle(reader, writer)
        (length,) = struct.unpack(">I", writer.sent[:4])
        return json.loads(writer.sent[4 : 4 + length])

    async def scenario() -> None:
        assert (await ask(first.uid, {"op": "get", "key": "secret.txt"}))["ok"] is True
        # The second app asks for the same key and reaches its own, empty store.
        answer = await ask(second.uid, {"op": "get", "key": "secret.txt"})
        assert answer["ok"] is False and answer["error"]["code"] == "not_found"
        # It cannot name the other app's directory either.
        answer = await ask(second.uid, {"op": "get", "key": "../../app-1/data/objects/secret.txt"})
        assert answer["error"]["code"] == "invalid_key"
        for uid in (0, None, 99999):
            answer = await ask(uid, {"op": "list", "prefix": ""})
            assert answer["ok"] is False and answer["error"]["code"] == "bad_request"

    asyncio.run(scenario())
