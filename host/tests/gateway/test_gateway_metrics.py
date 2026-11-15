from __future__ import annotations

import asyncio
import dataclasses
import json
import socket
import sys

import pytest

from efp_app_host.gateway import background
from efp_app_host.gateway.metrics import LATENCY_BOUNDS_MS, AccessLogListener, TrafficStore, build_payload, day_of, percentile
from efp_app_host.gateway.secret import access_log_key
from efp_app_host.paths import HostPaths
from efp_app_host.state import HostState

from gateway_support import NOW, Clock, add_app, make_gateway, run

KEY = "log-key"
DAY = "2025-11-15"
YESTERDAY = "2025-11-14"


def line(app: str = "app-1", *, t: float = NOW, status: int = 200, rt: float = 0.05, uht: str = "0.040", user: str = "", key: str = KEY, **extra) -> str:
    entry = {"k": key, "t": t, "app": app, "slug": "mindmap", "status": status, "rt": rt, "uht": uht, "user": user, "bytes": 512}
    entry.update(extra)
    return json.dumps(entry)


def store(tmp_path=None, *, clock: Clock | None = None, known=None) -> TrafficStore:
    state_file = None if tmp_path is None else tmp_path / "metrics" / "state.json"
    return TrafficStore(state_file, log_key=KEY, known_app=known, clock=clock or Clock())


def only(rows: list[dict], app_id: str = "app-1", day: str = DAY) -> dict:
    found = [row for row in rows if row["app_id"] == app_id and row["day"] == day]
    assert len(found) == 1, rows
    return found[0]


# ---- adding up ---------------------------------------------------------------------
def test_lines_are_added_up_per_app_and_day():
    traffic = store()
    assert traffic.ingest(line(status=200, user="12", t=NOW)) is True
    assert traffic.ingest(line(status=304, user="12", t=NOW + 5)) is True
    assert traffic.ingest(line(status=404, user="13", t=NOW + 6)) is True
    assert traffic.ingest(line(status=429)) is True
    assert traffic.ingest(line(status=500)) is True
    assert traffic.ingest(line(status=503)) is True
    assert traffic.ingest(line("app-2", status=200)) is True

    row = only(traffic.traffic())
    assert row == {
        "day": DAY,
        "app_id": "app-1",
        "requests": 6,
        "errors_4xx": 2,
        "errors_5xx": 2,
        "p95_ms": row["p95_ms"],
        "visitors": 2,
        "users": [
            {"user_id": 12, "requests": 2, "last_at": "2025-11-15T08:30:05Z"},
            {"user_id": 13, "requests": 1, "last_at": "2025-11-15T08:30:06Z"},
        ],
    }
    other = only(traffic.traffic(), "app-2")
    assert (other["requests"], other["visitors"], other["users"]) == (1, 0, [])
    assert traffic.dirty is True


def test_a_line_arrives_inside_a_syslog_frame():
    traffic = store()
    framed = b"<190>Nov 15 08:30:00 efp_gateway: " + line(user="12").encode("utf-8")
    assert traffic.ingest(framed) is True
    assert only(traffic.traffic())["requests"] == 1


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"<190>Nov 15 08:30:00 efp_gateway: not json",
        b"{broken",
        b"[1, 2]",
        b'{"t": 1, "app": "app-1", "status": 200}',  # no key
        line(key="someone-else").encode("utf-8"),
        line(key="").encode("utf-8"),
        line(status="oops").encode("utf-8"),
        line(t="yesterday").encode("utf-8"),
        b"\xff\xfe{",
    ],
)
def test_what_is_not_nginxs_line_is_dropped(raw):
    traffic = store()
    assert traffic.ingest(raw) is False
    assert traffic.traffic() == []
    assert traffic.rejected == 1
    assert traffic.dirty is False


def test_without_a_key_nothing_is_trusted():
    traffic = TrafficStore(None, log_key="", clock=Clock())
    assert traffic.ingest(line(key="")) is False
    assert traffic.traffic() == []


def test_requests_that_did_not_reach_an_app_are_not_counted():
    traffic = store(known=lambda app_id: app_id == "app-1")
    assert traffic.ingest(line("")) is False  # refused by the gateway: no app id in the line
    assert traffic.ingest(line("app-gone")) is False  # removed in the meantime
    assert traffic.ingest(line("app-1")) is True
    assert [row["app_id"] for row in traffic.traffic()] == ["app-1"]
    assert traffic.rejected == 0


def test_the_user_field():
    traffic = store()
    for user in ("", "-", "abc", "12", " 12 ", 12, None, True):
        traffic.ingest(line(user=user))
    row = only(traffic.traffic())
    assert row["requests"] == 8
    assert row["visitors"] == 1
    assert row["users"][0]["user_id"] == 12 and row["users"][0]["requests"] == 3


# ---- latency ---------------------------------------------------------------------------
def test_p95_comes_from_the_time_to_the_first_byte():
    traffic = store()
    for _ in range(95):
        traffic.ingest(line(uht="0.004", rt=0.004))
    for _ in range(5):
        traffic.ingest(line(uht="2.500", rt=2.5))
    # 95 of 100 took at most 5 ms.
    assert only(traffic.traffic())["p95_ms"] == 5

    traffic.ingest(line(uht="2.500", rt=2.5))
    slow = only(traffic.traffic())["p95_ms"]
    assert 2000 < slow <= 3000


def test_a_long_download_or_stream_does_not_look_slow():
    traffic = store()
    for _ in range(20):
        # Answered within 8 ms, then kept open for an hour.
        traffic.ingest(line(uht="0.008", rt=3600.0))
    assert only(traffic.traffic())["p95_ms"] <= 10


def test_latency_falls_back_to_the_request_time():
    traffic = store()
    for uht in ("-", "", None, "-, 0.004"):
        traffic.ingest(line(uht=uht, rt=0.150, status=502))
    row = only(traffic.traffic())
    assert row["errors_5xx"] == 4
    assert 100 < row["p95_ms"] <= 200

    # nginx retried: the first attempt is the one that counts.
    retried = store()
    retried.ingest(line(uht="0.004, 9.000", rt=9.1))
    retried.ingest(line(uht="0.004 : 9.000", rt=9.1))
    assert only(retried.traffic())["p95_ms"] <= 5


def test_a_websocket_without_a_first_byte_time_has_no_latency():
    traffic = store()
    traffic.ingest(line(status=101, uht="-", rt=7200.0))
    row = only(traffic.traffic())
    assert (row["requests"], row["p95_ms"]) == (1, 0)
    traffic.ingest(line(status=101, uht="0.003", rt=7200.0))
    assert only(traffic.traffic())["p95_ms"] <= 5


def test_percentile():
    empty = [0] * (len(LATENCY_BOUNDS_MS) + 1)
    assert percentile(empty, 0.95) == 0

    fast = list(empty)
    fast[0] = 100  # all within 1 ms
    assert percentile(fast, 0.95) == 1

    spread = list(empty)
    spread[LATENCY_BOUNDS_MS.index(100)] = 50  # 50..100 ms
    spread[LATENCY_BOUNDS_MS.index(200)] = 50  # 100..200 ms
    assert percentile(spread, 0.5) == 100
    assert percentile(spread, 0.95) == 190
    assert percentile(spread, 1.0) == 200

    endless = list(empty)
    endless[-1] = 10  # slower than the last bound
    assert percentile(endless, 0.95) == LATENCY_BOUNDS_MS[-1]


# ---- days ----------------------------------------------------------------------------------
def test_each_line_counts_for_the_utc_day_it_ended_on():
    clock = Clock()
    traffic = store(clock=clock)
    midnight = NOW - (NOW % 86400) + 86400
    clock.now = midnight + 1
    assert traffic.ingest(line(t=midnight - 0.5, user="12")) is True
    assert traffic.ingest(line(t=midnight + 0.5, user="12")) is True

    rows = traffic.traffic()
    assert [(row["day"], row["requests"]) for row in rows] == [(DAY, 1), ("2025-11-16", 1)]
    assert day_of(midnight - 0.5) == DAY and day_of(midnight) == "2025-11-16"


def test_yesterday_and_today_are_reported_and_older_days_dropped():
    clock = Clock()
    traffic = store(clock=clock)
    traffic.ingest(line(t=NOW))
    clock.advance(86400)
    traffic.ingest(line(t=clock.now))
    assert [row["day"] for row in traffic.traffic()] == [DAY, "2025-11-16"]

    clock.advance(86400)
    assert [row["day"] for row in traffic.traffic()] == ["2025-11-16"]
    traffic.prune()
    assert set(traffic.snapshot()["days"]) == {"2025-11-16"}

    # A line that is too old (or from the future) is not counted at all.
    assert traffic.ingest(line(t=NOW)) is False
    assert traffic.ingest(line(t=clock.now + 3 * 86400)) is False
    # A late line of yesterday still is.
    assert traffic.ingest(line(t=clock.now - 86400)) is True


# ---- on the volume ------------------------------------------------------------------------------
def test_the_numbers_survive_a_restart(tmp_path):
    clock = Clock()
    first = store(tmp_path, clock=clock)
    for index in range(40):
        first.ingest(line(user=str(12 + index % 3), status=200 if index % 10 else 500, uht="0.030", t=NOW + index))
    first.ingest(line(t=NOW - 86400))
    before = first.traffic()
    first.save()
    assert first.dirty is False
    assert (tmp_path / "metrics" / "state.json").is_file()

    second = store(tmp_path, clock=clock)
    second.load()
    assert second.traffic() == before
    assert second.dirty is False

    # ... and keep growing from where they were.
    second.ingest(line(user="12", t=NOW + 100))
    row = only(second.traffic())
    assert row["requests"] == 41
    assert row["users"][0] == {"user_id": 12, "requests": 15, "last_at": "2025-11-15T08:31:40Z"}
    assert only(second.traffic(), day=YESTERDAY)["requests"] == 1


def test_loading_drops_days_that_are_over(tmp_path):
    clock = Clock()
    first = store(tmp_path, clock=clock)
    first.ingest(line())
    first.save()
    clock.advance(3 * 86400)
    second = store(tmp_path, clock=clock)
    second.load()
    assert second.snapshot()["days"] == {}


@pytest.mark.parametrize("content", ["", "{", "[]", '{"version": 99}', '{"version": 1, "days": {"2025-11-15": {"app-1": {"users": {"x": []}}}}}'])
def test_an_unreadable_state_file_starts_afresh(tmp_path, content):
    path = tmp_path / "metrics" / "state.json"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")
    traffic = store(tmp_path)
    traffic.load()
    assert traffic.traffic() == []
    traffic.ingest(line())
    traffic.save()
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_other_latency_buckets_in_the_file_keep_the_counts_only(tmp_path):
    first = store(tmp_path)
    first.ingest(line(uht="0.500"))
    snapshot = first.snapshot()
    snapshot["bounds"] = [1, 10, 100]
    snapshot["days"][DAY]["app-1"]["latency"] = [1, 0, 0, 0]
    first.write(snapshot)
    second = store(tmp_path)
    second.load()
    row = only(second.traffic())
    assert (row["requests"], row["p95_ms"]) == (1, 0)


def test_missing_state_file_is_fine(tmp_path):
    traffic = store(tmp_path)
    traffic.load()
    assert traffic.traffic() == []
    TrafficStore(None, log_key=KEY).save()  # nowhere to save to: nothing happens


# ---- what goes to the Portal ----------------------------------------------------------------------
def test_the_payload_of_section_5_4(settings, registry, fake_runtime, fake_portal):
    clock = Clock()
    gateway = make_gateway(settings, registry, fake_runtime, fake_portal, clock=clock)
    add_app(registry, fake_runtime)
    add_app(registry, fake_runtime, "app-2", "review", deployed=False)
    key = gateway.access_log_key
    assert key == access_log_key(gateway.secret)

    gateway.metrics.ingest(line(key=key, user="12", t=NOW - 86400, status=500))
    gateway.metrics.ingest(line(key=key, user="12"))
    gateway.metrics.ingest(line(key=key, user="13", status=404))
    gateway.metrics.ingest(line(key=key))
    assert gateway.metrics.ingest(line("app-unknown", key=key)) is False
    assert gateway.metrics.ingest(line(key="forged")) is False

    payload = build_payload(gateway.metrics, fake_runtime, now=clock.now)
    assert set(payload) == {"sent_at", "traffic", "apps", "host"}
    assert payload["sent_at"] == "2025-11-15T08:30:00Z"
    assert payload["traffic"] == [
        {
            "day": YESTERDAY,
            "app_id": "app-1",
            "requests": 1,
            "errors_4xx": 0,
            "errors_5xx": 1,
            "p95_ms": payload["traffic"][0]["p95_ms"],
            "visitors": 1,
            "users": [{"user_id": 12, "requests": 1, "last_at": "2025-11-14T08:30:00Z"}],
        },
        {
            "day": DAY,
            "app_id": "app-1",
            "requests": 3,
            "errors_4xx": 1,
            "errors_5xx": 0,
            "p95_ms": payload["traffic"][1]["p95_ms"],
            "visitors": 2,
            "users": [
                {"user_id": 12, "requests": 1, "last_at": "2025-11-15T08:30:00Z"},
                {"user_id": 13, "requests": 1, "last_at": "2025-11-15T08:30:00Z"},
            ],
        },
    ]
    assert all(isinstance(row["p95_ms"], int) for row in payload["traffic"])
    assert [(item["id"], item["slug"], item["state"]) for item in payload["apps"]] == [
        ("app-1", "mindmap", "running"),
        ("app-2", "review", "undeployed"),
    ]
    assert payload["apps"][0] == fake_runtime.status("app-1").to_dict()
    assert payload["host"] == fake_runtime.host_info()
    json.dumps(payload)  # plain data all the way down


def test_push_once_sends_the_payload_and_never_raises(settings, registry, fake_runtime, fake_portal):
    clock = Clock()
    gateway = make_gateway(settings, registry, fake_runtime, fake_portal, clock=clock)
    state = HostState(settings=settings, paths=HostPaths.of(settings), registry=registry, runtime=fake_runtime, portal=fake_portal, gateway=gateway)
    add_app(registry, fake_runtime)
    gateway.metrics.ingest(line(key=gateway.access_log_key))

    assert run(background.push_once(state)) is True
    assert len(fake_portal.metrics) == 1
    assert fake_portal.metrics[0]["traffic"][0]["requests"] == 1

    # Counters are running totals: the next push repeats them.
    assert run(background.push_once(state)) is True
    assert fake_portal.metrics[1]["traffic"] == fake_portal.metrics[0]["traffic"]

    async def refuse(payload):
        raise RuntimeError("portal exploded")

    fake_portal.push_metrics = refuse
    assert run(background.push_once(state)) is False


def test_the_background_loops_push_save_and_stop(settings, registry, fake_runtime, fake_portal, monkeypatch):
    fast = dataclasses.replace(settings, metrics_push_seconds=0.05)
    gateway = make_gateway(fast, registry, fake_runtime, fake_portal)
    state = HostState(settings=fast, paths=HostPaths.of(fast), registry=registry, runtime=fake_runtime, portal=fake_portal, gateway=gateway)
    add_app(registry, fake_runtime)
    monkeypatch.setattr(background, "SAVE_SECONDS", 0.05)
    state_file = HostPaths.of(fast).metrics_state_file

    async def scenario():
        await background.start(state)
        await background.start(state)  # a second start changes nothing
        assert len(gateway.background.tasks) == 2
        gateway.metrics.record("app-1", at=gateway.clock(), status=200, latency_ms=12, user_id=12)
        for _ in range(500):  # up to ten seconds on a slow machine
            if fake_portal.metrics and state_file.is_file():
                break
            await asyncio.sleep(0.02)
        gateway.metrics.record("app-1", at=gateway.clock(), status=200, latency_ms=12, user_id=12)
        await background.stop(state)
        await background.stop(state)

    run(scenario())
    assert gateway.background is None
    assert fake_portal.metrics and fake_portal.metrics[-1]["traffic"][0]["app_id"] == "app-1"
    # What was counted last was saved on the way out.
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    (day,) = saved["days"]
    assert saved["days"][day]["app-1"]["requests"] == 2

    reloaded = make_gateway(fast, registry, fake_runtime, fake_portal)
    reloaded.metrics.load()
    assert reloaded.metrics.traffic()[0]["requests"] == 2


# ---- the socket ------------------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform != "linux", reason="unix datagram sockets")
def test_lines_sent_to_the_socket_are_counted(tmp_path):
    path = tmp_path / "access-log.sock"
    traffic = store()

    async def scenario():
        listener = AccessLogListener(path, traffic.ingest)
        assert listener.start() is True
        sender = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sender.connect(str(path))
            sender.send(b"<190>Nov 15 08:30:00 efp_gateway: " + line(user="12").encode("utf-8"))
            sender.send(line(key="forged").encode("utf-8"))
            sender.send(b"junk")
            sender.send(line(user="13").encode("utf-8"))
        finally:
            sender.close()
        for _ in range(500):
            if traffic.rejected == 2 and traffic.traffic() and traffic.traffic()[0]["requests"] == 2:
                break
            await asyncio.sleep(0.01)
        listener.stop()
        listener.stop()

    run(scenario())
    assert only(traffic.traffic())["visitors"] == 2
    assert traffic.rejected == 2
    assert not path.exists()


@pytest.mark.skipif(sys.platform != "linux", reason="unix datagram sockets")
def test_the_socket_is_writable_for_nginxs_workers_and_replaces_a_stale_one(tmp_path):
    import os

    path = tmp_path / "access-log.sock"
    path.write_text("stale", encoding="utf-8")

    async def scenario():
        listener = AccessLogListener(path, lambda data: None)
        assert listener.start() is True
        mode = os.stat(path).st_mode & 0o777
        listener.stop()
        return mode

    assert run(scenario()) == 0o666


@pytest.mark.skipif(sys.platform == "linux", reason="only where unix sockets are missing")
def test_without_unix_sockets_the_listener_stays_off(tmp_path):
    if hasattr(socket, "AF_UNIX"):
        pytest.skip("this platform has unix sockets")

    async def scenario():
        return AccessLogListener(tmp_path / "access-log.sock", lambda data: None).start()

    assert run(scenario()) is False
