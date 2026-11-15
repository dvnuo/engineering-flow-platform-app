"""The runtime half of the manager: what runs, as whom, and in what state.

``HostRuntime`` implements ``interfaces.AppRuntime`` for the gateway and adds
what the admin API needs (registration, deployments, the life cycle, logs).

State of an app's process lives here, in memory:

    stopped --ensure_running/start--> starting --healthy--> running
    running --exits by itself--> crashed --back-off--> starting ...
    running --idle / stop--> stopped        running --over memory--> killed
    starting --never healthy--> failed

supervisord only spawns and reaps (``controller``); it never restarts
anything on its own. The loops in ``boot`` call ``monitor_once``,
``watchdog_once``, ``scan_data_once`` and ``idle_once``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from . import __version__
from .applog import append_note, follow, tail_lines
from .config import Settings
from .controller import PROC_ABSENT, ProcessController, ProcessInfo, ProgramSpec, SupervisorController, program_name
from .deploy import Deployer, ReleaseBuilder, make_dir, remove_tree
from .interfaces import PortalApi
from .isolation import BACKEND_PLAIN, IsolationError, IsolationStatus, LaunchSpec, detect
from .jobs import JOB_FAILED, Job, JobStore, Redactor
from .launch import app_environment, build_command, health_url, run_dirs, static_root
from .manifest import strips_prefix
from .models import (
    STATE_CRASHED,
    STATE_DISABLED,
    STATE_FAILED,
    STATE_KILLED,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_UNDEPLOYED,
    AppRecord,
    AppStatus,
    Release,
    Upstream,
    iso,
    utcnow,
)
from .osusers import sync_os_users
from .procstats import CpuMeter, ProcessSampler, PsutilSampler, dir_size, free_bytes, host_numbers
from .pyenv import BUNDLED_WHEELS_DIR
from .registry import Registry
from .runner import CommandRunner, LauncherRunner
from .storage_service import ObjectStore

logger = logging.getLogger(__name__)

MIB = 1024 * 1024
LAUNCHER_BIN = "/opt/efp/bin/efp-app-launch"
GATEWAY_GROUP = "www-data"

REASON_IDLE = "idle"
REASON_MANUAL = "manual"
REASON_MEMORY = "memory"
REASON_DEPLOY = "deploy"
REASON_NOT_DEPLOYED = "not_deployed"
REASON_DISABLED = "disabled"
REASON_STARTUP_TIMEOUT = "startup_timeout"
REASON_START_ERROR = "start_error"
REASON_ISOLATION = "isolation_unavailable"

# An app that crashes is restarted after these delays; one more crash inside
# the window leaves it down until somebody opens it or starts it by hand.
CRASH_BACKOFF_SECONDS = (2.0, 10.0, 30.0)
CRASH_WINDOW_SECONDS = 600.0
VISIT_RETRY_SECONDS = 15.0
LAST_LOG_LINES = 30
LOCAL_DISK_LOW_BYTES = 512 * MIB

HealthProbe = Callable[[str], Awaitable["int | None"]]


class ReleaseNotFound(KeyError):
    """The sha is not a retained release of the app."""


class AppBusy(Exception):
    """The app is being deployed; the operation would pull the rug from under it."""


async def http_health(url: str) -> int | None:
    """The HTTP status of ``GET url``, or ``None`` when nothing answers."""

    import httpx

    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False, follow_redirects=False) as client:
            response = await client.get(url, headers={"User-Agent": "efp-app-host/health"})
            return response.status_code
    except httpx.HTTPError:
        return None


@dataclass
class AppProc:
    """What the manager knows about one app's process."""

    phase: str = STATE_STOPPED
    reason: str | None = None
    started_at: float = 0.0
    last_activity: float = 0.0
    restarts: int = 0
    rss_bytes: int = 0
    cpu_percent: float = 0.0
    data_bytes: int = 0
    over_quota: bool = False
    last_log_lines: list[str] = field(default_factory=list)
    crash_times: list[float] = field(default_factory=list)
    restart_at: float | None = None
    retry_after: float = 0.0
    start_task: "asyncio.Task[None] | None" = None
    # A deployment of this app is building or health-checking: processes of
    # the app's user are then not only the app.
    candidate_active: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def active(self) -> bool:
        return self.phase in (STATE_RUNNING, STATE_STARTING)


class HostRuntime:
    def __init__(
        self,
        settings: Settings,
        registry: Registry,
        *,
        portal: PortalApi | None = None,
        controller: Any = None,
        runner: CommandRunner | None = None,
        sampler: ProcessSampler | None = None,
        health: HealthProbe | None = None,
        isolation: IsolationStatus | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.settings = settings
        self.registry = registry
        self.portal = portal
        self.paths = registry.paths
        self.controller: ProcessController = controller or SupervisorController(self.paths.supervisor_socket, self.paths.supervisor_apps_dir)
        self.runner: CommandRunner = runner or LauncherRunner(self.paths.run / "launch")
        self.sampler: ProcessSampler = sampler or PsutilSampler()
        self.health: HealthProbe = health or http_health
        self.isolation = isolation or IsolationStatus(mode=settings.isolation_mode, error="the isolation has not been probed yet")
        self.clock = clock
        self.started_at = iso(utcnow())
        self.jobs = JobStore(self.paths.jobs_dir)
        self.builder = ReleaseBuilder(self)
        self.deployer = Deployer(self)
        self.launcher_bin = LAUNCHER_BIN
        # Where the image keeps the SDK wheel that deployments add by themselves.
        self.bundled_wheels_dir = BUNDLED_WHEELS_DIR
        self.manage_os_users = sys.platform == "linux" and getattr(os, "geteuid", lambda: 1)() == 0
        # Seconds between health probes / before a candidate counts as started;
        # ``None`` means the defaults. Tests shorten them.
        self.health_interval: float | None = None
        self.candidate_grace: float | None = None
        self._procs: dict[str, AppProc] = {}
        self._stores: dict[str, ObjectStore] = {}
        self._cpu = CpuMeter()
        self._tasks: set[asyncio.Task] = set()
        self._gateway_gid: int | None = None
        self._disk_low = False

    # ------------------------------------------------------------------ helpers
    def proc(self, app_id: str) -> AppProc:
        proc = self._procs.get(app_id)
        if proc is None:
            proc = self._procs[app_id] = AppProc()
        return proc

    def _spawn(self, coro: Awaitable[Any]) -> "asyncio.Task[Any]":
        task = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)

        def done(finished: "asyncio.Task[Any]") -> None:
            self._tasks.discard(finished)
            if not finished.cancelled() and finished.exception() is not None:
                logger.error("background task failed", exc_info=finished.exception())

        task.add_done_callback(done)
        return task

    def _emit(self, app_id: str, kind: str, detail: dict[str, Any] | None = None) -> None:
        """Tell the Portal; never lets a Portal problem reach the caller."""

        portal = self.portal
        if portal is None:
            return

        async def send() -> None:
            try:
                await portal.post_event(app_id, kind, detail or {})
            except Exception as exc:  # noqa: BLE001
                logger.warning("event %s for %s not delivered: %s", kind, app_id, exc)

        try:
            self._spawn(send())
        except RuntimeError:
            # No running loop (a synchronous caller in a test).
            pass

    def _redactor(self, app_id: str) -> Redactor:
        secrets = [self.settings.api_token]
        record = self.registry.find(app_id)
        if record is not None:
            secrets.append(record.visitor_key)
        try:
            secrets.extend(self.registry.read_env(app_id).values())
        except (OSError, ValueError):
            pass
        return Redactor(secrets)

    def _last_lines(self, app_id: str, count: int = LAST_LOG_LINES) -> list[str]:
        lines, _ = tail_lines(self.paths.log_file(app_id), count)
        redact = self._redactor(app_id)
        return [redact(line) for line in lines]

    def _note(self, app_id: str, message: str) -> None:
        append_note(self.paths.log_file(app_id), message)

    def gateway_gid(self) -> int:
        """The group of the gateway's worker processes (reads static sites in hidden mode)."""

        if self._gateway_gid is None:
            try:
                import grp

                self._gateway_gid = grp.getgrnam(GATEWAY_GROUP).gr_gid
            except (ImportError, KeyError):
                self._gateway_gid = 0
        return self._gateway_gid

    # ------------------------------------------------------------------ isolation
    async def detect_isolation(self) -> IsolationStatus:
        self.isolation = await asyncio.to_thread(detect, self.settings, self.paths)
        if self.isolation.ok:
            # A backend that was tried first and did not work is worth a line:
            # the host runs, but not with the isolation that was expected.
            for backend, message in self.isolation.attempts.items():
                if backend != self.isolation.backend:
                    logger.warning("isolation: %s does not work here: %s", backend, message)
            logger.info("isolation: mode %s, backend %s %s", self.isolation.mode, self.isolation.backend, self.isolation.options or "")
        else:
            logger.error("isolation: mode %s is NOT available, apps will not be started: %s", self.isolation.mode, self.isolation.error)
        return self.isolation

    # ------------------------------------------------------------------ status
    def _status(self, record: AppRecord) -> AppStatus:
        proc = self.proc(record.id)
        release = record.current
        previous = record.previous
        status = AppStatus(
            id=record.id,
            slug=record.slug,
            type=record.type,
            state=STATE_STOPPED,
            release=release.to_public() if release else None,
            previous_release=previous.to_public() if previous else None,
            restarts=proc.restarts,
            rss_bytes=proc.rss_bytes,
            cpu_percent=proc.cpu_percent,
            data_bytes=proc.data_bytes,
            last_activity_at=iso(datetime.fromtimestamp(proc.last_activity, timezone.utc)) if proc.last_activity else None,
            port=record.port,
        )
        if not record.enabled:
            status.state, status.reason = STATE_DISABLED, record.disabled_reason or REASON_DISABLED
        elif release is None:
            latest = self.jobs.latest_for(record.id)
            if latest is not None and latest.status == JOB_FAILED:
                status.state, status.reason = STATE_FAILED, REASON_DEPLOY
                if latest.error:
                    status.last_log_lines = [f"{latest.error.get('step')}: {latest.error.get('message')}"]
            else:
                status.state, status.reason = STATE_UNDEPLOYED, REASON_NOT_DEPLOYED
        elif release.type == "static":
            status.state = STATE_RUNNING
        elif not proc.active and not self.isolation.ok:
            status.state, status.reason = STATE_FAILED, REASON_ISOLATION
            status.last_log_lines = [self.isolation.error or "no isolation is available"]
        else:
            status.state, status.reason = proc.phase, proc.reason
            if proc.active and proc.started_at:
                status.uptime_seconds = max(0, int(self.clock() - proc.started_at))
            if proc.phase in (STATE_CRASHED, STATE_KILLED, STATE_FAILED):
                status.last_log_lines = list(proc.last_log_lines)
        return status

    def status(self, app_id: str) -> AppStatus:
        return self._status(self.registry.get(app_id))

    def statuses(self) -> list[AppStatus]:
        return [self._status(record) for record in self.registry.all()]

    def upstream(self, app_id: str) -> Upstream | None:
        record = self.registry.find(app_id)
        release = record.current if record else None
        if record is None or release is None:
            return None
        if release.type == "static":
            root = static_root(self.settings, self.paths, record.id, release)
            return Upstream(kind="static", strip_prefix=True, static_root=root.as_posix())
        return Upstream(kind="proxy", strip_prefix=strips_prefix(release.manifest), port=record.port)

    def touch(self, app_id: str) -> None:
        self.proc(app_id).last_activity = self.clock()

    def host_info(self) -> dict[str, Any]:
        statuses = self.statuses()
        info: dict[str, Any] = {
            "version": __version__,
            "isolation_mode": self.settings.isolation_mode,
            "sandbox_backend": self.isolation.sandbox_backend,
            "apps_total": len(statuses),
            "apps_running": sum(1 for status in statuses if status.state == STATE_RUNNING),
        }
        info.update(host_numbers(self.paths.store, self.paths.runtime))
        info["started_at"] = self.started_at
        if self.isolation.error:
            info["isolation_error"] = self.isolation.error
        return info

    # ------------------------------------------------------------------ provisioning
    def program(self, record: AppRecord) -> ProgramSpec:
        name = program_name(record.id)
        spec_file = (self.paths.run / "launch" / f"{name}.json").as_posix()
        if os.path.exists(self.launcher_bin):
            command = [self.launcher_bin, spec_file]
        else:
            command = [sys.executable, "-m", "efp_app_host.launcher", spec_file]
        return ProgramSpec(name=name, command=command, log_file=self.paths.log_file(record.id).as_posix())

    def ensure_dirs(self, record: AppRecord) -> None:
        """The app's directories, with the modes its isolation mode needs."""

        app_dir = self.paths.app_dir(record.id)
        make_dir(app_dir, 0o755)
        make_dir(app_dir / "releases", 0o755)
        make_dir(self.paths.logs_dir(record.id), 0o755)
        local = self.paths.local_app_dir(record.id)
        if self.settings.is_hidden:
            # Only the manager touches the volume; the app's files are on the
            # local disk, closed to every other app by the directory's group.
            make_dir(self.paths.data_dir(record.id), 0o700)
            make_dir(self.paths.objects_dir(record.id), 0o700)
            make_dir(local, 0o750, uid=0, gid=record.uid)
        else:
            make_dir(self.paths.data_dir(record.id), 0o700, uid=record.uid)
            make_dir(local, 0o755)
        make_dir(self.paths.local_tmp_dir(record.id), 0o700, uid=record.uid)

    def provision(self, record: AppRecord) -> None:
        """Everything an app needs before it can be deployed or started."""

        self.ensure_dirs(record)
        if self.manage_os_users:
            sync_os_users(self.registry.all())
        self.controller.add(self.program(record))

    def object_store(self, record: AppRecord) -> ObjectStore:
        """Hidden mode: the app's objects behind the storage service."""

        quota = (record.data_mb or self.settings.app_data_mb) * MIB
        store = self._stores.get(record.id)
        if store is None:
            store = self._stores[record.id] = ObjectStore(self.paths.objects_dir(record.id), quota_bytes=quota)
        store.quota_bytes = quota
        return store

    # ------------------------------------------------------------------ launch
    def run_spec(self, record: AppRecord, release: Release, *, port: int | None = None, tmp: Path | None = None) -> LaunchSpec:
        """The process of a release: command, environment, user and what it may see."""

        isolation = self.isolation
        if not isolation.ok:
            raise IsolationError(isolation.error or "no isolation is available")
        dirs = run_dirs(self.settings, self.paths, record.id, release.sha)
        command = build_command(release.manifest, python=dirs.python, port=record.port if port is None else port, base_path=record.base_path)
        env = app_environment(self.settings, self.paths, record, release, self.registry.read_env(record.id), port=port, extra=command.env)
        tmp_dir = dirs.tmp if tmp is None else tmp
        if tmp is not None:
            env["TMPDIR"] = tmp.as_posix()
            env["HOME"] = (tmp / "home").as_posix()
        sandboxed = isolation.backend != BACKEND_PLAIN
        return LaunchSpec(
            argv=command.argv,
            env=env,
            cwd=dirs.code.as_posix(),
            uid=record.uid,
            gid=record.uid,
            backend=isolation.backend or BACKEND_PLAIN,
            options=dict(isolation.options),
            ro_paths=[dirs.release.as_posix()] if sandboxed else [],
            rw_paths=[dirs.data.as_posix()] if sandboxed and dirs.data is not None else [],
            tmp_dir=tmp_dir.as_posix(),
            nproc=self.settings.app_nproc,
            label=record.slug,
        )

    def _write_launch_file(self, record: AppRecord, spec: LaunchSpec) -> None:
        import json

        directory = self.paths.run / "launch"
        make_dir(directory, 0o700)
        path = directory / f"{program_name(record.id)}.json"
        tmp = path.with_name(f".{path.name}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(spec.to_json(), handle)
        os.replace(tmp, path)

    def _reset_tmp(self, record: AppRecord) -> None:
        tmp = self.paths.local_tmp_dir(record.id)
        remove_tree(tmp)
        make_dir(tmp, 0o700, uid=record.uid)
        make_dir(tmp / "home", 0o700, uid=record.uid)

    def _begin_start(self, record: AppRecord, proc: AppProc, *, manual: bool) -> None:
        """Mark the app as starting and bring it up in the background."""

        if proc.start_task is not None and not proc.start_task.done():
            return
        now = self.clock()
        proc.phase, proc.reason = STATE_STARTING, None
        proc.started_at = now
        proc.last_activity = now
        proc.restart_at = None
        proc.last_log_lines = []
        if manual:
            proc.crash_times = []
        proc.start_task = self._spawn(self._start(record.id, proc))

    async def _start(self, app_id: str, proc: AppProc) -> None:
        name = program_name(app_id)
        try:
            record = self.registry.get(app_id)
            release = record.current
            if release is None:
                raise RuntimeError("the app has no release")
            if self.settings.is_hidden:
                await self.builder.ensure_local(
                    record,
                    release,
                    log=lambda text: self._note(app_id, text.rstrip()),
                    timeout=float(self.settings.deploy_timeout_seconds),
                )
            if not proc.candidate_active:
                await asyncio.to_thread(self.sampler.kill_uid, record.uid)
            await asyncio.to_thread(self._reset_tmp, record)
            spec = self.run_spec(record, release)
            await asyncio.to_thread(self._write_launch_file, record, spec)
            await asyncio.to_thread(self.controller.start, name)
            timeout = int(release.manifest.get("startup_timeout") or 60)
            proc.started_at = self.clock()
            deadline = time.monotonic() + timeout
            url = health_url(record, release)
            while True:
                info = await asyncio.to_thread(self.controller.info, name)
                if not info.alive:
                    self._on_exit(record, proc, info)
                    return
                code = await self.health(url)
                if code is not None and code < 500:
                    break
                if time.monotonic() >= deadline:
                    await asyncio.to_thread(self.controller.stop, name)
                    proc.phase, proc.reason = STATE_FAILED, REASON_STARTUP_TIMEOUT
                    proc.retry_after = self.clock() + VISIT_RETRY_SECONDS
                    self._note(app_id, f"stopped: not healthy within {timeout} seconds")
                    proc.last_log_lines = self._last_lines(app_id)
                    return
                await asyncio.sleep(0.5 if self.health_interval is None else self.health_interval)
            proc.phase, proc.reason = STATE_RUNNING, None
            proc.last_activity = self.clock()
            self._emit(app_id, "started", {"sha": release.sha})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - whatever it is, the app did not start
            logger.exception("starting %s failed", app_id)
            proc.phase, proc.reason = STATE_FAILED, REASON_START_ERROR
            proc.retry_after = self.clock() + VISIT_RETRY_SECONDS
            message = f"not started: {exc}"
            self._note(app_id, message)
            proc.last_log_lines = [message]

    def _on_exit(self, record: AppRecord, proc: AppProc, info: ProcessInfo) -> None:
        """The process went away by itself."""

        now = self.clock()
        code = info.exit_status
        proc.phase = STATE_CRASHED
        proc.reason = f"exit:{code}" if code is not None else "exit:unknown"
        proc.rss_bytes, proc.cpu_percent = 0, 0.0
        if info.detail and info.state != "exited":
            self._note(record.id, f"the process could not be started: {info.detail}")
        proc.last_log_lines = self._last_lines(record.id)
        proc.crash_times = [moment for moment in proc.crash_times if now - moment < CRASH_WINDOW_SECONDS] + [now]
        crashes = len(proc.crash_times)
        proc.restart_at = now + CRASH_BACKOFF_SECONDS[crashes - 1] if crashes <= len(CRASH_BACKOFF_SECONDS) else None
        proc.retry_after = now + VISIT_RETRY_SECONDS
        if proc.restart_at is None:
            self._note(record.id, f"crashed {crashes} times in a row ({proc.reason}); it stays down until it is opened or started again")
        logger.warning("app %s (%s) crashed: %s", record.slug, record.id, proc.reason)
        self._emit(record.id, "crashed", {"exit_code": code, "log": proc.last_log_lines})

    def _startable(self, record: AppRecord) -> bool:
        release = record.current
        return bool(record.enabled and release is not None and release.type != "static" and self.isolation.ok)

    async def ensure_running(self, app_id: str) -> AppStatus:
        record = self.registry.get(app_id)
        proc = self.proc(app_id)
        if self._startable(record) and not proc.active:
            waiting = proc.phase in (STATE_CRASHED, STATE_FAILED) and self.clock() < proc.retry_after
            if not waiting:
                self._begin_start(record, proc, manual=False)
        return self._status(record)

    async def start(self, app_id: str) -> AppStatus:
        """``POST /v1/apps/{id}/start``: start now, whatever happened before."""

        record = self.registry.get(app_id)
        proc = self.proc(app_id)
        if self._startable(record) and not proc.active:
            self._begin_start(record, proc, manual=True)
        return self._status(record)

    async def _stop_locked(self, record: AppRecord, proc: AppProc, reason: str) -> None:
        task = proc.start_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        proc.start_task = None
        was_active = proc.active
        await asyncio.to_thread(self.controller.stop, program_name(record.id))
        if not proc.candidate_active:
            await asyncio.to_thread(self.sampler.kill_uid, record.uid)
        proc.phase = STATE_KILLED if reason == REASON_MEMORY else STATE_STOPPED
        proc.reason = reason
        proc.restart_at = None
        proc.rss_bytes, proc.cpu_percent = 0, 0.0
        self._cpu.forget(record.uid)
        if was_active:
            self._note(record.id, f"stopped ({reason})")

    async def stop(self, app_id: str, reason: str) -> AppStatus:
        record = self.registry.get(app_id)
        proc = self.proc(app_id)
        async with proc.lock:
            await self._stop_locked(record, proc, reason)
        return self._status(record)

    async def restart(self, app_id: str) -> AppStatus:
        record = self.registry.get(app_id)
        proc = self.proc(app_id)
        async with proc.lock:
            await self._stop_locked(record, proc, REASON_MANUAL)
            if self._startable(record):
                self._begin_start(record, proc, manual=True)
        return self._status(record)

    # ------------------------------------------------------------------ admin operations
    async def register(self, app_id: str, *, slug: str, env: dict[str, str], **fields: Any) -> AppStatus:
        """``PUT /v1/apps/{id}``. Raises ``SlugTaken``."""

        record, _created = self.registry.upsert(app_id, slug=slug, **fields)
        await asyncio.to_thread(self.provision, record)
        changed = self.registry.write_env(app_id, env)
        proc = self.proc(app_id)
        if not record.enabled:
            if proc.active or proc.phase in (STATE_CRASHED,):
                await self.stop(app_id, REASON_DISABLED)
        elif changed and proc.active:
            await self.restart(app_id)
        return self._status(record)

    async def set_env(self, app_id: str, env: dict[str, str]) -> AppStatus:
        record = self.registry.get(app_id)
        changed = self.registry.write_env(app_id, env)
        if changed and self.proc(app_id).active:
            await self.restart(app_id)
        return self._status(record)

    def deploy(self, app_id: str, *, sha: str, bundle: Path, ref: str = "", entry_dir: str = "", requested_by: str = "") -> Job:
        """``POST /v1/apps/{id}/releases``. Raises ``AppNotFound`` / ``DeployInProgress``."""

        return self.deployer.submit(app_id, sha=sha, bundle=bundle, ref=ref, entry_dir=entry_dir, requested_by=requested_by)

    async def switch_release(self, app_id: str, release: Release) -> None:
        """Make a freshly built release current and run it (the last step of a deployment)."""

        record = self.registry.get(app_id)
        proc = self.proc(app_id)
        async with proc.lock:
            dropped = self.registry.add_release(app_id, release)

            def tidy() -> None:
                for sha in dropped:
                    self.builder.remove_release(app_id, sha)
                self.builder.publish_static(record, release)

            await asyncio.to_thread(tidy)
            await self._after_switch(record, proc)

    async def activate(self, app_id: str, sha: str) -> AppStatus:
        """``POST /v1/apps/{id}/activate``: switch to a retained release."""

        record = self.registry.get(app_id)
        if self.deployer.busy(app_id):
            raise AppBusy(app_id)
        proc = self.proc(app_id)
        async with proc.lock:
            if sha not in record.releases:
                raise ReleaseNotFound(sha)
            self.registry.activate(app_id, sha)
            release = record.releases[sha]
            await asyncio.to_thread(self.builder.publish_static, record, release)
            self._note(app_id, f"switched to release {sha[:12]}")
            await self._after_switch(record, proc)
        return self._status(record)

    async def _after_switch(self, record: AppRecord, proc: AppProc) -> None:
        await self._stop_locked(record, proc, REASON_DEPLOY)
        proc.restarts = 0
        proc.crash_times = []
        proc.last_log_lines = []
        if self._startable(record):
            self._begin_start(record, proc, manual=True)

    async def clear_data(self, app_id: str) -> AppStatus:
        """``DELETE /v1/apps/{id}/data``: empty the app's data; a running app is restarted."""

        record = self.registry.get(app_id)
        proc = self.proc(app_id)
        async with proc.lock:
            was_active = proc.active
            if was_active:
                await self._stop_locked(record, proc, REASON_MANUAL)

            def empty() -> None:
                data = self.paths.data_dir(app_id)
                remove_tree(data)
                self.ensure_dirs(record)

            await asyncio.to_thread(empty)
            store = self._stores.get(app_id)
            if store is not None:
                store.set_used(0)
            proc.data_bytes, proc.over_quota = 0, False
            self._note(app_id, "the app's data was emptied")
            if was_active and self._startable(record):
                self._begin_start(record, proc, manual=True)
        return self._status(record)

    async def delete(self, app_id: str, *, purge_data: bool) -> None:
        """``DELETE /v1/apps/{id}``: stop and remove; data survives unless purged.

        Works for an app that is no longer registered too, so that data kept
        at removal can be purged later.
        """

        record = self.registry.find(app_id)
        if record is not None:
            if self.deployer.busy(app_id):
                raise AppBusy(app_id)
            proc = self.proc(app_id)
            async with proc.lock:
                await self._stop_locked(record, proc, REASON_MANUAL)
                await asyncio.to_thread(self.controller.remove, program_name(app_id))
                self.registry.remove(app_id)
            self._procs.pop(app_id, None)
            self._stores.pop(app_id, None)
            self.jobs.forget_app(app_id)
            if self.manage_os_users:
                await asyncio.to_thread(sync_os_users, self.registry.all())

        def wipe() -> None:
            app_dir = self.paths.app_dir(app_id)
            remove_tree(app_dir / "releases")
            remove_tree(self.paths.logs_dir(app_id))
            for path in (self.paths.env_file(app_id), self.paths.run / "launch" / f"{program_name(app_id)}.json"):
                try:
                    path.unlink()
                except OSError:
                    pass
            remove_tree(self.paths.local_app_dir(app_id))
            remove_tree(self.builder.static_dir(app_id, "x").parent)
            if purge_data:
                remove_tree(app_dir)
            else:
                try:
                    if app_dir.is_dir() and not any(self.paths.data_dir(app_id).iterdir()):
                        remove_tree(app_dir)
                except OSError:
                    pass

        await asyncio.to_thread(wipe)

    # ------------------------------------------------------------------ logs
    def log_tail(self, app_id: str, count: int) -> tuple[list[str], bool]:
        self.registry.get(app_id)
        lines, truncated = tail_lines(self.paths.log_file(app_id), count)
        redact = self._redactor(app_id)
        return [redact(line) for line in lines], truncated

    async def log_follow(self, app_id: str, *, tail: int = 0, stop: Callable[[], bool] | None = None) -> AsyncIterator[str | None]:
        self.registry.get(app_id)
        redact = self._redactor(app_id)
        async for line in follow(self.paths.log_file(app_id), tail=tail, stop=stop):
            yield redact(line) if line is not None else None

    # ------------------------------------------------------------------ boot and loops
    def adopt(self) -> int:
        """After a restart of the manager alone: apps supervisord still runs are running."""

        adopted = 0
        try:
            infos = self.controller.all()
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not ask the supervisor what runs: %s", exc)
            return 0
        now = self.clock()
        for record in self.registry.all():
            info = infos.get(program_name(record.id))
            if info is not None and info.alive:
                proc = self.proc(record.id)
                proc.phase, proc.reason = STATE_RUNNING, None
                proc.started_at = info.started_at or now
                proc.last_activity = now
                adopted += 1
        return adopted

    async def monitor_once(self) -> None:
        """Notice processes that went away and restart crashed apps when their back-off is over."""

        infos = await asyncio.to_thread(self.controller.all)
        now = self.clock()
        for record in self.registry.all():
            proc = self._procs.get(record.id)
            if proc is None or proc.lock.locked():
                continue
            if proc.phase == STATE_RUNNING:
                info = infos.get(program_name(record.id)) or ProcessInfo(name=program_name(record.id), state=PROC_ABSENT)
                if not info.alive:
                    self._on_exit(record, proc, info)
            elif proc.phase == STATE_CRASHED and proc.restart_at is not None and now >= proc.restart_at:
                if self._startable(record):
                    proc.restarts += 1
                    self._note(record.id, f"restarting after a crash (restart {proc.restarts})")
                    self._begin_start(record, proc, manual=False)
                else:
                    proc.restart_at = None

    async def watchdog_once(self) -> None:
        """Memory and CPU of every app; an app over its memory ceiling is stopped."""

        usage = await asyncio.to_thread(self.sampler.sample)
        for record in self.registry.all():
            proc = self._procs.get(record.id)
            if proc is None:
                continue
            used = usage.get(record.uid)
            if used is None:
                proc.rss_bytes, proc.cpu_percent = 0, 0.0
                continue
            proc.rss_bytes = used.rss_bytes
            proc.cpu_percent = self._cpu.percent(record.uid, used.cpu_seconds)
            limit = (record.memory_mb or self.settings.app_memory_mb) * MIB
            if used.rss_bytes > limit and proc.active and not proc.candidate_active:
                await self._kill_for_memory(record, proc, used.rss_bytes, limit)

    async def _kill_for_memory(self, record: AppRecord, proc: AppProc, rss: int, limit: int) -> None:
        async with proc.lock:
            if not proc.active:
                return
            await asyncio.to_thread(self.sampler.kill_uid, record.uid)
            self._note(record.id, f"killed: {rss // MIB} MiB of memory in use, the limit is {limit // MIB} MiB")
            await self._stop_locked(record, proc, REASON_MEMORY)
            proc.last_log_lines = self._last_lines(record.id)
        logger.warning("app %s (%s) killed: %s MiB over the limit of %s MiB", record.slug, record.id, rss // MIB, limit // MIB)
        self._emit(record.id, "killed", {"reason": REASON_MEMORY, "rss_bytes": rss, "limit_bytes": limit, "log": proc.last_log_lines})

    async def scan_data_once(self) -> None:
        """Size of every app's data; crossing the soft quota is reported once."""

        for record in self.registry.all():
            size = await asyncio.to_thread(dir_size, self.paths.data_dir(record.id))
            proc = self.proc(record.id)
            proc.data_bytes = size
            store = self._stores.get(record.id)
            if store is not None:
                store.set_used(await asyncio.to_thread(dir_size, self.paths.objects_dir(record.id)))
            limit = (record.data_mb or self.settings.app_data_mb) * MIB
            if size > limit and not proc.over_quota:
                proc.over_quota = True
                self._note(record.id, f"the app's data uses {size // MIB} MiB, its quota is {limit // MIB} MiB")
                self._emit(record.id, "quota_exceeded", {"data_bytes": size, "limit_bytes": limit})
            elif size <= limit:
                proc.over_quota = False
        free = await asyncio.to_thread(free_bytes, self.paths.runtime)
        low = free is not None and free < LOCAL_DISK_LOW_BYTES
        if low and not self._disk_low:
            logger.warning("the local disk is almost full: %s MiB free under %s", (free or 0) // MIB, self.paths.runtime)
        self._disk_low = low

    async def idle_once(self) -> None:
        """Stop apps nobody used for ``idle_stop_minutes``."""

        minutes = self.settings.idle_stop_minutes
        if minutes <= 0:
            return
        now = self.clock()
        idle_apps: list[tuple[AppRecord, AppProc, float]] = []
        for record in self.registry.all():
            proc = self._procs.get(record.id)
            if proc is None or proc.phase != STATE_RUNNING or proc.candidate_active or proc.lock.locked():
                continue
            idle = now - max(proc.last_activity, proc.started_at)
            if idle >= minutes * 60:
                idle_apps.append((record, proc, idle))
        if not idle_apps:
            return
        # The gateway notes activity once per request, when the request
        # arrives. A WebSocket or a long download is one request that goes on
        # for hours: an app with an open connection on its port is in use.
        busy_ports = getattr(self.sampler, "busy_ports", None)
        try:
            busy = await asyncio.to_thread(busy_ports) if busy_ports is not None else set()
        except Exception:  # noqa: BLE001 - not knowing must not stop the loop
            logger.exception("looking for open connections")
            busy = set()
        for record, proc, idle in idle_apps:
            if record.port in busy:
                proc.last_activity = now
                continue
            await self.stop(record.id, REASON_IDLE)
            self._emit(record.id, "idle_stopped", {"idle_seconds": int(idle)})

    async def shutdown(self) -> None:
        await self.deployer.shutdown()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

