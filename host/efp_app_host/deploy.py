"""Deployments (docs/CONTRACTS.md, section 4.2).

A deployment builds a release next to the running one and only switches when
the new release has answered its health check:

    unpack -> manifest -> environment -> dependencies -> start -> health -> activate

- Nothing the running release uses is touched before ``activate``; a failed
  deployment removes what it built and leaves the app as it was.
- Everything that executes (the virtual environment, pip, the candidate
  process of the health check) runs through the launcher, as the app's own
  user and inside the app's isolation.
- One deployment per app at a time; ``install_concurrency`` deployments run
  at once across apps, the others wait as ``queued``.
- The whole job has ``deploy_timeout_seconds``.

Where a release lives depends on the isolation mode:

``sandbox``  code and virtual environment on the volume
             (``apps/<id>/releases/<sha>/{code,venv}``).
``hidden``   code and a wheel cache on the volume
             (``.../{code,wheels}``); what runs is a copy on the container's
             own disk, rebuilt from those two whenever it is missing
             (``ReleaseBuilder.ensure_local``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from . import pyenv
from .bundle import BundleError, unpack_bundle
from .isolation import BACKEND_PLAIN, IsolationError, LaunchSpec
from .jobs import JOB_RUNNING, STEP_FAILED, STEP_NAMES, STEP_SKIPPED, STEP_SUCCEEDED, Job, Redactor
from .launch import STATIC_AREA, health_url, run_dirs
from .manifest import ManifestError, load_manifest
from .models import AppRecord, Release, iso, utcnow
from .procstats import free_bytes
from .registry import AppNotFound, read_json, write_json_atomic
from .runner import CommandTimeout, LogSink, Spawned

if TYPE_CHECKING:
    from .runtime import HostRuntime

logger = logging.getLogger(__name__)

READY_MARKER = ".efp-ready"
MIN_FREE_BYTES = 256 * 1024 * 1024
CANDIDATE_GRACE_SECONDS = 1.0
HEALTH_POLL_SECONDS = 0.5


class DeployError(Exception):
    """A step failed; the message is what the owner reads."""


class DeployInProgress(Exception):
    """Another deployment of the same app is queued or running."""


def _chown(path: Path, uid: int, gid: int) -> None:
    try:
        os.chown(path, uid, gid)  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        # EFS behind an access point owns every file itself; Windows has no chown.
        pass


def _chmod(path: Path, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def make_dir(path: Path, mode: int, *, uid: int | None = None, gid: int | None = None) -> None:
    """Create ``path`` with ``mode`` (and owner). Directories that had to be
    created above it are open for passing through, whatever the umask: what
    must be closed is closed where it is named."""

    missing = []
    current = path
    while not current.exists() and current != current.parent:
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            continue
        if directory != path:
            _chmod(directory, 0o755)
    _chmod(path, mode)
    if uid is not None:
        _chown(path, uid, uid if gid is None else gid)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def free_port() -> int:
    """A loopback port nothing listens on right now."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ReleaseBuilder:
    """Builds what a release runs from; shared by deployments and by hidden mode's restore."""

    def __init__(self, runtime: "HostRuntime"):
        self.runtime = runtime
        self.settings = runtime.settings
        self.paths = runtime.paths

    # ---- where things are -------------------------------------------------
    def ready(self, app_id: str, sha: str) -> bool:
        release_dir = self.paths.release_dir(app_id, sha)
        return (release_dir / READY_MARKER).is_file() and self.paths.manifest_file(app_id, sha).is_file()

    def local_ready(self, app_id: str, sha: str) -> bool:
        return (self.paths.local_release_dir(app_id, sha) / READY_MARKER).is_file()

    def build_dir(self, app_id: str, token: str) -> Path:
        return self.paths.local_app_dir(app_id) / "build" / token

    def static_dir(self, app_id: str, sha: str) -> Path:
        return self.paths.runtime / STATIC_AREA / app_id / sha

    def remove_release(self, app_id: str, sha: str) -> None:
        remove_tree(self.paths.release_dir(app_id, sha))
        remove_tree(self.paths.local_release_dir(app_id, sha))
        remove_tree(self.static_dir(app_id, sha))

    # ---- commands ---------------------------------------------------------
    def _spec(self, record: AppRecord, argv: list[str], *, cwd: Path, tmp: Path, readable: list[Path], writable: list[Path], venv_bin: Path | None) -> LaunchSpec:
        isolation = self.runtime.isolation
        if not isolation.ok:
            raise IsolationError(isolation.error or "no isolation is available")
        env = pyenv.pip_environment(
            self.settings.passthrough_env,
            home=(tmp / "home").as_posix(),
            tmp=tmp.as_posix(),
            venv_bin=venv_bin.as_posix() if venv_bin else None,
        )
        sandboxed = isolation.backend != BACKEND_PLAIN
        extra = [path for path in pyenv.pip_readable_files(self.settings.passthrough_env) if os.path.exists(path)]
        return LaunchSpec(
            argv=argv,
            env=env,
            cwd=cwd.as_posix(),
            uid=record.uid,
            gid=record.uid,
            backend=isolation.backend or BACKEND_PLAIN,
            options=dict(isolation.options),
            ro_paths=[path.as_posix() for path in readable] + extra if sandboxed else [],
            rw_paths=[path.as_posix() for path in writable] if sandboxed else [],
            tmp_dir=tmp.as_posix(),
            nproc=self.settings.app_nproc,
            label=f"build:{record.slug}",
        )

    async def _run(self, spec: LaunchSpec, what: str, log: LogSink, timeout: float, *, capture: bool = False) -> str:
        log(f"$ {' '.join(spec.argv)}\n")
        try:
            result = await self.runtime.runner.run(spec, log=log, timeout=timeout, capture=capture)
        except CommandTimeout:
            raise DeployError(f"{what} did not finish in time") from None
        if result.code != 0:
            if capture and result.output:
                log(result.output)
            raise DeployError(f"{what} failed (exit code {result.code}); the log above says why")
        return result.output

    def _prepare_tmp(self, record: AppRecord, tmp: Path) -> None:
        remove_tree(tmp)
        make_dir(tmp.parent, 0o755)
        make_dir(tmp, 0o700, uid=record.uid)
        make_dir(tmp / "home", 0o700, uid=record.uid)

    async def create_venv(self, record: AppRecord, sha: str, *, tmp: Path, log: LogSink, timeout: float) -> None:
        dirs = run_dirs(self.settings, self.paths, record.id, sha)

        def prepare() -> None:
            self._prepare_tmp(record, tmp)
            remove_tree(dirs.venv)
            make_dir(dirs.venv, 0o755, uid=record.uid)

        await asyncio.to_thread(prepare)
        spec = self._spec(record, pyenv.venv_command(dirs.venv), cwd=dirs.code, tmp=tmp, readable=[dirs.release], writable=[dirs.venv], venv_bin=None)
        await self._run(spec, "creating the virtual environment", log, timeout)

    async def install(self, record: AppRecord, sha: str, manifest: dict[str, Any], *, tmp: Path, log: LogSink, timeout: Callable[[], float]) -> None:
        """Install the release's dependencies and write its lock file."""

        dirs = run_dirs(self.settings, self.paths, record.id, sha)
        plan = await asyncio.to_thread(pyenv.plan_install, dirs.code, manifest)
        for note in plan.notes:
            log(note + "\n")
        allow_sdist = self.settings.pip_allow_sdist
        venv_bin = dirs.venv / "bin"
        if plan.empty:
            log("nothing to install\n")
        elif self.settings.is_hidden:
            wheels = tmp.parent / "wheels"
            await asyncio.to_thread(lambda: (remove_tree(wheels), make_dir(wheels, 0o700, uid=record.uid)))
            spec = self._spec(record, pyenv.wheel_command(dirs.python, plan, wheels, allow_sdist=allow_sdist), cwd=dirs.code, tmp=tmp, readable=[], writable=[], venv_bin=venv_bin)
            await self._run(spec, "collecting the dependencies", log, timeout())
            await self._install_wheels(record, sha, wheels, tmp=tmp, log=log, timeout=timeout())
            await asyncio.to_thread(self._store_wheels, record.id, sha, wheels)
        else:
            spec = self._spec(
                record,
                pyenv.install_command(dirs.python, plan, allow_sdist=allow_sdist),
                cwd=dirs.code,
                tmp=tmp,
                readable=[dirs.release],
                writable=[dirs.venv],
                venv_bin=venv_bin,
            )
            await self._run(spec, "installing the dependencies", log, timeout())
        spec = self._spec(record, pyenv.freeze_command(dirs.python), cwd=dirs.code, tmp=tmp, readable=[dirs.release], writable=[], venv_bin=venv_bin)
        lock = await self._run(spec, "recording the installed versions", log, timeout(), capture=True)

        def finish() -> None:
            lock_file = self.paths.lock_file(record.id, sha)
            with open(lock_file, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(lock)
            _chmod(lock_file, 0o644)

        await asyncio.to_thread(finish)
        log(f"{len([line for line in lock.splitlines() if line.strip()])} packages installed\n")

    async def _install_wheels(self, record: AppRecord, sha: str, wheels_dir: Path, *, tmp: Path, log: LogSink, timeout: float) -> None:
        dirs = run_dirs(self.settings, self.paths, record.id, sha)
        wheels = await asyncio.to_thread(pyenv.cached_wheels, wheels_dir)
        if not wheels:
            return
        spec = self._spec(record, pyenv.offline_install_command(dirs.python, wheels), cwd=dirs.code, tmp=tmp, readable=[], writable=[], venv_bin=dirs.venv / "bin")
        await self._run(spec, "installing the dependencies", log, timeout)

    def _store_wheels(self, app_id: str, sha: str, wheels: Path) -> None:
        target = self.paths.wheels_dir(app_id, sha)
        remove_tree(target)
        make_dir(target, 0o700)
        for wheel in pyenv.cached_wheels(wheels):
            shutil.copyfile(wheel, target / wheel.name)
        remove_tree(wheels)

    # ---- hidden mode: the copy on the container's own disk ------------------
    def copy_code_local(self, record: AppRecord, sha: str) -> None:
        """Put the release's code on the local disk, readable by the app's group only."""

        local = self.paths.local_release_dir(record.id, sha)
        remove_tree(local)
        make_dir(self.paths.local_app_dir(record.id), 0o750, uid=0, gid=record.uid)
        make_dir(local.parent, 0o755)
        make_dir(local, 0o755)
        shutil.copytree(self.paths.code_dir(record.id, sha), local / "code", symlinks=True)

    def mark_local_ready(self, app_id: str, sha: str) -> None:
        marker = self.paths.local_release_dir(app_id, sha) / READY_MARKER
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(iso(utcnow()) or "")

    async def ensure_local(self, record: AppRecord, release: Release, *, log: LogSink, timeout: float) -> bool:
        """Hidden mode: rebuild the local copy of a release when it is missing.

        Works without the network: the code and every wheel come from the
        volume. Returns whether anything had to be rebuilt.
        """

        if not self.settings.is_hidden or release.type == "static":
            return False
        if await asyncio.to_thread(self.local_ready, record.id, release.sha):
            return False
        deadline = time.monotonic() + timeout
        log(f"restoring release {release.sha[:12]} to the local disk\n")
        token = f"restore-{release.sha[:12]}"
        tmp = self.build_dir(record.id, token) / "tmp"
        try:
            await asyncio.to_thread(self.copy_code_local, record, release.sha)
            await self.create_venv(record, release.sha, tmp=tmp, log=log, timeout=max(1.0, deadline - time.monotonic()))
            wheels = tmp.parent / "wheels"

            def fetch_wheels() -> None:
                remove_tree(wheels)
                make_dir(wheels, 0o700, uid=record.uid)
                for wheel in pyenv.cached_wheels(self.paths.wheels_dir(record.id, release.sha)):
                    shutil.copyfile(wheel, wheels / wheel.name)
                    _chown(wheels / wheel.name, record.uid, record.uid)

            await asyncio.to_thread(fetch_wheels)
            await self._install_wheels(record, release.sha, wheels, tmp=tmp, log=log, timeout=max(1.0, deadline - time.monotonic()))
            await asyncio.to_thread(self.mark_local_ready, record.id, release.sha)
        except BaseException:
            await asyncio.to_thread(remove_tree, self.paths.local_release_dir(record.id, release.sha))
            raise
        finally:
            await asyncio.to_thread(remove_tree, self.build_dir(record.id, token))
        log("release restored\n")
        return True

    def publish_static(self, record: AppRecord, release: Release) -> None:
        """Hidden mode: put a static release where the gateway can read it."""

        if not self.settings.is_hidden or release.type != "static":
            return
        target = self.static_dir(record.id, release.sha)
        if (target / READY_MARKER).is_file():
            return
        area = self.paths.runtime / STATIC_AREA
        make_dir(area, 0o750, uid=0, gid=self.runtime.gateway_gid())
        remove_tree(target)
        make_dir(target.parent, 0o755)
        shutil.copytree(self.paths.code_dir(record.id, release.sha), target, symlinks=True)
        with open(target / READY_MARKER, "w", encoding="utf-8") as handle:
            handle.write(iso(utcnow()) or "")


@dataclass
class _Context:
    job: Job
    record: AppRecord
    bundle: Path
    deadline: float
    log: LogSink
    reuse: bool = False
    manifest: dict[str, Any] = field(default_factory=dict)
    entry_dir: str = ""
    candidate: Spawned | None = None
    candidate_port: int = 0
    built: bool = False

    @property
    def static(self) -> bool:
        return self.manifest.get("type") == "static"

    def remaining(self) -> float:
        return max(1.0, self.deadline - time.monotonic())


class Deployer:
    def __init__(self, runtime: "HostRuntime"):
        self.runtime = runtime
        self.settings = runtime.settings
        self.paths = runtime.paths
        self.registry = runtime.registry
        self.jobs = runtime.jobs
        self.builder = runtime.builder
        self._slots: asyncio.Semaphore | None = None
        self._tasks: dict[str, asyncio.Task] = {}

    # ---- entry points -----------------------------------------------------
    def submit(self, app_id: str, *, sha: str, bundle: Path, ref: str = "", entry_dir: str = "", requested_by: str = "") -> Job:
        """Queue a deployment; ``bundle`` is moved into the job's keeping."""

        if self.registry.find(app_id) is None:
            raise AppNotFound(app_id)
        if self.jobs.active_for(app_id) is not None:
            raise DeployInProgress(app_id)
        job = self.jobs.create(app_id, sha, ref=ref, entry_dir=entry_dir, requested_by=requested_by)
        target = self.jobs.bundle_path(job.id)
        shutil.move(str(bundle), str(target))
        task = asyncio.get_running_loop().create_task(self._run(job, target))
        self._tasks[job.id] = task
        task.add_done_callback(lambda _task: self._tasks.pop(job.id, None))
        return job

    def busy(self, app_id: str) -> bool:
        return self.jobs.active_for(app_id) is not None

    async def wait(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---- the job ----------------------------------------------------------
    def _redactor(self, app_id: str) -> Redactor:
        secrets = [self.settings.api_token]
        record = self.registry.find(app_id)
        if record is not None:
            secrets.append(record.visitor_key)
        try:
            stored = self.registry.read_env(app_id)
        except (OSError, ValueError):
            stored = {}
        secrets.extend(stored.values())
        for value in self.settings.passthrough_env.values():
            # user:password@ inside an index or proxy URL
            if "://" in value and "@" in value:
                credentials = value.split("://", 1)[1].rsplit("@", 1)[0]
                secrets.append(credentials.split(":", 1)[1] if ":" in credentials else credentials)
        return Redactor(secrets)

    async def _run(self, job: Job, bundle: Path) -> None:
        if self._slots is None:
            self._slots = asyncio.Semaphore(max(1, self.settings.install_concurrency))
        redact = self._redactor(job.app_id)

        def log(text: str) -> None:
            self.jobs.append_log(job.id, redact(text))

        record = self.registry.find(job.app_id)
        proc = self.runtime.proc(job.app_id)
        context = _Context(job=job, record=record, bundle=bundle, deadline=0.0, log=log)  # type: ignore[arg-type]
        error: dict[str, str] | None = None
        try:
            if self._slots.locked():
                log("waiting for another deployment to finish\n")
            async with self._slots:
                job.status = JOB_RUNNING
                self.jobs.save(job)
                context.deadline = time.monotonic() + self.settings.deploy_timeout_seconds
                log(f"deploying {job.sha} ({job.ref or 'no ref'}) requested by {job.requested_by or 'unknown'}\n")
                proc.candidate_active = True
                error = await self._pipeline(context)
        except asyncio.CancelledError:
            error = {"step": job.current_step() or STEP_NAMES[0], "message": "the host is shutting down"}
            raise
        except Exception as exc:  # noqa: BLE001 - a job must always end
            logger.exception("deployment %s of %s broke", job.id, job.app_id)
            error = {"step": job.current_step() or STEP_NAMES[0], "message": f"internal error: {exc.__class__.__name__}"}
        finally:
            proc.candidate_active = False
            await asyncio.shield(self._cleanup(context, failed=error is not None))
            if error:
                log(f"FAILED at {error['step']}: {error['message']}\n")
            else:
                log("deployment finished\n")
            self.jobs.finish(job, error=error)
            self.jobs.prune()

    async def _pipeline(self, context: _Context) -> dict[str, str] | None:
        job = context.job
        steps: list[tuple[str, Callable[[_Context], Awaitable[str]]]] = [
            ("unpack", self._unpack),
            ("manifest", self._manifest),
            ("environment", self._environment),
            ("dependencies", self._dependencies),
            ("start", self._start),
            ("health", self._health),
            ("activate", self._activate),
        ]
        for name, action in steps:
            self.jobs.begin_step(job, name)
            context.log(f"--- {name} ---\n")
            message: str | None = None
            try:
                if context.deadline - time.monotonic() <= 0:
                    raise asyncio.TimeoutError
                outcome = await asyncio.wait_for(action(context), timeout=context.remaining())
            except asyncio.TimeoutError:
                message = f"the deployment took longer than {self.settings.deploy_timeout_seconds} seconds"
            except (DeployError, BundleError, ManifestError, IsolationError) as exc:
                message = str(exc)
            if message is not None:
                self.jobs.end_step(job, name, STEP_FAILED)
                return {"step": name, "message": message}
            self.jobs.end_step(job, name, outcome)
            if outcome == STEP_SKIPPED:
                context.log("skipped\n")
        return None

    async def _cleanup(self, context: _Context, *, failed: bool) -> None:
        record = context.record
        if context.candidate is not None:
            try:
                await context.candidate.stop()
            except Exception:  # noqa: BLE001
                logger.exception("stopping the candidate of %s", context.job.id)
            context.candidate = None

        def clean() -> None:
            if record is None:
                return
            remove_tree(self.builder.build_dir(record.id, context.job.id))
            if failed and context.built and not context.reuse:
                current = self.registry.find(record.id)
                if current is None or context.job.sha not in current.releases:
                    self.builder.remove_release(record.id, context.job.sha)

        await asyncio.to_thread(clean)

    # ---- steps ------------------------------------------------------------
    async def _unpack(self, context: _Context) -> str:
        record, sha = context.record, context.job.sha
        if record is None:
            raise DeployError("the app is not registered on this host")
        retained = sha in (record.current_sha, record.previous_sha) and sha in record.releases
        ready = await asyncio.to_thread(self.builder.ready, record.id, sha)
        if retained and ready:
            context.reuse = True
            context.entry_dir = record.releases[sha].entry_dir
            context.log(f"release {sha[:12]} is already on this host: reusing it\n")
            return STEP_SKIPPED
        if sha == record.current_sha:
            raise DeployError("the files of the running release are damaged and cannot be rebuilt in place; deploy another commit")
        for label, path in (("volume", self.paths.store), ("local disk", self.paths.runtime)):
            free = await asyncio.to_thread(free_bytes, path)
            if free is not None and free < MIN_FREE_BYTES:
                raise DeployError(f"the {label} of the app host is almost full ({free // (1024 * 1024)} MiB free); ask an administrator")

        def unpack():
            self.builder.remove_release(record.id, sha)
            release_dir = self.paths.release_dir(record.id, sha)
            make_dir(self.paths.app_dir(record.id) / "releases", 0o755)
            make_dir(release_dir, 0o755)
            return unpack_bundle(context.bundle, self.paths.code_dir(record.id, sha), entry_dir=context.job.entry_dir)

        context.built = True
        unpacked = await asyncio.to_thread(unpack)
        context.entry_dir = unpacked.entry_dir
        where = f"'{unpacked.entry_dir}'" if unpacked.entry_dir else "the root of the bundle"
        context.log(f"unpacked {unpacked.files} files ({unpacked.bytes} bytes) from {where}\n")
        return STEP_SUCCEEDED

    async def _manifest(self, context: _Context) -> str:
        record, sha = context.record, context.job.sha
        if context.reuse:
            context.manifest = await asyncio.to_thread(read_json, self.paths.manifest_file(record.id, sha), {})
            if not context.manifest.get("type"):
                raise DeployError("the retained release has no readable manifest")
            return STEP_SKIPPED

        def load() -> dict[str, Any]:
            manifest = load_manifest(self.paths.code_dir(record.id, sha))
            write_json_atomic(self.paths.manifest_file(record.id, sha), manifest, mode=0o644)
            return manifest

        context.manifest = await asyncio.to_thread(load)
        context.log(f"{context.manifest['name']}: type {context.manifest['type']}\n")
        return STEP_SUCCEEDED

    async def _environment(self, context: _Context) -> str:
        record, sha = context.record, context.job.sha
        if context.static or context.reuse:
            return STEP_SKIPPED
        if self.settings.is_hidden:
            await asyncio.to_thread(self.builder.copy_code_local, record, sha)
        tmp = self.builder.build_dir(record.id, context.job.id) / "tmp"
        await self.builder.create_venv(record, sha, tmp=tmp, log=context.log, timeout=context.remaining())
        return STEP_SUCCEEDED

    async def _dependencies(self, context: _Context) -> str:
        record, sha = context.record, context.job.sha
        if context.reuse:
            return STEP_SKIPPED

        def mark_ready() -> None:
            marker = self.paths.release_dir(record.id, sha) / READY_MARKER
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write(iso(utcnow()) or "")
            if self.settings.is_hidden and not context.static:
                self.builder.mark_local_ready(record.id, sha)

        if context.static:
            await asyncio.to_thread(mark_ready)
            return STEP_SKIPPED
        tmp = self.builder.build_dir(record.id, context.job.id) / "tmp"
        await self.builder.install(record, sha, context.manifest, tmp=tmp, log=context.log, timeout=context.remaining)
        await asyncio.to_thread(mark_ready)
        return STEP_SUCCEEDED

    def _candidate_release(self, context: _Context) -> Release:
        return Release(
            sha=context.job.sha,
            deployed_at=iso(utcnow()) or "",
            type=context.manifest["type"],
            entry_dir=context.entry_dir,
            manifest=context.manifest,
        )

    async def _start(self, context: _Context) -> str:
        record = context.record
        if context.static:
            return STEP_SKIPPED
        release = self._candidate_release(context)
        if self.settings.is_hidden:
            await self.builder.ensure_local(record, release, log=context.log, timeout=context.remaining())
        tmp = self.builder.build_dir(record.id, context.job.id) / "run-tmp"
        await asyncio.to_thread(self.builder._prepare_tmp, record, tmp)
        context.candidate_port = free_port()
        spec = self.runtime.run_spec(record, release, port=context.candidate_port, tmp=tmp)
        context.log(f"$ {' '.join(spec.argv)}\n")
        context.candidate = await self.runtime.runner.spawn(spec, log=context.log)
        await asyncio.sleep(CANDIDATE_GRACE_SECONDS if self.runtime.candidate_grace is None else self.runtime.candidate_grace)
        if context.candidate.returncode is not None:
            raise DeployError(f"the app exited at once (exit code {context.candidate.returncode}); the log above says why")
        return STEP_SUCCEEDED

    async def _health(self, context: _Context) -> str:
        record = context.record
        if context.static or context.candidate is None:
            return STEP_SKIPPED
        release = self._candidate_release(context)
        url = health_url(record, release, port=context.candidate_port)
        timeout = int(context.manifest.get("startup_timeout") or 60)
        deadline = time.monotonic() + timeout
        context.log(f"waiting up to {timeout} seconds for GET {release.manifest.get('health_path') or '/'} to answer\n")
        try:
            while True:
                if context.candidate.returncode is not None:
                    raise DeployError(f"the app exited while starting (exit code {context.candidate.returncode}); the log above says why")
                status = await self.runtime.health(url)
                if status is not None and status < 500:
                    context.log(f"healthy (HTTP {status})\n")
                    return STEP_SUCCEEDED
                if time.monotonic() >= deadline:
                    answered = f"last answer: HTTP {status}" if status is not None else "it never answered"
                    raise DeployError(f"the app was not healthy within {timeout} seconds ({answered})")
                await asyncio.sleep(HEALTH_POLL_SECONDS if self.runtime.health_interval is None else self.runtime.health_interval)
        finally:
            candidate, context.candidate = context.candidate, None
            await asyncio.shield(candidate.stop())

    async def _activate(self, context: _Context) -> str:
        record = context.record
        release = self._candidate_release(context)
        # Nothing of this deployment runs any more: the app's processes are
        # again only the app.
        self.runtime.proc(record.id).candidate_active = False
        await self.runtime.switch_release(record.id, release)
        context.log(f"release {release.sha[:12]} is now current\n")
        return STEP_SUCCEEDED
