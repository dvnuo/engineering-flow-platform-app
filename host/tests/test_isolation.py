from __future__ import annotations

import pytest
from conftest import make_settings

from efp_app_host.isolation import (
    BACKEND_BWRAP,
    BACKEND_BWRAP_USERNS,
    BACKEND_LANDLOCK,
    BACKEND_PLAIN,
    IsolationError,
    LaunchSpec,
    SystemLayout,
    build_exec_plan,
    detect,
)
from efp_app_host.landlock import FS_EXECUTE, FS_IOCTL_DEV, FS_MAKE_REG, FS_READ_DIR, FS_READ_FILE, FS_REFER, FS_TRUNCATE, READ_EXEC, READ_WRITE, handled_rights, rule_rights
from efp_app_host.paths import HostPaths

LAYOUT = SystemLayout(
    dirs=["/usr"],
    links=[("/bin", "usr/bin"), ("/lib", "usr/lib")],
    etc_masks=["/etc/nginx", "/etc/supervisor"],
    etc_entries=["/etc/hosts", "/etc/passwd", "/etc/ssl"],
    optional=["/opt/efp", "/sys/devices/system/cpu"],
)
RELEASE = "/var/lib/efp-host/store/apps/app-1/releases/abc"
DATA = "/var/lib/efp-host/store/apps/app-1/data"
TMP = "/srv/efp-apps/app-1/tmp"
APP = ["/venv/bin/python", "-m", "uvicorn", "main:app"]


def _spec(backend: str, **extra) -> LaunchSpec:
    values = dict(
        argv=list(APP),
        env={"PORT": "20000"},
        cwd=RELEASE + "/code",
        uid=20001,
        gid=20001,
        backend=backend,
        ro_paths=[RELEASE],
        rw_paths=[DATA],
        tmp_dir=TMP,
    )
    values.update(extra)
    return LaunchSpec(**values)


def _pairs(argv: list[str], flag: str) -> list[tuple[str, str]]:
    return [(argv[index + 1], argv[index + 2]) for index, value in enumerate(argv) if value == flag]


def test_bwrap_as_root_builds_the_view_then_drops_privileges():
    plan = build_exec_plan(_spec(BACKEND_BWRAP), LAYOUT)
    argv = plan.argv
    assert argv[0] == "/usr/bin/bwrap" and "--unshare-user" not in argv
    assert plan.ignore_signals and not plan.drop_in_process and plan.landlock is None
    # A new /proc of its own pid namespace.
    assert "--unshare-pid" in argv and ("--proc", "/proc") == (argv[argv.index("--proc")], argv[argv.index("--proc") + 1])
    ro = _pairs(argv, "--ro-bind")
    assert ("/usr", "/usr") in ro and ("/etc", "/etc") in ro and (RELEASE, RELEASE) in ro
    assert _pairs(argv, "--symlink") == [("usr/bin", "/bin"), ("usr/lib", "/lib")]
    assert _pairs(argv, "--bind") == [(TMP, "/tmp"), (TMP, TMP), (DATA, DATA)]
    # The host's own configuration is hidden; /dev/shm is a tmpfs of its own,
    # open to the app (bubblewrap's is a directory of root) and with a ceiling.
    masked = [argv[index + 1] for index, value in enumerate(argv) if value == "--tmpfs"]
    assert masked == ["/dev/shm", "/etc/nginx", "/etc/supervisor"]
    shm = argv.index("/dev/shm")
    assert argv[shm - 5 : shm] == ["--perms", "1777", "--size", str(64 * 1024 * 1024), "--tmpfs"]
    assert argv.index("--dev") < shm
    # What bubblewrap made itself is closed at the very end, the root last:
    # after it no mount point could be created any more.
    closed = [argv[index + 1] for index, value in enumerate(argv) if value == "--remount-ro"]
    assert closed == ["/etc/nginx", "/etc/supervisor", "/dev", "/"]
    last_mount = max(index for index, value in enumerate(argv) if value in ("--bind", "--ro-bind", "--tmpfs", "--dir", "--symlink", "--ro-bind-try"))
    first_close = argv.index("--remount-ro")
    assert last_mount < first_close < argv.index("--chdir")
    assert argv[argv.index("--chdir") - 2 : argv.index("--chdir")] == ["--remount-ro", "/"]
    # The app's data and tmp are mounts of their own: closing the root does not close them.
    assert DATA not in closed and TMP not in closed and "/dev/shm" not in closed and "/tmp" not in closed
    # Nothing of the volume besides the app's own two directories.
    volume = [source for source, _ in ro + _pairs(argv, "--bind") if source.startswith("/var/lib/efp-host")]
    assert volume == [RELEASE, DATA]
    assert argv[argv.index("--chdir") + 1] == RELEASE + "/code"
    # Inside the view: setpriv drops to the app's user, env restores SIGTERM, then the app.
    inner = argv[argv.index("--chdir") + 3 :]
    assert inner == [
        "/usr/bin/setpriv", "--reuid", "20001", "--regid", "20001", "--clear-groups", "--no-new-privs", "--inh-caps=-all", "--",
        "/usr/bin/env", "--default-signal=TERM", "--",
        *APP,
    ]  # fmt: skip


def test_the_data_directory_is_mounted_after_the_read_only_release():
    spec = _spec(BACKEND_BWRAP, ro_paths=[RELEASE], rw_paths=[RELEASE + "/venv"])
    argv = build_exec_plan(spec, LAYOUT).argv
    read_only = argv.index(RELEASE)
    writable = argv.index(RELEASE + "/venv")
    assert argv[read_only - 1] == "--ro-bind" and argv[writable - 1] == "--bind" and read_only < writable


def test_bwrap_in_a_user_namespace_drops_privileges_first():
    plan = build_exec_plan(_spec(BACKEND_BWRAP_USERNS), LAYOUT)
    argv = plan.argv
    assert argv[:9] == ["/usr/bin/setpriv", "--reuid", "20001", "--regid", "20001", "--clear-groups", "--no-new-privs", "--inh-caps=-all", "--"]
    assert argv[9] == "/usr/bin/bwrap" and "--unshare-user" in argv
    assert plan.ignore_signals
    assert argv[-len(APP) - 3 : -len(APP)] == ["/usr/bin/env", "--default-signal=TERM", "--"]


def test_bwrap_user_namespace_can_reuse_the_containers_proc():
    argv = build_exec_plan(_spec(BACKEND_BWRAP_USERNS, options={"proc": "bind"}), LAYOUT).argv
    assert "--unshare-pid" not in argv and "--proc" not in argv
    assert ("/proc", "/proc") in _pairs(argv, "--ro-bind")


def test_landlock_drops_in_process_and_lists_what_stays_reachable():
    plan = build_exec_plan(_spec(BACKEND_LANDLOCK), LAYOUT)
    assert plan.argv == APP and plan.drop_in_process and not plan.ignore_signals
    assert plan.chdir == RELEASE + "/code"
    rules = plan.landlock
    assert rules is not None
    assert RELEASE in rules.read_exec and "/usr" in rules.read_exec and "/etc/ssl" in rules.read_exec
    assert rules.read_write[:2] == [DATA, TMP]
    assert rules.devices == ["/dev"]
    everything = rules.read_exec + rules.read_write + rules.devices
    # No rule for the volume, the run directory or the host's configuration.
    assert not any(path in ("/", "/var", "/var/lib/efp-host/store", "/run", "/etc", "/etc/nginx", "/srv/efp-apps") for path in everything)
    assert [path for path in everything if path.startswith("/var/lib/efp-host")] == [RELEASE, DATA]


def test_plain_only_switches_user():
    plan = build_exec_plan(_spec(BACKEND_PLAIN, ro_paths=[], rw_paths=[]), LAYOUT)
    assert plan.argv == ["/usr/bin/setpriv", "--reuid", "20001", "--regid", "20001", "--clear-groups", "--no-new-privs", "--inh-caps=-all", "--", *APP]
    assert plan.chdir == RELEASE + "/code" and not plan.ignore_signals and not plan.drop_in_process


def test_a_launcher_that_is_already_the_apps_user_has_nothing_to_switch():
    plain = build_exec_plan(_spec(BACKEND_PLAIN), LAYOUT, current_uid=20001)
    assert plain.argv == ["/usr/bin/setpriv", "--no-new-privs", "--", *APP]
    landlock = build_exec_plan(_spec(BACKEND_LANDLOCK), LAYOUT, current_uid=20001)
    assert not landlock.drop_in_process and landlock.landlock is not None
    with pytest.raises(IsolationError, match="root"):
        build_exec_plan(_spec(BACKEND_BWRAP), LAYOUT, current_uid=20001)


def test_an_app_never_runs_as_root():
    with pytest.raises(IsolationError, match="root"):
        build_exec_plan(_spec(BACKEND_PLAIN, uid=0), LAYOUT)
    with pytest.raises(IsolationError, match="root"):
        build_exec_plan(_spec(BACKEND_BWRAP, gid=0), LAYOUT)
    with pytest.raises(IsolationError):
        build_exec_plan(_spec("chroot"), LAYOUT)
    with pytest.raises(IsolationError):
        build_exec_plan(_spec(BACKEND_PLAIN, argv=[]), LAYOUT)


def test_launch_spec_round_trip():
    spec = _spec(BACKEND_BWRAP, options={"proc": "bind"}, label="mindmap")
    assert LaunchSpec.from_json({**spec.to_json(), "unknown": 1}) == spec


def test_landlock_rights_by_abi():
    assert handled_rights(1) & FS_REFER == 0 and handled_rights(1) & FS_MAKE_REG
    assert handled_rights(2) & FS_REFER and handled_rights(2) & FS_TRUNCATE == 0
    assert handled_rights(3) & FS_TRUNCATE and handled_rights(4) & FS_IOCTL_DEV == 0
    assert handled_rights(5) & FS_IOCTL_DEV
    # A rule on a file may only carry file rights; a rule never asks for more than the kernel knows.
    assert rule_rights(READ_EXEC, 1, is_dir=False) == FS_EXECUTE | FS_READ_FILE
    assert rule_rights(READ_EXEC, 1, is_dir=True) == FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
    assert rule_rights(READ_WRITE, 1, is_dir=True) & (FS_REFER | FS_TRUNCATE) == 0
    assert rule_rights(READ_WRITE, 3, is_dir=True) & FS_TRUNCATE


# ---- detection ---------------------------------------------------------------------


class ProbeRunner:
    """Answers the probe of each backend with a canned exit code."""

    def __init__(self, results: dict[str, int | list[int]]):
        self.results = results
        self.specs: list[LaunchSpec] = []

    def __call__(self, spec: LaunchSpec, timeout: float) -> tuple[int, str]:
        self.specs.append(spec)
        result = self.results.get(spec.backend, 1)
        code = result.pop(0) if isinstance(result, list) else result
        return code, "probe ok\n" if code == 0 else f"bwrap: {spec.backend} does not work here\n"


def _detect(tmp_path, runner, **overrides):
    settings = make_settings(tmp_path, **overrides)
    return detect(settings, HostPaths.of(settings), runner=runner, platform="linux"), settings


def test_auto_takes_the_first_backend_that_works(tmp_path):
    runner = ProbeRunner({BACKEND_BWRAP: 0})
    status, settings = _detect(tmp_path, runner)
    assert status.ok and status.backend == BACKEND_BWRAP and status.sandbox_backend == BACKEND_BWRAP
    assert [spec.backend for spec in runner.specs] == [BACKEND_BWRAP]
    spec = runner.specs[0]
    # The probe runs as an unprivileged user and may see one directory of the volume only.
    assert spec.uid == settings.uid_range_start - 1
    assert len(spec.ro_paths) == 1 and spec.ro_paths[0].endswith("inside")
    assert "outside" in " ".join(spec.argv)
    # The probe leaves nothing behind.
    assert not list(settings.store_dir.glob(".probe-*")) and not list(settings.runtime_dir.glob(".probe-*"))


def test_auto_falls_through_in_order(tmp_path):
    runner = ProbeRunner({BACKEND_BWRAP: 1, BACKEND_BWRAP_USERNS: [1, 0]})
    status, _ = _detect(tmp_path, runner)
    assert status.backend == BACKEND_BWRAP_USERNS and status.options == {"proc": "bind"}
    assert [spec.backend for spec in runner.specs] == [BACKEND_BWRAP, BACKEND_BWRAP_USERNS, BACKEND_BWRAP_USERNS]

    runner = ProbeRunner({BACKEND_LANDLOCK: 0})
    status, _ = _detect(tmp_path, runner)
    assert status.backend == BACKEND_LANDLOCK
    assert [spec.backend for spec in runner.specs] == [BACKEND_BWRAP, BACKEND_BWRAP_USERNS, BACKEND_BWRAP_USERNS, BACKEND_LANDLOCK]


def test_nothing_works_means_no_backend_and_a_reason(tmp_path):
    status, _ = _detect(tmp_path, ProbeRunner({}))
    assert not status.ok and status.backend is None and status.sandbox_backend is None
    assert "no sandbox backend works" in status.error
    assert set(status.attempts) == {BACKEND_BWRAP, BACKEND_BWRAP_USERNS, BACKEND_LANDLOCK}
    assert "does not work here" in status.error


def test_a_named_backend_is_the_only_one_tried(tmp_path):
    runner = ProbeRunner({BACKEND_BWRAP: 0, BACKEND_LANDLOCK: 1})
    status, _ = _detect(tmp_path, runner, sandbox_backend="landlock")
    assert not status.ok and [spec.backend for spec in runner.specs] == [BACKEND_LANDLOCK]


def test_hidden_mode_checks_that_the_volume_is_out_of_reach(tmp_path):
    runner = ProbeRunner({BACKEND_PLAIN: 0})
    status, settings = _detect(tmp_path, runner, isolation_mode="hidden")
    assert status.ok and status.backend == BACKEND_PLAIN and status.sandbox_backend is None
    spec = runner.specs[0]
    assert spec.ro_paths == [] and spec.rw_paths == []
    # What the app may read is on the local disk; what it must not reach is on the volume.
    inside, outside = spec.argv[4], spec.argv[5]
    assert str(settings.runtime_dir) in inside and str(settings.store_dir) in outside

    status, _ = _detect(tmp_path, ProbeRunner({BACKEND_PLAIN: 42}), isolation_mode="hidden")
    assert not status.ok and "hidden mode is not safe here" in status.error


def test_isolation_needs_linux(tmp_path):
    settings = make_settings(tmp_path)
    status = detect(settings, HostPaths.of(settings), runner=ProbeRunner({BACKEND_BWRAP: 0}), platform="win32")
    assert not status.ok and "Linux" in status.error


# ---- the probe command -------------------------------------------------------------


def test_the_probe_recommends_the_first_isolation_that_works():
    from efp_app_host.probe import NEEDS, recommend

    results = [
        ("sandbox", BACKEND_BWRAP, False, "bwrap: Operation not permitted"),
        ("sandbox", BACKEND_BWRAP_USERNS, False, "no user namespaces"),
        ("sandbox", BACKEND_LANDLOCK, True, ""),
        ("hidden", BACKEND_PLAIN, True, ""),
    ]
    assert recommend(results) == ("sandbox", BACKEND_LANDLOCK)
    assert recommend(results[:2] + results[3:]) == ("hidden", BACKEND_PLAIN)
    assert recommend(results[:2]) is None
    # Every isolation the probe can name says what it needs.
    assert set(NEEDS) == {BACKEND_BWRAP, BACKEND_BWRAP_USERNS, BACKEND_LANDLOCK, BACKEND_PLAIN}


def test_the_probe_tries_every_isolation_on_the_given_volume(tmp_path, monkeypatch):
    import efp_app_host.probe as probe_module

    seen = []

    def fake_detect(settings, paths):
        from efp_app_host.isolation import IsolationStatus

        seen.append((settings.isolation_mode, settings.sandbox_backend, paths.store, paths.runtime))
        works = settings.isolation_mode == "hidden" or settings.sandbox_backend == "bwrap-userns"
        options = {"proc": "bind"} if settings.sandbox_backend == "bwrap-userns" else {}
        if works:
            return IsolationStatus(mode=settings.isolation_mode, backend="plain" if settings.isolation_mode == "hidden" else settings.sandbox_backend, options=options)
        return IsolationStatus(mode=settings.isolation_mode, error="does not work here")

    monkeypatch.setattr(probe_module, "detect", fake_detect)
    results = probe_module.probe(tmp_path / "volume" / "store", tmp_path / "local", tmp_path / "run")
    assert [(mode, backend, ok) for mode, backend, ok, _ in results] == [
        ("sandbox", "bwrap", False),
        ("sandbox", "bwrap-userns", True),
        ("sandbox", "landlock", False),
        ("hidden", "plain", True),
    ]
    assert results[1][3] == "proc=bind" and results[0][3] == "does not work here"
    # Each sandbox backend is asked for by name, so that one failure does not hide behind another's success.
    assert [item[:2] for item in seen] == [("sandbox", "bwrap"), ("sandbox", "bwrap-userns"), ("sandbox", "landlock"), ("hidden", "auto")]
    assert all(item[2] == tmp_path / "volume" / "store" for item in seen)
    only = probe_module.probe(tmp_path / "volume" / "store", tmp_path / "local", tmp_path / "run", only="hidden")
    assert [(mode, backend) for mode, backend, _, _ in only] == [("hidden", "plain")]


def test_a_user_namespace_view_is_closed_the_same_way():
    """In a user namespace bubblewrap's own tmpfs belong to the app: without this
    the app could write to the root of its view and to the directories above its own."""

    for options in ({"proc": "new"}, {"proc": "bind"}):
        argv = build_exec_plan(_spec(BACKEND_BWRAP_USERNS, options=options), LAYOUT).argv
        closed = [argv[index + 1] for index, value in enumerate(argv) if value == "--remount-ro"]
        assert closed == ["/etc/nginx", "/etc/supervisor", "/dev", "/"]
        assert argv[argv.index("--chdir") - 2 : argv.index("--chdir")] == ["--remount-ro", "/"]


def test_a_view_without_a_private_tmp_still_has_a_usable_one():
    argv = build_exec_plan(_spec(BACKEND_BWRAP, tmp_dir=""), LAYOUT).argv
    tmp = argv.index("/tmp")
    assert argv[tmp - 3 : tmp] == ["--perms", "1777", "--tmpfs"]


def test_the_probe_explains_a_root_that_may_not_use_its_capabilities(tmp_path):
    """bwrap as root keeps its capabilities; when setpriv inside still may not
    switch users, the cause is outside the container and the message says where to look."""

    class Refused:
        def __init__(self):
            self.specs = []

        def __call__(self, spec, timeout):
            self.specs.append(spec)
            if spec.backend == BACKEND_BWRAP:
                return 1, "setpriv: setresuid failed: Operation not permitted\n"
            return 0, "probe ok\n"

    status, _ = _detect(tmp_path, Refused())
    assert status.ok and status.backend == BACKEND_BWRAP_USERNS
    assert "setresuid failed" in status.attempts[BACKEND_BWRAP]
    assert "AppArmor" in status.attempts[BACKEND_BWRAP] and "deploy/apparmor/efp-app-host" in status.attempts[BACKEND_BWRAP]
    # Any other failure is reported as it is.
    status, _ = _detect(tmp_path, ProbeRunner({BACKEND_LANDLOCK: 0}))
    assert "AppArmor" not in status.attempts[BACKEND_BWRAP]


def test_every_directory_above_a_mount_is_made_open_before_the_mount():
    """bubblewrap would make them itself, but for its own user only: run as root,
    the app (another user by then) could not reach its own directory."""

    spec = _spec(BACKEND_BWRAP, ro_paths=[RELEASE], rw_paths=[RELEASE + "/venv", DATA])
    argv = build_exec_plan(spec, LAYOUT).argv
    made = [argv[index + 1] for index, value in enumerate(argv) if value == "--dir"]
    assert made == [
        "/opt",
        "/sys",
        "/sys/devices",
        "/sys/devices/system",
        "/run",
        "/var",
        "/srv",
        "/srv/efp-apps",
        "/srv/efp-apps/app-1",
        "/var/lib",
        "/var/lib/efp-host",
        "/var/lib/efp-host/store",
        "/var/lib/efp-host/store/apps",
        "/var/lib/efp-host/store/apps/app-1",
        "/var/lib/efp-host/store/apps/app-1/releases",
    ]
    assert len(made) == len(set(made))

    def position(option: str, target: str) -> int:
        return next(index for index, value in enumerate(argv) if value == option and argv[index + 2] == target)

    def made_before(directory: str, mount: int) -> bool:
        return any(argv[index] == "--dir" and argv[index + 1] == directory for index in range(mount))

    for option, target in (("--ro-bind-try", "/opt/efp"), ("--ro-bind-try", "/sys/devices/system/cpu"), ("--bind", TMP), ("--ro-bind", RELEASE), ("--bind", DATA)):
        mount = position(option, target)
        parts = target.strip("/").split("/")
        for count in range(1, len(parts)):
            assert made_before("/" + "/".join(parts[:count]), mount), (target, parts[:count])
    # A directory inside something already mounted comes from there: nothing is made in it.
    assert RELEASE not in made and not any(directory.startswith(RELEASE + "/") for directory in made)
    assert position("--ro-bind", RELEASE) < position("--bind", RELEASE + "/venv")
    # Places directly below the root need nothing.
    assert "/usr" not in made and "/etc" not in made and "/tmp" not in made


def test_the_directories_above_a_mount_are_the_same_in_a_user_namespace():
    root = build_exec_plan(_spec(BACKEND_BWRAP), LAYOUT).argv
    userns = build_exec_plan(_spec(BACKEND_BWRAP_USERNS), LAYOUT).argv
    dirs = lambda argv: [argv[index + 1] for index, value in enumerate(argv) if value == "--dir"]  # noqa: E731
    assert dirs(root) == dirs(userns) and "/var/lib/efp-host/store/apps/app-1" in dirs(userns)
