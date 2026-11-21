"""The container smoke test: the real image, a stub Portal, real apps.

    python smoke.py --phase first          --mode sandbox|hidden [options]
    python smoke.py --phase after-recreate --mode sandbox|hidden [options]

``first`` registers and deploys the starter templates, an example app
(``examples/team-poll``: a member signs in and votes) and a probe app,
checks them through the gateway, checks that one app cannot reach another,
that sign-in, refusals, cookies, WebSocket and static files behave behind the
real nginx, and that redeploy and rollback work. ``after-recreate`` runs
against a new container on the same volume: everything must come back.

Standard library only, so it runs on a bare CI machine. Every check prints
one line; a failed check prints what it saw. The exit code is the number of
failed checks (capped at 1).

``integration/ci.sh`` starts the containers and calls this.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import io
import json
import os
import socket
import struct
import sys
import tarfile
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

HERE = Path(__file__).resolve().parent
NAVIGATE = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Mode": "navigate"}
FETCH = {"Accept": "application/json", "Sec-Fetch-Mode": "cors"}
SESSION_COOKIE = "__Host-efp_apps"
# A sign-in under way (section 6.3): the gateway puts one value into the
# address it sends the browser to and into this cookie, and redeems a code
# only when both come back equal.
STATE_COOKIE = "__Host-efp_apps_state"
STATE = "smoke-state-of-a-sign-in-000001"


# ---- small tools (also imported by the unit tests) ------------------------------------


class Reply:
    def __init__(self, status: int, headers: list, body: bytes):
        self.status = status
        self.headers = headers
        self.body = body

    def header(self, name: str) -> str | None:
        for key, value in self.headers:
            if key.lower() == name.lower():
                return value
        return None

    def all(self, name: str) -> list:
        return [value for key, value in self.headers if key.lower() == name.lower()]

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.body)

    def __repr__(self) -> str:
        return f"<{self.status} {self.text[:300]!r}>"


def request(base: str, method: str, path: str, *, headers: dict | None = None, body: bytes | None = None, timeout: float = 30.0) -> Reply:
    """One HTTP request, sent exactly as given: no redirects followed, the path untouched."""

    parts = urlsplit(base)
    connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    try:
        connection.putrequest(method, path, skip_accept_encoding=True)
        for name, value in (headers or {}).items():
            connection.putheader(name, value)
        if body is not None:
            connection.putheader("Content-Length", str(len(body)))
        connection.endheaders(body)
        response = connection.getresponse()
        return Reply(response.status, response.getheaders(), response.read())
    finally:
        connection.close()


def encode_multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    """``multipart/form-data``: text ``fields`` and ``files`` as name -> (filename, bytes, content type)."""

    boundary = "efp" + uuid.uuid4().hex
    out = io.BytesIO()
    for name, value in fields.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        out.write(value.encode("utf-8") + b"\r\n")
    for name, (filename, content, content_type) in files.items():
        out.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode("utf-8"))
        out.write(content + b"\r\n")
    out.write(f"--{boundary}--\r\n".encode("utf-8"))
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def tar_bundle(directory: Path, *, prefix: str = "", overrides: dict | None = None) -> bytes:
    """A ``.tar.gz`` of ``directory``'s content below ``prefix``; ``overrides`` replace or add files."""

    files: dict[str, bytes] = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            files[path.relative_to(directory).as_posix()] = path.read_bytes()
    for name, content in (overrides or {}).items():
        files[name] = content.encode("utf-8") if isinstance(content, str) else content
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        if prefix:
            top = tarfile.TarInfo(prefix.split("/", 1)[0])
            top.type = tarfile.DIRTYPE
            top.mode = 0o755
            archive.addfile(top)
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(prefix + name)
            info.size = len(content)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(content))
    return out.getvalue()


def files_bundle(files: dict) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as archive:
        for name, content in sorted(files.items()):
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return out.getvalue()


def websocket_echo(base: str, path: str, message: str, *, headers: dict | None = None, timeout: float = 20.0) -> tuple[int, str]:
    """Open a WebSocket, send one text frame, return (handshake status, the text that came back)."""

    parts = urlsplit(base)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    lines = [
        f"GET {path} HTTP/1.1",
        f"Host: {parts.hostname}:{parts.port or 80}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13",
    ]
    lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
    with socket.create_connection((parts.hostname, parts.port or 80), timeout=timeout) as connection:
        connection.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
        reader = connection.makefile("rb")
        status_line = reader.readline().decode("latin-1")
        status = int(status_line.split()[1]) if len(status_line.split()) > 1 else 0
        answer_headers = {}
        while True:
            line = reader.readline().decode("latin-1").strip()
            if not line:
                break
            name, _, value = line.partition(":")
            answer_headers[name.strip().lower()] = value.strip()
        if status != 101:
            return status, ""
        expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
        if answer_headers.get("sec-websocket-accept") != expected:
            return status, "(wrong Sec-WebSocket-Accept)"
        payload = message.encode("utf-8")
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        connection.sendall(bytes([0x81, 0x80 | len(payload)]) + mask + masked)
        head = reader.read(2)
        length = head[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", reader.read(2))
        text = reader.read(length).decode("utf-8", "replace")
        connection.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        return status, text


def cookie_names(reply: Reply) -> dict:
    """Set-Cookie headers of a reply as name -> the whole header value."""

    return {value.split("=", 1)[0].strip(): value for value in reply.all("Set-Cookie")}


# ---- the test ---------------------------------------------------------------------------


class Failed(Exception):
    """A step the rest of the test depends on did not work."""


class Smoke:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.mode = args.mode
        self.admin = args.admin.rstrip("/")
        self.gateway = args.gateway.rstrip("/")
        self.portal = args.portal.rstrip("/")
        self.auth = {"Authorization": f"Bearer {args.token}"}
        self.failures: list[str] = []
        self.passed = 0
        self.state: dict = {"apps": {}, "jobs": []}

    # -- reporting ---------------------------------------------------------
    def check(self, name: str, condition: bool, seen: object = None) -> bool:
        if condition:
            self.passed += 1
            print(f"  ok    {name}", flush=True)
        else:
            self.failures.append(name)
            print(f"  FAIL  {name}", flush=True)
            if seen is not None:
                text = seen if isinstance(seen, str) else repr(seen)
                print("        saw: " + text[:1500].replace("\n", "\n             "), flush=True)
        return bool(condition)

    def section(self, title: str) -> None:
        print(f"\n== {title} ==", flush=True)

    # -- the manager API and the stub Portal -------------------------------
    def api(self, method: str, path: str, payload: object = None, *, timeout: float = 60.0) -> Reply:
        headers = dict(self.auth)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        return request(self.admin, method, path, headers=headers, body=body, timeout=timeout)

    def stub(self, method: str, path: str, payload: object = None) -> Reply:
        headers = dict(self.auth)
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        return request(self.portal, method, path, headers=headers, body=body)

    def page(self, path: str, *, headers: dict | None = None, method: str = "GET", payload: object = None) -> Reply:
        merged = dict(headers if headers is not None else NAVIGATE)
        body = None
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            merged["Content-Type"] = "application/json"
        return request(self.gateway, method, path, headers=merged, body=body)

    def callback(self, query: str) -> Reply:
        """The Portal's way back, as the browser that started the sign-in sends it."""
        return self.page(f"/_efp/callback?{query}&state={STATE}", headers={**NAVIGATE, "Cookie": f"{STATE_COOKIE}={STATE}"})

    def app_json(self, slug: str, path: str, payload: object = None, *, headers: dict | None = None):
        reply = self.page(f"/{slug}{path}", headers={"Accept": "application/json", **(headers or {})}, method="POST" if payload is not None else "GET", payload=payload)
        if reply.status != 200:
            raise Failed(f"{slug}{path} answered {reply!r}")
        return reply.json()

    def wait_ready(self) -> None:
        self.section("waiting for the host")
        deadline = time.time() + 180
        last = "nothing yet"
        while time.time() < deadline:
            try:
                host = self.api("GET", "/v1/host", timeout=5)
                health = request(self.gateway, "GET", "/_efp/health", timeout=5)
                last = f"manager {host.status}, gateway {health.status}"
                if host.status == 200 and health.status == 200 and health.text.strip() == "ok":
                    print(f"  ready: {last}")
                    return
            except OSError as exc:
                last = f"{exc.__class__.__name__}: {exc}"
            time.sleep(1)
        raise Failed(f"the host did not come up: {last}")

    def wait_page(self, path: str, *, contains: str | None = None, timeout: float = 240.0, headers: dict | None = None) -> Reply:
        """Ask the gateway until the app answers 200 (and with ``contains``)."""

        deadline = time.time() + timeout
        reply = None
        while time.time() < deadline:
            reply = self.page(path, headers=headers)
            if reply.status == 200 and (contains is None or contains in reply.text):
                return reply
            time.sleep(1)
        raise Failed(f"{path} never answered 200{' with ' + repr(contains) if contains else ''}; last: {reply!r}")

    def wait_state(self, app_id: str, states: tuple, timeout: float = 60.0) -> dict:
        deadline = time.time() + timeout
        status: dict = {}
        while time.time() < deadline:
            status = self.api("GET", f"/v1/apps/{app_id}").json()
            if status.get("state") in states:
                return status
            time.sleep(0.5)
        raise Failed(f"{app_id} never reached {states}: {status}")

    # -- apps ---------------------------------------------------------------
    def register(self, app_id: str, slug: str, *, visibility: str = "anyone", env: dict | None = None, enabled: bool = True, disabled_reason: str | None = None) -> dict:
        known = self.state["apps"].setdefault(app_id, {"slug": slug, "env": {"EFP_APP_TOKEN": "efpa_" + uuid.uuid4().hex + uuid.uuid4().hex[:11]}, "visitor_key": base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")})
        if env is not None:
            known["env"] = {**known["env"], **env}
        known.update(visibility=visibility, enabled=enabled, disabled_reason=disabled_reason)
        reply = self.api("PUT", f"/v1/apps/{app_id}", self.record(app_id))
        if reply.status != 200:
            raise Failed(f"PUT /v1/apps/{app_id} answered {reply!r}")
        return reply.json()

    def record(self, app_id: str) -> dict:
        known = self.state["apps"][app_id]
        return {
            "slug": known["slug"],
            "name": f"Smoke {known['slug']}",
            "owner_username": "alice",
            "visibility": known["visibility"],
            "enabled": known["enabled"],
            "disabled_reason": known["disabled_reason"],
            "visitor_key": known["visitor_key"],
            "env": known["env"],
            "limits": {"memory_mb": None, "data_mb": None},
        }

    def deploy(self, app_id: str, bundle: bytes, *, entry_dir: str = "", expect: str = "succeeded") -> tuple[str, dict]:
        sha = hashlib.sha1(bundle).hexdigest()
        spec = {"sha": sha, "ref": "smoke", "entry_dir": entry_dir, "requested_by": "ci"}
        body, content_type = encode_multipart({"spec": json.dumps(spec)}, {"bundle": ("bundle.tar.gz", bundle, "application/gzip")})
        reply = request(self.admin, "POST", f"/v1/apps/{app_id}/releases", headers={**self.auth, "Content-Type": content_type}, body=body, timeout=120)
        if reply.status != 202:
            raise Failed(f"POST releases of {app_id} answered {reply!r}")
        job_id = reply.json()["job_id"]
        deadline = time.time() + 900
        job: dict = {}
        while time.time() < deadline:
            job = self.api("GET", f"/v1/jobs/{job_id}").json()
            if job["status"] in ("succeeded", "failed"):
                break
            time.sleep(1)
        steps = ", ".join(f"{step['name']}={step['status']}" for step in job.get("steps", []))
        print(f"  deployment of {app_id} ({sha[:12]}): {job.get('status')} [{steps}]", flush=True)
        if job.get("status") != expect:
            log = self.api("GET", f"/v1/jobs/{job_id}/log").text
            print("  ---- job log ----\n" + log + "\n  ---- end of job log ----", flush=True)
            raise Failed(f"the deployment of {app_id} ended {job.get('status')}: {job.get('error')}")
        self.state["jobs"].append(job_id)
        self.state["apps"][app_id].setdefault("shas", []).append(sha)
        return sha, job

    # ====================================================================== first
    def first(self) -> None:
        mode = self.mode
        self.wait_ready()

        self.section("the host")
        host = self.api("GET", "/v1/host").json()
        print("  " + json.dumps(host))
        self.check("isolation mode is the one asked for", host.get("isolation_mode") == mode, host)
        self.check("the isolation works (no isolation_error)", "isolation_error" not in host, host.get("isolation_error"))
        if mode == "sandbox":
            self.check("a sandbox backend was chosen", bool(host.get("sandbox_backend")), host)
            if self.args.expect_backend:
                self.check(f"the sandbox backend is {self.args.expect_backend}", host.get("sandbox_backend") == self.args.expect_backend, host)
        else:
            self.check("hidden mode reports no sandbox backend", host.get("sandbox_backend") is None, host)
        if "isolation_error" in host:
            raise Failed("the host cannot isolate apps here; nothing below can work")

        self.section("reconciliation with the Portal at boot")
        seeded = None
        for _ in range(60):
            reply = self.api("GET", "/v1/apps/seed-0001")
            if reply.status == 200:
                seeded = reply.json()
                break
            time.sleep(1)
        self.check("an app the Portal lists is registered without being asked", seeded is not None and seeded["slug"] == "seeded", seeded)
        self.check("and nothing was deployed for it", seeded is not None and seeded["state"] == "undeployed" and seeded["release"] is None, seeded)

        self.section("the manager API")
        self.check("no token, no answer", request(self.admin, "GET", "/v1/apps").status == 401)
        wrong = request(self.admin, "GET", "/v1/apps", headers={"Authorization": "Bearer wrong"})
        self.check("a wrong token gets the error envelope", wrong.status == 401 and wrong.json()["error"]["code"] == "unauthorized", wrong)
        through = request(self.gateway, "GET", "/v1/host", headers={**self.auth, **FETCH})
        self.check("the manager API is not reachable through the gateway", through.status == 404, through)

        # ---- the starter templates --------------------------------------
        self.section("starter template: static")
        templates = Path(self.args.templates)
        self.register("tpl-static", "tpl-static")
        self.deploy("tpl-static", tar_bundle(templates / "static"))
        reply = self.wait_page("/tpl-static/")
        self.check("the static starter's page answers", "<" in reply.text and reply.status == 200, reply)
        upstream_type = self.api("GET", "/v1/apps/tpl-static").json()
        self.check("a static app is running without a process", upstream_type["state"] == "running" and upstream_type["type"] == "static", upstream_type)

        self.section("starter template: fastapi (from a GitHub-style tarball)")
        self.register("tpl-fastapi", "tpl-fastapi")
        _, job = self.deploy("tpl-fastapi", tar_bundle(templates / "fastapi", prefix="acme-starters-0123abc/templates/fastapi/"), entry_dir="templates/fastapi")
        self.check("every step of the deployment succeeded", all(step["status"] == "succeeded" for step in job["steps"]), job["steps"])
        reply = self.wait_page("/tpl-fastapi/")
        self.check("the FastAPI starter's page answers", reply.status == 200, reply)
        status = self.wait_state("tpl-fastapi", ("running",))
        self.check("the app reports running with a port and an uptime", status["port"] >= 20000 and status["type"] == "fastapi", status)

        # ---- an example app, the way a member would publish it ---------------
        self.section("example app: team-poll (visitor identity and storage, through the gateway)")
        self.register("ex-poll", "ex-poll")  # audience "anyone": a guest may look
        self.deploy("ex-poll", tar_bundle(Path(self.args.examples) / "team-poll"))
        self.wait_page("/ex-poll/", contains="Team poll")
        guest = self.app_json("ex-poll", "/api/poll")
        self.check("a guest sees the poll, without a vote", guest["signed_in"] is False and guest["total"] == 0 and guest["your_vote"] is None, guest)
        refused = self.page("/ex-poll/api/vote", headers=FETCH, method="POST", payload={"option": "hike"})
        self.check("a guest cannot vote", refused.status == 401, refused)
        entered = self.callback("code=ok.ex-poll.ex-poll.13&next=" + quote("/ex-poll/", safe=""))
        carol = next((value.split(";", 1)[0] for value in entered.all("Set-Cookie") if value.startswith(SESSION_COOKIE + "=")), "")
        self.check("a member signs in", entered.status == 302 and bool(carol), entered)
        voted = self.page("/ex-poll/api/vote", headers={**FETCH, "Cookie": carol}, method="POST", payload={"option": "hike"})
        self.check("and votes: the app trusts the visitor the gateway signed", voted.status == 200 and voted.json()["your_vote"] == "hike" and voted.json()["total"] == 1, voted)
        changed = self.page("/ex-poll/api/vote", headers={**FETCH, "Cookie": carol}, method="POST", payload={"option": "cook"})
        self.check("a second vote replaces the first", changed.status == 200 and changed.json()["your_vote"] == "cook" and changed.json()["total"] == 1, changed)
        counted = self.app_json("ex-poll", "/api/poll")
        self.check("everybody sees the count", {option["id"]: option["votes"] for option in counted["options"]} == {"hike": 0, "cook": 1, "escape": 0}, counted)

        # ---- a static site of our own: nested files, traversal, dot files ----
        self.section("static files behind the real nginx")
        site = {
            "efp-app.json": json.dumps({"name": "Smoke site", "type": "static", "entry": "public"}),
            "public/index.html": "<html><body>release one</body></html>\n",
            "public/sub/dir/nested.txt": "nested file\n",
            "public/.hidden.txt": "a dot file\n",
            "secret-outside.txt": "outside-the-site\n",
        }
        self.register("smoke-site", "smoke-site")
        site_one, _ = self.deploy("smoke-site", files_bundle(site))
        self.check("the front page", "release one" in self.wait_page("/smoke-site/", contains="release one").text)
        nested = self.page("/smoke-site/sub/dir/nested.txt")
        self.check("a nested file is served", nested.status == 200 and nested.text == "nested file\n", nested)
        self.check("a dot file is not", self.page("/smoke-site/.hidden.txt").status == 404)
        self.check("the host's own marker files are not", self.page("/smoke-site/.efp-ready").status == 404)
        for attempt in (
            "/smoke-site/../secret-outside.txt",
            "/smoke-site/%2e%2e/secret-outside.txt",
            "/smoke-site/..%2fsecret-outside.txt",
            "/smoke-site/sub/..%2f..%2f..%2fsecret-outside.txt",
            "/smoke-site/sub/dir/../../../secret-outside.txt",
            "/smoke-site//../secret-outside.txt",
        ):
            reply = self.page(attempt)
            self.check(f"no way out of the served directory: {attempt}", reply.status != 200 and "outside-the-site" not in reply.text, reply)
        redirect = self.page("/smoke-site")
        self.check("the address without the final slash redirects to the one with it", redirect.status in (301, 302) and (redirect.header("Location") or "").endswith("/smoke-site/"), redirect)

        # ---- two probe apps: can one reach the other? ---------------------
        self.section("isolation: a second app cannot reach the first")
        canary = "canary-" + uuid.uuid4().hex
        probe_dir = HERE / "probe_app"
        self.register("probe-aaaa", "probe-a", env={"VICTIM_SECRET": canary})
        self.register("probe-bbbb", "probe-b")
        probe_one = tar_bundle(probe_dir)
        victim_sha, _ = self.deploy("probe-aaaa", probe_one)
        attacker_sha, _ = self.deploy("probe-bbbb", probe_one)
        self.wait_page("/probe-a/", contains="version one")
        self.wait_page("/probe-b/", contains="version one")
        victim = self.app_json("probe-a", "/whoami")
        attacker = self.app_json("probe-b", "/whoami")
        print(f"  victim: uid {victim['uid']}, attacker: uid {attacker['uid']}")
        self.check("each app has its own unprivileged user", victim["uid"] >= 20000 and attacker["uid"] >= 20000 and victim["uid"] != attacker["uid"], (victim["uid"], attacker["uid"]))
        for label, who in (("victim", victim), ("attacker", attacker)):
            self.check(f"{label}: no_new_privs is set", who["no_new_privs"] == "1", who["no_new_privs"])
            self.check(f"{label}: no capabilities", set(who["cap_eff"]) == {"0"}, who["cap_eff"])
            self.check(f"{label}: no root group", 0 not in who["groups"] and who["gid"] != 0, (who["gid"], who["groups"]))
            leaked = [name for name in who["env_names"] if name.startswith("EFP_HOST_") or name.startswith("PIP_")]
            self.check(f"{label}: nothing of the manager's environment", leaked == [], leaked)
            self.check(f"{label}: a private home and tmp", who["home"].startswith(who["tmpdir"]) and who["app_id"] in who["tmpdir"], (who["home"], who["tmpdir"]))
        self.check("the app keeps the path prefix (type command)", victim["request_path"].startswith("/probe-a/whoami"), victim["request_path"])
        self.check("X-Forwarded-Prefix names the app", victim["forwarded_prefix"] == "/probe-a", victim["forwarded_prefix"])
        self.check("an anonymous visitor carries no visitor header", victim["visitor"] is None and victim["efp_headers"] == [], victim["efp_headers"])
        if mode == "sandbox":
            self.check("sandbox: the app is given a data directory, no storage socket", bool(victim["data_dir"]) and victim["storage_socket"] is None, victim)
        else:
            self.check("hidden: the app is given the storage socket, no data directory", bool(victim["storage_socket"]) and victim["data_dir"] is None, victim)

        store, runtime = self.args.store_dir, self.args.runtime_dir
        victim_dir = f"{store}/apps/probe-aaaa"
        own_code = f"{store}/apps/probe-bbbb/releases/{attacker_sha}/code" if mode == "sandbox" else f"{runtime}/probe-bbbb/releases/{attacker_sha}/code"

        # The victim keeps something.
        if mode == "sandbox":
            wrote = self.app_json("probe-a", "/write", {"path": f"{victim['data_dir']}/secret.txt", "content": canary})
            self.check("the victim writes its data directory", wrote["ok"], wrote)
        else:
            put = self.app_json("probe-a", "/storage", {"op": "put", "key": "secret.txt", "content": canary})
            self.check("the victim stores an object through the storage service", put.get("ok") is True, put)
            got = self.app_json("probe-a", "/storage", {"op": "get", "key": "secret.txt"})
            self.check("and reads it back", got.get("ok") is True and got.get("body") == canary, got)
            listed = self.app_json("probe-a", "/storage", {"op": "list", "prefix": ""})
            self.check("and sees it listed", [item["key"] for item in listed.get("keys", [])] == ["secret.txt"], listed)

        targets = [
            f"{store}/registry.json",
            f"{store}/keys",
            f"{store}/keys/session.key",
            f"{store}/jobs",
            f"{victim_dir}/env.json",
            f"{victim_dir}/data",
            f"{victim_dir}/data/secret.txt",
            f"{victim_dir}/data/objects/secret.txt",
            f"{victim_dir}/logs/app.log",
            f"{victim_dir}/releases/{victim_sha}/code/server.py",
            f"{victim_dir}/releases/{victim_sha}/manifest.json",
            f"{runtime}/probe-aaaa",
            f"{runtime}/probe-aaaa/tmp",
            f"{runtime}/probe-aaaa/releases/{victim_sha}/code/server.py",
            "/run/efp-host/launch",
            "/run/efp-host/gateway.secret",
            "/etc/nginx/nginx.conf",
            "/proc/1/environ",
        ]
        results = self.app_json("probe-b", "/read", {"paths": targets + [f"{own_code}/server.py", f"{store}/apps"]})["results"]
        by_path = {item["path"]: item for item in results}
        self.check("the attacker reads its own code (the probe tells the truth)", by_path[f"{own_code}/server.py"]["ok"], by_path[f"{own_code}/server.py"])
        for target in targets:
            item = by_path[target]
            self.check(f"the attacker cannot read {target}", not item["ok"], item)
        apps_listing = by_path[f"{store}/apps"]
        self.check("the attacker cannot see which other apps exist", not apps_listing["ok"] or "probe-aaaa" not in (apps_listing["sample"] or []), apps_listing)

        # Nothing but its own data and tmp can be written. The directories
        # above its own and the root of its view are part of that: in a
        # sandbox they are not the real ones, but they are not scratch space.
        for target in (
            f"{victim_dir}/data/planted.txt",
            f"{victim_dir}/env.json",
            f"{own_code}/planted.py",
            f"{store}/planted.txt",
            f"{store}/apps/planted.txt",
            f"{store}/apps/probe-bbbb/planted.txt",
            "/etc/planted",
            "/var/planted",
            "/run/planted",
            "/dev/planted",
            "/planted",
        ):
            wrote = self.app_json("probe-b", "/write", {"path": target})
            self.check(f"the attacker cannot write {target}", not wrote["ok"], wrote)
        shared = self.app_json("probe-b", "/write", {"path": "/dev/shm/efp-smoke.txt"})
        self.check("shared memory (/dev/shm) is usable", shared["ok"], shared)
        own_tmp = self.app_json("probe-b", "/write", {"path": f"{attacker['tmpdir']}/scratch.txt"})
        self.check("and so is the app's private tmp", own_tmp["ok"], own_tmp)

        # The supervisor's control socket would let an app start and stop the others.
        sockets = self.app_json("probe-b", "/connect", {"paths": ["/run/efp-host/supervisor.sock"]})["results"]
        self.check("the attacker cannot connect to the supervisor", not sockets[0]["ok"], sockets[0])

        scan = self.app_json("probe-b", "/environ-scan", {"needles": [canary, self.args.token, self.state["apps"]["probe-aaaa"]["env"]["EFP_APP_TOKEN"]]})
        self.check("no other process's environment gives the attacker a secret", scan["found"] == [], scan)

        if mode == "sandbox":
            own = self.app_json("probe-b", "/write", {"path": f"{attacker['data_dir']}/mine.txt", "content": "mine"})
            self.check("the attacker's own data directory works", own["ok"], own)
        else:
            stolen = self.app_json("probe-b", "/storage", {"op": "get", "key": "secret.txt"})
            self.check("the storage service gives the attacker its own (empty) store", stolen.get("ok") is False and stolen["error"]["code"] == "not_found", stolen)
            escape = self.app_json("probe-b", "/storage", {"op": "get", "key": "../../probe-aaaa/data/objects/secret.txt"})
            self.check("and no key leads out of it", escape.get("ok") is False and escape["error"]["code"] == "invalid_key", escape)
            listed = self.app_json("probe-b", "/storage", {"op": "list", "prefix": ""})
            self.check("and its listing is empty", listed.get("ok") is True and listed["keys"] == [], listed)

        # ---- what only a real nginx proves -------------------------------
        self.section("the gateway: headers, cookies, WebSocket")
        forged = self.page("/probe-b/whoami", headers={**FETCH, "X-EFP-Visitor": "v1.forged.forged"})
        self.check("a client that sends X-EFP-Visitor is answered 400", forged.status == 400, forged)
        cookies = self.page("/probe-b/cookies", headers=FETCH)
        names = cookie_names(cookies)
        self.check("the app's cookies arrive, the one with Domain= and the reserved name are dropped", sorted(names) == ["good", "scoped"], cookies.all("Set-Cookie"))
        self.check("their path is the app's", all("Path=/probe-b/" in value for value in names.values()) and not any("domain=" in value.lower() for value in names.values()), names)
        self.check("Service-Worker-Allowed is removed", cookies.header("Service-Worker-Allowed") is None, cookies.headers)
        hostwide = [name for name in ("NEL", "Report-To", "Reporting-Endpoints", "Clear-Site-Data") if cookies.header(name) is not None]
        self.check("headers that act on the whole hostname are removed", hostwide == [], hostwide)
        accel = self.page("/probe-b/accel", headers=FETCH)
        leaked = [name for name, _value in accel.headers if name.lower().startswith(("x-efp-", "x-accel-"))]
        self.check(
            "an app's X-Accel-Redirect is not followed: its own answer arrives, nothing of the access check",
            accel.status == 200 and accel.body.strip().startswith(b"{") and accel.json().get("own") is True and leaked == [],
            (accel, leaked),
        )
        self.check(
            "the gateway's security headers are added",
            cookies.header("X-Content-Type-Options") == "nosniff"
            and cookies.header("X-Robots-Tag") == "noindex"
            and cookies.header("Referrer-Policy") == "same-origin"
            and "camera=()" in (cookies.header("Permissions-Policy") or ""),
            cookies.headers,
        )
        try:
            status, echoed = websocket_echo(self.gateway, "/probe-b/ws", "hello through nginx")
        except OSError as exc:
            status, echoed = 0, f"{exc.__class__.__name__}: {exc}"
        self.check("a WebSocket goes through the gateway", status == 101 and echoed == "echo:hello through nginx", (status, echoed))

        self.section("the gateway: sign-in and refusals")
        self.register("probe-aaaa", "probe-a", visibility="platform")
        nav = self.page("/probe-a/whoami?x=1")
        location = nav.header("Location") or ""
        self.check("a page request without a session is sent to the Portal's launch page", nav.status == 302 and location.startswith(f"{self.args.portal_public}/apps/launch/probe-a?next="), nav)
        self.check("and the way back is kept", parse_qs(urlsplit(location).query).get("next") == ["/probe-a/whoami?x=1"], location)
        started = (parse_qs(urlsplit(location).query).get("state") or [""])[0]
        held = next((value.split(";", 1)[0] for value in nav.all("Set-Cookie") if value.startswith(STATE_COOKIE + "=")), "")
        self.check("the sign-in is bound to this browser: one state in the address and in a cookie", len(started) >= 22 and held == f"{STATE_COOKIE}={started}", (location, nav.all("Set-Cookie")))
        other_tab = self.page("/probe-a/other", headers={**NAVIGATE, "Cookie": held})
        self.check("a second tab of the same browser joins that sign-in", (parse_qs(urlsplit(other_tab.header("Location") or "").query).get("state") or [""])[0] == started, other_tab)
        fetch = self.page("/probe-a/whoami", headers=FETCH)
        self.check("a script's request without a session gets 401 as JSON", fetch.status == 401 and fetch.json()["error"]["code"] == "sign_in_required", fetch)

        bad = self.callback("code=nonsense&next=/probe-a/")
        self.check("a bad sign-in code is answered 400 with a page", bad.status == 400 and "<" in bad.text and bad.header("Set-Cookie") is None, bad)
        planted = self.page("/_efp/callback?code=ok.probe-aaaa.probe-a.12&next=" + quote("/probe-a/whoami", safe=""))
        self.check("a code that arrives in a browser that did not start the sign-in is not redeemed", planted.status == 400 and planted.header("Set-Cookie") is None, planted)
        foreign = self.page("/_efp/callback?code=ok.probe-aaaa.probe-a.12&next=/probe-a/&state=" + STATE, headers={**NAVIGATE, "Cookie": f"{STATE_COOKIE}=another-state-of-another-sign-in"})
        self.check("nor with the state of another sign-in", foreign.status == 400 and foreign.header("Set-Cookie") is None, foreign)
        back = self.callback("code=ok.probe-aaaa.probe-a.12&next=" + quote("/probe-a/whoami", safe=""))
        session = next((value.split(";", 1)[0] for value in back.all("Set-Cookie") if value.startswith(SESSION_COOKIE + "=")), "")
        self.check("a good code starts a session and goes back into the app (the two refusals did not spend it)", back.status == 302 and back.header("Location") == "/probe-a/whoami" and bool(session), back)
        flags = next((value.lower() for value in back.all("Set-Cookie") if value.startswith(SESSION_COOKIE + "=")), "")
        self.check("the session cookie is HttpOnly, Secure, SameSite=Lax, Path=/", all(flag in flags for flag in ("httponly", "secure", "samesite=lax", "path=/")), flags)
        self.check("and the state of the finished sign-in is cleared", any(value.startswith(STATE_COOKIE + "=;") and "max-age=0" in value.lower() for value in back.all("Set-Cookie")), back.all("Set-Cookie"))
        again = self.callback("code=ok.probe-aaaa.probe-a.12&next=/probe-a/")
        self.check("a code works once", again.status == 400, again)
        elsewhere = self.callback("code=ok.probe-aaaa.probe-a.13&next=" + quote("//evil.example.com/", safe=""))
        self.check("the callback never redirects out of the app", elsewhere.status == 302 and elsewhere.header("Location") == "/probe-a/", elsewhere)

        signed = self.page("/probe-a/whoami", headers={**FETCH, "Cookie": f"mine=1; {session}"})
        self.check("with the session the app answers", signed.status == 200, signed)
        if signed.status == 200:
            seen = signed.json()
            self.check("the app gets the signed visitor header", [name.lower() for name in seen["efp_headers"]] == ["x-efp-visitor"], seen["efp_headers"])
            claims = seen["visitor"] or {}
            self.check("which names the visitor and the app", claims.get("username") == "bob" and claims.get("sub") == 12 and claims.get("app") == "probe-aaaa" and claims.get("exp", 0) > time.time() - 5, claims)
            self.check("the app sees the visitor's own cookies but not the gateway's session", "mine=1" in (seen["cookie"] or "") and SESSION_COOKIE not in (seen["cookie"] or ""), seen["cookie"])

        self.stub("PUT", "/_stub/deny", {"pairs": [["probe-bbbb", 12]]})
        self.register("probe-bbbb", "probe-b", visibility="platform")
        denied_page = self.page("/probe-b/", headers={**NAVIGATE, "Cookie": session})
        self.check("a signed-in visitor without access gets the 403 page", denied_page.status == 403 and "<" in denied_page.text, denied_page)
        self.check("which names the owner", "alice" in denied_page.text, denied_page.text[-800:])
        denied_json = self.page("/probe-b/whoami", headers={**FETCH, "Cookie": session})
        self.check("and a script gets 403 no_access as JSON", denied_json.status == 403 and denied_json.json()["error"]["code"] == "no_access", denied_json)
        self.stub("PUT", "/_stub/deny", {"pairs": []})
        self.register("probe-bbbb", "probe-b", visibility="anyone")

        self.register("probe-bbbb", "probe-b", enabled=False, disabled_reason="taken down for the smoke test")
        status = self.api("GET", "/v1/apps/probe-bbbb").json()
        self.check("a disabled app is stopped and says why", status["state"] == "disabled" and status["reason"] == "taken down for the smoke test", status)
        off_page = self.page("/probe-b/")
        self.check("its page is 403 and states the reason", off_page.status == 403 and "taken down for the smoke test" in off_page.text, off_page)
        off_json = self.page("/probe-b/whoami", headers=FETCH)
        self.check("a script gets 403 app_disabled", off_json.status == 403 and off_json.json()["error"]["code"] == "app_disabled", off_json)
        self.register("probe-bbbb", "probe-b")
        self.wait_page("/probe-b/", contains="version one")

        missing_page = self.page("/no-such-app/")
        self.check("an unknown app is a 404 page", missing_page.status == 404 and "<" in missing_page.text, missing_page)
        missing_json = self.page("/no-such-app/x", headers=FETCH)
        self.check("or 404 app_not_found for a script", missing_json.status == 404 and missing_json.json()["error"]["code"] == "app_not_found", missing_json)
        self.register("probe-aaaa", "probe-a", visibility="anyone")

        # ---- the app center ---------------------------------------------------
        self.section("the app center at the root of the address")
        root = self.page("/")
        self.check("without a session the root sends the visitor to sign in", root.status == 302 and (root.header("Location") or "").startswith(f"{self.args.portal_public}/apps/launch?state="), root)
        script = self.page("/", headers=FETCH)
        self.check("and a script gets 401 as JSON", script.status == 401 and script.json()["error"]["code"] == "sign_in_required", script)
        owner = {"username": "alice", "display_name": "Alice"}
        cards = [
            {"id": "probe-aaaa", "slug": "probe-a", "name": "Probe A", "description": "The first probe.", "type": "command", "visibility": "platform", "mine": False, "owner": owner, "icon": "smoke1", "last_visited_at": None},
            {"id": "smoke-site", "slug": "smoke-site", "name": "Smoke <site>", "description": "", "type": "static", "visibility": "private", "mine": True, "owner": owner, "icon": None, "last_visited_at": None},
        ]
        self.stub("PUT", "/_stub/catalog", {"apps": cards, "icons": ["probe-aaaa"]})
        entered = self.callback("code=center.12&next=%2F")
        member = next((value.split(";", 1)[0] for value in entered.all("Set-Cookie") if value.startswith(SESSION_COOKIE + "=")), "")
        self.check("the center's sign-in comes back to the list with a session", entered.status == 302 and entered.header("Location") == "/?signed_in=1" and bool(member), entered)
        listed = self.page("/?signed_in=1", headers={**NAVIGATE, "Cookie": member})
        self.check("the list has a card for each app the Portal names", listed.status == 200 and 'href="/probe-a/"' in listed.text and 'href="/smoke-site/"' in listed.text, listed)
        self.check("what the Portal sends is escaped", "Smoke &lt;site&gt;" in listed.text and "<site>" not in listed.text, listed.text[-1500:])
        self.check("the page runs its one script under a nonce", "script-src 'nonce-" in (listed.header("Content-Security-Policy") or ""), listed.headers)
        image = self.page("/_efp/icon/probe-a?v=smoke1", headers={"Cookie": member})
        self.check("an app's icon reaches the member through the gateway", image.status == 200 and image.header("Content-Type") == "image/png" and image.body.startswith(b"\x89PNG"), image)
        self.check("and nobody without a session", self.page("/_efp/icon/probe-a?v=smoke1").status == 404)
        lost = self.page("/?signed_in=1")
        self.check("a browser that kept no cookie is told so, not sent round again", lost.status == 400 and "<" in lost.text and lost.header("Location") is None, lost)
        counts = self.stub("GET", "/_stub/state").json()["calls"]
        self.check("the Portal was asked for the list and for the icon", counts.get("catalog", 0) >= 1 and counts.get("icon", 0) == 1, counts)

        # ---- redeploy and rollback ----------------------------------------
        self.section("redeploy and rollback")
        site_two, _ = self.deploy("smoke-site", files_bundle({**site, "public/index.html": "<html><body>release two</body></html>\n"}))
        self.check("static: the new release is served", "release two" in self.wait_page("/smoke-site/", contains="release two").text)
        status = self.api("GET", "/v1/apps/smoke-site").json()
        self.check("static: current and previous release", status["release"]["sha"] == site_two and status["previous_release"]["sha"] == site_one, status)
        rolled = self.api("POST", "/v1/apps/smoke-site/activate", {"sha": site_one})
        self.check("static: rollback is accepted", rolled.status == 200 and rolled.json()["release"]["sha"] == site_one, rolled)
        self.check("static: the old release is served again", "release one" in self.wait_page("/smoke-site/", contains="release one").text)
        gone = self.api("POST", "/v1/apps/smoke-site/activate", {"sha": "f" * 40})
        self.check("a release the host does not keep is 404 release_not_found", gone.status == 404 and gone.json()["error"]["code"] == "release_not_found", gone)

        probe_two = tar_bundle(probe_dir, overrides={"version.txt": "two\n"})
        probe_two_sha, _ = self.deploy("probe-bbbb", probe_two)
        self.check("process app: the new release answers", "version two" in self.wait_page("/probe-b/", contains="version two").text)
        broken = tar_bundle(probe_dir, overrides={"server.py": "raise SystemExit('this release is broken')\n", "version.txt": "broken\n"})
        try:
            self.deploy("probe-bbbb", broken, expect="failed")
            failed_job = self.api("GET", f"/v1/jobs/{self.state['jobs'][-1]}").json()
            self.check("a release that does not start fails its deployment", failed_job["error"]["step"] in ("start", "health"), failed_job["error"])
            self.check("and the reason is in its log", "this release is broken" in self.api("GET", f"/v1/jobs/{failed_job['id']}/log").text)
        except Failed as exc:
            self.check("a release that does not start fails its deployment", False, str(exc))
        still = self.page("/probe-b/")
        self.check("the running release was not touched by the failed deployment", still.status == 200 and "version two" in still.text, still)
        status = self.api("GET", "/v1/apps/probe-bbbb").json()
        self.check("and stays current", status["release"]["sha"] == probe_two_sha and status["previous_release"]["sha"] == attacker_sha and status["state"] == "running", status)
        self.api("POST", "/v1/apps/probe-bbbb/activate", {"sha": attacker_sha})
        self.check("process app: rollback brings the old release back", "version one" in self.wait_page("/probe-b/", contains="version one").text)

        # ---- life cycle ----------------------------------------------------
        self.section("stop, start on a visit, environment, logs")
        stopped = self.api("POST", "/v1/apps/probe-aaaa/stop").json()
        self.check("stop", stopped["state"] == "stopped" and stopped["reason"] == "manual", stopped)
        first_visit = self.page("/probe-a/")
        self.check("the first visit to a stopped app is the 'starting' page", first_visit.status == 503 and first_visit.header("Retry-After") is not None and "<" in first_visit.text, first_visit)
        self.wait_page("/probe-a/", contains="version one")
        self.check("and the visit started it", self.api("GET", "/v1/apps/probe-aaaa").json()["state"] == "running")
        script_visit_app = "probe-aaaa"
        self.api("POST", f"/v1/apps/{script_visit_app}/stop")
        script_visit = self.page("/probe-a/whoami", headers=FETCH)
        self.check("a script's request to a stopped app gets 503 app_starting", script_visit.status == 503 and script_visit.json()["error"]["code"] == "app_starting", script_visit)
        self.wait_page("/probe-a/", contains="version one")

        new_env = {**self.state["apps"]["probe-aaaa"]["env"], "ADDED_LATER": "yes"}
        self.state["apps"]["probe-aaaa"]["env"] = new_env
        changed = self.api("PUT", "/v1/apps/probe-aaaa/env", {"env": new_env})
        self.check("a changed environment is accepted", changed.status == 200, changed)
        deadline = time.time() + 60
        names: list = []
        while time.time() < deadline:
            reply = self.page("/probe-a/whoami", headers=FETCH)
            names = reply.json()["env_names"] if reply.status == 200 else []
            if "ADDED_LATER" in names:
                break
            time.sleep(1)
        self.check("and the app was restarted with it", "ADDED_LATER" in names, names)

        logs = self.api("GET", "/v1/apps/probe-aaaa/logs?tail=100").json()
        self.check("the app's log can be read", any("probe app version one" in line for line in logs["lines"]), logs["lines"][-10:])
        self.check("and never shows the app's token", not any(self.state["apps"]["probe-aaaa"]["env"]["EFP_APP_TOKEN"] in line for line in logs["lines"]))

        # ---- what the Portal was told --------------------------------------
        self.section("events and metrics at the Portal")
        portal = {}
        deadline = time.time() + 60
        while time.time() < deadline:
            portal = self.stub("GET", "/_stub/state").json()
            traffic = (portal.get("last_metrics") or {}).get("traffic") or []
            if any(item.get("app_id") == "probe-bbbb" and item.get("requests", 0) > 0 for item in traffic):
                break
            time.sleep(2)
        kinds = sorted({event.get("kind") for event in portal.get("events", [])})
        print(f"  events: {kinds}, metrics posts: {portal.get('metrics_posts')}, calls: {portal.get('calls')}")
        self.check("the Portal was told that apps started", "started" in kinds, kinds)
        self.check("every call to the Portal carried the host token", portal.get("unauthorized") == 0, portal.get("unauthorized"))
        metrics = portal.get("last_metrics") or {}
        traffic = {item.get("app_id"): item for item in metrics.get("traffic") or []}
        self.check("nginx's access log reached the manager: the metrics count the app's requests", traffic.get("probe-bbbb", {}).get("requests", 0) > 0, metrics.get("traffic"))
        self.check("the metrics carry the status of every app and the host", len(metrics.get("apps") or []) >= 5 and (metrics.get("host") or {}).get("isolation_mode") == mode, {"apps": len(metrics.get("apps") or []), "host": metrics.get("host")})

        # ---- hand over to the second phase ----------------------------------
        self.state["canary"] = canary
        self.state["victim_data_dir"] = victim.get("data_dir")
        desired = []
        for app_id, known in self.state["apps"].items():
            status = self.api("GET", f"/v1/apps/{app_id}").json()
            desired.append({**self.record(app_id), "id": app_id, "current_sha": (status.get("release") or {}).get("sha")})
        # The seeded app is left out on purpose: the next boot must disable it.
        reply = self.stub("PUT", "/_stub/apps", {"apps": desired})
        self.check("the Portal's desired state is ready for the next boot", reply.status == 200, reply)

    # ====================================================================== after-recreate
    def after_recreate(self) -> None:
        mode = self.mode
        self.wait_ready()
        self.section("a new container on the same volume")
        host = self.api("GET", "/v1/host").json()
        self.check("the isolation works again", "isolation_error" not in host and host.get("isolation_mode") == mode, host)
        apps = {item["id"]: item for item in self.api("GET", "/v1/apps").json()["apps"]}
        print("  " + ", ".join(f"{app_id}={item['state']}" for app_id, item in sorted(apps.items())))
        for app_id in self.state["apps"]:
            self.check(f"{app_id} is still registered with its release", app_id in apps and apps[app_id]["release"] is not None, apps.get(app_id))
        processes = [apps[app_id]["state"] for app_id in ("tpl-fastapi", "probe-aaaa", "probe-bbbb") if app_id in apps]
        self.check("no app is started at boot", all(state == "stopped" for state in processes), processes)

        seeded = None
        for _ in range(60):
            seeded = self.api("GET", "/v1/apps/seed-0001").json()
            if seeded.get("state") == "disabled":
                break
            time.sleep(1)
        self.check("an app the Portal no longer lists is disabled, not deleted", seeded["state"] == "disabled" and seeded["reason"] == "removed from the Portal", seeded)
        still_enabled = [app_id for app_id in self.state["apps"] if self.api("GET", f"/v1/apps/{app_id}").json()["state"] == "disabled"]
        self.check("the apps the Portal lists stay enabled", still_enabled == [], still_enabled)

        self.section("everything answers again, started by the visit")
        self.check("static starter", self.wait_page("/tpl-static/").status == 200)
        self.check("static site, at the release rolled back to", "release one" in self.wait_page("/smoke-site/", contains="release one").text)
        self.check("nested static file", self.page("/smoke-site/sub/dir/nested.txt").text == "nested file\n")
        self.check("FastAPI starter", self.wait_page("/tpl-fastapi/").status == 200)
        self.wait_page("/ex-poll/", contains="Team poll")
        poll = self.app_json("ex-poll", "/api/poll")
        self.check("example app: the vote it stored is still counted", poll["total"] == 1 and poll["signed_in"] is False, poll)
        self.check("probe app, at the release rolled back to", "version one" in self.wait_page("/probe-b/", contains="version one").text)
        self.wait_page("/probe-a/", contains="version one")

        self.section("data and history survived")
        canary = self.state["canary"]
        if mode == "sandbox":
            kept = self.app_json("probe-a", "/read", {"paths": [f"{self.state['victim_data_dir']}/secret.txt"]})["results"][0]
            self.check("the app's data directory still holds its file", kept["ok"] and kept["sample"] == canary, kept)
        else:
            kept = self.app_json("probe-a", "/storage", {"op": "get", "key": "secret.txt"})
            self.check("the app's stored object is still there", kept.get("ok") is True and kept.get("body") == canary, kept)
            logs = self.api("GET", "/v1/apps/probe-aaaa/logs?tail=400").json()["lines"]
            self.check("the release was rebuilt on the local disk from the volume", any("restoring release" in line for line in logs) and any("release restored" in line for line in logs), logs[-15:])
        env_names = self.app_json("probe-a", "/whoami")["env_names"]
        self.check("the app's environment survived", "ADDED_LATER" in env_names and "VICTIM_SECRET" in env_names, env_names)
        for job_id in self.state["jobs"][:2]:
            job = self.api("GET", f"/v1/jobs/{job_id}")
            self.check(f"job {job_id[:8]} can still be read", job.status == 200 and job.json()["status"] in ("succeeded", "failed") and job.json()["log_size"] > 0, job)

    # ----------------------------------------------------------------------
    def run(self) -> int:
        state_file = Path(self.args.state)
        try:
            if self.args.phase == "first":
                self.first()
                state_file.write_text(json.dumps(self.state), encoding="utf-8")
            else:
                self.state = json.loads(state_file.read_text(encoding="utf-8"))
                self.after_recreate()
        except Failed as exc:
            self.failures.append(str(exc))
            print(f"\n  STOPPED: {exc}", flush=True)
        except Exception as exc:  # noqa: BLE001 - say what broke, then fail
            import traceback

            traceback.print_exc()
            self.failures.append(f"{exc.__class__.__name__}: {exc}")
        print(f"\n{self.args.phase} ({self.mode}): {self.passed} checks passed, {len(self.failures)} failed", flush=True)
        for failure in self.failures:
            print(f"  FAILED: {failure}", flush=True)
        return 1 if self.failures else 0


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--phase", choices=["first", "after-recreate"], required=True)
    parser.add_argument("--mode", choices=["sandbox", "hidden"], required=True)
    parser.add_argument("--admin", default="http://127.0.0.1:9000")
    parser.add_argument("--gateway", default="http://127.0.0.1:8080")
    parser.add_argument("--portal", default="http://127.0.0.1:18000", help="the stub Portal as this script reaches it")
    parser.add_argument("--portal-public", default="http://portal.test", help="EFP_PORTAL_PUBLIC_URL of the host")
    parser.add_argument("--token", default=os.environ.get("EFP_HOST_API_TOKEN", "ci-token"))
    parser.add_argument("--templates", default=str(HERE.parent / "templates"))
    parser.add_argument("--examples", default=str(HERE.parent / "examples"))
    parser.add_argument("--state", default="smoke-state.json")
    parser.add_argument("--store-dir", default="/var/lib/efp-host/store")
    parser.add_argument("--runtime-dir", default="/srv/efp-apps")
    parser.add_argument("--expect-backend", default="", help="sandbox mode: the backend the host must have chosen")
    return Smoke(parser.parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
