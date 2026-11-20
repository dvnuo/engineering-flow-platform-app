"""examples/env-status: a plain Python web server run as a "command" app.

There is no framework to hand the app to a test client, so the server is
started for real on a free local port, in a thread, and asked over HTTP: with
and without a path prefix, as a guest and as a member. A second local server
stands in for the test environments it checks. One test runs the manifest's
command as a process of its own, as the host does. The arithmetic of the page
(``static/spark.js``) is run under node when node is on the PATH.
"""

import ast
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from efp_app import errors, storage
from conftest import EXAMPLES, SDK_ROOT
from example_support import load_module, signed
from test_visitor import APP_ID, VISITOR_KEY

HERE = EXAMPLES / "env-status"
EVE = {"sub": 13, "username": "eve", "name": "Eve"}

pytestmark = pytest.mark.skipif(not HERE.is_dir(), reason="needs a checkout of the repository (examples/)")


# --- The test environments: one local server that answers what a test needs ------------------------


class Environment(BaseHTTPRequestHandler):
    def do_GET(self):
        server = self.server
        with server.guard:
            server.seen.append(self.path)
            server.busy += 1
            server.most_busy = max(server.most_busy, server.busy)
        try:
            self.reply()
        finally:
            with server.guard:
                server.busy -= 1

    def reply(self):
        port = self.server.server_address[1]
        if self.path == "/ok":
            self.send(200, b"ok")
        elif self.path == "/down":
            self.send(503, b"maintenance")
        elif self.path == "/teapot":
            self.send(418, b"short and stout")
        elif self.path == "/json":
            self.send(200, b'{"status":"UP"}')
        elif self.path == "/json-down":
            self.send(200, b'{"status":"DOWN"}')
        elif self.path == "/late-text":
            self.send(200, b"x" * 5000 + b'{"status":"UP"}')  # the text is there, beyond what is read
        elif self.path.startswith("/wait"):
            time.sleep(0.3)
            self.send(200, b"ok")
        elif self.path == "/slow":
            time.sleep(self.server.slow)
            self.send(200, b"ok")
        elif self.path == "/here":
            self.send(302, b"", {"Location": "/ok"})
        elif self.path == "/away":
            self.send(302, b"", {"Location": "http://localhost:%d/secret" % port})  # the same machine under another name
        elif self.path == "/loop":
            self.send(302, b"", {"Location": "/loop"})
        else:
            self.send(404, b"no such page")

    def send(self, status, body, headers=None):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class Environments(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), Environment)
        self.guard = threading.Lock()
        self.seen = []  # every path that was asked, in order
        self.busy = self.most_busy = 0  # requests being answered at the same time
        self.slow = 5.0  # seconds /slow takes

    def handle_error(self, request, client_address):
        pass  # a client that hung up before the answer: what the timeout test wants

    def url(self, path):
        return "http://127.0.0.1:%d%s" % (self.server_address[1], path)

    def target(self, target_id, path, **more):
        """An entry as ``load_targets`` makes it, for a path of this server."""
        entry = {"id": target_id, "name": target_id.title(), "group": "Test", "url": self.url(path), "expect": 200, "contains": None}
        return dict(entry, **more)


@pytest.fixture
def environments():
    server = Environments()
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


# --- The app -------------------------------------------------------------------------------------


@pytest.fixture
def status(deployed, monkeypatch):
    """server.py as on a host: the prefix is /example."""
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")  # the stand-in is on this machine: no proxy in between
    return load_module("example_env_status", HERE / "server.py")


@pytest.fixture
def clock(status, monkeypatch):
    hands = {"now": datetime(2026, 11, 20, 9, 0, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(status, "now", lambda: hands["now"])
    return hands


class Running:
    """The app's server on a free port, in a thread, and a way to ask it."""

    def __init__(self, module, base_path=None):
        self.server = module.make_server(0, base_path)
        self.port = self.server.server_address[1]

    def __enter__(self):
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()
        return self

    def __exit__(self, *exc_info):
        self.server.shutdown()
        self.server.server_close()

    def ask(self, method, path, headers=None, body=None):
        """``(status, body, headers)``; a JSON body is decoded, anything else is text."""
        return ask(self.port, method, path, headers, body)

    def get(self, path, headers=None):
        return self.ask("GET", path, headers)

    def check(self, headers=None, path="/example/api/check", body=None):
        return self.ask("POST", path, signed() if headers is None else headers, body)


def ask(port, method, path, headers=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        answer = connection.getresponse()
        raw = answer.read()
        text = raw.decode("utf-8")
        is_json = "json" in (answer.getheader("Content-Type") or "") and raw
        return answer.status, (json.loads(text) if is_json else text), dict(answer.getheaders())
    finally:
        connection.close()


def by_id(overview):
    return {target["id"]: target for target in overview["targets"]}


# --- A command app -------------------------------------------------------------------------------


def test_env_status_is_a_command_app_on_the_standard_library():
    manifest = json.loads((HERE / "efp-app.json").read_text(encoding="utf-8"))
    assert manifest["type"] == "command" and manifest["command"] == "python server.py" and manifest["health_path"] == "/healthz"
    assert "entry" not in manifest and "requirements" not in manifest and not (HERE / "requirements.txt").exists()
    assert [path.name for path in HERE.glob("*.py")] == ["server.py"]

    imported = set()
    for node in ast.walk(ast.parse((HERE / "server.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert "efp_app" in imported
    if hasattr(sys, "stdlib_module_names"):  # Python 3.10 and later can say what the standard library is
        assert imported - {"efp_app"} <= set(sys.stdlib_module_names)  # nothing to install

    page = (HERE / "static" / "index.html").read_text(encoding="utf-8")
    assert 'href="static/style.css"' in page and '<script type="module" src="static/app.js">' in page
    script = (HERE / "static" / "app.js").read_text(encoding="utf-8")
    assert 'ask("api/status")' in script and 'ask("api/check", "POST")' in script and 'from "./spark.js"' in script
    assert "setTimeout" not in script and "body:" not in script  # no timer, and a check that carries nothing


def test_env_status_checks_only_what_its_file_lists(status, tmp_path):
    shipped = status.load_targets(HERE / "targets.json")
    assert len(shipped) >= 4 and len({target["id"] for target in shipped}) == len(shipped)
    for target in shipped:
        host = status.urlsplit(target["url"]).hostname
        assert target["url"].startswith("https://") and host.endswith(".example.com")  # documentation hosts, to be replaced
        assert target["name"] and target["group"] and target["expect"] == 200
    assert status.TARGETS == shipped

    def load(*entries):
        path = tmp_path / "targets.json"
        path.write_text(json.dumps({"targets": list(entries)}), encoding="utf-8")
        return status.load_targets(path)

    good = {"id": "qa", "url": "http://qa.example.com:8080/health"}
    assert load(good) == [{"id": "qa", "name": "qa", "group": "Environments", "url": good["url"], "expect": 200, "contains": None}]
    assert load(dict(good, expect=204, contains="UP", name="QA", group="Test"))[0]["expect"] == 204
    refused = (
        [good, good],  # the id names the stored results: it must be one environment's
        [dict(good, id="QA web")],
        [dict(good, id="../team")],
        [dict(good, id="")],
        [{"url": good["url"]}],
        [dict(good, url="ftp://qa.example.com/health")],
        [dict(good, url="file:///etc/passwd")],
        [dict(good, url="qa.example.com/health")],
        [dict(good, url="https://ci:secret@qa.example.com/health")],  # a password in a file that is shown to members
        [dict(good, url=None)],
        [dict(good, expect=99)],
        [dict(good, expect="200")],
        [dict(good, expect=True)],
        [dict(good, contains="")],
        [dict(good, contains=5)],
    )
    for entries in refused:
        with pytest.raises(ValueError):
            load(*entries)


def test_env_status_takes_the_prefix_off_every_path(status):
    # A command app gets /{slug} in every request (EFP_APP_BASE_PATH is /example here).
    with Running(status) as app:
        code, page, headers = app.get("/example/")
        assert code == 200 and headers["Content-Type"].startswith("text/html") and 'src="static/app.js"' in page
        assert headers["Server"] == "env-status"  # and not the version of Python
        for name, kind in (("app.js", "text/javascript"), ("spark.js", "text/javascript"), ("style.css", "text/css")):
            code, _text, headers = app.get("/example/static/" + name)
            assert code == 200 and headers["Content-Type"].startswith(kind), name
        assert app.get("/example/healthz")[:2] == (200, "ok\n")  # the health check comes under the prefix too
        assert app.get("/example/api/status?refresh=1")[0] == 200

        for outside in ("/", "/healthz", "/api/status", "/static/app.js", "/examplex/", "/other/example/"):
            code, body, _headers = app.get(outside)
            assert code == 404 and body["error"]["code"] == "not_found", outside
        code, _body, headers = app.get("/example")  # without the slash, relative URLs would point one level too high
        assert code == 308 and headers["Location"] == "/example/"

        for unknown in ("/server.py", "/targets.json", "/static/../server.py", "/static/%2e%2e/server.py", "/static/"):
            code, body, _headers = app.get("/example" + unknown)
            assert code == 404 and body["error"]["code"] == "not_found", unknown  # only the files of the table are served
        for method, path in (("POST", "/api/status"), ("GET", "/api/check"), ("POST", "/"), ("POST", "/healthz")):
            code, body, _headers = app.ask(method, "/example" + path, signed())
            assert code == 405 and body["error"]["code"] == "method_not_allowed", (method, path)

        code, body, headers = app.ask("HEAD", "/example/")
        assert code == 200 and body == "" and int(headers["Content-Length"]) > 500


def test_env_status_serves_at_the_root_on_a_developers_machine(status, monkeypatch):
    def prefix():
        server = status.make_server(0)
        server.server_close()
        return server.base_path

    assert prefix() == "/example"
    monkeypatch.setenv("EFP_APP_BASE_PATH", "status/")  # however it is written
    assert prefix() == "/status"
    monkeypatch.delenv("EFP_APP_BASE_PATH")  # `efp-app dev`: no gateway, no prefix
    with Running(status) as app:
        assert app.server.base_path == ""
        assert app.get("/")[0] == 200 and app.get("/static/app.js")[0] == 200 and app.get("/healthz")[:2] == (200, "ok\n")
        assert app.get("/api/status")[0] == 200
        assert app.get("/example/")[0] == 404


# --- Checking ------------------------------------------------------------------------------------


def test_env_status_shows_results_to_a_guest_and_checks_for_a_member(status, environments, monkeypatch):
    monkeypatch.setattr(
        status,
        "TARGETS",
        [
            environments.target("web", "/ok"),
            environments.target("api", "/down"),
            environments.target("health", "/json", contains='"status":"UP"'),
        ],
    )
    with Running(status) as app:
        code, before, _headers = app.get("/example/api/status")
        assert code == 200 and before["signed_in"] is False and before["name"] is None and before["checked"] is None
        assert (before["max_age"], before["kept"]) == (30, 50)
        untouched = {"last": None, "checks": 0, "uptime": None, "latency": []}
        assert before["targets"][0] == dict(untouched, id="web", name="Web", group="Test")

        code, refused, _headers = app.check(headers={})  # not signed in: looking, not checking
        assert code == 401 and refused == {"error": {"code": "sign_in_required", "message": "Sign in to check the environments."}}
        assert environments.seen == [] and storage.list("") == []

        code, checked, _headers = app.check()
        assert code == 200 and checked["signed_in"] is True and checked["name"] == "Bob" and checked["checked"] == 3
        seen = by_id(checked)
        web, api, health = seen["web"]["last"], seen["api"]["last"], seen["health"]["last"]
        assert (web["up"], web["status"], web["note"]) == (True, 200, "") and isinstance(web["ms"], int) and web["at"].endswith("+00:00")
        assert (api["up"], api["status"], api["note"]) == (False, 503, "It answered 503, not 200.")
        assert (health["up"], health["status"]) == (True, 200)
        assert seen["web"]["url"] == environments.url("/ok")  # a member sees the address
        assert (seen["web"]["uptime"], seen["web"]["latency"]) == (100, [web["ms"]])
        assert (seen["api"]["uptime"], seen["api"]["latency"]) == (0, [None])  # a failed check has no latency to draw
        assert sorted(environments.seen) == ["/down", "/json", "/ok"]  # every environment once

        assert [item.key for item in storage.list("")] == ["history/api.json", "history/health.json", "history/web.json"]
        assert storage.load_json("history/api.json") == {"results": [api]}

        code, after, _headers = app.get("/example/api/status")  # the guest sees what the member's check found
        guest = by_id(after)
        assert guest["api"]["last"] == api and guest["web"]["uptime"] == 100 and after["checked"] is None
        assert all("url" not in target for target in after["targets"])  # and no address of the test network
        assert len(environments.seen) == 3  # looking asks nobody


def test_env_status_reuses_a_result_for_thirty_seconds_and_keeps_fifty(status, environments, clock, monkeypatch):
    monkeypatch.setattr(status, "TARGETS", [environments.target("web", "/ok")])
    old = [
        {"at": "2026-11-19T08:%02d:00+00:00" % minute, "up": minute % 5 != 0, "status": 200, "ms": 100 + minute, "note": ""}
        for minute in range(50)
    ]
    storage.save_json("history/web.json", {"results": old})
    with Running(status) as app:
        first = app.check()[1]
        assert first["checked"] == 1 and environments.seen == ["/ok"]
        kept = storage.load_json("history/web.json")["results"]
        assert len(kept) == 50 and kept[:49] == old[1:] and kept[49]["at"] == "2026-11-20T09:00:00+00:00"  # the oldest made room
        web = by_id(first)["web"]
        assert web["checks"] == 50 and web["uptime"] == 82 and web["latency"][0] == 101 and web["latency"][4] is None

        clock["now"] += timedelta(seconds=29)
        again = app.check(headers=signed(**EVE))[1]  # somebody else, a moment later
        assert again["checked"] == 0 and by_id(again)["web"]["last"] == kept[49] and environments.seen == ["/ok"]

        clock["now"] += timedelta(seconds=2)
        assert app.check()[1]["checked"] == 1 and environments.seen == ["/ok", "/ok"]

        # A target that was added to the file is checked at once; the one that is fresh is left alone.
        monkeypatch.setattr(status, "TARGETS", status.TARGETS + [environments.target("api", "/json")])
        added = app.check()[1]
        assert added["checked"] == 1 and environments.seen == ["/ok", "/ok", "/json"] and by_id(added)["api"]["checks"] == 1

        storage.save_json("history/api.json", {"results": "damaged"})  # an object that is not what was saved
        assert by_id(app.get("/example/api/status")[1])["api"]["checks"] == 0


def test_env_status_does_not_follow_a_redirect_to_another_host(status, environments, monkeypatch):
    monkeypatch.setattr(
        status,
        "TARGETS",
        [
            environments.target("here", "/here"),
            environments.target("away", "/away"),
            environments.target("loop", "/loop"),
            environments.target("teapot", "/teapot", expect=418),
            environments.target("login", "/away", expect=302),
        ],
    )
    with Running(status) as app:
        seen = by_id(app.check()[1])
    assert (seen["here"]["last"]["up"], seen["here"]["last"]["status"]) == (True, 200)  # on the same host: followed
    away = seen["away"]["last"]
    assert (away["up"], away["status"]) == (False, 302) and away["note"] == "It redirects to another host, which is not followed."
    assert "/secret" not in environments.seen and "/ok" in environments.seen
    assert (seen["loop"]["last"]["up"], seen["loop"]["last"]["status"]) == (False, 302)
    assert environments.seen.count("/loop") == status.MAX_REDIRECTS + 1  # and not for ever
    assert seen["teapot"]["last"]["up"] is True  # "up" is the status the file expects, whatever it is
    assert (seen["login"]["last"]["up"], seen["login"]["last"]["note"]) == (True, "")  # a page that is expected to redirect

    opener = status.build_opener()
    for address in ("file:///etc/passwd", "ftp://127.0.0.1/pub", "data:text/plain,hello"):
        with pytest.raises(urllib.error.URLError):
            opener.open(address, timeout=2)  # http and https, nothing else


def test_env_status_gives_up_on_an_environment_that_does_not_answer(status, environments, monkeypatch):
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        closed = "http://127.0.0.1:%d/health" % taken.getsockname()[1]  # nobody listens there once the socket is gone
    monkeypatch.setattr(status, "TIMEOUT", 0.5)
    monkeypatch.setattr(status, "TARGETS", [environments.target("slow", "/slow"), environments.target("gone", "/x", url=closed)])
    started = time.monotonic()
    with Running(status) as app:
        seen = by_id(app.check()[1])
    assert time.monotonic() - started < environments.slow - 1  # the timeout decides, not the environment
    slow, gone = seen["slow"]["last"], seen["gone"]["last"]
    assert (slow["up"], slow["status"], slow["note"]) == (False, None, "It did not answer within 0.5 seconds.")
    assert (gone["up"], gone["status"]) == (False, None)
    assert gone["note"] in ("It could not be reached.", "It did not answer within 0.5 seconds.")  # refused at once, or silently
    assert [target["latency"] for target in seen.values()] == [[None], [None]]


def test_env_status_reads_only_the_start_of_an_answer(status, environments, monkeypatch):
    wanted = '"status":"UP"'
    monkeypatch.setattr(
        status,
        "TARGETS",
        [
            environments.target("fine", "/json", contains=wanted),
            environments.target("down", "/json-down", contains=wanted),  # 200, and the body says otherwise
            environments.target("late", "/late-text", contains=wanted),  # the text is there, 5000 bytes in
        ],
    )
    with Running(status) as app:
        seen = by_id(app.check()[1])
    assert seen["fine"]["last"]["up"] is True
    assert (seen["down"]["last"]["up"], seen["down"]["last"]["status"]) == (False, 200)
    assert seen["down"]["last"]["note"] == "Its answer does not contain the expected text."
    assert seen["late"]["last"]["up"] is False  # beyond the 2 KiB that are read

    class Endless:
        """An answer that never ends, 100 bytes at a time."""

        handed_out = 0

        def read1(self, size):
            self.handed_out += min(size, 100)
            return b"x" * min(size, 100)

    answer = Endless()
    assert len(status.read_some(answer, status.MAX_BODY, time.monotonic() + 60)) == status.MAX_BODY == answer.handed_out == 2048
    # Nothing is read after the deadline.
    assert status.read_some(answer, status.MAX_BODY, time.monotonic() - 1) == b"" and answer.handed_out == 2048


def test_env_status_page_cannot_name_an_address(status, environments, monkeypatch):
    monkeypatch.setattr(status, "TARGETS", [environments.target("web", "/ok")])
    wish = {"url": environments.url("/secret"), "targets": [{"id": "web", "url": environments.url("/secret")}], "id": "../../team"}
    headers = dict(signed(), **{"Content-Type": "application/json", "X-Target": environments.url("/secret")})
    with Running(status) as app:
        code, checked, _headers = app.check(headers, "/example/api/check?url=" + environments.url("/secret"), json.dumps(wish))
        assert code == 200 and checked["checked"] == 1 and [target["id"] for target in checked["targets"]] == ["web"]
        assert environments.seen == ["/ok"]  # the file's one address, whatever the request wished for
        assert [item.key for item in storage.list("")] == ["history/web.json"]

        # A request that announces more than the page ever sends is refused before it is read.
        code, body, _headers = app.check(dict(signed(), **{"Content-Length": "100000"}))
        assert code == 413 and body["error"]["code"] == "too_large"
        assert app.ask("POST", "/example/api/status", signed(), "x" * 5000)[0] == 405  # a body on the wrong address is read and ignored


def test_env_status_two_people_pressing_at_once_cause_one_round(status, environments, monkeypatch):
    monkeypatch.setattr(status, "TARGETS", [environments.target("env%d" % number, "/wait%d" % number) for number in range(4)])
    start = threading.Barrier(2)
    answers = []

    with Running(status) as app:

        def press(who):
            start.wait()
            answers.append(app.check(headers=who)[1])

        threads = [threading.Thread(target=press, args=(who,)) for who in (signed(), signed(**EVE))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
    assert sorted(answer["checked"] for answer in answers) == [0, 4]  # the second found results that were a moment old
    assert sorted(environments.seen) == ["/wait0", "/wait1", "/wait2", "/wait3"]  # each environment was asked once
    assert environments.most_busy >= 2  # and they were asked side by side, not one after the other
    assert all(by_id(answer)["env3"]["last"]["up"] for answer in answers)
    assert len(storage.load_json("history/env0.json")["results"]) == 1
    workers = [thread.name for thread in threading.enumerate() if thread.name.startswith("ThreadPoolExecutor")]
    assert workers == []  # no thread outlives the request


def test_env_status_turns_failures_into_sentences(status, monkeypatch):
    with Running(status) as app:

        def gone(key, default=None):
            raise errors.StorageUnavailableError("cannot reach the storage service at /run/efp/storage.sock")

        monkeypatch.setattr(storage, "load_json", gone)
        for code, body, _headers in (app.get("/example/api/status"), app.check()):
            assert code == 502
            assert body == {"error": {"code": "storage_unavailable", "message": errors.StorageUnavailableError().user_message}}

        def broken(histories, who, checked=None):
            raise RuntimeError("a bug at line 42")

        monkeypatch.setattr(storage, "load_json", lambda key, default=None: default)
        monkeypatch.setattr(status, "overview", broken)
        code, body, _headers = app.get("/example/api/status")
        assert code == 500 and body == {"error": {"code": "failed", "message": "Something went wrong in the app. Please try again."}}
        assert app.get("/example/healthz")[0] == 200  # the server itself is fine


def test_env_status_runs_as_the_manifest_says(tmp_path):
    """The command of the manifest as a process of its own: PORT, the prefix, the visitor, the log on stdout."""
    command = json.loads((HERE / "efp-app.json").read_text(encoding="utf-8"))["command"].split()
    assert command[0] == "python"
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    env = dict(
        os.environ,
        PORT=str(port),
        EFP_APP_BASE_PATH="/status",
        EFP_APP_ID=APP_ID,
        EFP_VISITOR_KEY=VISITOR_KEY,
        EFP_APP_DATA_DIR=str(tmp_path / "data"),
        PYTHONPATH=str(SDK_ROOT),
        PYTHONDONTWRITEBYTECODE="1",
    )
    process = subprocess.Popen([sys.executable] + command[1:], cwd=HERE, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for _attempt in range(300):  # it listens within a moment; a busy machine gets a minute
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                assert process.poll() is None, process.stderr.read().decode("utf-8", "replace")
                time.sleep(0.2)
        assert ask(port, "GET", "/status/healthz")[:2] == (200, "ok\n")
        assert ask(port, "GET", "/healthz")[0] == 404
        code, overview, _headers = ask(port, "GET", "/status/api/status", signed())
        assert code == 200 and overview["signed_in"] is True and overview["name"] == "Bob"
        shipped = json.loads((HERE / "targets.json").read_text(encoding="utf-8"))["targets"]
        assert [target["id"] for target in overview["targets"]] == [target["id"] for target in shipped]
        assert ask(port, "GET", "/status/api/status")[1]["signed_in"] is False
    finally:
        process.terminate()
        out, err = process.communicate(timeout=60)
    log = out.decode("utf-8", "replace")
    assert "listening on 127.0.0.1:%d, served under /status/" % port in log
    assert '"GET /status/healthz HTTP/1.1" 200' in log and '"GET /healthz HTTP/1.1" 404' in log  # the access log, on stdout
    assert err == b""


# --- The arithmetic of the page, under node ---------------------------------------------------------


def page_copy(tmp_path):
    """A copy of the page's modules that node can import; the node to run, or a skipped test.

    Node takes a ``.js`` file for an ES module only inside a package that says
    so; the example has no package.json (it needs no npm), so the copy gets one.
    """
    runner = shutil.which("node")
    if runner is None:
        pytest.skip("node is not on the PATH")
    work = tmp_path / "page"
    if not work.exists():
        shutil.copytree(HERE / "static", work / "static")
        (work / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return runner, work


def test_env_status_draws_latency_and_marks_failures(tmp_path):
    runner, work = page_copy(tmp_path)
    (work / "check.mjs").write_text(
        """
        import * as spark from "./static/spark.js";
        const target = (id, group, last) => ({ id, group, last });
        const targets = [target("a", "QA", { up: true }), target("b", "Staging", { up: false }), target("c", "QA", null)];
        console.log(JSON.stringify({
          line: spark.sparkline([100, 300, 200], 120, 28),
          gaps: spark.sparkline([100, null, 300, 200, null, null, 150], 124, 28),
          level: spark.sparkline([80, 80, 80], 120, 28).lines[0].map((point) => point.y),
          one: spark.sparkline([80], 120, 28),
          none: spark.sparkline([], 120, 28),
          failed: spark.sparkline([null, null], 120, 28),
          points: spark.pointsOf([{ x: 2, y: 26 }, { x: 60, y: 2 }]),
          durations: [0, 182, 999, 1000, 1449, 12345].map(spark.duration),
          tally: spark.tally(targets),
          headlines: [targets, targets.slice(0, 1), targets.slice(2), []].map(spark.headline),
          groups: spark.grouped(targets).map((group) => [group.name, group.targets.map((entry) => entry.id)]),
        }));
        """,
        encoding="utf-8",
    )
    done = subprocess.run([runner, "check.mjs"], cwd=work, capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
    seen = json.loads(done.stdout)

    assert seen["line"] == {
        "lines": [[{"x": 2, "y": 26}, {"x": 60, "y": 2}, {"x": 118, "y": 14}]],  # the slowest check is at the top
        "misses": [],
        "low": 100,
        "high": 300,
    }
    gaps = seen["gaps"]
    assert [len(piece) for piece in gaps["lines"]] == [1, 2, 1]  # a failed check breaks the line
    assert gaps["misses"] == [22, 82, 102] and (gaps["low"], gaps["high"]) == (100, 300)
    assert gaps["lines"][0][0] == {"x": 2, "y": 26} and gaps["lines"][2][0]["x"] == 122
    assert seen["level"] == [14, 14, 14]  # all the same: a level line through the middle
    assert seen["one"]["lines"] == [[{"x": 60, "y": 14}]] and seen["none"] == {"lines": [], "misses": [], "low": 0, "high": 0}
    assert seen["failed"]["lines"] == [] and seen["failed"]["misses"] == [2, 118]
    assert seen["points"] == "2,26 60,2"
    assert seen["durations"] == ["0 ms", "182 ms", "999 ms", "1.0 s", "1.4 s", "12.3 s"]
    assert seen["tally"] == {"up": 1, "down": 1, "unknown": 1}
    assert seen["headlines"][:2] == ["1 up, 1 down, 1 not checked yet.", "1 up, 0 down."]
    assert seen["headlines"][2:] == ["Nothing has been checked yet.", "No environment is listed in targets.json."]
    assert seen["groups"] == [["QA", ["a", "c"]], ["Staging", ["b"]]]  # in the order of the file


def test_env_status_page_script_parses(tmp_path):
    # spark.js is run by the test above; app.js needs a page, so it is only parsed.
    runner, work = page_copy(tmp_path)
    done = subprocess.run([runner, "--check", "static/app.js"], cwd=work, capture_output=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr
