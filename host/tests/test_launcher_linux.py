"""The launcher and the isolation backends on a real Linux kernel.

Nothing here runs on Windows. What runs where:

- as an ordinary user (the unit-test job of CI): the launcher with the
  ``plain`` backend for the current user, the command runner, Landlock when
  the kernel has it;
- as root (CI runs this file again inside the image, with the capabilities
  of the sandbox variant): dropping to another uid, bubblewrap as root, the
  isolation probe of both modes.

``EFP_TEST_REQUIRE_BWRAP=1`` turns "bubblewrap does not work here" from a
skip into a failure; CI sets it where bubblewrap must work.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from efp_app_host import landlock
from efp_app_host.isolation import BACKEND_BWRAP, BACKEND_BWRAP_USERNS, BACKEND_LANDLOCK, BACKEND_PLAIN, LaunchSpec, detect, launcher_command

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="needs a Linux kernel")

APP_UID = 23456
REQUIRE_BWRAP = os.environ.get("EFP_TEST_REQUIRE_BWRAP") == "1"


def _is_root() -> bool:
    return os.geteuid() == 0


def _target_uid() -> int:
    """The app's uid: another user when the test runs as root, the current one otherwise."""

    return APP_UID if _is_root() else os.getuid()


@pytest.fixture
def area():
    """A directory every user can reach, with what one app may and may not see.

    pytest's own temporary directory is closed to other users, so it cannot
    stand in for the volume when the app runs under another uid. Not below
    /tmp either: inside a sandbox /tmp is the app's private tmp.
    """

    base = Path(tempfile.mkdtemp(prefix="efp-iso-", dir="/var/tmp"))
    os.chmod(base, 0o755)
    uid = _target_uid()
    layout = {
        "release": base / "store" / "apps" / "mine" / "releases" / "abc",
        "data": base / "store" / "apps" / "mine" / "data",
        "other": base / "store" / "apps" / "other",
        "tmp": base / "local" / "mine" / "tmp",
    }
    for path in layout.values():
        path.mkdir(parents=True)
    (layout["release"] / "code").mkdir()
    (layout["release"] / "code" / "main.txt").write_text("my code\n", encoding="utf-8")
    (layout["other"] / "env.json").write_text('{"EFP_APP_TOKEN": "efpa_other"}\n', encoding="utf-8")
    for path in base.rglob("*"):
        os.chmod(path, 0o755 if path.is_dir() else 0o644)
    for name in ("data", "tmp"):
        if _is_root():
            os.chown(layout[name], uid, uid)
        os.chmod(layout[name], 0o700)
    layout["base"] = base
    try:
        yield layout
    finally:
        shutil.rmtree(base, ignore_errors=True)


def _spec(area, backend: str, script: str, *, options=None, env=None) -> LaunchSpec:
    uid = _target_uid()
    sandboxed = backend != BACKEND_PLAIN
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "TMPDIR": str(area["tmp"]), "HOME": str(area["tmp"]), "MARK": "from-the-spec"}
    environment.update(env or {})
    return LaunchSpec(
        argv=["/bin/sh", "-c", script, "app", str(area["release"]), str(area["data"]), str(area["other"])],
        env=environment,
        cwd=str(area["release"] / "code"),
        uid=uid,
        gid=uid if _is_root() else os.getgid(),
        backend=backend,
        options=dict(options or {}),
        ro_paths=[str(area["release"])] if sandboxed else [],
        rw_paths=[str(area["data"])] if sandboxed else [],
        tmp_dir=str(area["tmp"]),
        # Generous: the limit counts every process of the user, and as an
        # ordinary user that includes whatever else the test machine runs.
        nproc=4096,
        nofile=512,
        label="test",
    )


def _launch(area, spec: LaunchSpec, timeout: float = 60.0) -> tuple[int, str]:
    spec_file = area["base"] / f"spec-{time.time_ns()}.json"
    spec_file.write_text(json.dumps(spec.to_json()), encoding="utf-8")
    os.chmod(spec_file, 0o600)
    environment = dict(os.environ)
    # What the manager's environment holds must never reach the app.
    environment["EFP_HOST_API_TOKEN"] = "host-secret-must-not-leak"
    done = subprocess.run(launcher_command(str(spec_file)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=environment)
    assert not spec_file.exists(), "the launcher removes the spec file: it holds the app's token"
    return done.returncode, done.stdout.decode("utf-8", "replace")


# Runs inside the isolation. $1 release, $2 data, $3 another app's directory.
CHECKS = r"""
fail() { echo "FAIL: $1"; exit 1; }
[ "$(id -u)" = "$WANT_UID" ] || fail "uid is $(id -u), wanted $WANT_UID"
[ "$(pwd)" = "$1/code" ] || fail "the working directory is $(pwd)"
[ "$(cat main.txt)" = "my code" ] || fail "cannot read its own code"
[ "$MARK" = "from-the-spec" ] || fail "the environment of the spec is missing"
[ -z "${EFP_HOST_API_TOKEN:-}" ] || fail "the manager's environment leaked"
grep -q '^NoNewPrivs:[[:space:]]*1' /proc/self/status || fail "no_new_privs is not set"
grep -q '^CapEff:[[:space:]]*0000000000000000' /proc/self/status || fail "the process has capabilities"
grep -q '^Max open files[[:space:]]*512[[:space:]]' /proc/self/limits || fail "RLIMIT_NOFILE: $(grep '^Max open files' /proc/self/limits)"
grep -q '^Max processes[[:space:]]*4096[[:space:]]' /proc/self/limits || fail "RLIMIT_NPROC: $(grep '^Max processes' /proc/self/limits)"
grep -q '^Max core file size[[:space:]]*0[[:space:]]' /proc/self/limits || fail "core dumps are not switched off"
touch "$TMPDIR/scratch" || fail "cannot write its private tmp"
echo "checks passed"
"""

SANDBOX_CHECKS = CHECKS + r"""
touch "$2/kept.txt" || fail "cannot write its data directory"
if touch "$1/code/new.txt" 2>/dev/null; then fail "can write its release"; fi
if cat "$3/env.json" >/dev/null 2>&1; then fail "can read another app's environment"; fi
if ls "$3" >/dev/null 2>&1; then fail "can list another app's directory"; fi
if ls "$1/../../../other" >/dev/null 2>&1; then fail "can reach another app through its own path"; fi
echo "sandbox checks passed"
"""


def test_plain_backend_runs_the_command_with_exactly_the_spec(area):
    spec = _spec(area, BACKEND_PLAIN, CHECKS, env={"WANT_UID": str(_target_uid())})
    code, output = _launch(area, spec)
    assert code == 0, output
    assert "checks passed" in output


def test_the_exit_code_and_output_of_the_app_come_through(area):
    code, output = _launch(area, _spec(area, BACKEND_PLAIN, "echo to-stdout; echo to-stderr >&2; exit 7"))
    assert code == 7 and "to-stdout" in output and "to-stderr" in output


def test_a_spec_that_cannot_be_launched_says_so(area):
    spec = _spec(area, BACKEND_PLAIN, "true")
    spec.argv = ["/no/such/program"]
    code, output = _launch(area, spec)
    assert code != 0 and ("No such file" in output or "could not be launched" in output)
    broken = _spec(area, "chroot", "true")
    code, output = _launch(area, broken)
    assert code == 111 and "could not be launched" in output and "unknown isolation backend" in output


@pytest.mark.skipif(sys.platform != "linux" or not _is_root(), reason="switching to another uid needs root")
def test_plain_backend_drops_to_the_apps_uid(area):
    code, output = _launch(area, _spec(area, BACKEND_PLAIN, CHECKS + 'id -G; echo "groups done"', env={"WANT_UID": str(APP_UID)}))
    assert code == 0, output
    # No supplementary groups survive.
    assert f"{APP_UID}\ngroups done" in output


def test_landlock_confines_the_app_to_its_own_directories(area):
    if landlock.abi_version() <= 0:
        pytest.skip("this kernel has no Landlock")
    spec = _spec(area, BACKEND_LANDLOCK, SANDBOX_CHECKS, env={"WANT_UID": str(_target_uid())})
    code, output = _launch(area, spec)
    assert code == 0, output
    assert "sandbox checks passed" in output
    assert (area["data"] / "kept.txt").is_file()


def test_landlock_reports_its_abi_and_rejects_nothing_silently():
    abi = landlock.abi_version()
    assert isinstance(abi, int) and abi >= 0
    if abi > 0:
        assert landlock.handled_rights(abi) & landlock.FS_READ_FILE


def _bwrap_or_skip(area, backend: str, options: dict) -> None:
    """Skip (or fail, where CI says bubblewrap must work) when the backend cannot run here."""

    code, output = _launch(area, _spec(area, backend, "true", options=options))
    if code != 0:
        message = f"{backend} does not work here: {output.strip()[-300:]}"
        if REQUIRE_BWRAP and backend == BACKEND_BWRAP:
            pytest.fail(message)
        pytest.skip(message)


@pytest.mark.skipif(sys.platform != "linux" or not _is_root(), reason="bubblewrap as root")
def test_bwrap_as_root_shows_the_app_only_its_own_directories(area):
    _bwrap_or_skip(area, BACKEND_BWRAP, {"proc": "new"})
    extra = r"""
[ ! -e "$3" ] || fail "another app's directory exists in the view"
[ "$(ls "$1/../../.." | tr '\n' ' ')" = "mine " ] || fail "the apps directory shows: $(ls "$1/../../.." | tr '\n' ' ')"
[ -z "$(ls /etc/nginx 2>/dev/null)" ] || fail "the gateway's configuration is visible"
[ -z "$(ls /run 2>/dev/null)" ] || fail "the run directory is visible"
tr '\0' ' ' < /proc/1/cmdline | grep -q bwrap || fail "process 1 is not the sandbox's own: $(tr '\0' ' ' < /proc/1/cmdline)"
echo "view checks passed"
"""
    spec = _spec(area, BACKEND_BWRAP, SANDBOX_CHECKS + extra, env={"WANT_UID": str(APP_UID)})
    code, output = _launch(area, spec)
    assert code == 0, output
    assert "view checks passed" in output
    assert (area["data"] / "kept.txt").is_file()
    assert os.stat(area["data"] / "kept.txt").st_uid == APP_UID


def test_bwrap_in_a_user_namespace_shows_the_app_only_its_own_directories(area):
    if shutil.which("bwrap") is None:
        pytest.skip("bubblewrap is not installed")
    options = {"proc": "new"}
    code, _ = _launch(area, _spec(area, BACKEND_BWRAP_USERNS, "true", options=options))
    if code != 0:
        options = {"proc": "bind"}
        _bwrap_or_skip(area, BACKEND_BWRAP_USERNS, options)
    extra = r"""
[ ! -e "$3" ] || fail "another app's directory exists in the view"
echo "view checks passed"
"""
    spec = _spec(area, BACKEND_BWRAP_USERNS, SANDBOX_CHECKS + extra, options=options, env={"WANT_UID": str(_target_uid())})
    code, output = _launch(area, spec)
    assert code == 0, output
    assert "view checks passed" in output


@pytest.mark.skipif(sys.platform != "linux" or not _is_root(), reason="bubblewrap as root")
def test_stopping_the_process_group_lets_a_sandboxed_app_end_by_itself(area):
    _bwrap_or_skip(area, BACKEND_BWRAP, {"proc": "new"})
    # The app writes a file when it gets SIGTERM: proof that the signal reached it
    # (and was not swallowed by the wrapper, which ignores it) and that it had time to act.
    script = 'trap "echo stopped > $2/stopped.txt; exit 0" TERM; echo ready > $2/ready.txt; while true; do sleep 0.1; done'
    spec = _spec(area, BACKEND_BWRAP, script)
    spec_file = area["base"] / "spec-signal.json"
    spec_file.write_text(json.dumps(spec.to_json()), encoding="utf-8")
    process = subprocess.Popen(launcher_command(str(spec_file)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        deadline = time.time() + 30
        while not (area["data"] / "ready.txt").exists():
            assert time.time() < deadline and process.poll() is None, "the app did not come up"
            time.sleep(0.05)
        os.killpg(process.pid, signal.SIGTERM)
        code = process.wait(timeout=15)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    assert (area["data"] / "stopped.txt").read_text(encoding="utf-8").strip() == "stopped"
    assert code == 0, (process.stdout.read() if process.stdout else b"").decode("utf-8", "replace")


# ---- the probe the manager runs at boot ----------------------------------------------------


def _probe_settings(area, mode: str, backend: str = "auto"):
    from conftest import make_settings

    base = area["base"] / f"probe-{mode}"
    volume_parent = base / "volume"
    (volume_parent / "store").mkdir(parents=True)
    (base / "runtime").mkdir()
    (base / "run").mkdir()
    os.chmod(base, 0o755)
    os.chmod(base / "runtime", 0o755)
    os.chmod(volume_parent / "store", 0o755)
    os.chmod(volume_parent, 0o700 if mode == "hidden" else 0o755)
    return make_settings(base, isolation_mode=mode, sandbox_backend=backend, store_dir=volume_parent / "store", uid_range_start=APP_UID)


@pytest.mark.skipif(sys.platform != "linux" or not _is_root(), reason="the probe drops to an unprivileged uid")
def test_the_probe_accepts_hidden_mode_when_the_volume_is_closed(area):
    from efp_app_host.paths import HostPaths

    settings = _probe_settings(area, "hidden")
    status = detect(settings, HostPaths.of(settings))
    assert status.ok and status.backend == BACKEND_PLAIN, status


@pytest.mark.skipif(sys.platform != "linux" or not _is_root(), reason="the probe drops to an unprivileged uid")
def test_the_probe_refuses_hidden_mode_when_apps_can_reach_the_volume(area):
    from efp_app_host.paths import HostPaths

    settings = _probe_settings(area, "hidden")
    os.chmod(settings.store_dir.parent, 0o755)
    status = detect(settings, HostPaths.of(settings))
    assert not status.ok and "hidden mode is not safe here" in status.error
    assert "outside its directory" in status.error


@pytest.mark.skipif(sys.platform != "linux" or not _is_root(), reason="the probe drops to an unprivileged uid")
def test_the_probe_finds_a_sandbox_backend(area):
    from efp_app_host.paths import HostPaths

    settings = _probe_settings(area, "sandbox")
    status = detect(settings, HostPaths.of(settings))
    if not status.ok:
        if REQUIRE_BWRAP:
            pytest.fail(f"no sandbox backend although one is required here: {status.error}")
        pytest.skip(f"no sandbox backend works here: {status.error}")
    assert status.backend in (BACKEND_BWRAP, BACKEND_BWRAP_USERNS, BACKEND_LANDLOCK)
    if REQUIRE_BWRAP:
        assert status.backend == BACKEND_BWRAP, status
    assert not list(settings.store_dir.glob(".probe-*"))


@pytest.mark.skipif(sys.platform != "linux" or not _is_root(), reason="the probe drops to an unprivileged uid")
def test_a_named_backend_that_does_not_work_is_not_replaced_by_another(area):
    from efp_app_host.paths import HostPaths

    settings = _probe_settings(area, "sandbox", backend="landlock")
    status = detect(settings, HostPaths.of(settings))
    if landlock.abi_version() > 0:
        assert status.ok and status.backend == BACKEND_LANDLOCK, status
    else:
        assert not status.ok and status.backend is None and "landlock" in status.error


# ---- the command runner ----------------------------------------------------------------------


def test_the_runner_hands_on_output_and_the_exit_code(area):
    from efp_app_host.runner import LauncherRunner

    async def scenario():
        runner = LauncherRunner(area["base"] / "launch")
        lines: list[str] = []
        logged = await runner.run(_spec(area, BACKEND_PLAIN, "echo one; echo two >&2; printf 'no newline'; exit 3"), log=lines.append, timeout=60)
        captured = await runner.run(_spec(area, BACKEND_PLAIN, "echo pkg==1.0"), log=lines.append, timeout=60, capture=True)
        return logged, captured, lines

    logged, captured, lines = asyncio.run(scenario())
    assert logged.code == 3 and logged.output == ""
    assert sorted(lines) == ["no newline\n", "one\n", "two\n"]
    assert captured.code == 0 and captured.output == "pkg==1.0\n"
    assert list((area["base"] / "launch").iterdir()) == [], "no spec file is left behind"


def test_the_runner_kills_what_outlives_its_time(area):
    from efp_app_host.runner import CommandTimeout, LauncherRunner

    marker = area["data"] / "child.pid"

    async def scenario():
        runner = LauncherRunner(area["base"] / "launch")
        started = time.time()
        with pytest.raises(CommandTimeout):
            await runner.run(_spec(area, BACKEND_PLAIN, f"sleep 300 & echo $! > {marker}; wait"), timeout=1.0)
        return time.time() - started

    elapsed = asyncio.run(scenario())
    assert elapsed < 20
    child = int(marker.read_text(encoding="utf-8").strip())
    # The whole process group is gone, the background child included.
    deadline = time.time() + 5
    while not _gone(child) and time.time() < deadline:
        time.sleep(0.1)
    assert _gone(child), f"process {child} is still running"


def _gone(pid: int) -> bool:
    """No such process any more (a zombie waiting to be reaped counts as gone)."""

    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().rsplit(")", 1)[-1].split()[0] == "Z"
    except OSError:
        return True


def test_the_runner_starts_and_stops_a_long_running_process(area):
    from efp_app_host.runner import LauncherRunner

    async def scenario():
        runner = LauncherRunner(area["base"] / "launch")
        lines: list[str] = []
        process = await runner.spawn(_spec(area, BACKEND_PLAIN, "echo up; exec sleep 300"), log=lines.append)
        for _ in range(200):
            if lines:
                break
            await asyncio.sleep(0.05)
        alive = process.returncode
        await process.stop()
        quick = await runner.spawn(_spec(area, BACKEND_PLAIN, "exit 4"), log=lines.append)
        for _ in range(200):
            if quick.returncode is not None:
                break
            await asyncio.sleep(0.05)
        await quick.stop()
        return lines, alive, process.returncode, quick.returncode

    lines, alive, stopped, quick = asyncio.run(scenario())
    assert lines == ["up\n"] and alive is None
    assert stopped is not None and stopped != 0
    assert quick == 4
