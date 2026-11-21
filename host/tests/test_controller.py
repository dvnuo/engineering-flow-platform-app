from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from efp_app_host.controller import (
    PROC_ABSENT,
    PROC_EXITED,
    PROC_FATAL,
    PROC_RUNNING,
    PROC_STOPPED,
    ControllerError,
    FakeController,
    ProgramSpec,
    SupervisorController,
    program_name,
    render_program,
    to_process_info,
)


def test_program_section():
    program = ProgramSpec(
        name=program_name("3f2a"),
        command=["/opt/efp/bin/efp-app-launch", "/run/efp-host/launch/app-3f2a.json"],
        log_file="/var/lib/efp-host/store/apps/3f2a/logs/app.log",
    )
    text = render_program(program)
    lines = text.splitlines()
    assert lines[0] == "[program:app-3f2a]"
    assert "command=/opt/efp/bin/efp-app-launch /run/efp-host/launch/app-3f2a.json" in lines
    # supervisord only spawns and reaps: the runtime decides about restarts.
    assert "autostart=false" in lines and "autorestart=false" in lines and "startretries=0" in lines and "startsecs=0" in lines
    # The stop signal reaches the whole process group (the sandbox wrapper and the app).
    assert "stopasgroup=true" in lines and "killasgroup=true" in lines and "stopsignal=TERM" in lines
    assert "redirect_stderr=true" in lines
    assert "stdout_logfile=/var/lib/efp-host/store/apps/3f2a/logs/app.log" in lines
    assert "stdout_logfile_backups=2" in lines
    # Nothing secret and no environment in the file.
    assert "environment" not in text and "user=" not in text


def test_program_section_quotes_and_escapes():
    program = ProgramSpec(name="app-x", command=["/usr/bin/python 3", "-m", "100%", 'say "hi"'], log_file="/logs/50%/app.log")
    text = render_program(program)
    assert 'command="/usr/bin/python 3" -m 100%% "say \\"hi\\""' in text
    assert "stdout_logfile=/logs/50%%/app.log" in text


def test_supervisor_process_info_mapping():
    running = to_process_info("app-x", {"statename": "RUNNING", "pid": 77, "start": 100, "stop": 0, "exitstatus": 0, "description": "pid 77, uptime 0:00:05"})
    assert (running.state, running.pid, running.exit_status, running.alive) == (PROC_RUNNING, 77, None, True)
    exited = to_process_info("app-x", {"statename": "EXITED", "pid": 0, "start": 100, "stop": 130, "exitstatus": 3, "spawnerr": ""})
    assert (exited.state, exited.exit_status, exited.alive, exited.stopped_at) == (PROC_EXITED, 3, False, 130.0)
    fatal = to_process_info("app-x", {"statename": "FATAL", "exitstatus": 0, "spawnerr": "can't find command '/nope'"})
    assert fatal.state == PROC_FATAL and "can't find command" in fatal.detail
    assert to_process_info("app-x", {"statename": "BACKOFF"}).state == PROC_FATAL
    assert to_process_info("app-x", {"statename": "STOPPED"}).state == PROC_STOPPED


def test_fake_controller_life_cycle():
    controller = FakeController()
    program = ProgramSpec(name="app-x", command=["true"], log_file="/dev/null")
    assert controller.info("app-x").state == PROC_ABSENT
    with pytest.raises(ControllerError):
        controller.start("app-x")
    controller.add(program)
    assert controller.info("app-x").state == PROC_STOPPED
    controller.start("app-x")
    assert controller.info("app-x").alive and controller.info("app-x").pid > 0
    controller.exit("app-x", 2)
    assert controller.info("app-x").exit_status == 2 and not controller.info("app-x").alive
    controller.start("app-x")
    controller.stop("app-x")
    assert controller.info("app-x").state == PROC_STOPPED
    controller.apply([])
    assert controller.all() == {} and controller.started("app-x") == 3


def test_supervisor_controller_writes_one_file_per_program(tmp_path, monkeypatch):
    controller = SupervisorController(tmp_path / "supervisor.sock", tmp_path / "supervisor.d")
    reloads = []
    monkeypatch.setattr(controller, "_reload", lambda: reloads.append(1))
    first = ProgramSpec(name="app-a", command=["launch", "a"], log_file="/logs/a.log")
    second = ProgramSpec(name="app-b", command=["launch", "b"], log_file="/logs/b.log")
    controller.apply([first, second])
    assert sorted(path.name for path in (tmp_path / "supervisor.d").iterdir()) == ["app-a.conf", "app-b.conf"]
    controller.apply([second])
    assert sorted(path.name for path in (tmp_path / "supervisor.d").iterdir()) == ["app-b.conf"]
    controller.remove("app-b")
    assert list((tmp_path / "supervisor.d").iterdir()) == []
    assert len(reloads) == 3


def test_an_unreachable_supervisor_is_a_controller_error(tmp_path):
    controller = SupervisorController(tmp_path / "missing.sock", tmp_path / "supervisor.d", timeout=1.0)
    with pytest.raises(ControllerError):
        controller.all()


# ---- the real thing ------------------------------------------------------------------

SUPERVISORD_CONF = """
[unix_http_server]
file={sock}
chmod=0700

[supervisord]
nodaemon=true
logfile={dir}/supervisord.log
pidfile={dir}/supervisord.pid
childlogdir={dir}
{user}

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[include]
files = {dir}/supervisor.d/*.conf
"""


@pytest.fixture
def supervisord(tmp_path):
    # A unix socket path is short: keep it out of pytest's long temporary directory.
    import tempfile

    short = tempfile.mkdtemp(prefix="efp-sv-")
    sock = os.path.join(short, "s.sock")
    (tmp_path / "supervisor.d").mkdir()
    conf = tmp_path / "supervisord.conf"
    user = "user=root" if os.geteuid() == 0 else ""
    conf.write_text(SUPERVISORD_CONF.format(sock=sock, dir=tmp_path, user=user), encoding="utf-8")
    process = subprocess.Popen([sys.executable, "-m", "supervisor.supervisord", "-c", str(conf)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if os.path.exists(sock):
                break
            time.sleep(0.1)
        else:
            pytest.fail("supervisord did not come up")
        yield SupervisorController(sock, tmp_path / "supervisor.d")
    finally:
        process.terminate()
        process.wait(timeout=20)
        shutil.rmtree(short, ignore_errors=True)


def _wait_for(check, timeout: float = 10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("condition not met in time")


@pytest.mark.skipif(sys.platform != "linux", reason="supervisord runs on Linux")
def test_real_supervisord_runs_what_the_controller_says(supervisord, tmp_path):
    controller = supervisord
    log = tmp_path / "sleeper.log"
    sleeper = ProgramSpec(name="app-sleeper", command=["/bin/sh", "-c", "echo started; exec sleep 300"], log_file=str(log), stop_wait_seconds=3)
    quitter = ProgramSpec(name="app-quitter", command=["/bin/sh", "-c", "echo bye; exit 7"], log_file=str(tmp_path / "quitter.log"))
    assert controller.info("app-sleeper").state == PROC_ABSENT

    controller.apply([sleeper, quitter])
    assert set(controller.all()) == {"app-sleeper", "app-quitter"}
    # Nothing starts by itself.
    assert controller.info("app-sleeper").state == PROC_STOPPED

    controller.start("app-sleeper")
    info = _wait_for(lambda: controller.info("app-sleeper") if controller.info("app-sleeper").alive else None)
    assert info.pid > 0 and info.started_at > 0
    controller.start("app-sleeper")  # already running: not an error
    _wait_for(lambda: "started" in log.read_text(encoding="utf-8"))

    controller.stop("app-sleeper")
    assert controller.info("app-sleeper").state == PROC_STOPPED
    controller.stop("app-sleeper")  # already stopped: not an error

    # A process that ends by itself is reported with its exit code and is not restarted.
    controller.start("app-quitter")
    exited = _wait_for(lambda: controller.info("app-quitter") if controller.info("app-quitter").state == PROC_EXITED else None)
    assert exited.exit_status == 7
    time.sleep(1.5)
    assert controller.info("app-quitter").state == PROC_EXITED

    # A changed program is replaced; a removed one is forgotten.
    controller.add(ProgramSpec(name="app-quitter", command=["/bin/sh", "-c", "exit 9"], log_file=str(tmp_path / "quitter.log")))
    controller.start("app-quitter")
    assert _wait_for(lambda: controller.info("app-quitter").exit_status == 9)
    controller.remove("app-quitter")
    assert controller.info("app-quitter").state == PROC_ABSENT
    assert set(controller.all()) == {"app-sleeper"}

    with pytest.raises(ControllerError):
        controller.start("app-unknown")


@pytest.mark.skipif(sys.platform != "linux", reason="supervisord runs on Linux")
def test_real_supervisord_stops_the_whole_process_group(supervisord, tmp_path):
    controller = supervisord
    marker = tmp_path / "child.pid"
    # The parent ignores SIGTERM (as the sandbox wrapper does); its child, which
    # gets the default back the way the launcher arranges it, must still be stopped.
    script = f"trap '' TERM; (echo $BASHPID > {marker}; exec env --default-signal=TERM sleep 300) & wait"
    controller.add(ProgramSpec(name="app-group", command=["/bin/bash", "-c", script], log_file=str(tmp_path / "group.log"), stop_wait_seconds=5))
    controller.start("app-group")
    child = int(_wait_for(lambda: marker.read_text(encoding="utf-8").strip() if marker.exists() else None))
    started = time.time()
    controller.stop("app-group")
    assert time.time() - started < 4.5, "the child got the signal through the group, so nothing had to be killed after the wait"
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)
