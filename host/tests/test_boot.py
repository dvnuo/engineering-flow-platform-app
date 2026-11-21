from __future__ import annotations

import asyncio
import stat
import sys

import pytest
from conftest import FakePortal, FakeRuntime, make_settings
from runtime_helpers import SHA_A, STATIC_FILES, Harness

from efp_app_host import boot
from efp_app_host.isolation import IsolationStatus
from efp_app_host.paths import HostPaths
from efp_app_host.registry import Registry
from efp_app_host.state import HostState


def _state(harness: Harness) -> HostState:
    return HostState(settings=harness.settings, paths=harness.paths, registry=harness.registry, runtime=harness.runtime, portal=harness.portal)


def _desired(app_id="app-1", slug="mindmap", **extra):
    item = {
        "id": app_id,
        "slug": slug,
        "name": "Mind map",
        "owner_username": "alice",
        "visibility": "platform",
        "enabled": True,
        "disabled_reason": None,
        "current_sha": None,
        "visitor_key": "vkey-" + app_id,
        "env": {"EFP_APP_TOKEN": "efpa_" + app_id},
        "limits": {"memory_mb": 512, "data_mb": None},
    }
    item.update(extra)
    return item


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_directories_are_created(tmp_path):
    settings = make_settings(tmp_path / "host")
    paths = HostPaths.of(settings)
    assert boot.prepare_directories(settings, paths) == []
    for directory in (paths.store / "apps", paths.keys_dir, paths.jobs_dir, paths.runtime, paths.run / "launch", paths.supervisor_apps_dir):
        assert directory.is_dir()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_directory_modes_follow_the_isolation_mode(tmp_path):
    sandbox = make_settings(tmp_path / "sandbox", isolation_mode="sandbox")
    boot.prepare_directories(sandbox, HostPaths.of(sandbox))
    paths = HostPaths.of(sandbox)
    # sandbox: the gateway and a Landlock-confined app reach the volume by its real path.
    assert _mode(paths.store.parent) == 0o755
    assert _mode(paths.keys_dir) == 0o700 and _mode(paths.jobs_dir) == 0o700 and _mode(paths.run / "launch") == 0o700

    hidden = make_settings(tmp_path / "hidden", isolation_mode="hidden")
    boot.prepare_directories(hidden, HostPaths.of(hidden))
    # hidden: nobody but root may enter the directory above the volume.
    assert _mode(HostPaths.of(hidden).store.parent) == 0o700


def test_hidden_mode_needs_a_parent_directory_it_can_close(tmp_path):
    from pathlib import Path

    root = Path(tmp_path.anchor)
    settings = make_settings(tmp_path, isolation_mode="hidden", store_dir=root / "efp-store-test-never-created")
    paths = HostPaths.of(settings)
    assert paths.store.parent == paths.store.parent.parent

    # Only the check: the directories themselves are not wanted at the root of a disk.
    import efp_app_host.boot as module

    made = []
    original = module.make_dir
    module.make_dir = lambda path, mode, **kwargs: made.append(path)
    try:
        problems = boot.prepare_directories(settings, paths)
    finally:
        module.make_dir = original
    assert len(problems) == 1 and "hidden mode needs a parent directory" in problems[0]


def test_reconcile_registers_updates_and_disables_but_never_deploys(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register("app-old", "legacy")
        await harness.deploy("app-old", SHA_A, STATIC_FILES)
        await harness.register("app-1", "mindmap", env={"EFP_APP_TOKEN": "efpa_stale"})
        jobs_before = len(list(harness.paths.jobs_dir.glob("*.json")))
        harness.portal.desired = [
            _desired("app-1", "mindmap", current_sha="f" * 40),
            _desired("app-2", "review", enabled=False, disabled_reason="taken down"),
            _desired("app-3", "login"),  # a reserved slug
            _desired("app-4", "mindmap"),  # the slug of app-1
            "not an object",
        ]
        answered = await boot.reconcile(_state(harness))
        await harness.settle()
        return harness, answered, jobs_before

    harness, answered, jobs_before = asyncio.run(scenario())
    assert answered is True
    registry = harness.registry
    assert sorted(record.id for record in registry.all()) == ["app-1", "app-2", "app-old"]

    first = registry.get("app-1")
    assert (first.name, first.visibility, first.memory_mb, first.visitor_key) == ("Mind map", "platform", 512, "vkey-app-1")
    assert registry.read_env("app-1") == {"EFP_APP_TOKEN": "efpa_app-1"}
    # The Portal expects a release the host does not have: nothing is deployed for it.
    assert first.current_sha is None and len(list(harness.paths.jobs_dir.glob("*.json"))) == jobs_before
    assert harness.runner.runs == [] and harness.runner.spawned == []

    second = registry.get("app-2")
    assert (second.enabled, second.disabled_reason) == (False, "taken down")
    assert "app-app-2" in harness.controller.programs

    # No longer listed: disabled, with everything it had still on the volume.
    old = registry.get("app-old")
    assert (old.enabled, old.disabled_reason) == (False, "removed from the Portal")
    assert old.current_sha == SHA_A and harness.paths.release_dir("app-old", SHA_A).is_dir()
    status = harness.runtime.status("app-old")
    assert (status.state, status.reason) == ("disabled", "removed from the Portal")


def test_an_app_listed_again_is_enabled_again(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        await harness.register("app-1", "mindmap")
        state = _state(harness)
        harness.portal.desired = []
        await boot.reconcile(state)
        disabled = harness.registry.get("app-1").enabled
        harness.portal.desired = [_desired("app-1", "mindmap")]
        await boot.reconcile(state)
        return disabled, harness.registry.get("app-1")

    disabled, record = asyncio.run(scenario())
    assert disabled is False and record.enabled is True and record.disabled_reason is None


def test_a_portal_that_does_not_answer_changes_nothing(tmp_path):
    class DownPortal(FakePortal):
        async def desired_apps(self):
            from efp_app_host.portal_client import PortalUnavailable

            raise PortalUnavailable("connection refused")

    async def scenario():
        harness = Harness(tmp_path)
        await harness.register("app-1", "mindmap")
        harness.portal = DownPortal()
        state = _state(harness)
        answered = await boot.reconcile(state)
        return answered, harness.registry.get("app-1").enabled

    assert asyncio.run(scenario()) == (False, True)


def test_startup_and_shutdown(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "MONITOR_SECONDS", 0.01)
    monkeypatch.setattr(boot, "WATCHDOG_SECONDS", 0.01)
    monkeypatch.setattr(boot, "IDLE_SECONDS", 0.01)
    monkeypatch.setattr(boot, "DATA_SCAN_SECONDS", 0.01)

    async def scenario():
        # A first life of the host: one app deployed and running, one deployment cut short.
        first = Harness(tmp_path)
        await first.register("app-1", "mindmap")
        await first.deploy("app-1")
        cut = first.runtime.jobs.create("app-1", "b" * 40)
        cut.status = "running"
        first.runtime.jobs.begin_step(cut, "unpack")

        # The manager restarts; supervisord and what it runs stay.
        harness = Harness(tmp_path)
        harness.runtime.controller = first.controller
        harness.controller = first.controller
        probes = []

        async def detect():
            probes.append(1)
            harness.runtime.isolation = IsolationStatus(mode="sandbox", backend="landlock")
            return harness.runtime.isolation

        harness.runtime.detect_isolation = detect
        # The Portal lists the app with the environment the host already has.
        harness.portal.desired = [_desired("app-1", "mindmap", env=first.registry.read_env("app-1")), _desired("app-2", "review")]
        state = _state(harness)
        await boot.startup(state)
        for _ in range(200):
            if harness.registry.find("app-2") is not None:
                break
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        starts = harness.controller.started("app-app-1")
        running = harness.runtime.status("app-1")

        # The loops are alive: a process that goes away is noticed.
        harness.controller.exit("app-app-1", 1)
        for _ in range(200):
            if harness.runtime.status("app-1").state == "crashed":
                break
            await asyncio.sleep(0.01)
        crashed = harness.runtime.status("app-1")
        tasks = list(state.extras["tasks"])
        await boot.shutdown(state)
        return harness, probes, cut.id, starts, running, crashed, tasks, state

    harness, probes, cut_id, starts, running, crashed, tasks, state = asyncio.run(scenario())
    assert probes == [1]
    assert harness.runtime.jobs.get(cut_id).status == "failed"
    assert harness.runtime.jobs.get(cut_id).error["message"] == "the host restarted during the deployment"
    # The app that kept running was adopted, not started again; nothing is started at boot.
    assert running.state == "running" and starts == 1
    assert sorted(harness.controller.programs) == ["app-app-1", "app-app-2"]
    assert harness.controller.started("app-app-2") == 0
    assert harness.registry.get("app-2").visitor_key == "vkey-app-2"
    assert crashed.state == "crashed"
    assert len(tasks) == 5 and all(task.done() for task in tasks)
    assert "tasks" not in state.extras and "storage" not in state.extras


def test_startup_with_a_test_double_only_reconciles(tmp_path):
    async def scenario():
        settings = make_settings(tmp_path)
        registry = Registry(settings)
        portal = FakePortal()
        portal.desired = [_desired("app-1", "mindmap")]
        state = HostState(settings=settings, paths=HostPaths.of(settings), registry=registry, runtime=FakeRuntime(registry), portal=portal)
        await boot.startup(state)
        for _ in range(200):
            if registry.find("app-1") is not None:
                break
            await asyncio.sleep(0.01)
        await boot.shutdown(state)
        return registry

    registry = asyncio.run(scenario())
    assert registry.get("app-1").slug == "mindmap" and registry.read_env("app-1") == {"EFP_APP_TOKEN": "efpa_app-1"}


def test_hidden_mode_puts_static_sites_back_at_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(boot, "StorageService", _NoSocketService)

    async def scenario():
        first = Harness(tmp_path, mode="hidden")
        await first.register("app-1", "docs")
        await first.deploy("app-1", SHA_A, STATIC_FILES)
        root = first.runtime.upstream("app-1").static_root
        import shutil

        shutil.rmtree(first.settings.runtime_dir)

        harness = Harness(tmp_path, mode="hidden")

        async def detect():
            return harness.runtime.isolation

        harness.runtime.detect_isolation = detect
        harness.portal.desired = [_desired("app-1", "docs")]
        state = _state(harness)
        await boot.startup(state)
        service = state.extras.get("storage")
        await boot.shutdown(state)
        return root, service

    root, service = asyncio.run(scenario())
    assert open(root + "/index.html", encoding="utf-8").read() == "<h1>hello</h1>\n"
    # Hidden mode starts the storage service, on the socket the apps are told about.
    assert isinstance(service, _NoSocketService) and service.started and service.stopped
    assert service.socket_path.name == "storage.sock"


class _NoSocketService:
    """Stands in for the storage service where unix sockets do not exist."""

    def __init__(self, socket_path, *, find_app, store_for):
        self.socket_path = socket_path
        self.find_app = find_app
        self.store_for = store_for
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


def test_one_app_that_cannot_be_registered_does_not_end_the_reconciliation(tmp_path, monkeypatch):
    from efp_app_host.controller import ControllerError

    async def scenario():
        harness = Harness(tmp_path)
        await harness.register("app-old", "legacy")
        add = harness.controller.add
        broken = {"app-app-1"}

        def flaky_add(program):
            if program.name in broken:
                raise ControllerError("supervisor reloadConfig: not now")
            add(program)

        harness.controller.add = flaky_add
        harness.portal.desired = [
            _desired("app-1", "mindmap"),
            _desired("app-2", "review", enabled=False, disabled_reason="taken down"),
        ]
        state = _state(harness)
        first = await boot.reconcile(state)
        after_first = {
            "app-2": harness.registry.find("app-2") is not None and harness.registry.get("app-2").enabled is False,
            "app-old": harness.registry.get("app-old").enabled,
            "app-1 program": "app-app-1" in harness.controller.programs,
        }
        # The background task comes back for what failed, instead of ending for good.
        monkeypatch.setattr(boot, "RECONCILE_RETRY_SECONDS", 0.01)
        task = asyncio.get_running_loop().create_task(boot._reconcile_until_done(state))
        await asyncio.sleep(0.1)
        still_trying = not task.done()
        broken.clear()
        await asyncio.wait_for(task, 10)
        return harness, first, after_first, still_trying

    harness, first, after_first, still_trying = asyncio.run(scenario())
    # Not done, but everything else was: the app after the broken one is
    # registered (disabled, as the Portal says) and the one the Portal no
    # longer lists is disabled.
    assert first is False
    assert after_first == {"app-2": True, "app-old": False, "app-1 program": False}
    assert still_trying is True
    assert "app-app-1" in harness.controller.programs and harness.registry.get("app-1").enabled is True


def test_an_error_nobody_expected_does_not_end_the_reconciliation_either(tmp_path, monkeypatch):
    async def scenario():
        harness = Harness(tmp_path)
        state = _state(harness)
        calls = []
        real = boot.reconcile

        async def reconcile(given):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("something new")
            return await real(given)

        monkeypatch.setattr(boot, "reconcile", reconcile)
        monkeypatch.setattr(boot, "RECONCILE_RETRY_SECONDS", 0.01)
        await asyncio.wait_for(boot._reconcile_until_done(state), 10)
        return calls

    assert len(asyncio.run(scenario())) == 2


def test_an_app_id_the_manager_api_would_refuse_is_not_registered_at_boot(tmp_path):
    async def scenario():
        harness = Harness(tmp_path)
        harness.portal.desired = [
            _desired("../../etc", "climber"),
            _desired("app-1\n", "newline"),
            _desired("a" * 65, "too-long"),
            _desired("app-ok", "fine-app"),
        ]
        answered = await boot.reconcile(_state(harness))
        return harness, answered

    harness, answered = asyncio.run(scenario())
    # The id becomes a directory on the volume and a program name of the supervisor.
    assert answered is True
    assert [record.id for record in harness.registry.all()] == ["app-ok"]
    assert sorted(path.name for path in (harness.paths.store / "apps").iterdir()) == ["app-ok"]
