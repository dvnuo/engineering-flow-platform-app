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


def test_a_job_id_may_not_end_in_a_line_break():
    assert is_job_id("0" * 8 + "-0000-0000-0000-" + "0" * 12)
    assert not is_job_id("0" * 8 + "-0000-0000-0000-" + "0" * 12 + "\n")


def test_the_log_of_a_deployment_has_a_ceiling_but_the_managers_own_lines_are_always_written(tmp_path, monkeypatch):
    from efp_app_host import jobs as jobs_module

    monkeypatch.setattr(jobs_module, "MAX_JOB_LOG_BYTES", 1000)
    store = JobStore(tmp_path / "jobs")
    job = store.create("app-1", "a" * 40)
    store.append_log(job.id, "--- start ---\n", force=True)
    store.append_log(job.id, "a" * 600 + "\n")
    store.append_log(job.id, "b" * 600 + "\n")  # goes over: cut, with one line that says so
    for _ in range(200):
        store.append_log(job.id, "c" * 600 + "\n")  # dropped
    store.append_log(job.id, "FAILED at health: it never answered\n", force=True)
    data, size = store.read_log(job.id)
    text = data.decode("utf-8")
    assert size == len(data) < 1000 + 300
    assert text.startswith("--- start ---\n" + "a" * 600) and "c" not in text
    assert text.count("the rest of the output is not kept") == 1
    assert text.endswith("FAILED at health: it never answered\n")
    assert "FAILED at health" in store.public(job)["log_tail"]


def test_appending_to_a_log_does_not_wait_for_the_disk(tmp_path, monkeypatch):
    import threading

    from efp_app_host import jobs as jobs_module

    release = threading.Event()
    opened = threading.Event()
    real_open = open

    def slow_open(path, mode="r", *args, **kwargs):
        if mode == "ab":
            opened.set()
            assert release.wait(30), "the test never released the writer"
        return real_open(path, mode, *args, **kwargs)

    store = JobStore(tmp_path / "jobs")
    job = store.create("app-1", "a" * 40)
    monkeypatch.setattr(jobs_module, "open", slow_open, raising=False)
    try:
        # The volume is stuck; whoever produces the lines (the event loop) is not.
        for index in range(1000):
            store.append_log(job.id, f"line {index}\n")
        assert opened.wait(30)
        assert store.log_path(job.id).stat().st_size == 0
    finally:
        release.set()
    # A reader waits for what was queued before it looks.
    data, _ = store.read_log(job.id)
    assert data.decode("utf-8").splitlines() == [f"line {index}" for index in range(1000)]


def test_a_job_that_reads_as_finished_comes_with_its_whole_log(tmp_path, monkeypatch):
    store = JobStore(tmp_path / "jobs")
    job = store.create("app-1", "a" * 40)
    store.append_log(job.id, "--- activate ---\n", force=True)
    read_tail = store.log_tail

    def the_job_ends_while_its_log_is_read(job_id):
        answer = read_tail(job_id)
        store.append_log(job_id, "deployment finished\n", force=True)
        store.finish(job)
        return answer

    # A reader works in a thread of its own; the deployment ends on the event loop meanwhile.
    monkeypatch.setattr(store, "log_tail", the_job_ends_while_its_log_is_read)
    seen = store.public(job)
    # Not "succeeded" with the log of a moment ago: whoever stops asking at
    # the last status must have the last line. (It used to be exactly that.)
    assert seen["status"] == JOB_QUEUED and seen["finished_at"] is None and "deployment finished" not in seen["log_tail"]
    monkeypatch.setattr(store, "log_tail", read_tail)
    final = store.public(job)
    assert final["status"] == JOB_SUCCEEDED and final["finished_at"] and final["log_tail"].endswith("deployment finished\n")
    assert final["log_size"] == len(store.read_log(job.id)[0])


def test_the_size_of_a_log_and_its_bytes_come_from_one_look(tmp_path, monkeypatch):
    from efp_app_host import jobs as jobs_module

    store = JobStore(tmp_path / "jobs")
    job = store.create("app-1", "a" * 40)
    store.append_log(job.id, "first\n")
    store.flush_logs()
    real_open = open

    def open_while_the_log_grows(path, mode="r", *args, **kwargs):
        if mode == "rb":
            # What the writer's thread appends between two steps of a reader.
            with real_open(path, "ab") as late:
                late.write(b"late\n")
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(jobs_module, "open", open_while_the_log_grows, raising=False)
    data, size = store.read_log(job.id)
    # The size used to be taken before the file was opened: a reader that
    # went on from it got "late" a second time.
    assert data == b"first\nlate\n" and size == len(data)
    rest, total = store.read_log(job.id, size)
    assert rest == b"late\n" and total == size + len(rest)
    tail, tail_size = store.log_tail(job.id)
    assert tail == "first\n" + "late\n" * 3 and tail_size == len(tail)
