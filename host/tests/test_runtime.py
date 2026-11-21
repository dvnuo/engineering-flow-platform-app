from __future__ import annotations

import asyncio
import json
import os
import stat
import sys

import pytest
from runtime_helpers import FASTAPI_FILES, SHA_A, SHA_B, STATIC_FILES, Harness

from efp_app_host.interfaces import AppRuntime
from efp_app_host.isolation import IsolationStatus
from efp_app_host.procstats import UidUsage
from efp_app_host.registry import AppNotFound
from efp_app_host.runtime import CRASH_BACKOFF_SECONDS, VISIT_RETRY_SECONDS, AppBusy, ReleaseNotFound

MIB = 1024 * 1024
PROGRAM = "app-app-1"


async def _deployed(tmp_path, **kwargs) -> Harness:
    files = kwargs.pop("files", None)
    harness = Harness(tmp_path, **kwargs)
    await harness.register()
    job = await harness.deploy(files=files)
    assert job.status == "succeeded", job.error
    return harness


def test_it_is_an_app_runtime(tmp_path):
    assert isinstance(Harness(tmp_path).runtime, AppRuntime)


def test_status_before_and_after_registration(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        with pytest.raises(AppNotFound):
            harness.runtime.status("app-1")
        status = await harness.register()
        return harness, status

    harness, status = asyncio.run(scenario())
    record = harness.registry.get("app-1")
    assert status.to_dict() == {
        "id": "app-1",
        "slug": "mindmap",
        "type": None,
        "state": "undeployed",
        "reason": "not_deployed",
        "release": None,
        "previous_release": None,
        "uptime_seconds": 0,
        "restarts": 0,
        "rss_bytes": 0,
        "cpu_percent": 0.0,
        "data_bytes": 0,
        "last_activity_at": None,
        "port": record.port,
        "last_log_lines": [],
    }
    assert harness.runtime.upstream("app-1") is None and harness.runtime.upstream("missing") is None
    # Registration made the app's directories and its supervisor program; nothing runs.
    assert harness.paths.data_dir("app-1").is_dir() and harness.paths.logs_dir("app-1").is_dir()
    assert harness.paths.local_tmp_dir("app-1").is_dir()
    program = harness.controller.programs[PROGRAM]
    assert program.command[-1] == (harness.paths.run / "launch" / "app-app-1.json").as_posix()
    assert program.log_file == harness.paths.log_file("app-1").as_posix()
    assert harness.controller.started(PROGRAM) == 0
    assert harness.registry.read_env("app-1")["EFP_APP_TOKEN"].startswith("efpa_")


def test_a_running_app_and_its_upstream(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        harness.clock.advance(120)
        return harness

    harness = asyncio.run(scenario())
    record = harness.registry.get("app-1")
    status = harness.runtime.status("app-1")
    assert (status.state, status.reason, status.type) == ("running", None, "fastapi")
    assert status.release["sha"] == SHA_A and status.release["deployed_at"].endswith("Z")
    assert status.uptime_seconds == 120 and status.last_log_lines == []
    upstream = harness.runtime.upstream("app-1")
    assert (upstream.kind, upstream.host, upstream.port, upstream.strip_prefix) == ("proxy", "127.0.0.1", record.port, True)
    assert harness.events("started") == [{"app_id": "app-1", "kind": "started", "detail": {"sha": SHA_A}}]
    info = harness.runtime.host_info()
    assert info["apps_total"] == 1 and info["apps_running"] == 1
    assert info["isolation_mode"] == "sandbox" and info["sandbox_backend"] == "bwrap" and "isolation_error" not in info
    assert set(info) >= {"version", "memory", "cpu_percent", "disk", "started_at"}


def test_the_launch_file_holds_the_whole_process_and_is_private(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register(env={"EFP_APP_TOKEN": "efpa_token", "MY_SETTING": "x"})
        captured = {}
        original = harness.controller.start

        def start(name):
            with open(harness.paths.run / "launch" / f"{name}.json", encoding="utf-8") as handle:
                captured["spec"] = json.load(handle)
            original(name)

        harness.controller.start = start
        await harness.deploy()
        return harness, captured["spec"]

    harness, spec = asyncio.run(scenario())
    record = harness.registry.get("app-1")
    release = harness.paths.release_dir("app-1", SHA_A)
    assert spec["argv"][1:4] == ["-m", "uvicorn", "main:app"] and spec["argv"][0] == (release / "venv" / "bin" / "python").as_posix()
    assert (spec["uid"], spec["gid"], spec["backend"], spec["nproc"]) == (record.uid, record.uid, "bwrap", harness.settings.app_nproc)
    assert spec["cwd"] == (release / "code").as_posix()
    assert spec["ro_paths"] == [release.as_posix()] and spec["rw_paths"] == [harness.paths.data_dir("app-1").as_posix()]
    assert spec["tmp_dir"] == harness.paths.local_tmp_dir("app-1").as_posix()
    assert spec["env"]["EFP_APP_TOKEN"] == "efpa_token" and spec["env"]["MY_SETTING"] == "x" and spec["env"]["PORT"] == str(record.port)
    # The manager's own secret is never part of an app's environment.
    assert harness.settings.api_token not in json.dumps(spec)
    # The supervisor program itself carries no environment.
    assert all("efpa_token" not in part for part in harness.controller.programs[PROGRAM].command)


def test_ensure_running_returns_at_once_and_starts_in_the_background(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        await harness.runtime.stop("app-1", "manual")
        stopped = harness.runtime.status("app-1")
        gate = asyncio.Event()

        async def slow_health(url):
            await gate.wait()
            return 200

        harness.runtime.health = slow_health
        first = await harness.runtime.ensure_running("app-1")
        second = await harness.runtime.ensure_running("app-1")
        starts = harness.controller.started(PROGRAM)
        gate.set()
        await harness.settle()
        third = await harness.runtime.ensure_running("app-1")
        return harness, stopped, first, second, starts, third

    harness, stopped, first, second, starts, third = asyncio.run(scenario())
    assert (stopped.state, stopped.reason) == ("stopped", "manual")
    assert first.state == "starting" and second.state == "starting" and third.state == "running"
    # Two visits while it comes up cause one start (plus the one of the deployment),
    # and neither visit waited for it.
    assert harness.controller.started(PROGRAM) == 2 and starts <= 2
    # The private tmp is emptied for every start and leftovers of the app's user are cleared.
    assert list(harness.paths.local_tmp_dir("app-1").iterdir()) == [harness.paths.local_tmp_dir("app-1") / "home"]
    assert harness.sampler.killed.count(harness.registry.get("app-1").uid) >= 2


def test_ensure_running_does_nothing_for_apps_that_cannot_start(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        undeployed = await harness.runtime.ensure_running("app-1")
        await harness.register("app-2", "docs")
        await harness.deploy("app-2", files=STATIC_FILES)
        static = await harness.runtime.ensure_running("app-2")
        await harness.register("app-3", "off", enabled=False, disabled_reason="taken down")
        await harness.deploy("app-3", SHA_B)
        disabled = await harness.runtime.ensure_running("app-3")
        started = await harness.runtime.start("app-3")
        with pytest.raises(AppNotFound):
            await harness.runtime.ensure_running("missing")
        return harness, undeployed, static, disabled, started

    harness, undeployed, static, disabled, started = asyncio.run(scenario())
    assert (undeployed.state, undeployed.reason) == ("undeployed", "not_deployed")
    assert static.state == "running"
    assert (disabled.state, disabled.reason) == ("disabled", "taken down") and started.state == "disabled"
    assert harness.controller.calls.count(("start", "app-app-3")) == 0 and harness.controller.calls.count(("start", "app-app-2")) == 0


def test_no_isolation_means_no_app_is_started_and_the_status_says_why(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        await harness.runtime.stop("app-1", "manual")
        harness.runtime.isolation = IsolationStatus(mode="sandbox", error="no sandbox backend works (bwrap: Operation not permitted; landlock: not available)")
        starts = harness.controller.started(PROGRAM)
        visit = await harness.runtime.ensure_running("app-1")
        manual = await harness.runtime.start("app-1")
        restarted = await harness.runtime.restart("app-1")
        await harness.settle()
        return harness, starts, visit, manual, restarted

    harness, starts, visit, manual, restarted = asyncio.run(scenario())
    for status in (visit, manual, restarted):
        assert (status.state, status.reason) == ("failed", "isolation_unavailable")
        assert "no sandbox backend works" in status.last_log_lines[0]
    assert harness.controller.started(PROGRAM) == starts
    info = harness.runtime.host_info()
    assert info["sandbox_backend"] is None and "no sandbox backend works" in info["isolation_error"]


def test_a_crash_is_reported_and_restarted_with_back_off(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        runtime, controller, clock = harness.runtime, harness.controller, harness.clock
        with open(harness.paths.log_file("app-1"), "a", encoding="utf-8") as handle:
            handle.write("Traceback (most recent call last):\nValueError: boom efpa_" + "t" * 43 + "\n")
        seen = []

        controller.exit(PROGRAM, 1)
        await runtime.monitor_once()
        seen.append(runtime.status("app-1"))
        # Too early for the first restart.
        clock.advance(CRASH_BACKOFF_SECONDS[0] - 0.5)
        await runtime.monitor_once()
        seen.append(runtime.status("app-1"))
        clock.advance(1)
        await runtime.monitor_once()
        await harness.settle()
        seen.append(runtime.status("app-1"))

        # It keeps crashing: the delays grow, then the host gives up.
        for delay in CRASH_BACKOFF_SECONDS[1:]:
            controller.exit(PROGRAM, 1)
            await runtime.monitor_once()
            clock.advance(delay + 0.1)
            await runtime.monitor_once()
            await harness.settle()
        seen.append(runtime.status("app-1"))
        controller.exit(PROGRAM, 139)
        await runtime.monitor_once()
        clock.advance(3600)
        await runtime.monitor_once()
        await harness.settle()
        seen.append(runtime.status("app-1"))
        return harness, seen

    harness, seen = asyncio.run(scenario())
    crashed, waiting, restarted, third, gave_up = seen
    assert (crashed.state, crashed.reason, crashed.restarts) == ("crashed", "exit:1", 0)
    assert crashed.last_log_lines[-2] == "Traceback (most recent call last):"
    assert crashed.last_log_lines[-1] == "ValueError: boom ***"  # the token is blanked
    assert waiting.state == "crashed"
    assert (restarted.state, restarted.restarts) == ("running", 1)
    assert (third.state, third.restarts) == ("running", 3)
    assert (gave_up.state, gave_up.reason, gave_up.restarts) == ("crashed", "exit:139", 3)
    events = harness.events("crashed")
    assert len(events) == 4
    assert events[0]["detail"]["exit_code"] == 1 and events[0]["detail"]["log"][-1] == "ValueError: boom ***"
    assert events[-1]["detail"]["exit_code"] == 139
    log, _ = harness.runtime.log_tail("app-1", 50)
    assert any("restarting after a crash (restart 1)" in line for line in log)
    assert any("stays down until it is opened or started again" in line for line in log)


def test_a_visit_brings_a_crashed_app_back_after_a_pause(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        runtime, clock = harness.runtime, harness.clock
        for _ in range(len(CRASH_BACKOFF_SECONDS)):
            harness.controller.exit(PROGRAM, 1)
            await runtime.monitor_once()
            clock.advance(60)
            await runtime.monitor_once()
            await harness.settle()
        harness.controller.exit(PROGRAM, 1)
        await runtime.monitor_once()
        early = await runtime.ensure_running("app-1")
        clock.advance(VISIT_RETRY_SECONDS + 1)
        later = await runtime.ensure_running("app-1")
        await harness.settle()
        return early, later, runtime.status("app-1")

    early, later, final = asyncio.run(scenario())
    assert early.state == "crashed" and later.state == "starting" and final.state == "running"


def test_an_app_that_exits_while_starting_is_a_crash(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        await harness.runtime.stop("app-1", "manual")
        harness.controller.fail_start[PROGRAM] = "can't find command '/nope'"
        status = await harness.runtime.start("app-1")
        await harness.settle()
        return harness, status

    harness, status = asyncio.run(scenario())
    assert status.state == "starting"
    final = harness.runtime.status("app-1")
    assert (final.state, final.reason) == ("crashed", "exit:unknown")
    assert any("can't find command" in line for line in final.last_log_lines)


def test_an_app_that_never_gets_healthy_is_stopped_as_failed(tmp_path):
    files = dict(FASTAPI_FILES)
    files["efp-app.json"] = json.dumps({"name": "Mind map", "type": "fastapi", "entry": "main:app", "startup_timeout": 1})

    async def scenario():
        harness = await _deployed(tmp_path, files=files)
        await harness.runtime.stop("app-1", "manual")
        harness.health.status = None
        await harness.runtime.start("app-1")
        await harness.settle()
        failed = harness.runtime.status("app-1")
        process = harness.controller.info(PROGRAM).state
        early = await harness.runtime.ensure_running("app-1")
        harness.health.status = 200
        harness.clock.advance(VISIT_RETRY_SECONDS + 1)
        later = await harness.runtime.ensure_running("app-1")
        await harness.settle()
        return harness, failed, process, early, later

    harness, failed, process, early, later = asyncio.run(scenario())
    assert (failed.state, failed.reason) == ("failed", "startup_timeout")
    assert any("not healthy within 1 seconds" in line for line in failed.last_log_lines)
    # The process that never answered was taken down, not left running.
    assert process == "stopped"
    assert early.state == "failed" and later.state == "starting"
    assert harness.runtime.status("app-1").state == "running"


def test_a_health_answer_below_500_counts_as_up(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        harness.health.status = 404
        await harness.register()
        return await harness.deploy(), harness

    job, harness = asyncio.run(scenario())
    assert job.status == "succeeded" and harness.runtime.status("app-1").state == "running"


def test_idle_apps_are_stopped_and_a_visit_keeps_them_alive(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, idle_stop_minutes=30)
        runtime, clock = harness.runtime, harness.clock
        clock.advance(29 * 60)
        await runtime.idle_once()
        still = runtime.status("app-1").state
        runtime.touch("app-1")
        clock.advance(29 * 60)
        await runtime.idle_once()
        touched = runtime.status("app-1")
        clock.advance(2 * 60)
        await runtime.idle_once()
        await harness.settle()
        stopped = runtime.status("app-1")
        visit = await runtime.ensure_running("app-1")
        await harness.settle()
        return harness, still, touched, stopped, visit

    harness, still, touched, stopped, visit = asyncio.run(scenario())
    assert still == "running" and touched.state == "running" and touched.last_activity_at.endswith("Z")
    assert (stopped.state, stopped.reason) == ("stopped", "idle")
    assert ("stop", PROGRAM) in harness.controller.calls
    assert visit.state == "starting" and harness.runtime.status("app-1").state == "running"
    (event,) = harness.events("idle_stopped")
    assert event["detail"]["idle_seconds"] >= 30 * 60


def test_an_open_connection_keeps_an_app_from_being_stopped_as_idle(tmp_path):
    """One WebSocket is one request for the gateway: it touches the app once, then nothing for hours."""

    async def scenario():
        harness = await _deployed(tmp_path, idle_stop_minutes=30)
        runtime, clock, sampler = harness.runtime, harness.clock, harness.sampler
        port = harness.registry.get("app-1").port
        # Not idle yet: nobody needs to look at connections.
        clock.advance(10 * 60)
        await runtime.idle_once()
        looked_early = sampler.busy_calls

        sampler.busy = {port, 45678}
        clock.advance(25 * 60)
        await runtime.idle_once()
        kept = runtime.status("app-1")
        clock.advance(31 * 60)
        await runtime.idle_once()
        still = runtime.status("app-1")

        # A connection on another app's port does not count.
        sampler.busy = {port + 1}
        clock.advance(31 * 60)
        await runtime.idle_once()
        await harness.settle()
        return harness, looked_early, kept, still, runtime.status("app-1")

    harness, looked_early, kept, still, stopped = asyncio.run(scenario())
    assert looked_early == 0
    assert kept.state == "running" and still.state == "running"
    # The open connection counts as activity: the status shows the app in use.
    assert still.last_activity_at > kept.last_activity_at
    assert (stopped.state, stopped.reason) == ("stopped", "idle")
    assert len(harness.events("idle_stopped")) == 1


def test_idle_stop_survives_a_sampler_that_cannot_see_connections(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, idle_stop_minutes=30)

        def broken():
            raise PermissionError("no access to the socket table")

        harness.sampler.busy_ports = broken
        harness.clock.advance(31 * 60)
        await harness.runtime.idle_once()
        return harness.runtime.status("app-1")

    status = asyncio.run(scenario())
    assert (status.state, status.reason) == ("stopped", "idle")


def test_idle_stop_can_be_switched_off(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, idle_stop_minutes=0)
        harness.clock.advance(10 * 86400)
        await harness.runtime.idle_once()
        return harness.runtime.status("app-1").state

    assert asyncio.run(scenario()) == "running"


def test_the_watchdog_kills_an_app_over_its_memory_ceiling(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, app_memory_mb=256)
        runtime = harness.runtime
        uid = harness.registry.get("app-1").uid
        harness.sampler.usage = {uid: UidUsage(rss_bytes=200 * MIB, cpu_seconds=5.0, pids=[1, 2])}
        await runtime.watchdog_once()
        fine = runtime.status("app-1")
        kills = len(harness.sampler.killed)
        harness.sampler.usage = {uid: UidUsage(rss_bytes=300 * MIB, cpu_seconds=6.0, pids=[1, 2])}
        await runtime.watchdog_once()
        await harness.settle()
        killed = runtime.status("app-1")
        # The next visit starts it again.
        visit = await runtime.ensure_running("app-1")
        await harness.settle()
        return harness, fine, kills, killed, visit

    harness, fine, kills, killed, visit = asyncio.run(scenario())
    assert fine.state == "running" and fine.rss_bytes == 200 * MIB
    assert (killed.state, killed.reason, killed.rss_bytes) == ("killed", "memory", 0)
    assert any("killed: 300 MiB of memory in use, the limit is 256 MiB" in line for line in killed.last_log_lines)
    assert len(harness.sampler.killed) > kills and harness.controller.calls[-2] == ("stop", PROGRAM)
    (event,) = harness.events("killed")
    assert event["detail"]["reason"] == "memory" and event["detail"]["rss_bytes"] == 300 * MIB and event["detail"]["limit_bytes"] == 256 * MIB
    assert visit.state == "starting"


def test_a_limit_of_the_app_wins_over_the_hosts_default(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, app_memory_mb=256)
        await harness.register(memory_mb=512)
        await harness.deploy()
        uid = harness.registry.get("app-1").uid
        harness.sampler.usage = {uid: UidUsage(rss_bytes=300 * MIB)}
        await harness.runtime.watchdog_once()
        return harness.runtime.status("app-1").state

    assert asyncio.run(scenario()) == "running"


def test_memory_is_not_enforced_while_a_deployment_runs_beside_the_app(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, app_memory_mb=256)
        uid = harness.registry.get("app-1").uid
        harness.runtime.proc("app-1").candidate_active = True
        harness.sampler.usage = {uid: UidUsage(rss_bytes=400 * MIB)}
        await harness.runtime.watchdog_once()
        return harness.runtime.status("app-1").state

    assert asyncio.run(scenario()) == "running"


def test_data_over_the_quota_is_reported_once(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, app_data_mb=1)
        runtime = harness.runtime
        data = harness.paths.data_dir("app-1")
        (data / "small.bin").write_bytes(b"x" * 1000)
        await runtime.scan_data_once()
        small = runtime.status("app-1").data_bytes
        (data / "big.bin").write_bytes(b"x" * (2 * MIB))
        await runtime.scan_data_once()
        await runtime.scan_data_once()
        await harness.settle()
        first = len(harness.events("quota_exceeded"))
        (data / "big.bin").unlink()
        await runtime.scan_data_once()
        (data / "big.bin").write_bytes(b"x" * (2 * MIB))
        await runtime.scan_data_once()
        await harness.settle()
        return harness, small, first

    harness, small, first = asyncio.run(scenario())
    assert small == 1000 and first == 1
    events = harness.events("quota_exceeded")
    assert len(events) == 2 and events[0]["detail"] == {"data_bytes": 2 * MIB + 1000, "limit_bytes": MIB}
    # A soft quota: the app keeps running.
    assert harness.runtime.status("app-1").state == "running"


def test_a_changed_environment_restarts_a_running_app_only(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        runtime = harness.runtime
        starts = harness.controller.started(PROGRAM)
        same = await runtime.set_env("app-1", harness.registry.read_env("app-1"))
        unchanged = harness.controller.started(PROGRAM) - starts
        changed = await runtime.set_env("app-1", {"EFP_APP_TOKEN": "efpa_new", "MODE": "x"})
        await harness.settle()
        restarted = harness.controller.started(PROGRAM) - starts
        await runtime.stop("app-1", "manual")
        await runtime.set_env("app-1", {"EFP_APP_TOKEN": "efpa_newer"})
        await harness.settle()
        return harness, same, unchanged, changed, restarted, starts

    harness, same, unchanged, changed, restarted, starts = asyncio.run(scenario())
    assert same.state == "running" and unchanged == 0
    assert changed.state == "starting" and restarted == 1
    assert harness.controller.started(PROGRAM) == starts + 1
    assert harness.runtime.status("app-1").state == "stopped"
    assert harness.registry.read_env("app-1") == {"EFP_APP_TOKEN": "efpa_newer"}


def test_registering_again_with_a_new_token_restarts_and_disabling_stops(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        starts = harness.controller.started(PROGRAM)
        status = await harness.register(env={"EFP_APP_TOKEN": "efpa_rotated"})
        await harness.settle()
        rotated = harness.controller.started(PROGRAM) - starts
        disabled = await harness.register(env={"EFP_APP_TOKEN": "efpa_rotated"}, enabled=False, disabled_reason="taken down by an administrator")
        visit = await harness.runtime.ensure_running("app-1")
        enabled = await harness.register(env={"EFP_APP_TOKEN": "efpa_rotated"}, enabled=True)
        return harness, status, rotated, disabled, visit, enabled

    harness, status, rotated, disabled, visit, enabled = asyncio.run(scenario())
    assert status.state == "starting" and rotated == 1
    assert (disabled.state, disabled.reason) == ("disabled", "taken down by an administrator")
    assert harness.controller.info(PROGRAM).state == "stopped"
    assert visit.state == "disabled"
    assert enabled.state == "stopped"


def test_rollback_switches_to_the_retained_release_and_restarts(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        await harness.deploy(sha=SHA_B)
        with pytest.raises(ReleaseNotFound):
            await harness.runtime.activate("app-1", "f" * 40)
        status = await harness.runtime.activate("app-1", SHA_A)
        await harness.settle()
        return harness, status

    harness, status = asyncio.run(scenario())
    record = harness.registry.get("app-1")
    assert (record.current_sha, record.previous_sha) == (SHA_A, SHA_B)
    assert status.state == "starting" and status.release["sha"] == SHA_A and status.previous_release["sha"] == SHA_B
    assert harness.runtime.status("app-1").state == "running"
    # Both releases are still on disk: the switch can be undone.
    assert harness.paths.release_dir("app-1", SHA_A).is_dir() and harness.paths.release_dir("app-1", SHA_B).is_dir()
    assert harness.controller.calls[-2:] == [("stop", PROGRAM), ("start", PROGRAM)]


def test_nothing_switches_under_a_running_deployment(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        await harness.deploy(sha=SHA_B)
        harness.runner.delay = 0.2
        from runtime_helpers import SHA_C, make_bundle

        job = harness.runtime.deploy("app-1", sha=SHA_C, bundle=make_bundle(tmp_path / "c.tar.gz", FASTAPI_FILES))
        await asyncio.sleep(0.01)
        with pytest.raises(AppBusy):
            await harness.runtime.activate("app-1", SHA_A)
        with pytest.raises(AppBusy):
            await harness.runtime.delete("app-1", purge_data=False)
        await harness.runtime.deployer.wait(job.id)
        await harness.settle()
        return harness.runtime.jobs.get(job.id)

    assert asyncio.run(scenario()).status == "succeeded"


def test_clear_data_empties_the_directory_and_restarts(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        data = harness.paths.data_dir("app-1")
        (data / "db.sqlite").write_bytes(b"x" * 100)
        (data / "objects").mkdir(exist_ok=True)
        (data / "objects" / "a.txt").write_text("a", encoding="utf-8")
        await harness.runtime.scan_data_once()
        before = harness.runtime.status("app-1").data_bytes
        status = await harness.runtime.clear_data("app-1")
        await harness.settle()
        return harness, before, status

    harness, before, status = asyncio.run(scenario())
    assert before == 101
    assert status.state == "starting" and status.data_bytes == 0
    assert list(harness.paths.data_dir("app-1").iterdir()) == []
    assert harness.runtime.status("app-1").state == "running"
    # The release is untouched.
    assert harness.paths.release_dir("app-1", SHA_A).is_dir()


def test_delete_keeps_the_data_unless_purged(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        (harness.paths.data_dir("app-1") / "keep.txt").write_text("k", encoding="utf-8")
        await harness.runtime.delete("app-1", purge_data=False)
        after_delete = {
            "registered": harness.registry.find("app-1") is not None,
            "program": PROGRAM in harness.controller.programs,
            "data": (harness.paths.data_dir("app-1") / "keep.txt").is_file(),
            "releases": (harness.paths.app_dir("app-1") / "releases").exists(),
            "env": harness.paths.env_file("app-1").exists(),
            "logs": harness.paths.logs_dir("app-1").exists(),
            "local": harness.paths.local_app_dir("app-1").exists(),
        }
        # Registered again under the same id, the data is still there.
        await harness.register()
        kept = (harness.paths.data_dir("app-1") / "keep.txt").is_file()
        await harness.runtime.delete("app-1", purge_data=True)
        # Deleting what is already gone is not an error.
        await harness.runtime.delete("app-1", purge_data=True)
        return harness, after_delete, kept

    harness, after_delete, kept = asyncio.run(scenario())
    assert after_delete == {"registered": False, "program": False, "data": True, "releases": False, "env": False, "logs": False, "local": False}
    assert kept is True
    assert not harness.paths.app_dir("app-1").exists()
    assert harness.controller.info(PROGRAM).state == "absent"


def test_the_manager_adopts_apps_that_kept_running_across_its_restart(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        # A new manager process: same registry, same supervisor, nothing in memory.
        fresh = Harness(tmp_path)
        fresh.runtime.controller = harness.controller
        assert fresh.runtime.status("app-1").state == "stopped"
        adopted = fresh.runtime.adopt()
        return adopted, fresh.runtime.status("app-1")

    adopted, status = asyncio.run(scenario())
    assert adopted == 1 and status.state == "running"


def test_logs_are_read_with_secrets_blanked(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register(env={"EFP_APP_TOKEN": "efpa_secret-token-value"})
        log = harness.paths.log_file("app-1")
        with open(log, "w", encoding="utf-8") as handle:
            handle.write("".join(f"line {number}\n" for number in range(1, 301)) + "token efpa_secret-token-value\n")
        lines, truncated = harness.runtime.log_tail("app-1", 2)
        seen = []
        stop = {"now": False}

        async def read():
            async for line in harness.runtime.log_follow("app-1", tail=1, stop=lambda: stop["now"]):
                seen.append(line)
                if line is not None and line.startswith("fresh"):
                    stop["now"] = True

        task = asyncio.create_task(read())
        for _ in range(200):
            if seen:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.1)
        with open(log, "a", encoding="utf-8") as handle:
            handle.write("fresh efpa_secret-token-value\n")
        await asyncio.wait_for(task, 5)
        with pytest.raises(AppNotFound):
            harness.runtime.log_tail("missing", 5)
        return lines, truncated, seen

    lines, truncated, seen = asyncio.run(scenario())
    assert lines == ["line 300", "token ***"] and truncated is True
    assert [line for line in seen if line is not None] == ["token ***", "fresh ***"]


def test_a_backend_that_was_passed_over_is_logged(tmp_path, monkeypatch, caplog):
    import logging

    import efp_app_host.runtime as runtime_module

    def fake_detect(settings, paths):
        return IsolationStatus(
            mode="sandbox",
            backend="bwrap-userns",
            options={"proc": "bind"},
            attempts={"bwrap": "setpriv: setresuid failed: Operation not permitted", "bwrap-userns": "ok"},
        )

    monkeypatch.setattr(runtime_module, "detect", fake_detect)
    harness = Harness(tmp_path)
    with caplog.at_level(logging.INFO, logger="efp_app_host.runtime"):
        status = asyncio.run(harness.runtime.detect_isolation())
    assert status.backend == "bwrap-userns"
    warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert warnings == ["isolation: bwrap does not work here: setpriv: setresuid failed: Operation not permitted"]
    assert any("backend bwrap-userns" in record.getMessage() for record in caplog.records if record.levelno == logging.INFO)


# ---- whose environment, whose port, whose directories ---------------------------------


def test_loader_variables_of_an_owner_never_reach_a_process(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        env = {
            "EFP_APP_TOKEN": "efpa_token",
            "MY_SETTING": "x",
            "LD_PRELOAD": "/var/lib/efp-host/store/apps/app-1/data/hook.so",
            "LD_LIBRARY_PATH": "/tmp/lib",
            "ld_audit": "/tmp/audit.so",
            "GCONV_PATH": "/tmp/gconv",
            "GLIBC_TUNABLES": "glibc.malloc.check=3",
        }
        await harness.register(env=env)
        await harness.deploy()
        record = harness.registry.get("app-1")
        return harness, harness.runtime.run_spec(record, record.current)

    harness, spec = asyncio.run(scenario())
    # Stored as the Portal sent it (so that a later change is still noticed) ...
    assert "LD_PRELOAD" in harness.registry.read_env("app-1")
    # ... but no process of the app, and no candidate of a deployment, is given it.
    assert spec.env["MY_SETTING"] == "x"
    assert not [name for name in spec.env if name.upper().startswith("LD_") or name in ("GCONV_PATH", "GLIBC_TUNABLES")]
    candidates = [candidate for candidate, _ in harness.runner.spawned]
    assert candidates and all("LD_PRELOAD" not in candidate.env and "GCONV_PATH" not in candidate.env for candidate in candidates)


def test_an_answer_on_the_apps_port_counts_only_when_the_apps_own_process_gave_it(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        await harness.register("app-2", "squatter")
        assert (await harness.deploy("app-2", sha=SHA_B)).status == "succeeded"
        victim, squatter = harness.registry.get("app-1"), harness.registry.get("app-2")
        await harness.runtime.stop("app-1", "manual")
        # Somebody else listens on the stopped app's port and answers its health probe.
        harness.health.status = 200
        harness.sampler.listeners[victim.port] = {squatter.uid}
        harness.sampler.killed.clear()
        await harness.runtime.start("app-1")
        await harness.settle()
        taken = harness.runtime.status("app-1")
        process = harness.controller.info(PROGRAM).state
        other = harness.runtime.status("app-2")
        notes = (harness.paths.log_file("app-2")).read_text(encoding="utf-8")
        # The squatter is gone: the next visit, after the usual pause, starts the app.
        harness.sampler.listeners[victim.port] = {victim.uid}
        harness.clock.advance(VISIT_RETRY_SECONDS + 1)
        await harness.runtime.ensure_running("app-1")
        await harness.settle()
        return harness, victim, squatter, taken, process, other, notes

    harness, victim, squatter, taken, process, other, notes = asyncio.run(scenario())
    # It answered, and still the app is not running: the gateway sends nobody there.
    assert (taken.state, taken.reason) == ("failed", "port_taken")
    assert taken.last_log_lines == [f"not started: port {victim.port} is held by the app squatter, not by this app"]
    assert process == "stopped"
    # The app that held the port was stopped, and its log says why.
    assert (other.state, other.reason) == ("stopped", "port_conflict")
    assert f"it listened on port {victim.port}, which belongs to the app mindmap" in notes
    assert squatter.uid in harness.sampler.killed
    assert harness.runtime.status("app-1").state == "running"


def test_a_listener_nobody_can_be_named_for_is_killed_by_its_uid(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        victim = harness.registry.get("app-1")
        leftover = victim.uid + 50  # of an app that is long gone
        await harness.runtime.stop("app-1", "manual")
        harness.sampler.killed.clear()
        harness.sampler.listeners[victim.port] = {leftover}
        await harness.runtime.start("app-1")
        await harness.settle()
        first = harness.runtime.status("app-1")
        killed = list(harness.sampler.killed)
        # A system user's listener is not the manager's to kill: the start just fails.
        harness.sampler.killed.clear()
        harness.sampler.listeners[victim.port] = {33}
        harness.clock.advance(VISIT_RETRY_SECONDS + 1)
        await harness.runtime.start("app-1")
        await harness.settle()
        return harness, leftover, first, killed, harness.runtime.status("app-1")

    harness, leftover, first, killed, second = asyncio.run(scenario())
    assert first.reason == "port_taken" and leftover in killed
    assert second.reason == "port_taken" and "uid 33" in second.last_log_lines[0] and 33 not in harness.sampler.killed


def test_an_empty_answer_about_the_listener_keeps_the_start_waiting(tmp_path):
    files = dict(FASTAPI_FILES)
    files["efp-app.json"] = json.dumps({"name": "Mind map", "type": "fastapi", "entry": "main:app", "startup_timeout": 1})

    async def scenario():
        harness = await _deployed(tmp_path, files=files)
        victim = harness.registry.get("app-1")
        await harness.runtime.stop("app-1", "manual")
        # Something answered, but nobody listens there any more when the manager looks.
        harness.sampler.listeners[victim.port] = set()
        await harness.runtime.start("app-1")
        await harness.settle()
        return harness.runtime.status("app-1")

    status = asyncio.run(scenario())
    assert (status.state, status.reason) == ("failed", "startup_timeout")


def test_a_candidate_whose_port_somebody_else_holds_fails_its_deployment(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path)
        from efp_app_host import deploy as deploy_module

        original = deploy_module.free_port
        deploy_module.free_port = lambda: 45678
        harness.sampler.listeners[45678] = {harness.registry.get("app-1").uid + 1}
        try:
            job = await harness.deploy(sha=SHA_B)
        finally:
            deploy_module.free_port = original
        return harness, job

    harness, job = asyncio.run(scenario())
    assert job.status == "failed" and job.error["step"] == "health" and "another user's process" in job.error["message"]
    assert harness.registry.get("app-1").current_sha == SHA_A


def test_everything_of_the_app_is_killed_before_its_directories_are_rebuilt(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, mode="hidden")
        record = harness.registry.get("app-1")
        await harness.runtime.stop("app-1", "manual")
        order: list[str] = []
        kill, ensure_local, reset = harness.sampler.kill_uid, harness.runtime.builder.ensure_local, harness.runtime._reset_tmp

        async def watched_ensure_local(*args, **kwargs):
            order.append("restore")
            return await ensure_local(*args, **kwargs)

        harness.sampler.kill_uid = lambda uid: order.append("kill") or kill(uid)
        harness.runtime.builder.ensure_local = watched_ensure_local
        harness.runtime._reset_tmp = lambda item: order.append("tmp") or reset(item)
        await harness.runtime.start("app-1")
        await harness.settle()
        return harness, record, order

    harness, record, order = asyncio.run(scenario())
    # The restore of hidden mode copies into directories the app's user can
    # read: it used to run before the kill.
    assert order == ["kill", "restore", "tmp"]
    assert harness.runtime.status("app-1").state == "running"


def test_the_tmp_of_an_app_is_made_by_the_manager_and_handed_over_last(tmp_path, monkeypatch):
    from efp_app_host import runtime as runtime_module

    calls: list[tuple] = []

    async def scenario():
        harness = await _deployed(tmp_path)
        record = harness.registry.get("app-1")
        tmp = harness.paths.local_tmp_dir("app-1")
        (tmp / "home" / "left-by-the-app").write_text("x", encoding="utf-8")
        real = runtime_module.fresh_dir
        monkeypatch.setattr(runtime_module, "fresh_dir", lambda path, mode, **kwargs: calls.append((path, mode, kwargs)) or real(path, mode, **kwargs))
        await harness.runtime.restart("app-1")
        await harness.settle()
        return harness, record, tmp

    harness, record, tmp = asyncio.run(scenario())
    # One call that removes, creates, fills and only then gives away: not
    # "make tmp, give it to the app, then make tmp/home in it".
    assert calls == [(tmp, 0o700, {"uid": record.uid, "children": ("home",)})]
    assert (tmp / "home").is_dir() and list((tmp / "home").iterdir()) == []


@pytest.mark.skipif(sys.platform != "linux", reason="symbolic links need Linux here")
def test_a_link_planted_as_tmp_home_is_not_followed_when_the_app_starts(tmp_path):
    async def scenario():
        harness = await _deployed(tmp_path, mode="hidden")
        gate = tmp_path / "var-lib-efp-host"  # stands for the directory above the volume
        gate.mkdir(mode=0o711)
        tmp = harness.paths.local_tmp_dir("app-1")
        await harness.runtime.stop("app-1", "manual")
        # What a process of the app does while the manager is busy elsewhere.
        (tmp / "home").rmdir()
        os.symlink(gate, tmp / "home")
        await harness.runtime.start("app-1")
        await harness.settle()
        return harness, gate, tmp

    harness, gate, tmp = asyncio.run(scenario())
    assert harness.runtime.status("app-1").state == "running"
    assert (tmp / "home").is_dir() and not (tmp / "home").is_symlink()
    assert stat.S_IMODE(os.stat(gate).st_mode) == 0o711 and os.stat(gate).st_uid == os.getuid()


def test_a_tmp_that_keeps_filling_up_fails_the_start_instead_of_being_handed_over(tmp_path, monkeypatch):
    from efp_app_host import safefs

    async def scenario():
        harness = await _deployed(tmp_path)
        await harness.runtime.stop("app-1", "manual")
        # A process of the app that puts its entries back as fast as they are removed.
        monkeypatch.setattr(safefs, "remove_tree", lambda path: None)
        await harness.runtime.start("app-1")
        await harness.settle()
        return harness.runtime.status("app-1")

    status = asyncio.run(scenario())
    assert (status.state, status.reason) == ("failed", "start_error")
    assert "could not be emptied" in status.last_log_lines[0]


@pytest.mark.skipif(sys.platform != "linux", reason="symbolic links need Linux here")
def test_hidden_mode_does_not_walk_into_a_link_left_as_the_object_store(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, mode="hidden")
        await harness.register("app-2", "victim")
        victim = harness.paths.objects_dir("app-2")
        (victim / "secret.txt").write_text("theirs", encoding="utf-8")
        # What the app could do while the host ran in sandbox mode, where it wrote data/ itself.
        data = harness.paths.data_dir("app-1")
        data.mkdir(parents=True)
        os.symlink(victim, data / "objects")
        await harness.register("app-1", "mindmap")
        record = harness.registry.get("app-1")
        return harness, harness.runtime.object_store(record)

    harness, store = asyncio.run(scenario())
    objects = harness.paths.objects_dir("app-1")
    assert objects.is_dir() and not objects.is_symlink()
    assert store.list("") == []
    assert (harness.paths.objects_dir("app-2") / "secret.txt").read_text(encoding="utf-8") == "theirs"
