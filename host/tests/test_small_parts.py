"""OS user entries, log reading, process numbers."""

from __future__ import annotations

import asyncio
import os

from efp_app_host.applog import append_note, follow, tail_lines
from efp_app_host.models import AppRecord
from efp_app_host.osusers import sync_os_users
from efp_app_host.procstats import CpuMeter, PsutilSampler, dir_size, disk_usage, free_bytes, host_numbers


# ---- OS users ------------------------------------------------------------------------


def test_os_user_entries_are_added_and_replaced(tmp_path):
    passwd, group = tmp_path / "passwd", tmp_path / "group"
    passwd.write_text("root:x:0:0:root:/root:/bin/bash\nwww-data:x:33:33:www-data:/var/www:/usr/sbin/nologin\nefpapp19000:x:19000:19000:EFP app:/nonexistent:/usr/sbin/nologin\n", encoding="utf-8")
    group.write_text("root:x:0:\nwww-data:x:33:\n", encoding="utf-8")
    records = [AppRecord(id="a", slug="one", uid=20001), AppRecord(id="b", slug="two", uid=20000), AppRecord(id="c", slug="none", uid=0)]

    assert sync_os_users(records, passwd=passwd, group=group) is True
    assert passwd.read_text(encoding="utf-8").splitlines() == [
        "root:x:0:0:root:/root:/bin/bash",
        "www-data:x:33:33:www-data:/var/www:/usr/sbin/nologin",
        "efpapp20000:x:20000:20000:EFP app:/nonexistent:/usr/sbin/nologin",
        "efpapp20001:x:20001:20001:EFP app:/nonexistent:/usr/sbin/nologin",
    ]
    assert group.read_text(encoding="utf-8").splitlines() == ["root:x:0:", "www-data:x:33:", "efpapp20000:x:20000:", "efpapp20001:x:20001:"]
    # Nothing to do the second time.
    assert sync_os_users(records, passwd=passwd, group=group) is False
    # An app that went away loses its entry.
    assert sync_os_users(records[:1], passwd=passwd, group=group) is True
    assert "efpapp20000" not in passwd.read_text(encoding="utf-8") and "efpapp20001" in passwd.read_text(encoding="utf-8")


# ---- logs ------------------------------------------------------------------------------


def test_tail_lines(tmp_path):
    log = tmp_path / "app.log"
    assert tail_lines(log, 10) == ([], False)
    log.write_bytes(b"".join(b"line %d\n" % number for number in range(1, 501)))
    lines, truncated = tail_lines(log, 3)
    assert lines == ["line 498", "line 499", "line 500"] and truncated is True
    lines, truncated = tail_lines(log, 1000)
    assert len(lines) == 500 and lines[0] == "line 1" and truncated is False
    assert tail_lines(log, 0) == ([], False)


def test_tail_lines_copes_with_long_and_unfinished_lines(tmp_path):
    log = tmp_path / "app.log"
    log.write_bytes(b"first\n" + b"y" * 200_000 + b"\nbad \xff byte\nno newline at the end")
    lines, truncated = tail_lines(log, 3)
    assert lines[1] == "bad \ufffd byte" and lines[2] == "no newline at the end"
    assert lines[0].startswith("yyy") and lines[0].endswith(" ...") and len(lines[0]) < 9000
    assert truncated is True


def test_append_note_marks_the_hosts_own_lines(tmp_path):
    log = tmp_path / "logs" / "app.log"
    append_note(log, "stopped (idle)")
    lines, _ = tail_lines(log, 5)
    assert len(lines) == 1 and lines[0].startswith("[efp-host ") and lines[0].endswith("] stopped (idle)")


def test_follow_yields_new_lines_and_survives_rotation(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("old 1\nold 2\n", encoding="utf-8")

    async def scenario() -> list:
        seen: list = []
        done = asyncio.Event()

        async def reader() -> None:
            async for line in follow(log, tail=1, poll_seconds=0.01, keepalive_seconds=0.05, stop=done.is_set):
                seen.append(line)

        async def until(count: int) -> None:
            for _ in range(500):
                if len([line for line in seen if line is not None]) >= count:
                    return
                await asyncio.sleep(0.01)
            raise AssertionError(f"only saw {seen}")

        task = asyncio.create_task(reader())
        # The reader takes its starting point when it first runs: wait for it.
        await until(1)
        await asyncio.sleep(0.1)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("new 1\nhalf")
        await until(2)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(" done\n")
        await until(3)
        # Rotation: the file starts again from nothing.
        os.replace(log, tmp_path / "app.log.1")
        log.write_text("after rotation\n", encoding="utf-8")
        await until(4)
        await asyncio.sleep(0.15)
        done.set()
        await asyncio.wait_for(task, 2)
        return seen

    seen = asyncio.run(scenario())
    lines = [line for line in seen if line is not None]
    assert lines == ["old 2", "new 1", "half done", "after rotation"]
    assert None in seen  # keepalives while nothing was written


# ---- numbers ---------------------------------------------------------------------------


def test_cpu_meter():
    meter = CpuMeter()
    assert meter.percent(20000, 10.0, now=100.0) == 0.0
    assert meter.percent(20000, 11.0, now=102.0) == 50.0
    assert meter.percent(20000, 11.0, now=104.0) == 0.0
    # A restarted process starts counting again.
    assert meter.percent(20000, 0.5, now=106.0) == 0.0
    meter.forget(20000)
    assert meter.percent(20000, 99.0, now=108.0) == 0.0


def test_dir_size_and_disk(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "one.bin").write_bytes(b"x" * 1000)
    (tmp_path / "a" / "b" / "two.bin").write_bytes(b"x" * 234)
    assert dir_size(tmp_path / "a") == 1234
    assert dir_size(tmp_path / "missing") == 0
    usage = disk_usage(tmp_path / "not" / "there" / "yet")
    assert usage["total_bytes"] > 0 and 0 <= usage["used_bytes"] <= usage["total_bytes"]
    assert (free_bytes(tmp_path) or 0) > 0


def test_host_numbers_have_the_contract_shape(tmp_path):
    numbers = host_numbers(tmp_path, tmp_path)
    assert set(numbers) == {"memory", "cpu_percent", "disk"}
    assert set(numbers["memory"]) == {"total_bytes", "used_bytes"} and numbers["memory"]["total_bytes"] > 0
    assert set(numbers["disk"]) == {"store", "local"}
    assert isinstance(numbers["cpu_percent"], float)


def test_the_real_sampler_does_not_fail_here():
    usage = PsutilSampler().sample()
    assert isinstance(usage, dict)
    assert 0 not in usage
    assert PsutilSampler().kill_uid(0) == 0


def test_the_real_sampler_sees_an_accepted_connection_on_its_port():
    import socket

    sampler = PsutilSampler()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        # Listening alone is not a connection.
        assert port not in sampler.busy_ports()
        client = socket.create_connection(("127.0.0.1", port), timeout=5)
        accepted, _ = server.accept()
        try:
            busy = sampler.busy_ports()
            if not busy:
                import pytest

                pytest.skip("this system does not let the test read the socket table")
            assert port in busy
        finally:
            client.close()
            accepted.close()
        assert port not in sampler.busy_ports()
    finally:
        server.close()
