from __future__ import annotations

import os
import time

from efp_app_host.jobs import JOB_FAILED, JOB_QUEUED, JOB_SUCCEEDED, STEP_NAMES, STEP_SKIPPED, JobStore, Redactor, is_job_id


def test_a_new_job_has_the_seven_steps_pending(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = store.create("app-1", "a" * 40, ref="main", entry_dir="apps/x", requested_by="alice")
    assert is_job_id(job.id) and job.status == JOB_QUEUED
    public = store.public(job)
    assert [step["name"] for step in public["steps"]] == list(STEP_NAMES) == ["unpack", "manifest", "environment", "dependencies", "start", "health", "activate"]
    assert {step["status"] for step in public["steps"]} == {"pending"}
    assert set(public) == {"id", "app_id", "kind", "status", "sha", "steps", "error", "log_tail", "log_size", "created_at", "finished_at"}
    assert public["kind"] == "deploy" and public["error"] is None and public["finished_at"] is None and public["log_size"] == 0


def test_steps_and_log(tmp_path):
    store = JobStore(tmp_path / "jobs")
    job = store.create("app-1", "a" * 40)
    store.begin_step(job, "unpack")
    assert job.step("unpack")["status"] == "running" and job.step("unpack")["started_at"]
    assert job.current_step() == "unpack"
    store.end_step(job, "unpack")
    store.end_step(job, "manifest", STEP_SKIPPED)
    assert job.step("manifest")["status"] == "skipped" and job.step("manifest")["finished_at"]
    assert job.current_step() == "environment"

    store.append_log(job.id, "line one\n")
    store.append_log(job.id, "x" * 5000 + "\nlast line\n")
    public = store.public(job)
    assert public["log_size"] == 9 + 5001 + 10
    assert len(public["log_tail"]) == 4000 and public["log_tail"].endswith("last line\n")
    data, size = store.read_log(job.id, 9)
    assert size == public["log_size"] and data.startswith(b"xxx") and len(data) == size - 9
    assert store.read_log(job.id, 10**9) == (b"", size)

    store.finish(job, error={"step": "environment", "message": "pip failed"})
    assert job.status == JOB_FAILED and job.finished_at and not job.active
    assert store.public(job)["error"] == {"step": "environment", "message": "pip failed"}


def test_jobs_survive_a_restart_and_interrupted_ones_are_closed(tmp_path):
    store = JobStore(tmp_path / "jobs")
    done = store.create("app-1", "a" * 40)
    store.finish(done)
    cut = store.create("app-2", "b" * 40)
    cut.status = "running"
    store.begin_step(cut, "unpack")
    store.end_step(cut, "unpack")
    store.begin_step(cut, "manifest")
    store.bundle_path(cut.id).write_bytes(b"bundle")

    reloaded = JobStore(tmp_path / "jobs")
    assert reloaded.get(done.id).status == JOB_SUCCEEDED
    assert reloaded.active_for("app-2").id == cut.id
    interrupted = reloaded.fail_interrupted()
    assert [job.id for job in interrupted] == [cut.id]
    job = reloaded.get(cut.id)
    assert job.status == JOB_FAILED and job.error == {"step": "manifest", "message": "the host restarted during the deployment"}
    assert job.step("manifest")["status"] == "failed" and job.step("environment")["status"] == "pending"
    assert "restarted" in reloaded.public(job)["log_tail"]
    assert reloaded.active_for("app-2") is None and reloaded.latest_for("app-2").id == cut.id
    assert not reloaded.bundle_path(cut.id).exists()

    again = JobStore(tmp_path / "jobs")
    assert again.fail_interrupted() == [] and again.get(cut.id).status == JOB_FAILED


def test_old_jobs_are_pruned_but_the_newest_of_an_app_stays(tmp_path):
    store = JobStore(tmp_path / "jobs")
    old = store.create("app-1", "a" * 40)
    store.finish(old)
    newest = store.create("app-1", "b" * 40)
    store.finish(newest)
    week = 7 * 86400
    for job in (old, newest):
        stamp = time.time() - 6 * week
        os.utime(tmp_path / "jobs" / f"{job.id}.json", (stamp, stamp))
    assert store.prune() == 1
    assert store.get(old.id) is None and not (tmp_path / "jobs" / f"{old.id}.log").exists()
    assert store.get(newest.id) is not None
    assert store.latest_for("app-1").id == newest.id


def test_job_ids_are_checked():
    assert not is_job_id("../../etc/passwd") and not is_job_id("") and not is_job_id("1234")


def test_redactor_blanks_secrets():
    redact = Redactor(["host-token-123", "efpa_abcdef", "short", "", "efpa_abcdef-longer"])
    assert redact("Authorization: Bearer host-token-123") == "Authorization: Bearer ***"
    assert redact("token=efpa_abcdef-longer and efpa_abcdef") == "token=*** and ***"
    assert redact("a short word stays") == "a short word stays"
