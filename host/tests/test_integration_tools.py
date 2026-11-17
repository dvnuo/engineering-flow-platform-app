"""The tools of the container smoke test, checked where they can be: here.

The smoke test itself (``integration/smoke.py``) only runs in CI, with
the real image. Its stub Portal, its probe app and its hand-written HTTP,
multipart and WebSocket helpers are plain Python, so that a mistake in them
is found on a developer's machine and not as a red CI run that says nothing
about the host.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from conftest import API_TOKEN, make_settings
from fastapi.testclient import TestClient
from runtime_helpers import Harness

from efp_app_host.app import create_app
from efp_app_host.bundle import unpack_bundle
from efp_app_host.manifest import load_manifest
from efp_app_host.portal_client import PortalClient

INTEGRATION = Path(__file__).resolve().parents[2] / "integration"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load("efp_smoke", INTEGRATION / "smoke.py")
stub_portal = _load("efp_stub_portal", INTEGRATION / "stub_portal.py")


@pytest.fixture
def serve():
    """Run an ``http.server`` handler class on a free loopback port."""

    servers = []

    def start(handler) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


# ---- the stub Portal speaks section 5 as the host's own client expects it ------------------


def test_the_stub_portal_answers_the_hosts_client(serve, tmp_path):
    handler = type("Handler", (stub_portal.Handler,), {"state": stub_portal.State(API_TOKEN, [{"id": "seed-0001", "slug": "seeded"}])})
    base = serve(handler)
    settings = make_settings(tmp_path, portal_internal_url=base)

    async def scenario():
        client = PortalClient(settings)
        try:
            redeemed = await client.redeem_handoff("ok.app-1.mindmap.12")
            again = await client.redeem_handoff("ok.app-1.mindmap.12")
            bad = await client.redeem_handoff("nonsense")
            allowed = await client.check_access("app-1", 12)
            unknown = await client.check_access("app-1", 99)
            desired = await client.desired_apps()
            pushed = await client.push_metrics({"sent_at": "2026-11-15T00:00:00Z", "traffic": [], "apps": [], "host": {}})
            event = await client.post_event("app-1", "started", {"sha": "s"})
            return redeemed, again, bad, allowed, unknown, desired, pushed, event
        finally:
            await client.aclose()

    redeemed, again, bad, allowed, unknown, desired, pushed, event = asyncio.run(scenario())
    assert redeemed == {"app_id": "app-1", "slug": "mindmap", "user": {"id": 12, "username": "bob", "display_name": "Bob", "group_id": 3, "group_name": "QA"}}
    assert again is None and bad is None  # a code works once
    assert allowed[0] is True and allowed[1] is None and allowed[2].username == "bob"
    assert unknown[0] is False and unknown[1] == "user_inactive" and unknown[2] is None
    assert desired == [{"id": "seed-0001", "slug": "seeded"}]
    assert pushed is True and event is True

    auth = {"Authorization": f"Bearer {API_TOKEN}"}
    assert smoke.request(base, "PUT", "/_stub/deny", headers=auth, body=json.dumps({"pairs": [["app-1", 12]]}).encode()).status == 200
    assert smoke.request(base, "PUT", "/_stub/apps", headers=auth, body=json.dumps({"apps": [{"id": "x", "slug": "new"}]}).encode()).status == 200

    async def after():
        client = PortalClient(settings)
        try:
            return await client.check_access("app-1", 12), await client.desired_apps()
        finally:
            await client.aclose()

    denied, desired = asyncio.run(after())
    assert denied[0] is False and denied[1] == "no_access"
    assert desired == [{"id": "x", "slug": "new"}]

    state = smoke.request(base, "GET", "/_stub/state", headers=auth).json()
    assert [item["kind"] for item in state["events"]] == ["started"] and state["events"][0]["detail"] == {"sha": "s"}
    assert state["metrics_posts"] == 1 and state["unauthorized"] == 0
    assert state["calls"]["redeem"] == 3 and state["calls"]["access_check"] == 3

    # Without the host token nothing is answered, and the stub counts it.
    refused = smoke.request(base, "GET", stub_portal.PREFIX + "/apps")
    assert refused.status == 401 and refused.json()["error"]["code"] == "unauthorized"
    assert smoke.request(base, "GET", "/_stub/state", headers=auth).json()["unauthorized"] == 1


# ---- the smoke test's own helpers ---------------------------------------------------------


def test_the_hand_written_multipart_body_is_what_the_manager_parses(tmp_path):
    harness = Harness(tmp_path)
    app = create_app(harness.settings, registry=harness.registry, runtime=harness.runtime, portal=harness.portal, run_background=False)
    site = {"efp-app.json": json.dumps({"name": "Site", "type": "static", "entry": "public"}), "public/index.html": "hi\n"}
    bundle = smoke.files_bundle(site)
    spec = {"sha": "a" * 40, "ref": "main", "entry_dir": "", "requested_by": "ci"}
    body, content_type = smoke.encode_multipart({"spec": json.dumps(spec)}, {"bundle": ("bundle.tar.gz", bundle, "application/gzip")})
    with TestClient(app) as client:
        client.headers["Authorization"] = f"Bearer {API_TOKEN}"
        assert client.put("/v1/apps/app-1", json={"slug": "site", "env": {}}).status_code == 200
        response = client.post("/v1/apps/app-1/releases", content=body, headers={"Content-Type": content_type})
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        for _ in range(500):
            job = client.get(f"/v1/jobs/{job_id}").json()
            if job["status"] != "queued" and job["status"] != "running":
                break
        assert job["status"] == "succeeded", job
    assert (harness.paths.code_dir("app-1", "a" * 40) / "public" / "index.html").read_text(encoding="utf-8") == "hi\n"


def test_the_bundles_the_smoke_test_builds_unpack_as_intended(tmp_path):
    probe = INTEGRATION / "probe_app"
    plain = tmp_path / "plain.tar.gz"
    plain.write_bytes(smoke.tar_bundle(probe, overrides={"version.txt": "two\n"}))
    result = unpack_bundle(plain, tmp_path / "plain")
    assert result.entry_dir == "" and (tmp_path / "plain" / "version.txt").read_text(encoding="utf-8") == "two\n"
    manifest = load_manifest(tmp_path / "plain")
    assert manifest["type"] == "command" and manifest["command"] == "python server.py" and manifest["health_path"] == "/healthz"

    github = tmp_path / "github.tar.gz"
    github.write_bytes(smoke.tar_bundle(probe, prefix="acme-starters-0123abc/templates/probe/"))
    result = unpack_bundle(github, tmp_path / "github", entry_dir="templates/probe")
    assert (result.entry_dir, result.skipped_prefix) == ("templates/probe", "acme-starters-0123abc")
    assert (tmp_path / "github" / "server.py").is_file() and (tmp_path / "github" / "version.txt").read_text(encoding="utf-8") == "one\n"


def test_request_sends_the_path_untouched_and_follows_nothing(serve):
    seen = []

    from http.server import BaseHTTPRequestHandler

    class Echo(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            return None

        def do_GET(self) -> None:
            seen.append((self.path, self.headers.get("Cookie")))
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Set-Cookie", "a=1; Path=/")
            self.send_header("Set-Cookie", "b=2; Domain=example.com")
            self.send_header("Content-Length", "0")
            self.end_headers()

    base = serve(Echo)
    reply = smoke.request(base, "GET", "/app/..%2f..%2fsecret/../x?q=1", headers={"Cookie": "s=1"})
    assert seen == [("/app/..%2f..%2fsecret/../x?q=1", "s=1")]
    assert reply.status == 302 and reply.header("location") == "/elsewhere"
    assert smoke.cookie_names(reply) == {"a": "a=1; Path=/", "b": "b=2; Domain=example.com"}


# ---- the probe app ----------------------------------------------------------------------------


@pytest.fixture
def probe(serve, monkeypatch):
    module = _load("efp_probe_app", INTEGRATION / "probe_app" / "server.py")
    monkeypatch.setattr(module, "BASE", "/probe-x")
    return module, serve(module.Handler)


def test_the_probe_app_serves_under_its_prefix(probe):
    module, base = probe
    front = smoke.request(base, "GET", "/probe-x/")
    assert front.status == 200 and "version one" in front.text
    assert smoke.request(base, "GET", "/probe-x/healthz").status == 200
    assert smoke.request(base, "GET", "/probe-x/nothing").status == 404
    cookies = smoke.request(base, "GET", "/probe-x/cookies")
    assert sorted(smoke.cookie_names(cookies)) == ["__Host-efp_apps", "good", "scoped", "wide"]
    assert cookies.header("Service-Worker-Allowed") == "/"


def test_the_probe_app_reports_reads_and_writes_truthfully(probe, tmp_path):
    module, base = probe
    (tmp_path / "there.txt").write_text("content", encoding="utf-8")
    body = json.dumps({"paths": [str(tmp_path / "there.txt"), str(tmp_path / "missing.txt"), str(tmp_path)]}).encode()
    results = smoke.request(base, "POST", "/probe-x/read", body=body).json()["results"]
    assert [(item["ok"], item["kind"]) for item in results] == [(True, "file"), (False, "file"), (True, "dir")]
    assert results[0]["sample"] == "content" and results[1]["error"] == "FileNotFoundError" and results[2]["sample"] == ["there.txt"]
    wrote = smoke.request(base, "POST", "/probe-x/write", body=json.dumps({"path": str(tmp_path / "new.txt"), "content": "x"}).encode()).json()
    assert wrote["ok"] is True and (tmp_path / "new.txt").read_text(encoding="utf-8") == "x"
    refused = smoke.request(base, "POST", "/probe-x/write", body=json.dumps({"path": str(tmp_path / "no" / "dir" / "f.txt")}).encode()).json()
    assert refused["ok"] is False and refused["error"] == "FileNotFoundError"
    no_socket = smoke.request(base, "POST", "/probe-x/storage", body=json.dumps({"op": "list", "prefix": ""}).encode()).json()
    assert no_socket["ok"] is False and no_socket["error"]["code"] == "no_socket"
    if hasattr(module.socket, "AF_UNIX"):
        refused = smoke.request(base, "POST", "/probe-x/connect", body=json.dumps({"paths": [str(tmp_path / "no.sock")]}).encode()).json()["results"]
        assert refused == [{"path": str(tmp_path / "no.sock"), "ok": False, "error": "FileNotFoundError"}]


def test_the_websocket_client_and_the_probe_apps_echo_understand_each_other(probe):
    module, base = probe
    assert smoke.websocket_echo(base, "/probe-x/ws", "hello") == (101, "echo:hello")
    assert smoke.websocket_echo(base, "/probe-x/ws", "x" * 120) == (101, "echo:" + "x" * 120)
    # Not an upgrade: an ordinary answer, no hanging connection.
    plain = smoke.request(base, "GET", "/probe-x/ws")
    assert plain.status == 400
    assert smoke.websocket_echo(base, "/probe-x/nothing", "hello")[0] == 404


@pytest.mark.skipif(sys.platform == "win32", reason="the probe app reports POSIX ids")
def test_the_probe_app_says_who_it_is(probe):
    module, base = probe
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"sub": 12, "username": "bob"}).encode()).decode().rstrip("=")
    who = smoke.request(base, "GET", "/probe-x/whoami", headers={"X-EFP-Visitor": f"v1.{payload}.sig", "Cookie": "mine=1"}).json()
    assert who["visitor"] == {"sub": 12, "username": "bob"} and who["cookie"] == "mine=1"
    assert [name.lower() for name in who["efp_headers"]] == ["x-efp-visitor"]
    assert who["no_new_privs"] in ("0", "1") and len(who["cap_eff"]) == 16
    scan = smoke.request(base, "POST", "/probe-x/environ-scan", body=json.dumps({"needles": ["certainly-not-in-any-environment"]}).encode()).json()
    assert scan["found"] == [] and scan["processes_visible"] > 0
