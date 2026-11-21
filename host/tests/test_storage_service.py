from __future__ import annotations

import asyncio
import json
import os
import shutil
import struct
import sys
import tempfile

import pytest
from conftest import make_settings

from efp_app_host.registry import Registry
from efp_app_host.storage_service import ObjectStore, StorageError, StorageService, validate_key

MIB = 1024 * 1024


@pytest.mark.parametrize("key", ["a", "a/b.json", "A-Z_0.9/x", "x" * 255, "/".join(["x" * 127, "y" * 127, "z" * 127, "w" * 128]), "notes/2026-11-15.txt", ".hidden", "a.b..c", "..."])
def test_valid_keys(key):
    assert validate_key(key) == key


@pytest.mark.parametrize("key", ["", "/a", "a//b", "a/", "../a", "a/../b", "a/./b", ".", "a b", "a\\b", "caf\u00e9", "x" * 513, "x" * 256, "a/" + "y" * 256 + "/b", None, 5, "a\x00b"])
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


# ---- the quota under one lock, links, many connections ------------------------------------


@pytest.mark.parametrize("key", ["a\n", "a/b\n", "notes\n/x"])
def test_a_key_may_not_hold_a_line_break(key):
    with pytest.raises(StorageError) as caught:
        validate_key(key)
    assert caught.value.code == "invalid_key"
    with pytest.raises(StorageError):
        ObjectStore("unused", quota_bytes=MIB).list("a\n")


def test_puts_that_overlap_cannot_exceed_the_quota_together(tmp_path):
    store = ObjectStore(tmp_path / "data" / "objects", quota_bytes=1000)
    # Two requests whose bodies are still on their way: the second is counted against the first.
    first = store.check_put("a", 600)
    with pytest.raises(StorageError) as caught:
        store.check_put("b", 600)
    assert caught.value.code == "quota_exceeded"
    # (It used to pass: the check looked only at what was already on the disk.)
    path, existing = first
    tmp, handle = store.open_upload(path)
    with handle:
        handle.write(b"x" * 600)
    store.commit_upload(tmp, path, 600, existing)
    assert store.used() == 600
    # What was reserved became the object: there is room for 400 more, not for 401, not for 1000.
    with pytest.raises(StorageError):
        store.check_put("b", 401)
    store.put("b", b"y" * 400)
    assert store.used() == 1000

    # A put that never happens gives its reservation back.
    store.delete("b")
    path, existing = store.check_put("c", 400)
    with pytest.raises(StorageError):
        store.check_put("d", 1)
    store.cancel_put(400, existing)
    store.put("d", b"z" * 400)
    # Replacing an object reserves only what it grows by.
    store.put("d", b"z" * 300)
    assert store.used() == 900


def test_a_put_that_fails_releases_what_it_reserved(tmp_path):
    store = ObjectStore(tmp_path / "data" / "objects", quota_bytes=1000)
    store.put("a/b", b"1")
    with pytest.raises(StorageError):
        store.put("a/b/c", b"x" * 900)  # "a/b" is an object, not a prefix
    store.put("big", b"x" * 999)
    assert store.used() == 1000


def test_requests_that_overlap_on_one_key_cannot_count_its_object_off_twice(tmp_path):
    from efp_app_host.procstats import dir_size

    root = tmp_path / "data" / "objects"
    store = ObjectStore(root, quota_bytes=1000)
    store.put("big", b"x" * 600)
    store.put("other", b"y" * 300)

    def receive(key: str, body: bytes):
        """A put up to the moment its body has arrived."""

        path, existing = store.check_put(key, len(body))
        tmp, handle = store.open_upload(path)
        with handle:
            handle.write(body)
        return tmp, path, len(body), existing

    # Two puts of one key whose bodies are on their way together: each was
    # told that it replaces 600 bytes, and only the first one does.
    first, second = receive("big", b"1"), receive("big", b"2")
    store.commit_upload(*first)
    store.commit_upload(*second)
    assert store.used() == dir_size(root) == 301
    # (It used to say 0: the 600 bytes counted off twice, and room for a thousand more.)
    with pytest.raises(StorageError) as caught:
        store.check_put("more", 700)
    assert caught.value.code == "quota_exceeded"

    # A put that replaces an object, and a delete of that object before the put is done.
    store.put("big", b"x" * 600)
    pending = receive("big", b"z" * 600)
    assert store.delete("big") is True and store.used() == 300
    store.commit_upload(*pending)
    assert store.used() == dir_size(root) == 900

    # The same, but the room the delete made went to somebody else meanwhile:
    # the put replaces nothing any more and does not fit.
    tmp, path, size, existing = receive("big", b"w" * 600)
    assert store.delete("big") is True
    store.put("filler", b"f" * 600)
    with pytest.raises(StorageError) as caught:
        store.commit_upload(tmp, path, size, existing)
    assert caught.value.code == "quota_exceeded" and not path.exists()
    # What the service and put() do with a put that failed.
    tmp.unlink()
    store.cancel_put(size, existing)
    assert store.used() == dir_size(root) == 900
    store.put("last", b"l" * 100)
    assert store.used() == 1000


def test_deletes_of_one_key_at_the_same_time_count_the_object_off_once(tmp_path):
    import threading

    from efp_app_host.procstats import dir_size

    root = tmp_path / "data" / "objects"
    store = ObjectStore(root, quota_bytes=MIB)
    store.put("kept", b"k" * 1000)
    for _ in range(20):
        store.put("gone", b"g" * 500)
        together = threading.Barrier(8)
        answers: list[bool] = []

        def remove() -> None:
            together.wait()
            answers.append(store.delete("gone"))

        threads = [threading.Thread(target=remove) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # One of them removed it. (Each used to count its 500 bytes off, and
        # the store then believed it held less than it did.)
        assert answers.count(True) == 1
        assert store.used() == dir_size(root) == 1000


def test_what_a_run_that_ended_badly_left_among_the_uploads_is_removed(tmp_path):
    uploads = tmp_path / "data" / ".uploads"
    uploads.mkdir(parents=True)
    (uploads / "half-received").write_bytes(b"x" * 500)
    store = ObjectStore(tmp_path / "data" / "objects", quota_bytes=1000)
    store.put("a", b"1")
    assert list(uploads.iterdir()) == []


def test_one_app_cannot_hold_every_connection_of_the_service(tmp_path):
    from efp_app_host import storage_service

    settings = make_settings(tmp_path, isolation_mode="hidden")
    registry = Registry(settings)
    record, _ = registry.upsert("app-1", slug="one")
    other, _ = registry.upsert("app-2", slug="two")
    stores = {item.id: ObjectStore(registry.paths.objects_dir(item.id), quota_bytes=MIB) for item in (record, other)}

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

        def close(self):
            return None

        async def wait_closed(self):
            return None

    def answer_of(writer) -> dict:
        (length,) = struct.unpack(">I", writer.sent[:4])
        return json.loads(writer.sent[4 : 4 + length])

    async def scenario():
        service = StorageService(tmp_path / "unused.sock", find_app=registry.find_by_uid, store_for=lambda app: stores[app.id], identify=lambda uid: uid)
        # Connections that are open and say nothing.
        silent = [asyncio.StreamReader() for _ in range(storage_service.MAX_CONNECTIONS_PER_APP)]
        waiting = [asyncio.ensure_future(service._serve(reader, Writer(record.uid))) for reader in silent]
        await asyncio.sleep(0.05)
        # One more of the same app is answered at once, unread.
        refused = Writer(record.uid)
        await asyncio.wait_for(service._serve(asyncio.StreamReader(), refused), 5)
        # Another app is served as ever.
        raw = json.dumps({"op": "list", "prefix": ""}).encode("utf-8")
        reader = asyncio.StreamReader()
        reader.feed_data(struct.pack(">I", len(raw)) + raw)
        reader.feed_eof()
        served = Writer(other.uid)
        await asyncio.wait_for(service._serve(reader, served), 5)
        for reader in silent:
            reader.feed_eof()
        await asyncio.gather(*waiting)
        # Every connection that ended is counted out again.
        return answer_of(refused), answer_of(served), dict(service._open)

    refused, served, left = asyncio.run(scenario())
    assert refused["ok"] is False and refused["error"]["code"] == "bad_request" and "too many" in refused["error"]["message"]
    assert served == {"ok": True, "keys": []}
    assert left == {}


@pytest.mark.skipif(sys.platform != "linux", reason="symbolic links need Linux here")
def test_the_store_follows_no_link(tmp_path):
    victim = tmp_path / "apps" / "victim" / "data" / "objects"
    victim.mkdir(parents=True)
    (victim / "secret.txt").write_text("theirs", encoding="utf-8")
    key_file = tmp_path / "keys" / "session.key"
    key_file.parent.mkdir()
    key_file.write_text("the-session-key", encoding="utf-8")
    root = tmp_path / "apps" / "mine" / "data" / "objects"
    (root / "plain").mkdir(parents=True)
    (root / "plain" / "own.txt").write_text("mine", encoding="utf-8")
    # What the app left there while it wrote its data directory itself (sandbox mode).
    os.symlink(key_file, root / "key")
    os.symlink(victim, root / "theirs")
    os.symlink(victim / "secret.txt", root / "plain" / "alias.txt")
    store = ObjectStore(root, quota_bytes=MIB)

    for key in ("key", "theirs/secret.txt", "plain/alias.txt"):
        with pytest.raises(StorageError) as caught:
            store.get(key)
        assert caught.value.code == "not_found", key
        assert store.delete(key) is False
    assert [item["key"] for item in store.list("")] == ["plain/own.txt"]
    assert store.list("theirs/") == []
    # Nothing is written through a link either.
    with pytest.raises(StorageError) as caught:
        store.put("theirs/planted.txt", b"x")
    assert caught.value.code == "invalid_key"
    assert sorted(path.name for path in victim.iterdir()) == ["secret.txt"]
    # A put on a name that is a link replaces the link; what it pointed at stays as it was.
    store.put("key", b"my own object")
    assert store.get("key") == b"my own object" and key_file.read_text(encoding="utf-8") == "the-session-key"
    assert not (root / "key").is_symlink()

    # A store whose directory is itself a link serves nothing and writes nothing.
    linked = tmp_path / "apps" / "other" / "data"
    linked.mkdir(parents=True)
    os.symlink(victim, linked / "objects")
    through = ObjectStore(linked / "objects", quota_bytes=MIB)
    assert through.list("") == [] and through.used() == 0
    with pytest.raises(StorageError):
        through.get("secret.txt")
    with pytest.raises(StorageError):
        through.put("planted.txt", b"x")
    # The uploads directory is not followed through a link. (The store above
    # made a directory there for its own puts; the link takes its place.)
    uploads = tmp_path / "apps" / "mine" / "data" / ".uploads"
    shutil.rmtree(uploads, ignore_errors=True)
    os.symlink(victim, uploads)
    fresh = ObjectStore(root, quota_bytes=MIB)
    fresh.put("later.txt", b"1")
    assert sorted(path.name for path in victim.iterdir()) == ["secret.txt"]
