from __future__ import annotations

import asyncio
import json

import pytest
from runtime_helpers import FASTAPI_FILES, SHA_A, SHA_B, SHA_C, STATIC_FILES, Harness, make_bundle

from efp_app_host.deploy import READY_MARKER, DeployInProgress
from efp_app_host.isolation import IsolationStatus
from efp_app_host.registry import AppNotFound

STEPS = ["unpack", "manifest", "environment", "dependencies", "start", "health", "activate"]


def _statuses(job) -> dict[str, str]:
    return {step["name"]: step["status"] for step in job.steps}


def _log(harness: Harness, job) -> str:
    return harness.runtime.jobs.read_log(job.id)[0].decode("utf-8")


def test_a_deployment_in_sandbox_mode(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        job = await harness.deploy(ref="main", requested_by="alice", entry_dir="apps/mindmap")
        return harness, job

    harness, job = asyncio.run(scenario())
    paths = harness.paths
    assert job.status == "succeeded" and job.error is None and job.finished_at
    assert [step["name"] for step in job.steps] == STEPS
    assert set(_statuses(job).values()) == {"succeeded"}
    assert all(step["started_at"] and step["finished_at"] for step in job.steps)

    # The release is on the volume, where the contract puts it.
    release = paths.release_dir("app-1", SHA_A)
    assert (release / "code" / "main.py").is_file()
    assert json.loads(paths.manifest_file("app-1", SHA_A).read_text(encoding="utf-8"))["entry"] == "main:app"
    assert (release / "venv" / "bin" / "python").is_file()
    assert paths.lock_file("app-1", SHA_A).read_text(encoding="utf-8") == "fastapi==0.110.0\nuvicorn==0.29.0\n"
    assert (release / READY_MARKER).is_file()
    assert not paths.wheels_dir("app-1", SHA_A).exists()

    record = harness.registry.get("app-1")
    assert record.current_sha == SHA_A and record.previous_sha is None
    assert record.current.type == "fastapi" and record.current.manifest["health_path"] == "/"

    # venv, pip install (wheels only, runner added), pip freeze - each as the app's user, inside its sandbox.
    commands = harness.runner.commands()
    assert len(commands) == 3
    assert commands[0].endswith(f"-m venv {(release / 'venv').as_posix()}")
    assert " install --only-binary :all: " in commands[1] and commands[1].endswith(" uvicorn")
    assert commands[2].endswith(" freeze")
    for spec in harness.runner.runs:
        assert (spec.uid, spec.gid, spec.backend) == (record.uid, record.uid, "bwrap")
        assert release.as_posix() in spec.ro_paths
        assert all(not path.endswith("/data") for path in spec.rw_paths)
        assert "EFP_APP_TOKEN" not in spec.env and "EFP_HOST_API_TOKEN" not in spec.env
    assert harness.runner.runs[1].rw_paths == [(release / "venv").as_posix()]

    # The health check ran against a candidate on another port, which was stopped afterwards.
    (candidate_spec, candidate), = harness.runner.spawned
    assert candidate.stopped
    assert candidate_spec.env["PORT"] != str(record.port)
    assert f"--port {candidate_spec.env['PORT']}" in " ".join(candidate_spec.argv)
    assert candidate_spec.ro_paths == [release.as_posix()] and candidate_spec.rw_paths == [paths.data_dir("app-1").as_posix()]
    assert harness.health.urls[0] == f"http://127.0.0.1:{candidate_spec.env['PORT']}/"

    # Then the app itself was started on its own port.
    assert harness.controller.started("app-app-1") == 1
    assert harness.runtime.status("app-1").state == "running"
    assert harness.health.urls[-1] == f"http://127.0.0.1:{record.port}/"

    log = _log(harness, job)
    assert "--- unpack ---" in log and "--- activate ---" in log and "healthy (HTTP 200)" in log
    assert "requested by alice" in log
    # Build leftovers are gone.
    assert not (paths.local_app_dir("app-1") / "build").exists() or not list((paths.local_app_dir("app-1") / "build").iterdir())
    assert not harness.runtime.jobs.bundle_path(job.id).exists()


def test_secrets_never_reach_the_job_log(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, passthrough_env={"PIP_INDEX_URL": "https://ci:index-pass-123@pip.example.com/simple"})
        await harness.register(env={"EFP_APP_TOKEN": "efpa_secret-token-value", "DB_PASSWORD": "db-pass-456789"})

        async def leaky_spawn(spec, *, log=None):
            log(f"connecting with {spec.env['DB_PASSWORD']} and {spec.env['EFP_APP_TOKEN']}\n")
            log("index https://ci:index-pass-123@pip.example.com/simple\n")
            log("host token test-host-token, visitor key visitor-key-000000\n")
            return await original(spec, log=log)

        original = harness.runner.spawn
        harness.runner.spawn = leaky_spawn
        job = await harness.deploy()
        return harness, job

    harness, job = asyncio.run(scenario())
    assert job.status == "succeeded"
    log = _log(harness, job)
    assert "connecting with *** and ***" in log
    for secret in ("efpa_secret-token-value", "db-pass-456789", "index-pass-123", "test-host-token", "visitor-key-000000"):
        assert secret not in log
        assert secret not in harness.runtime.jobs.public(job)["log_tail"]


def test_a_deployment_in_hidden_mode_keeps_code_and_wheels_on_the_volume_and_runs_from_the_local_disk(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, mode="hidden")
        await harness.register()
        job = await harness.deploy()
        return harness, job

    harness, job = asyncio.run(scenario())
    paths, settings = harness.paths, harness.settings
    assert job.status == "succeeded", _log(harness, job)
    volume = paths.release_dir("app-1", SHA_A)
    local = paths.local_release_dir("app-1", SHA_A)
    assert (volume / "code" / "main.py").is_file() and not (volume / "venv").exists()
    assert [path.name for path in paths.wheels_dir("app-1", SHA_A).iterdir()] == ["fastapi-0.110.0-py3-none-any.whl"]
    assert paths.lock_file("app-1", SHA_A).is_file()
    assert (local / "code" / "main.py").is_file() and (local / "venv" / "bin" / "python").is_file() and (local / READY_MARKER).is_file()

    commands = harness.runner.commands()
    assert [command.split(" --no-cache-dir ")[-1].split(" ")[0] for command in commands[1:]] == ["wheel", "install", "freeze"]
    assert "--only-binary :all:" in commands[1]
    assert "--no-index --no-deps" in commands[2] and commands[2].endswith("fastapi-0.110.0-py3-none-any.whl")
    # Nothing a process is given points into the volume.
    store = settings.store_dir.as_posix()
    for spec in harness.runner.runs + [spec for spec, _ in harness.runner.spawned]:
        assert spec.backend == "plain" and spec.ro_paths == [] and spec.rw_paths == []
        assert store not in " ".join(spec.argv) and store not in spec.cwd
        assert not any(store in value for value in spec.env.values())
    candidate_spec, _ = harness.runner.spawned[0]
    assert candidate_spec.env["EFP_APP_STORAGE_SOCKET"] == paths.storage_socket.as_posix()
    assert "EFP_APP_DATA_DIR" not in candidate_spec.env
    assert harness.runtime.status("app-1").state == "running"


def test_hidden_mode_restores_a_release_after_the_local_disk_was_lost(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, mode="hidden")
        await harness.register()
        await harness.deploy()
        await harness.runtime.stop("app-1", "manual")
        # A new container: the volume is there, the local disk is empty.
        import shutil

        shutil.rmtree(harness.settings.runtime_dir)
        harness.runner.runs.clear()
        status = await harness.runtime.ensure_running("app-1")
        assert status.state == "starting"
        await harness.settle()
        return harness

    harness = asyncio.run(scenario())
    local = harness.paths.local_release_dir("app-1", SHA_A)
    assert (local / "code" / "main.py").is_file() and (local / READY_MARKER).is_file()
    commands = harness.runner.commands()
    # venv, then an install from the wheel cache only: no index, no network.
    assert len(commands) == 2 and "-m venv" in commands[0]
    assert "--no-index --no-deps" in commands[1] and "fastapi-0.110.0-py3-none-any.whl" in commands[1]
    assert harness.runtime.status("app-1").state == "running"
    log, _ = harness.runtime.log_tail("app-1", 20)
    assert any("restoring release" in line for line in log)


def test_a_static_app_skips_everything_that_needs_a_process(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        job = await harness.deploy(files=STATIC_FILES)
        return harness, job

    harness, job = asyncio.run(scenario())
    assert job.status == "succeeded"
    assert _statuses(job) == {
        "unpack": "succeeded",
        "manifest": "succeeded",
        "environment": "skipped",
        "dependencies": "skipped",
        "start": "skipped",
        "health": "skipped",
        "activate": "succeeded",
    }
    assert harness.runner.runs == [] and harness.runner.spawned == [] and harness.controller.started("app-app-1") == 0
    status = harness.runtime.status("app-1")
    assert (status.state, status.type) == ("running", "static")
    upstream = harness.runtime.upstream("app-1")
    assert upstream.kind == "static" and upstream.static_root == (harness.paths.code_dir("app-1", SHA_A) / "public").as_posix()


def test_a_static_app_in_hidden_mode_is_copied_where_the_gateway_reads_it(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, mode="hidden")
        await harness.register()
        job = await harness.deploy(files=STATIC_FILES)
        return harness, job

    harness, job = asyncio.run(scenario())
    assert job.status == "succeeded", _log(harness, job)
    upstream = harness.runtime.upstream("app-1")
    root = upstream.static_root
    assert harness.settings.runtime_dir.as_posix() in root and harness.settings.store_dir.as_posix() not in root
    assert open(root + "/index.html", encoding="utf-8").read() == "<h1>hello</h1>\n"


def test_a_static_app_deploys_without_any_isolation(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        harness.runtime.isolation = IsolationStatus(mode="sandbox", error="no sandbox backend works (bwrap: nope)")
        await harness.register()
        static = await harness.deploy(files=STATIC_FILES)
        await harness.register("app-2", "review")
        process = await harness.deploy("app-2", SHA_B)
        return harness, static, process

    harness, static, process = asyncio.run(scenario())
    assert static.status == "succeeded"
    # An app with a process is refused before anything of it is built or run.
    assert process.status == "failed" and process.error["step"] == "environment"
    assert "no sandbox backend works" in process.error["message"]
    assert harness.runner.runs == [] and harness.runner.spawned == []
    assert not harness.paths.release_dir("app-2", SHA_B).exists()


@pytest.mark.parametrize(
    "files, step, fragment",
    [
        ({"main.py": "x"}, "unpack", "efp-app.json was not found"),
        ({"efp-app.json": "{broken"}, "manifest", "not valid JSON"),
        ({"efp-app.json": json.dumps({"name": "A", "type": "fastapi", "entry": "main:app"})}, "manifest", "main.py does not exist"),
        ({"efp-app.json": json.dumps({"name": "A", "type": "nodejs"})}, "manifest", "type is required"),
    ],
)
def test_a_bad_bundle_fails_early_and_leaves_nothing_behind(tmp_path, files, step, fragment):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        job = await harness.deploy(files=files)
        return harness, job

    harness, job = asyncio.run(scenario())
    assert job.status == "failed" and job.error["step"] == step and fragment in job.error["message"]
    statuses = _statuses(job)
    assert statuses[step] == "failed"
    assert all(statuses[name] == "pending" for name in STEPS[STEPS.index(step) + 1 :])
    assert not harness.paths.release_dir("app-1", SHA_A).exists()
    status = harness.runtime.status("app-1")
    assert (status.state, status.reason) == ("failed", "deploy")
    assert fragment in status.last_log_lines[0]
    assert f"FAILED at {step}" in _log(harness, job)


def test_a_failed_deployment_leaves_the_running_release_untouched(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        await harness.deploy(sha=SHA_A)
        starts_before = list(harness.controller.calls)
        kills_before = list(harness.sampler.killed)
        results = {"kills_before": kills_before}

        harness.runner.fail = {" install ": 1}
        results["pip"] = await harness.deploy(sha=SHA_B)
        harness.runner.fail = {}

        harness.runner.spawn_exit = 3
        results["exit"] = await harness.deploy(sha=SHA_B)
        harness.runner.spawn_exit = None

        harness.health.status = lambda url: 200 if url.endswith(f":{harness.registry.get('app-1').port}/") else 503
        files = dict(FASTAPI_FILES)
        files["efp-app.json"] = json.dumps({"name": "Mind map", "type": "fastapi", "entry": "main:app", "startup_timeout": 1})
        results["health"] = await harness.deploy(sha=SHA_B, files=files)
        return harness, results, starts_before

    harness, results, starts_before = asyncio.run(scenario())
    assert results["pip"].error["step"] == "dependencies" and "installing the dependencies failed (exit code 1)" in results["pip"].error["message"]
    assert "could not find a version" in _log(harness, results["pip"])
    assert results["exit"].error["step"] == "start" and "exit code 3" in results["exit"].error["message"]
    assert results["health"].error["step"] == "health" and "not healthy within 1 seconds (last answer: HTTP 503)" in results["health"].error["message"]
    assert _statuses(results["health"])["activate"] == "pending"

    record = harness.registry.get("app-1")
    assert record.current_sha == SHA_A and record.previous_sha is None and list(record.releases) == [SHA_A]
    assert harness.paths.release_dir("app-1", SHA_A).is_dir() and not harness.paths.release_dir("app-1", SHA_B).exists()
    # The app was never stopped or restarted, and every candidate was taken down.
    assert harness.controller.calls == starts_before
    status = harness.runtime.status("app-1")
    assert (status.state, status.release["sha"]) == ("running", SHA_A)
    assert all(process.stopped for _, process in harness.runner.spawned)
    # Nothing of the app's user was killed: its processes were the running app and the candidates.
    assert harness.sampler.killed == results["kills_before"]


def test_current_and_previous_are_kept_and_older_releases_leave_the_disk(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        for sha in (SHA_A, SHA_B, SHA_C):
            job = await harness.deploy(sha=sha)
            assert job.status == "succeeded"
        return harness

    harness = asyncio.run(scenario())
    record = harness.registry.get("app-1")
    assert (record.current_sha, record.previous_sha) == (SHA_C, SHA_B) and sorted(record.releases) == [SHA_B, SHA_C]
    releases = harness.paths.app_dir("app-1") / "releases"
    assert sorted(path.name for path in releases.iterdir()) == [SHA_B, SHA_C]
    status = harness.runtime.status("app-1")
    assert status.release["sha"] == SHA_C and status.previous_release["sha"] == SHA_B
    # Each switch stopped the old process and started the new one.
    assert harness.controller.started("app-app-1") == 3


def test_redeploying_a_retained_release_reuses_it(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        await harness.deploy(sha=SHA_A)
        await harness.deploy(sha=SHA_B)
        harness.runner.runs.clear()
        again = await harness.deploy(sha=SHA_A)
        same = await harness.deploy(sha=SHA_A)
        return harness, again, same

    harness, again, same = asyncio.run(scenario())
    for job in (again, same):
        assert job.status == "succeeded"
        assert _statuses(job) == {
            "unpack": "skipped",
            "manifest": "skipped",
            "environment": "skipped",
            "dependencies": "skipped",
            "start": "succeeded",
            "health": "succeeded",
            "activate": "succeeded",
        }
    assert harness.runner.runs == []
    record = harness.registry.get("app-1")
    assert (record.current_sha, record.previous_sha) == (SHA_A, SHA_B)
    assert "already on this host" in _log(harness, again)


def test_one_deployment_per_app_and_a_queue_across_apps(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        await harness.register("app-2", "review")
        harness.runner.delay = 0.05
        first = harness.runtime.deploy("app-1", sha=SHA_A, bundle=make_bundle(tmp_path / "1.tar.gz", FASTAPI_FILES))
        with pytest.raises(DeployInProgress):
            harness.runtime.deploy("app-1", sha=SHA_B, bundle=make_bundle(tmp_path / "2.tar.gz", FASTAPI_FILES))
        second = harness.runtime.deploy("app-2", sha=SHA_A, bundle=make_bundle(tmp_path / "3.tar.gz", FASTAPI_FILES))
        await asyncio.sleep(0.02)
        states = (harness.runtime.jobs.get(first.id).status, harness.runtime.jobs.get(second.id).status)
        await harness.runtime.deployer.wait(first.id)
        await harness.runtime.deployer.wait(second.id)
        await harness.settle()
        with pytest.raises(AppNotFound):
            harness.runtime.deploy("missing", sha=SHA_A, bundle=make_bundle(tmp_path / "4.tar.gz", FASTAPI_FILES))
        return harness, first, second, states

    harness, first, second, states = asyncio.run(scenario())
    assert states == ("running", "queued")
    assert harness.runner.max_running == 1
    assert harness.runtime.jobs.get(first.id).status == "succeeded" and harness.runtime.jobs.get(second.id).status == "succeeded"
    assert "waiting for another deployment" in _log(harness, second)
    assert harness.runtime.jobs.active_for("app-1") is None


def test_install_concurrency_lets_two_apps_build_at_once(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, install_concurrency=2)
        await harness.register()
        await harness.register("app-2", "review")
        harness.runner.delay = 0.05
        first = harness.runtime.deploy("app-1", sha=SHA_A, bundle=make_bundle(tmp_path / "1.tar.gz", FASTAPI_FILES))
        second = harness.runtime.deploy("app-2", sha=SHA_A, bundle=make_bundle(tmp_path / "2.tar.gz", FASTAPI_FILES))
        await harness.runtime.deployer.wait(first.id)
        await harness.runtime.deployer.wait(second.id)
        await harness.settle()
        return harness

    harness = asyncio.run(scenario())
    assert harness.runner.max_running == 2


def test_the_whole_deployment_has_a_time_limit(tmp_path):
    async def scenario():
        harness = Harness(tmp_path, deploy_timeout_seconds=1)
        await harness.register()
        harness.runner.delay = 5
        job = await harness.deploy()
        return harness, job

    harness, job = asyncio.run(scenario())
    assert job.status == "failed" and job.error["step"] == "environment"
    assert "longer than 1 seconds" in job.error["message"]
    assert not harness.paths.release_dir("app-1", SHA_A).exists()
    assert harness.runtime.jobs.active_for("app-1") is None


def test_a_deployment_is_refused_when_the_disk_is_almost_full(tmp_path, monkeypatch):
    import efp_app_host.deploy as deploy

    monkeypatch.setattr(deploy, "free_bytes", lambda path: 10 * 1024 * 1024)

    async def scenario():
        harness = Harness(tmp_path)
        await harness.register()
        return await harness.deploy()

    job = asyncio.run(scenario())
    assert job.status == "failed" and job.error["step"] == "unpack" and "almost full (10 MiB free)" in job.error["message"]


def test_a_disabled_app_can_be_deployed_but_is_not_started(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register(enabled=False, disabled_reason="taken down by an administrator")
        job = await harness.deploy()
        return harness, job

    harness, job = asyncio.run(scenario())
    assert job.status == "succeeded"
    assert harness.controller.started("app-app-1") == 0
    status = harness.runtime.status("app-1")
    assert (status.state, status.reason) == ("disabled", "taken down by an administrator")


def _modes_down_to(path, top) -> dict[str, int]:
    """Mode bits of ``path`` and of every directory above it, up to and including ``top``."""

    import stat
    from pathlib import Path

    found = {}
    current = Path(path)
    while True:
        found[current.as_posix()] = stat.S_IMODE(current.stat().st_mode)
        if current == Path(top) or current == current.parent:
            return found
        current = current.parent


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX file modes")
def test_the_gateway_can_reach_a_static_site_in_sandbox_mode(tmp_path):
    """nginx's worker (www-data) reads the directory itself: every level must let others in."""

    import os

    # A closed umask: the modes the gateway needs must not be left to it.
    old = os.umask(0o077)
    try:
        async def scenario():
            harness = Harness(tmp_path)
            from efp_app_host import boot

            boot.prepare_directories(harness.settings, harness.paths)
            await harness.register()
            await harness.deploy(files=STATIC_FILES)
            return harness

        harness = asyncio.run(scenario())
    finally:
        os.umask(old)
    root = harness.runtime.upstream("app-1").static_root
    modes = _modes_down_to(root, harness.settings.store_dir.parent)
    assert len(modes) >= 7
    for path, mode in modes.items():
        assert mode & 0o005 == 0o005, f"{path} is {oct(mode)}: closed to the gateway"
    import stat

    assert stat.S_IMODE(os.stat(root + "/index.html").st_mode) & 0o004
    # The same openness never reaches what holds secrets.
    assert stat.S_IMODE(harness.paths.env_file("app-1").stat().st_mode) == 0o600
    assert stat.S_IMODE(harness.paths.registry_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(harness.paths.jobs_dir.stat().st_mode) == 0o700


@pytest.mark.skipif(__import__("sys").platform == "win32", reason="POSIX file modes")
def test_the_gateway_can_reach_a_static_site_in_hidden_mode_without_the_volume(tmp_path):
    import os
    import stat

    old = os.umask(0o077)
    try:
        async def scenario():
            harness = Harness(tmp_path, mode="hidden")
            from efp_app_host import boot

            boot.prepare_directories(harness.settings, harness.paths)
            await harness.register()
            await harness.deploy(files=STATIC_FILES)
            return harness

        harness = asyncio.run(scenario())
    finally:
        os.umask(old)
    settings = harness.settings
    root = harness.runtime.upstream("app-1").static_root
    # The copy the gateway reads is on the local disk; the volume stays closed.
    assert root.startswith(settings.runtime_dir.as_posix() + "/.static/")
    assert stat.S_IMODE(settings.store_dir.parent.stat().st_mode) == 0o700
    area = settings.runtime_dir / ".static"
    # The area is for the gateway's group only (no other app may read a private site) ...
    assert stat.S_IMODE(area.stat().st_mode) == 0o750
    # ... and below and above it nothing stands in the gateway's way.
    assert stat.S_IMODE(settings.runtime_dir.stat().st_mode) & 0o005 == 0o005
    below = {path: mode for path, mode in _modes_down_to(root, area).items() if path != area.as_posix()}
    assert len(below) >= 3
    for path, mode in below.items():
        assert mode & 0o005 == 0o005, f"{path} is {oct(mode)}"
    assert stat.S_IMODE(os.stat(root + "/index.html").st_mode) & 0o004


def test_the_bundled_sdk_is_added_when_the_image_has_it(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        wheels = harness.runtime.bundled_wheels_dir
        wheels.mkdir(parents=True)
        (wheels / "efp_app_sdk-0.1.0-py3-none-any.whl").write_bytes(b"wheel")
        await harness.register()
        job = await harness.deploy()
        named = dict(FASTAPI_FILES)
        named["requirements.txt"] = "fastapi\nuvicorn\nefp-app-sdk==0.1.0\n"
        again = await harness.deploy(sha=SHA_B, files=named)
        return harness, job, again

    harness, job, again = asyncio.run(scenario())
    assert job.status == "succeeded" and again.status == "succeeded"
    installs = [command for command in harness.runner.commands() if " install " in command]
    wheels = harness.runtime.bundled_wheels_dir.as_posix()
    # Not named by the app: the host adds the runner and the SDK, the SDK from the image's own wheel.
    assert installs[0].endswith(" uvicorn efp-app-sdk") and f"--find-links {wheels} " in installs[0]
    assert "adding the bundled efp-app-sdk (efp_app_sdk-0.1.0-py3-none-any.whl)" in _log(harness, job)
    # Named by the app: its requirements decide, nothing is added.
    assert installs[1].endswith("/code/requirements.txt") and "--find-links" not in installs[1]
