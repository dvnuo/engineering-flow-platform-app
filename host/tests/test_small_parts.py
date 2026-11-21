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


# ---- stopping everything a user owns ---------------------------------------------------


class _ModelSampler(PsutilSampler):
    """The loop of ``kill_uid`` over a model of the process table.

    ``sitting`` are processes that stay where they are until they are
    signalled. ``forking`` stands for a process that forks and lets its
    parent exit: every look sees a new pid, and a signal to a pid that was
    seen comes too late.
    """

    def __init__(self, *, sitting: set[int], forking: bool, kill_all_works: bool = True):
        self.sitting = set(sitting)
        self.forking = forking
        self.kill_all_works = kill_all_works
        self.next_pid = 5000
        self.looks = 0
        self.kill_all_calls = 0

    def pids_of(self, uid: int) -> list[int]:
        self.looks += 1
        pids = sorted(self.sitting)
        if self.forking:
            self.next_pid += 7
            pids.append(self.next_pid)
        return pids

    def signal_pid(self, pid: int) -> None:
        self.sitting.discard(pid)  # a signal to the forking one hits a pid that is gone

    def kill_all(self, uid: int) -> bool:
        self.kill_all_calls += 1
        if self.kill_all_works:
            self.sitting.clear()
            self.forking = False
        return self.kill_all_works


def test_kill_uid_goes_on_until_a_fresh_look_finds_nothing(monkeypatch):
    from efp_app_host import procstats

    monkeypatch.setattr(procstats.time, "sleep", lambda seconds: None)
    # A process that sits still is gone after the first round; the second look confirms it.
    quiet = _ModelSampler(sitting={101, 102}, forking=False)
    assert quiet.kill_uid(20000) == 2 and quiet.looks == 2

    # One walk over the pids never gets the one that keeps forking (what
    # kill_uid used to be); the kernel's kill-everything-of-this-user does.
    chain = _ModelSampler(sitting={101}, forking=True)
    assert chain.kill_uid(20000) >= 2
    assert chain.kill_all_calls == 1 and chain.pids_of(20000) == []

    # Without that call (the manager is not root) the loop still ends, and says how far it got.
    stubborn = _ModelSampler(sitting=set(), forking=True, kill_all_works=False)
    stubborn.kill_uid(20000)
    assert stubborn.looks == procstats.KILL_ROUNDS + 1 and stubborn.kill_all_calls == procstats.KILL_ROUNDS
    assert _ModelSampler(sitting={1}, forking=True).kill_uid(0) == 0, "root is never a target"


def test_killing_a_users_processes_at_once_needs_root():
    from efp_app_host.procstats import kill_all_of

    if getattr(os, "geteuid", lambda: 1)() != 0:
        assert kill_all_of(20000) is False
    assert kill_all_of(0) is False and kill_all_of(-1) is False


def test_a_child_that_was_stopped_is_killed_instead_of_waited_for(monkeypatch):
    import signal

    from efp_app_host import procstats

    # Once the child has become the app's user, a process of the app can stop
    # it (SIGSTOP cannot be blocked) before it has sent its own signal. The
    # manager used to wait for such a child for ever.
    reports = ["stopped", "killed"]
    calls: list[tuple] = []
    monkeypatch.setattr(procstats.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(procstats.os, "fork", lambda: 4242, raising=False)
    monkeypatch.setattr(procstats.os, "WUNTRACED", 2, raising=False)
    monkeypatch.setattr(procstats.os, "WIFSTOPPED", lambda status: status == "stopped", raising=False)
    monkeypatch.setattr(procstats.os, "WIFEXITED", lambda status: status == "exited", raising=False)
    monkeypatch.setattr(procstats.os, "WEXITSTATUS", lambda status: 0, raising=False)
    monkeypatch.setattr(procstats.os, "waitpid", lambda pid, options: calls.append(("wait", pid, options)) or (pid, reports.pop(0)))
    monkeypatch.setattr(procstats.os, "kill", lambda pid, sig: calls.append(("kill", pid, sig)))
    assert procstats.kill_all_of(20000) is False, "nothing was done in this round, and the caller is told"
    sigkill = getattr(signal, "SIGKILL", signal.SIGTERM)
    assert calls == [("wait", 4242, 2), ("kill", 4242, sigkill), ("wait", 4242, 2)]
    # A child that did what it was made for is reported as ever.
    reports[:] = ["exited"]
    assert procstats.kill_all_of(20000) is True


def test_the_real_sampler_tells_whose_process_listens_on_a_port():
    import socket

    import pytest

    sampler = PsutilSampler()
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        owners = sampler.listener_uids(server.getsockname()[1])
        if owners is None:
            pytest.skip("only root sees whose socket it is")
        assert owners == {os.getuid()}
        assert sampler.listener_uids(1) in (set(), None)
    finally:
        server.close()


def test_a_listener_on_another_address_is_not_the_apps_listener(monkeypatch):
    # 127.0.0.2:port or [::]:port can be taken by anybody while the app runs;
    # neither gets what the gateway sends to 127.0.0.1:port.
    import types

    import psutil

    from efp_app_host import procstats

    def connection(ip: str, pid: int, status: str = psutil.CONN_LISTEN, port: int = 20007):
        return types.SimpleNamespace(status=status, laddr=types.SimpleNamespace(ip=ip, port=port), raddr=(), pid=pid)

    uids = {11: 20007, 12: 20009, 13: 20009}
    table = [connection("127.0.0.1", 11), connection("127.0.0.2", 12), connection("::", 13), connection("127.0.0.1", 12, port=20008)]
    monkeypatch.setattr(procstats.os, "geteuid", lambda: 0, raising=False)
    # Where the kernel's own list cannot be read, psutil is asked.
    monkeypatch.setattr(procstats, "proc_listeners", lambda: None)
    monkeypatch.setattr(psutil, "net_connections", lambda kind="tcp": list(table))
    monkeypatch.setattr(psutil, "Process", lambda pid: types.SimpleNamespace(uids=lambda: types.SimpleNamespace(real=uids[pid])))
    sampler = PsutilSampler()
    assert sampler.listener_uids(20007) == {20007}
    assert sampler.listener_uids(20008) == {20009}
    assert sampler.listener_uids(20010) == set()
    # Nothing on 127.0.0.1 itself: the wildcard address is what a connection would reach.
    table[:] = [connection("0.0.0.0", 12), connection("127.0.0.2", 11)]
    assert sampler.listener_uids(20007) == {20009}
    # A socket nobody can be named for: not known, rather than "nobody".
    table[:] = [connection("127.0.0.1", 0)]
    assert sampler.listener_uids(20007) is None


def _tcp_table(*sockets: tuple[str, int, str, int]) -> str:
    """The text of /proc/net/tcp or tcp6 for (address, port, state, uid), as this machine's kernel would print it."""

    import ipaddress
    import struct

    lines = ["  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"]
    for number, (address, port, state, uid) in enumerate(sockets):
        packed = ipaddress.ip_address(address).packed
        local = "".join(f"{struct.unpack('=I', packed[index : index + 4])[0]:08X}" for index in range(0, len(packed), 4))
        remote = "0" * len(local)
        lines.append(f"{number:4d}: {local}:{port:04X} {remote}:0000 {state} 00000000:00000000 00:00000000 00000000 {uid:5d}        0 {4000 + number} 1 0000000000000000 100 0 0 10 0")
    return "\n".join(lines) + "\n"


def test_whose_socket_listens_is_read_from_the_kernels_own_list(monkeypatch):
    # The host's container has no right over the processes of other users
    # (no SYS_PTRACE), so psutil could not name the process of an app's
    # socket there and the manager was told "not known" for every app: a
    # listener of another app passed for the app. The kernel's list of
    # sockets carries the uid and asks for no such right.
    from efp_app_host import procstats

    listen, established = "0A", "01"
    four = _tcp_table(
        ("127.0.0.1", 20007, listen, 20007),
        ("127.0.0.2", 20007, listen, 20009),
        ("127.0.0.1", 20008, listen, 20009),
        ("127.0.0.1", 20011, established, 20009),
        ("0.0.0.0", 20012, listen, 20012),
        ("10.1.2.3", 20013, listen, 20009),
    )
    six = _tcp_table(("::", 20007, listen, 20009), ("::ffff:127.0.0.1", 20014, listen, 20014), ("::1", 20008, listen, 20007), ("::", 20012, listen, 20009))
    assert procstats.parse_tcp_table(four) == [("127.0.0.1", 20007, 20007), ("127.0.0.1", 20008, 20009), ("0.0.0.0", 20012, 20012)]
    assert procstats.parse_tcp_table(six) == [("::", 20007, 20009), ("::ffff:127.0.0.1", 20014, 20014), ("::", 20012, 20009)]
    assert procstats.parse_tcp_table(_tcp_table()) == []
    # A list this code does not know says nothing, instead of something wrong.
    assert procstats.parse_tcp_table("sl local\n   0: 0100007F:4E27 00\n") is None
    assert procstats.parse_tcp_table(four.replace(" 20007 ", " user ", 1)) is None
    assert procstats.parse_tcp_table("header\n   0: 7F:4E27 00000000:0000 0A 0:0 0:0 0 20007 0 1\n") is None

    tables = {"/proc/net/tcp": four, "/proc/net/tcp6": six}

    def read(path, *args, **kwargs):
        import io

        if path not in tables:
            raise FileNotFoundError(path)
        return io.StringIO(tables[path])

    monkeypatch.setattr(procstats, "open", read, raising=False)
    monkeypatch.setattr(procstats.os, "geteuid", lambda: 0, raising=False)
    sampler = PsutilSampler()
    # The most specific address that is taken gets the connection.
    assert sampler.listener_uids(20007) == {20007}
    assert sampler.listener_uids(20008) == {20009}, "the app's port, held by another app"
    assert sampler.listener_uids(20012) == {20012}
    assert sampler.listener_uids(20014) == {20014}
    # Connected, on another address, or nothing at all: nobody listens.
    assert sampler.listener_uids(20011) == set() and sampler.listener_uids(20013) == set() and sampler.listener_uids(20099) == set()
    # A kernel without IPv6 has one list only.
    del tables["/proc/net/tcp6"]
    assert sampler.listener_uids(20007) == {20007} and sampler.listener_uids(20014) == set()
    # A manager that is not root runs every app as itself: there is nothing to compare.
    monkeypatch.setattr(procstats.os, "geteuid", lambda: 1000, raising=False)
    assert sampler.listener_uids(20007) is None


def test_the_kernels_list_names_the_user_of_a_real_listener():
    import socket

    import pytest

    from efp_app_host import procstats

    listeners = procstats.proc_listeners()
    if listeners is None:
        pytest.skip("this system has no /proc/net/tcp")
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    try:
        # Bound is not listening.
        assert not [row for row in procstats.proc_listeners() or [] if row[1] == port]
        server.listen(1)
        assert [row for row in procstats.proc_listeners() or [] if row[1] == port] == [("127.0.0.1", port, os.getuid())]
    finally:
        server.close()
    assert not [row for row in procstats.proc_listeners() or [] if row[1] == port]


# ---- root's file operations where an app can write -------------------------------------


def test_a_fresh_directory_replaces_what_was_there(tmp_path):
    from efp_app_host.safefs import fresh_dir

    tmp = tmp_path / "app" / "tmp"
    (tmp / "home" / "old").mkdir(parents=True)
    (tmp / "left.txt").write_text("x", encoding="utf-8")
    fresh_dir(tmp, 0o700, children=("home",))
    assert sorted(item.name for item in tmp.iterdir()) == ["home"] and list((tmp / "home").iterdir()) == []
    # A file where the directory should be is replaced as well; missing parents are made.
    (tmp_path / "other").write_text("x", encoding="utf-8")
    fresh_dir(tmp_path / "other", 0o700)
    fresh_dir(tmp_path / "deep" / "er" / "wheels", 0o750)
    assert (tmp_path / "other").is_dir() and (tmp_path / "deep" / "er" / "wheels").is_dir()


def test_a_directory_that_cannot_be_emptied_is_not_handed_over(tmp_path, monkeypatch):
    import pytest

    from efp_app_host import safefs

    tmp = tmp_path / "tmp"
    tmp.mkdir()
    # A process of the app that puts its entry back as fast as it is removed.
    monkeypatch.setattr(safefs, "remove_tree", lambda path: None)
    with pytest.raises(safefs.UnsafePath, match="could not be emptied"):
        safefs.fresh_dir(tmp, 0o700, uid=20000, children=("home",))
    assert not (tmp / "home").exists()


def test_a_link_is_refused_where_no_descriptor_calls_exist(tmp_path, monkeypatch):
    # The fallback (Windows, where the host only runs its tests) must refuse too.
    import pytest

    from efp_app_host import safefs

    target = tmp_path / "home"
    target.mkdir()
    monkeypatch.setattr(safefs, "_BY_DESCRIPTOR", False)
    monkeypatch.setattr(safefs.os.path, "islink", lambda path: str(path) == str(target))
    with pytest.raises(safefs.UnsafePath):
        safefs.own(target, 0o700, uid=20000)
    with pytest.raises(safefs.UnsafePath):
        safefs.make_dir(target, 0o700, uid=20000)
    # "follow" is for the directories an operator names.
    safefs.make_dir(target, 0o755, follow=True)


def test_copy_from_app_copies_a_plain_file(tmp_path):
    from efp_app_host.safefs import copy_from_app

    (tmp_path / "pkg-1.0-py3-none-any.whl").write_bytes(b"wheel" * 1000)
    assert copy_from_app(tmp_path / "pkg-1.0-py3-none-any.whl", tmp_path / "kept.whl") == 5000
    assert (tmp_path / "kept.whl").read_bytes() == b"wheel" * 1000


def _needs_links():
    import sys

    import pytest

    return pytest.mark.skipif(sys.platform != "linux", reason="symbolic links need Linux here")


@_needs_links()
def test_root_does_not_follow_a_link_an_app_planted(tmp_path):
    import stat

    import pytest

    from efp_app_host.safefs import UnsafePath, copy_from_app, fresh_dir, make_dir, own

    gate = tmp_path / "var-lib-efp-host"  # stands for the directory that closes the volume
    gate.mkdir(mode=0o711)
    secret = gate / "session.key"
    secret.write_text("secret", encoding="utf-8")
    tmp = tmp_path / "app" / "tmp"
    tmp.mkdir(parents=True)
    os.symlink(gate, tmp / "home")

    # What the manager used to do: chmod and chown through "tmp/home".
    with pytest.raises(UnsafePath):
        make_dir(tmp / "home", 0o700, uid=os.getuid())
    with pytest.raises(UnsafePath):
        own(tmp / "home", 0o700, uid=os.getuid())
    assert stat.S_IMODE(os.stat(gate).st_mode) == 0o711

    # The reset of a tmp: the link goes, a directory of root's making takes its place.
    fresh_dir(tmp, 0o700, uid=os.getuid(), children=("home",))
    assert (tmp / "home").is_dir() and not (tmp / "home").is_symlink()
    assert stat.S_IMODE(os.stat(gate).st_mode) == 0o711 and secret.read_text(encoding="utf-8") == "secret"

    # A link where the directory itself was (the app may write its parent) is not followed either.
    area = tmp_path / "area"
    area.mkdir()
    os.symlink(gate, area / "wheels")
    fresh_dir(area / "wheels", 0o700)
    assert not (area / "wheels").is_symlink() and secret.exists()

    # Copying what an app wrote: a link is not read through, neither symbolic nor hard.
    os.symlink(secret, area / "wheels" / "evil-1.0-py3-none-any.whl")
    os.link(secret, area / "wheels" / "hard-1.0-py3-none-any.whl")
    for name in ("evil-1.0-py3-none-any.whl", "hard-1.0-py3-none-any.whl"):
        with pytest.raises(UnsafePath):
            copy_from_app(area / "wheels" / name, tmp_path / "stolen")
    assert not (tmp_path / "stolen").exists() or (tmp_path / "stolen").read_bytes() == b""


@_needs_links()
def test_a_planted_link_does_not_get_its_target_chowned(tmp_path):
    import pytest

    from efp_app_host.safefs import UnsafePath, fresh_dir, make_dir

    if os.geteuid() != 0:
        pytest.skip("chown needs root")
    base = tmp_path
    os.chmod(base, 0o755)
    gate = base / "gate"
    gate.mkdir(mode=0o700)
    tmp = base / "tmp"
    tmp.mkdir()
    os.chown(tmp, 23456, 23456)
    os.symlink(gate, tmp / "home")
    with pytest.raises(UnsafePath):
        make_dir(tmp / "home", 0o700, uid=23456)
    fresh_dir(tmp, 0o700, uid=23456, children=("home",))
    assert (os.stat(gate).st_uid, os.stat(gate).st_gid) == (0, 0)
    assert os.stat(tmp).st_uid == 23456 and os.lstat(tmp / "home").st_uid == 23456 and not (tmp / "home").is_symlink()
