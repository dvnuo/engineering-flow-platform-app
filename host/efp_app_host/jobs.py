"""Deployment jobs: their record and their log (docs/CONTRACTS.md, 4.2 and 4.3).

A job is two files under ``jobs/`` on the volume: ``<id>.json`` (rewritten at
every step) and ``<id>.log`` (appended to). They outlive the manager, so the
Portal can still read how a deployment ended after a restart; a job that was
cut short by one is marked failed when the manager comes back.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import iso, utcnow
from .registry import read_json, write_json_atomic

STEP_NAMES = ("unpack", "manifest", "environment", "dependencies", "start", "health", "activate")

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
ACTIVE = (JOB_QUEUED, JOB_RUNNING)

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_SKIPPED = "skipped"

LOG_TAIL_CHARS = 4000
JOB_RETENTION_DAYS = 30
_JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def is_job_id(value: str) -> bool:
    return bool(_JOB_ID_RE.match(value or ""))


def _new_steps() -> list[dict[str, Any]]:
    return [{"name": name, "status": STEP_PENDING, "started_at": None, "finished_at": None} for name in STEP_NAMES]


@dataclass
class Job:
    id: str
    app_id: str
    sha: str
    kind: str = "deploy"
    status: str = JOB_QUEUED
    steps: list[dict[str, Any]] = field(default_factory=_new_steps)
    error: dict[str, str] | None = None
    created_at: str = ""
    finished_at: str | None = None
    ref: str = ""
    entry_dir: str = ""
    requested_by: str = ""

    def step(self, name: str) -> dict[str, Any]:
        for item in self.steps:
            if item["name"] == name:
                return item
        raise KeyError(name)

    @property
    def active(self) -> bool:
        return self.status in ACTIVE

    def current_step(self) -> str | None:
        for item in self.steps:
            if item["status"] == STEP_RUNNING:
                return item["name"]
        for item in self.steps:
            if item["status"] == STEP_PENDING:
                return item["name"]
        return None


class Redactor:
    """Blanks out secrets before a line reaches a log."""

    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets = sorted({secret for secret in secrets if secret and len(secret) >= 6}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for secret in self._secrets:
            if secret in text:
                text = text.replace(secret, "***")
        return text


class JobStore:
    def __init__(self, jobs_dir: Path):
        self.dir = Path(jobs_dir)
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._latest: dict[str, str] = {}
        self._load()

    # ---- persistence ------------------------------------------------------
    def _file(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.json"

    def log_path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.log"

    def bundle_path(self, job_id: str) -> Path:
        return self.dir / f"{job_id}.bundle"

    def _load(self) -> None:
        try:
            files = sorted(self.dir.glob("*.json"))
        except OSError:
            files = []
        for path in files:
            try:
                data = read_json(path, None)
            except (ValueError, OSError):
                continue
            if not isinstance(data, dict) or not is_job_id(str(data.get("id") or "")):
                continue
            known = Job.__dataclass_fields__
            job = Job(**{key: value for key, value in data.items() if key in known})
            self._jobs[job.id] = job
        for job in sorted(self._jobs.values(), key=lambda item: item.created_at):
            self._latest[job.app_id] = job.id

    def save(self, job: Job) -> None:
        with self._lock:
            self._jobs[job.id] = job
            write_json_atomic(self._file(job.id), asdict(job), mode=0o600)

    # ---- life cycle -------------------------------------------------------
    def create(self, app_id: str, sha: str, *, ref: str = "", entry_dir: str = "", requested_by: str = "") -> Job:
        with self._lock:
            job = Job(
                id=str(uuid.uuid4()),
                app_id=app_id,
                sha=sha,
                created_at=iso(utcnow()) or "",
                ref=ref,
                entry_dir=entry_dir,
                requested_by=requested_by,
            )
            self.dir.mkdir(parents=True, exist_ok=True)
            self.log_path(job.id).touch()
            self._latest[app_id] = job.id
            self.save(job)
            return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def active_for(self, app_id: str) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.app_id == app_id and job.active:
                    return job
            return None

    def latest_for(self, app_id: str) -> Job | None:
        with self._lock:
            job_id = self._latest.get(app_id)
            return self._jobs.get(job_id) if job_id else None

    def begin_step(self, job: Job, name: str) -> None:
        step = job.step(name)
        step["status"] = STEP_RUNNING
        step["started_at"] = iso(utcnow())
        self.save(job)

    def end_step(self, job: Job, name: str, status: str = STEP_SUCCEEDED) -> None:
        step = job.step(name)
        step["status"] = status
        now = iso(utcnow())
        if status == STEP_SKIPPED:
            step["started_at"] = step["started_at"] or now
        step["finished_at"] = now
        self.save(job)

    def finish(self, job: Job, *, error: dict[str, str] | None = None) -> None:
        job.status = JOB_FAILED if error else JOB_SUCCEEDED
        job.error = error
        job.finished_at = iso(utcnow())
        self.save(job)
        try:
            self.bundle_path(job.id).unlink()
        except OSError:
            pass

    def fail_interrupted(self) -> list[Job]:
        """Close the jobs a restart of the manager cut short."""

        interrupted: list[Job] = []
        with self._lock:
            for job in list(self._jobs.values()):
                if not job.active:
                    continue
                step = job.current_step() or STEP_NAMES[0]
                item = job.step(step)
                if item["status"] == STEP_RUNNING:
                    item["status"] = STEP_FAILED
                    item["finished_at"] = iso(utcnow())
                self.append_log(job.id, "The host restarted while this deployment was running; deploy again.\n")
                self.finish(job, error={"step": step, "message": "the host restarted during the deployment"})
                interrupted.append(job)
        return interrupted

    def prune(self, *, max_age_days: int = JOB_RETENTION_DAYS, now: float | None = None) -> int:
        """Drop the files of old finished jobs (the newest job of an app always stays)."""

        cutoff = (time.time() if now is None else now) - max_age_days * 86400
        removed = 0
        with self._lock:
            keep = set(self._latest.values())
            for job in list(self._jobs.values()):
                if job.active or job.id in keep:
                    continue
                try:
                    if self._file(job.id).stat().st_mtime > cutoff:
                        continue
                except OSError:
                    pass
                for path in (self._file(job.id), self.log_path(job.id), self.bundle_path(job.id)):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                del self._jobs[job.id]
                removed += 1
        return removed

    def forget_app(self, app_id: str) -> None:
        with self._lock:
            self._latest.pop(app_id, None)

    # ---- log --------------------------------------------------------------
    def append_log(self, job_id: str, text: str) -> None:
        if not text:
            return
        with self._lock:
            try:
                with open(self.log_path(job_id), "a", encoding="utf-8", newline="\n") as handle:
                    handle.write(text)
            except OSError:
                pass

    def read_log(self, job_id: str, offset: int = 0) -> tuple[bytes, int]:
        path = self.log_path(job_id)
        try:
            size = path.stat().st_size
            offset = max(0, min(int(offset), size))
            with open(path, "rb") as handle:
                handle.seek(offset)
                return handle.read(), size
        except OSError:
            return b"", 0

    def log_tail(self, job_id: str) -> tuple[str, int]:
        path = self.log_path(job_id)
        try:
            size = path.stat().st_size
            with open(path, "rb") as handle:
                handle.seek(max(0, size - LOG_TAIL_CHARS * 4))
                text = handle.read().decode("utf-8", "replace")
        except OSError:
            return "", 0
        return text[-LOG_TAIL_CHARS:], size

    def public(self, job: Job) -> dict[str, Any]:
        """The job as ``GET /v1/jobs/{id}`` returns it."""

        tail, size = self.log_tail(job.id)
        return {
            "id": job.id,
            "app_id": job.app_id,
            "kind": job.kind,
            "status": job.status,
            "sha": job.sha,
            "steps": [dict(step) for step in job.steps],
            "error": dict(job.error) if job.error else None,
            "log_tail": tail,
            "log_size": size,
            "created_at": job.created_at,
            "finished_at": job.finished_at,
        }
