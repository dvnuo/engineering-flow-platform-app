from __future__ import annotations

import json
import time

import pytest
from conftest import API_TOKEN
from fastapi.testclient import TestClient
from runtime_helpers import FASTAPI_FILES, SHA_A, SHA_B, STATIC_FILES, Harness, make_bundle

from efp_app_host.admin_api import sse_event
from efp_app_host.app import create_app

AUTH = {"Authorization": f"Bearer {API_TOKEN}"}
APP_BODY = {
    "slug": "mindmap",
    "name": "Mind map",
    "owner_username": "alice",
    "visibility": "public",
    "enabled": True,
    "disabled_reason": None,
    "visitor_key": "dmlzaXRvci1rZXktdmlzaXRvci1rZXktdmlzaXRvcg",
    "env": {"EFP_APP_TOKEN": "efpa_" + "t" * 43, "MY_SETTING": "x"},
    "limits": {"memory_mb": None, "data_mb": 2048},
}
STATUS_KEYS = {
    "id", "slug", "type", "state", "reason", "release", "previous_release", "uptime_seconds", "restarts",
    "rss_bytes", "cpu_percent", "data_bytes", "last_activity_at", "port", "last_log_lines",
}  # fmt: skip


@pytest.fixture
def api(tmp_path):
    harness = Harness(tmp_path)
    app = create_app(harness.settings, registry=harness.registry, runtime=harness.runtime, portal=harness.portal, run_background=False)
    with TestClient(app) as client:
        client.headers.update(AUTH)
        yield client, harness


def _deploy(client, harness, app_id="app-1", sha=SHA_A, files=None, **spec):
    bundle = make_bundle(harness.tmp / "upload" / f"{sha}.tar.gz", FASTAPI_FILES if files is None else files)
    body = {"sha": sha, "ref": "main", "entry_dir": "", "requested_by": "alice", **spec}
    with open(bundle, "rb") as handle:
        response = client.post(f"/v1/apps/{app_id}/releases", data={"spec": json.dumps(body)}, files={"bundle": ("bundle.tar.gz", handle, "application/gzip")})
    return response


def _wait_job(client, job_id, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.02)
    raise AssertionError("the job did not finish")


def _wait_state(client, app_id, state, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.get(f"/v1/apps/{app_id}").json()
        if status["state"] == state:
            return status
        time.sleep(0.02)
    raise AssertionError(f"never reached {state}: {status}")


# ---- authentication and the error envelope ------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer wrong"}, {"Authorization": API_TOKEN}, {"Authorization": "Basic " + API_TOKEN}, {"Authorization": "Bearer "}, {"Authorization": "Bearer " + API_TOKEN + "x"}],
)
def test_every_route_needs_the_host_token(api, headers):
    client, _ = api
    client.headers.pop("Authorization")
    for method, path in (
        ("GET", "/v1/apps"),
        ("GET", "/v1/host"),
        ("PUT", "/v1/apps/app-1"),
        ("POST", "/v1/apps/app-1/releases"),
        ("GET", "/v1/jobs/x"),
        ("POST", "/v1/apps/app-1/stop"),
        ("DELETE", "/v1/apps/app-1"),
        ("POST", "/v1/sessions/revoke"),
        ("GET", "/v1/apps/app-1/logs/stream"),
        ("GET", "/v1/no/such/route"),
    ):
        response = client.request(method, path, headers=headers)
        assert response.status_code == 401, (method, path)
        assert response.json()["error"]["code"] == "unauthorized" and response.json()["error"]["message"]


def test_errors_always_use_the_envelope(api):
    client, harness = api
    cases = [
        (client.get("/v1/apps/missing"), 404, "app_not_found"),
        (client.post("/v1/apps/missing/start"), 404, "app_not_found"),
        (client.get("/v1/apps/missing/logs"), 404, "app_not_found"),
        (client.get("/v1/nothing/here"), 404, "not_found"),
        (client.patch("/v1/apps/app-1"), 404, "not_found"),
        (client.get("/v1/jobs/not-a-job"), 404, "job_not_found"),
        (client.get("/v1/jobs/00000000-0000-0000-0000-000000000000/log"), 404, "job_not_found"),
        (client.put("/v1/apps/app-1", content=b"{not json"), 400, "invalid_request"),
        (client.put("/v1/apps/app-1", json=["a list"]), 400, "invalid_request"),
        (client.put("/v1/apps/app-1", json={**APP_BODY, "slug": "login"}), 400, "invalid_request"),
        (client.put("/v1/apps/app-1", json={**APP_BODY, "slug": "Bad Slug"}), 400, "invalid_request"),
        (client.put("/v1/apps/app-1", json={**APP_BODY, "visibility": "everyone"}), 400, "invalid_request"),
        (client.put("/v1/apps/app-1", json={**APP_BODY, "env": {"BAD NAME": "x"}}), 400, "invalid_request"),
        (client.put("/v1/apps/app-1", json={**APP_BODY, "env": ["x"]}), 400, "invalid_request"),
        (client.put("/v1/apps/app-1", json={**APP_BODY, "limits": {"memory_mb": -5}}), 400, "invalid_request"),
        (client.put("/v1/apps/..%2F..%2Fetc", json=APP_BODY), 404, "not_found"),
        (client.put("/v1/apps/.hidden", json=APP_BODY), 400, "invalid_request"),
        (client.post("/v1/sessions/revoke", json={"user_id": "12"}), 400, "invalid_request"),
        (client.get("/v1/apps/app-1/logs?tail=many"), 400, "invalid_request"),
    ]
    for response, status, code in cases:
        assert response.status_code == status, response.text
        body = response.json()
        assert set(body) == {"error"} and body["error"]["code"] == code and isinstance(body["error"]["message"], str), response.text
    assert harness.registry.all() == []


# ---- 4.1 ----------------------------------------------------------------------------------


def test_put_app_creates_and_updates_the_record(api):
    client, harness = api
    response = client.put("/v1/apps/app-1", json=APP_BODY)
    assert response.status_code == 200
    status = response.json()
    assert set(status) == STATUS_KEYS
    assert (status["id"], status["slug"], status["state"], status["reason"]) == ("app-1", "mindmap", "undeployed", "not_deployed")
    record = harness.registry.get("app-1")
    assert (record.name, record.owner_username, record.visibility, record.data_mb, record.memory_mb) == ("Mind map", "alice", "public", 2048, None)
    assert record.visitor_key == APP_BODY["visitor_key"]
    assert harness.registry.read_env("app-1") == APP_BODY["env"]
    assert "app-app-1" in harness.controller.programs

    response = client.put("/v1/apps/app-1", json={**APP_BODY, "name": "Renamed", "visibility": "anyone"})
    assert response.status_code == 200 and harness.registry.get("app-1").name == "Renamed"
    assert (harness.registry.get("app-1").uid, harness.registry.get("app-1").port) == (record.uid, record.port)

    taken = client.put("/v1/apps/app-2", json=APP_BODY)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "slug_taken"
    assert harness.registry.find("app-2") is None

    listing = client.get("/v1/apps").json()
    assert [item["id"] for item in listing["apps"]] == ["app-1"] and set(listing["apps"][0]) == STATUS_KEYS


# ---- 4.2 / 4.3 ----------------------------------------------------------------------------


def test_deploy_through_the_api(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    response = _deploy(client, harness)
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = _wait_job(client, job_id)
    assert set(job) == {"id", "app_id", "kind", "status", "sha", "steps", "error", "log_tail", "log_size", "created_at", "finished_at"}
    assert (job["id"], job["app_id"], job["kind"], job["status"], job["sha"], job["error"]) == (job_id, "app-1", "deploy", "succeeded", SHA_A, None)
    assert [step["name"] for step in job["steps"]] == ["unpack", "manifest", "environment", "dependencies", "start", "health", "activate"]
    assert all(set(step) == {"name", "status", "started_at", "finished_at"} for step in job["steps"])
    assert job["log_size"] > 0 and "deployment finished" in job["log_tail"]

    log = client.get(f"/v1/jobs/{job_id}/log")
    assert log.status_code == 200 and log.headers["content-type"].startswith("text/plain")
    assert int(log.headers["X-Log-Size"]) == len(log.content) == job["log_size"]
    rest = client.get(f"/v1/jobs/{job_id}/log", params={"offset": job["log_size"] - 20})
    assert rest.content == log.content[-20:] and rest.headers["X-Log-Size"] == log.headers["X-Log-Size"]
    assert client.get(f"/v1/jobs/{job_id}/log", params={"offset": 10**9}).content == b""

    status = _wait_state(client, "app-1", "running")
    assert status["type"] == "fastapi" and status["release"]["sha"] == SHA_A and status["previous_release"] is None
    # The upload is not kept once the job is over.
    assert list(harness.paths.jobs_dir.glob("*.bundle")) == [] and list(harness.paths.jobs_dir.glob(".upload-*")) == []


def test_deploy_requests_are_checked_before_anything_is_queued(api):
    client, harness = api
    unknown = _deploy(client, harness, app_id="nope")
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "app_not_found"
    client.put("/v1/apps/app-1", json=APP_BODY)
    for response in (
        _deploy(client, harness, sha="abc"),
        _deploy(client, harness, sha="A" * 40),
        _deploy(client, harness, entry_dir="../other"),
        client.post("/v1/apps/app-1/releases", json={"sha": SHA_A}),
        client.post("/v1/apps/app-1/releases", data={"spec": json.dumps({"sha": SHA_A})}, files={"other": ("x", b"x")}),
        client.post("/v1/apps/app-1/releases", data={"spec": "{bad"}, files={"bundle": ("b.tar.gz", b"x")}),
        client.post("/v1/apps/app-1/releases", data={"spec": json.dumps({"sha": SHA_A})}, files={"bundle": ("b.tar.gz", b"")}),
    ):
        assert response.status_code == 400 and response.json()["error"]["code"] == "invalid_request", response.text
    assert harness.runtime.jobs.latest_for("app-1") is None
    assert list(harness.paths.jobs_dir.glob(".upload-*")) == []


def test_the_spec_may_also_be_sent_as_a_file_part(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    bundle = make_bundle(harness.tmp / "b.tar.gz", STATIC_FILES)
    spec = json.dumps({"sha": SHA_A, "ref": "v1", "entry_dir": "", "requested_by": "alice"}).encode("utf-8")
    response = client.post(
        "/v1/apps/app-1/releases",
        files={"spec": ("spec.json", spec, "application/json"), "bundle": ("bundle.tar.gz", bundle.read_bytes(), "application/gzip")},
    )
    assert response.status_code == 202
    assert _wait_job(client, response.json()["job_id"])["status"] == "succeeded"


def test_a_second_deployment_of_the_same_app_is_refused_while_one_runs(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    harness.runner.delay = 0.3
    first = _deploy(client, harness)
    second = _deploy(client, harness, sha=SHA_B)
    assert first.status_code == 202
    assert second.status_code == 409 and second.json()["error"]["code"] == "deploy_in_progress"
    rollback = client.post("/v1/apps/app-1/activate", json={"sha": SHA_A})
    assert rollback.status_code == 409 and rollback.json()["error"]["code"] == "deploy_in_progress"
    removal = client.delete("/v1/apps/app-1")
    assert removal.status_code == 409 and removal.json()["error"]["code"] == "deploy_in_progress"
    assert _wait_job(client, first.json()["job_id"])["status"] == "succeeded"


def test_a_failed_deployment_reports_the_step(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    harness.runner.fail = {" install ": 1}
    job = _wait_job(client, _deploy(client, harness).json()["job_id"])
    assert job["status"] == "failed" and job["error"]["step"] == "dependencies" and job["error"]["message"]
    statuses = {step["name"]: step["status"] for step in job["steps"]}
    assert statuses["dependencies"] == "failed" and statuses["start"] == "pending"
    status = client.get("/v1/apps/app-1").json()
    assert (status["state"], status["reason"]) == ("failed", "deploy") and status["last_log_lines"]


# ---- 4.4 ----------------------------------------------------------------------------------


def test_life_cycle(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    _wait_job(client, _deploy(client, harness).json()["job_id"])
    _wait_state(client, "app-1", "running")

    stopped = client.post("/v1/apps/app-1/stop")
    assert stopped.status_code == 200 and (stopped.json()["state"], stopped.json()["reason"]) == ("stopped", "manual")
    started = client.post("/v1/apps/app-1/start")
    assert started.status_code == 200 and started.json()["state"] in ("starting", "running")
    _wait_state(client, "app-1", "running")
    restarted = client.post("/v1/apps/app-1/restart")
    assert restarted.status_code == 200 and set(restarted.json()) == STATUS_KEYS
    _wait_state(client, "app-1", "running")

    starts = harness.controller.started("app-app-1")
    env = client.put("/v1/apps/app-1/env", json={"env": {"EFP_APP_TOKEN": "efpa_rotated", "N": 5}})
    assert env.status_code == 200
    _wait_state(client, "app-1", "running")
    assert harness.controller.started("app-app-1") == starts + 1
    assert harness.registry.read_env("app-1") == {"EFP_APP_TOKEN": "efpa_rotated", "N": "5"}
    assert client.put("/v1/apps/app-1/env", json={}).json()["error"]["code"] == "invalid_request"

    (harness.paths.data_dir("app-1") / "file.bin").write_bytes(b"x")
    emptied = client.delete("/v1/apps/app-1/data")
    assert emptied.status_code == 200 and set(emptied.json()) == STATUS_KEYS
    assert list(harness.paths.data_dir("app-1").iterdir()) == []
    _wait_state(client, "app-1", "running")


def test_rollback(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    _wait_job(client, _deploy(client, harness, sha=SHA_A).json()["job_id"])
    _wait_job(client, _deploy(client, harness, sha=SHA_B).json()["job_id"])
    status = _wait_state(client, "app-1", "running")
    assert (status["release"]["sha"], status["previous_release"]["sha"]) == (SHA_B, SHA_A)

    missing = client.post("/v1/apps/app-1/activate", json={"sha": "f" * 40})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "release_not_found"
    assert client.post("/v1/apps/app-1/activate", json={}).status_code == 400

    back = client.post("/v1/apps/app-1/activate", json={"sha": SHA_A})
    assert back.status_code == 200
    assert (back.json()["release"]["sha"], back.json()["previous_release"]["sha"]) == (SHA_A, SHA_B)
    _wait_state(client, "app-1", "running")


def test_delete_and_purge(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    (harness.paths.data_dir("app-1") / "keep.txt").write_text("k", encoding="utf-8")
    response = client.delete("/v1/apps/app-1", params={"purge_data": "false"})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert client.get("/v1/apps/app-1").status_code == 404
    assert (harness.paths.data_dir("app-1") / "keep.txt").is_file()
    # The data kept at removal can be purged later, when the app is long gone.
    assert client.delete("/v1/apps/app-1", params={"purge_data": "true"}).json() == {"ok": True}
    assert not harness.paths.app_dir("app-1").exists()


def test_session_revocation_reaches_the_registry(api):
    client, harness = api
    before = time.time()
    response = client.post("/v1/sessions/revoke", json={"user_id": 12})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert harness.registry.revoked_before(12) >= before - 1
    assert harness.registry.revoked_before(13) == 0.0


# ---- 4.5 ----------------------------------------------------------------------------------


def test_log_tail(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    assert client.get("/v1/apps/app-1/logs").json() == {"lines": [], "truncated": False}
    with open(harness.paths.log_file("app-1"), "w", encoding="utf-8") as handle:
        handle.write("".join(f"line {number}\n" for number in range(1, 251)))
    default = client.get("/v1/apps/app-1/logs").json()
    assert len(default["lines"]) == 200 and default["lines"][-1] == "line 250" and default["truncated"] is True
    few = client.get("/v1/apps/app-1/logs", params={"tail": 2}).json()
    assert few == {"lines": ["line 249", "line 250"], "truncated": True}
    assert client.get("/v1/apps/app-1/logs", params={"tail": 100000}).json()["truncated"] is False


def test_log_stream_is_server_sent_events(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    with open(harness.paths.log_file("app-1"), "w", encoding="utf-8") as handle:
        handle.write('first\nsecond "quoted" \u00e9\n')
    client.app.state.host.extras["log_stream_max_seconds"] = 0.3
    response = client.get("/v1/apps/app-1/logs/stream", params={"tail": 2})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = [block for block in response.text.split("\n\n") if block]
    data = [json.loads(block[len("data: ") :]) for block in events if block.startswith("data: ")]
    assert data == [{"line": "first"}, {"line": 'second "quoted" \u00e9'}]
    assert all(block.startswith("data: ") or block.startswith(": ") for block in events)


def test_sse_event_format():
    assert sse_event("a line") == 'data: {"line": "a line"}\n\n'
    assert sse_event(None) == ": keepalive\n\n"
    # One event stays one line, whatever the log line holds.
    assert sse_event("two\nlines \u00e9").count("\n") == 2 and "\\u00e9" in sse_event("two\nlines \u00e9")


# ---- 4.6 ----------------------------------------------------------------------------------


def test_host_object(api):
    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    host = client.get("/v1/host").json()
    assert set(host) == {"version", "isolation_mode", "sandbox_backend", "apps_total", "apps_running", "memory", "cpu_percent", "disk", "started_at"}
    assert (host["version"], host["isolation_mode"], host["sandbox_backend"], host["apps_total"], host["apps_running"]) == ("0.1.0", "sandbox", "bwrap", 1, 0)
    assert set(host["memory"]) == {"total_bytes", "used_bytes"}
    assert set(host["disk"]) == {"store", "local"} and set(host["disk"]["store"]) == {"total_bytes", "used_bytes"}
    assert host["started_at"].endswith("Z")


def test_the_token_is_compared_in_constant_time(monkeypatch, api):
    client, _ = api
    import efp_app_host.admin_api as admin_api

    seen = []
    real = admin_api.hmac.compare_digest
    monkeypatch.setattr(admin_api.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    assert client.get("/v1/apps").status_code == 200
    assert seen == [(API_TOKEN.encode("utf-8"), API_TOKEN.encode("utf-8"))]


def test_an_id_a_sha_or_a_name_may_not_end_in_a_line_break(api):
    client, harness = api
    # "$" in a pattern lets one line break through; an app id becomes a
    # section of the supervisor's configuration and a directory name.
    assert client.put("/v1/apps/app-1%0A", json=APP_BODY).status_code == 400
    assert client.get("/v1/apps/app-1%0A").status_code == 400
    assert client.put("/v1/apps/app-1", json={**APP_BODY, "env": {"NAME\n": "x"}}).status_code == 400
    assert client.put("/v1/apps/app-1", json={**APP_BODY, "slug": "mindmap\n"}).status_code == 400
    assert client.put("/v1/apps/app-1", json=APP_BODY).status_code == 200
    with_break = client.post("/v1/apps/app-1/releases", data={"spec": json.dumps({"sha": SHA_A + "\n"})}, files={"bundle": ("b.tar.gz", b"x")})
    assert with_break.status_code == 400 and "spec.sha" in with_break.json()["error"]["message"]
    assert client.get("/v1/jobs/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12 + "%0A").status_code == 404
    assert [record.id for record in harness.registry.all()] == ["app-1"]
    assert harness.runtime.jobs.latest_for("app-1") is None


def test_a_body_over_the_limit_is_refused_before_it_is_stored(api, monkeypatch):
    from efp_app_host import admin_api

    client, harness = api
    client.put("/v1/apps/app-1", json=APP_BODY)
    monkeypatch.setattr(admin_api, "MAX_RELEASE_BODY_BYTES", 2000)
    # A body that says how long it is: refused on that word alone.
    told = client.post(
        "/v1/apps/app-1/releases",
        data={"spec": json.dumps({"sha": SHA_A})},
        files={"bundle": ("bundle.tar.gz", b"x" * 5000, "application/gzip")},
    )
    assert told.status_code == 413 and told.json()["error"]["code"] == "bundle_too_large", told.text

    # A body that does not say (chunked): counted as it arrives, cut off at the limit.
    boundary = "efpboundary"

    def chunks():
        yield f'--{boundary}\r\nContent-Disposition: form-data; name="spec"\r\n\r\n{json.dumps({"sha": SHA_A})}\r\n'.encode("utf-8")
        yield f'--{boundary}\r\nContent-Disposition: form-data; name="bundle"; filename="b.tar.gz"\r\nContent-Type: application/gzip\r\n\r\n'.encode("utf-8")
        for _ in range(10):
            yield b"x" * 1000
        yield f"\r\n--{boundary}--\r\n".encode("utf-8")

    untold = client.post("/v1/apps/app-1/releases", content=chunks(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert untold.status_code == 413 and untold.json()["error"]["code"] == "bundle_too_large", untold.text
    assert harness.runtime.jobs.latest_for("app-1") is None
    assert list(harness.paths.jobs_dir.glob(".upload-*")) == []
    # A deployment of ordinary size still goes through the same door.
    monkeypatch.setattr(admin_api, "MAX_RELEASE_BODY_BYTES", 10 * 1024 * 1024)
    assert _deploy(client, harness).status_code == 202


def test_loader_variables_are_stored_but_never_handed_to_a_process(api):
    client, harness = api
    env = {**APP_BODY["env"], "LD_PRELOAD": "/data/hook.so", "GCONV_PATH": "/data/gconv"}
    # Not refused here: a record must stay updatable (disabling an app, say)
    # whatever its owner once entered. The names are dropped where it counts.
    assert client.put("/v1/apps/app-1", json={**APP_BODY, "env": env}).status_code == 200
    job = _wait_job(client, _deploy(client, harness).json()["job_id"])
    assert job["status"] == "succeeded"
    for spec, _ in harness.runner.spawned:
        assert spec.env["MY_SETTING"] == "x" and "LD_PRELOAD" not in spec.env and "GCONV_PATH" not in spec.env
