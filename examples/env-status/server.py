"""Example EFP app: are the test environments up? And: any Python web server as an app.

It shows:

- what an app of type "command" has to do itself        listen on PORT, take the path prefix off, answer the health check
- who is visiting, without a framework's helper         efp_app.visitor.current(headers)
- requests a server makes for its visitors, on a leash  targets.json, SameHostRedirects, check_one
- results in storage, one object per environment        history/<target id>.json

The manifest says "type": "command" and "command": "python server.py": the
host runs that line and expects a web server. This file is that server,
written with the standard library alone (http.server), so that everything a
framework would do for you is visible. The same duties hold for whatever you
start with a command:

1. Listen on 127.0.0.1 and the port in PORT. Nothing else is reachable.
2. The gateway does NOT take the path prefix off for a command app. Requests
   arrive as /{slug}/api/status; EFP_APP_BASE_PATH is "/{slug}" on the host
   and empty on your own machine. Strip it from every path, and keep every
   URL in the page relative.
3. Answer the manifest's health_path (under the prefix) with a status below
   500, or the deployment fails at the "health" step.
4. The visitor is in a signed header: efp_app.visitor.current(headers).
5. Write the log to standard output; the host shows it on the app's page.

What it does: "Check now" makes this server request every URL of
targets.json and say whether the answer was the expected one. A server that
fetches addresses for its visitors is a way into the network it runs in, so
the page can never name an address: the list is a file in the repository,
redirects to other hosts are not followed, an answer is read only as far as
needed and never passed on.
"""

import http.client
import json
import logging
import os
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit

from efp_app import Visitor, storage, visitor
from efp_app import app as about
from efp_app.errors import EfpError

HERE = Path(__file__).parent
MAX_AGE = 30  # seconds a result is reused: two people pressing "Check now" cause one round of requests
KEPT = 50  # results kept for every environment
TIMEOUT = 4.0  # seconds to connect, and again to answer
MAX_BODY = 2048  # bytes of an answer that are read, and only when a target wants a text in it
MAX_WORKERS = 8
MAX_REDIRECTS = 3
MAX_REQUEST_BODY = 64 * 1024
USER_AGENT = "efp-env-status/1.0"
TARGET_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")

#: The only files this server hands out. A table, not a path built from the
#: request: nothing a visitor types decides which file is opened.
FILES = {
    "/": ("static/index.html", "text/html; charset=utf-8"),
    "/static/style.css": ("static/style.css", "text/css; charset=utf-8"),
    "/static/app.js": ("static/app.js", "text/javascript; charset=utf-8"),
    "/static/spark.js": ("static/spark.js", "text/javascript; charset=utf-8"),
}
ROUTES = {"/healthz": "GET", "/api/status": "GET", "/api/check": "POST"}

log = logging.getLogger("app")


# --- The environments ----------------------------------------------------------------------------


def load_targets(path: Path) -> List[dict]:
    """The environments to check, from the file that ships with the app.

    This is the whole list of addresses the server will ever request. It is
    checked when the app starts, so a slip in the file is a failed deployment
    with a clear message, not a surprise at the first click.
    """
    targets, seen = [], set()
    for entry in json.loads(path.read_text(encoding="utf-8"))["targets"]:
        target_id, url, expect, contains = entry.get("id"), entry.get("url"), entry.get("expect", 200), entry.get("contains")
        if not isinstance(target_id, str) or not TARGET_ID.fullmatch(target_id) or target_id in seen:
            # The id becomes part of a storage key.
            raise ValueError('%s: "id" must be unique: lower-case letters, digits and "-" (%r)' % (path.name, target_id))
        parts = urlsplit(url if isinstance(url, str) else "")
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
            raise ValueError('%s: "url" of %s must be an http(s) address without a user name or password' % (path.name, target_id))
        if isinstance(expect, bool) or not isinstance(expect, int) or not 100 <= expect <= 599:
            raise ValueError('%s: "expect" of %s must be an HTTP status' % (path.name, target_id))
        if contains is not None and (not isinstance(contains, str) or not 0 < len(contains) <= 200):
            raise ValueError('%s: "contains" of %s must be a short text' % (path.name, target_id))
        seen.add(target_id)
        targets.append(
            {
                "id": target_id,
                "name": str(entry.get("name") or target_id),
                "group": str(entry.get("group") or "Environments"),
                "url": url,
                "expect": expect,
                "contains": contains,
            }
        )
    return targets


TARGETS = load_targets(HERE / "targets.json")


class OffHost(urllib.error.HTTPError):
    """A redirect that was not followed. It carries the status of the answer that asked for it."""


class SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when it stays on the host that was asked.

    A health page that answers "go to http://10.0.0.5/admin" (by mistake, or
    because somebody controls it) would otherwise send this server to an
    address nobody put on the list.
    """

    max_redirections = max_repeats = MAX_REDIRECTS  # a chain of redirects ends here, and so does a loop

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        asked, wanted = urlsplit(req.full_url), urlsplit(newurl)
        if wanted.scheme not in ("http", "https") or wanted.hostname != asked.hostname:
            raise OffHost(req.full_url, code, "a redirect to another host is not followed", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener() -> urllib.request.OpenerDirector:
    """An opener for http and https and nothing else.

    ``urllib.request.urlopen`` would also open file: and ftp: addresses. The
    targets are checked to be http(s) when they are loaded; leaving the other
    handlers out means there is no second place where that has to hold.
    Proxy settings of the environment (HTTPS_PROXY, NO_PROXY) are honoured.
    """
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.UnknownHandler(),  # any other scheme is an error, not silence
        SameHostRedirects(),
    ):
        opener.add_handler(handler)
    return opener


def now() -> datetime:
    return datetime.now(timezone.utc)


def read_some(answer, limit: int, deadline: float) -> bytes:
    """At most ``limit`` bytes of an answer, and no reading after ``deadline``.

    Never ``answer.read()``: an environment that answers with a gigabyte, or
    with one byte a second, must not fill the memory of this app or hold its
    request for ever.
    """
    chunks, size = [], 0
    while size < limit and time.monotonic() < deadline:
        chunk = answer.read1(limit - size)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
    return b"".join(chunks)


def check_one(target: dict, opener: urllib.request.OpenerDirector) -> dict:
    """Ask one environment once. Every failure is a result ("down", and why), never an exception."""
    started = time.monotonic()
    status, body, note = None, b"", ""
    request = urllib.request.Request(target["url"], headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        try:
            answer = opener.open(request, timeout=TIMEOUT)
        except urllib.error.HTTPError as exc:
            answer = exc  # a 404 or a 503 is an answer too; so is a redirect that was not followed
            if isinstance(exc, OffHost):
                note = "It redirects to another host, which is not followed."
        with answer:
            status = answer.status
            if target["contains"]:
                body = read_some(answer, MAX_BODY, started + 2 * TIMEOUT)
    except (TimeoutError, urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):  # one and the same from Python 3.10 on
            note = "It did not answer within %g seconds." % TIMEOUT
        elif isinstance(reason, ssl.SSLCertVerificationError):
            note = "Its certificate was not accepted."
        elif isinstance(reason, ssl.SSLError):
            note = "A secure connection could not be set up."
        else:
            note = "It could not be reached."
        log.info("%s: %s", target["id"], exc)  # the detail is for the log, not for the page
    took = int((time.monotonic() - started) * 1000)
    up = status == target["expect"] and (not target["contains"] or target["contains"].encode("utf-8") in body)
    if up:
        note = ""  # a redirect that leads elsewhere is fine for a target that expects exactly that redirect
    elif status is not None and not note:
        if status != target["expect"]:
            note = "It answered %d, not %d." % (status, target["expect"])
        else:
            note = "Its answer does not contain the expected text."
    return {"at": now().isoformat(timespec="seconds"), "up": up, "status": status, "ms": took, "note": note}


# --- The results ---------------------------------------------------------------------------------

# One process per app: this lock makes "read the results, check, save" one
# step. The second of two people who press at the same moment waits, then
# finds results that are seconds old and causes no request at all.
_checking = threading.Lock()


def history_key(target_id: str) -> str:
    return "history/%s.json" % target_id


def read_history(target_id: str) -> List[dict]:
    """The kept results of one environment, oldest first; the shape is checked, a stored object can be old."""
    kept = storage.load_json(history_key(target_id), default=None)
    results = kept.get("results") if isinstance(kept, dict) else None
    return [r for r in results if isinstance(r, dict) and isinstance(r.get("at"), str)] if isinstance(results, list) else []


def is_fresh(results: List[dict]) -> bool:
    """Whether the last result is young enough to be shown instead of checking again."""
    try:
        return bool(results) and (now() - datetime.fromisoformat(results[-1]["at"])).total_seconds() < MAX_AGE
    except (ValueError, TypeError):
        return False


def overview(histories: Dict[str, List[dict]], who: Optional[Visitor], checked: Optional[int] = None) -> dict:
    """What the page shows: every environment with its last result, its uptime and its latencies."""
    targets = []
    for target in TARGETS:
        results = histories[target["id"]]
        item = {
            "id": target["id"],
            "name": target["name"],
            "group": target["group"],
            "last": results[-1] if results else None,
            "checks": len(results),
            "uptime": round(100 * sum(1 for r in results if r.get("up")) / len(results)) if results else None,
            # A failed check has no latency worth drawing: null leaves a gap in the line.
            "latency": [r.get("ms") if r.get("up") else None for r in results],
        }
        if who is not None:
            item["url"] = target["url"]  # an address of the test network: for members, not for guests
        targets.append(item)
    return {
        "signed_in": who is not None,
        "name": who.name if who else None,
        "max_age": MAX_AGE,
        "kept": KEPT,
        "checked": checked,  # how many environments this request asked; null when it only looked
        "targets": targets,
    }


def check_now(who: Visitor) -> dict:
    """Check every environment whose last result is older than MAX_AGE, all at the same time."""
    with _checking:
        histories = {target["id"]: read_history(target["id"]) for target in TARGETS}
        due = [target for target in TARGETS if not is_fresh(histories[target["id"]])]
        if due:
            opener = build_opener()
            # Leaving the "with" block waits for every worker: no thread outlives this request.
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(due))) as pool:
                results = list(pool.map(lambda target: check_one(target, opener), due))
            for target, result in zip(due, results):
                history = (histories[target["id"]] + [result])[-KEPT:]
                storage.save_json(history_key(target["id"]), {"results": history})
                histories[target["id"]] = history
            log.info("%s checked %d environments, %d up", who.username, len(due), sum(1 for r in results if r["up"]))
    return overview(histories, who, checked=len(due))


# --- The web server ------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    """One request. ``self.server.base_path`` is the prefix the app is served under ("" locally)."""

    def version_string(self) -> str:
        return "env-status"  # the Server header; the default names the Python version, which is nobody's business

    def do_GET(self) -> None:
        self.respond("GET")

    def do_HEAD(self) -> None:
        self.respond("GET")  # send() leaves the body out for HEAD

    def do_POST(self) -> None:
        # Whatever was sent is read first, up to a limit, and then ignored:
        # closing a connection on a body that is still unread can cost the
        # client the answer. No route of this app takes a parameter.
        length = (self.headers.get("Content-Length") or "0").strip()
        if not length.isdigit() or int(length) > MAX_REQUEST_BODY:
            self.refuse(413, "too_large", "This request is larger than anything the page sends.")
            return
        self.rfile.read(int(length))
        self.respond("POST")

    def log_message(self, format: str, *args) -> None:
        """Duty 5: the access log goes to standard output with everything else (the default is stderr)."""
        log.info(format, *args)

    # -- Duty 2: the path prefix --

    def app_path(self) -> Optional[str]:
        """The path of this request inside the app: no query, no prefix. ``None`` when it is outside the app."""
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        base = self.server.base_path
        if not base:
            return path
        if path.startswith(base + "/"):
            return path[len(base) :]
        return None

    def respond(self, method: str) -> None:
        path = self.app_path()
        try:
            if path is None:
                if self.path.split("?", 1)[0] == self.server.base_path:
                    # "/{slug}" without the slash: relative URLs would resolve one level too high.
                    self.send(308, b"", "text/plain; charset=utf-8", {"Location": self.server.base_path + "/"})
                else:
                    self.refuse(404, "not_found", "There is nothing at this address.")
            elif path in FILES and method == "GET":
                name, content_type = FILES[path]
                self.send(200, (HERE / name).read_bytes(), content_type)
            elif path not in ROUTES and path not in FILES:
                self.refuse(404, "not_found", "There is nothing at this address.")
            elif method != ROUTES.get(path, "GET"):
                self.refuse(405, "method_not_allowed", "This address does not take that kind of request.")
            elif path == "/healthz":
                self.send(200, b"ok\n", "text/plain; charset=utf-8")  # duty 3; it touches neither storage nor the network
            elif path == "/api/status":
                # Everybody who may open the app sees the last results; looking asks no environment.
                histories = {target["id"]: read_history(target["id"]) for target in TARGETS}
                self.send_json(200, overview(histories, self.who()))
            else:
                self.check()
        except EfpError as exc:
            # Every SDK error ends as a sentence for the visitor; the detail goes to the log.
            log.warning("%s", exc)
            self.refuse(429 if exc.status == 429 else 502, exc.code, exc.user_message)
        except Exception:
            log.exception("%s %s failed", method, path)
            self.refuse(500, "failed", "Something went wrong in the app. Please try again.")

    def check(self) -> None:
        """POST api/check. The request names nothing: what is checked is decided by targets.json alone."""
        who = self.who()
        if who is None:
            self.refuse(401, "sign_in_required", "Sign in to check the environments.")
            return
        self.send_json(200, check_now(who))

    # -- Duty 4: the visitor --

    def who(self) -> Optional[Visitor]:
        """The signed-in visitor, or ``None``. The gateway signs the header; nothing else says who is asking."""
        return visitor.current(self.headers)

    # -- Answers --

    def send(self, status: int, body: bytes, content_type: str, headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if content_type.startswith("application/json") else "no-cache")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, status: int, payload: dict) -> None:
        self.send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def refuse(self, status: int, code: str, message: str) -> None:
        """An error in the shape the gateway uses as well: {"error": {"code", "message"}}."""
        self.send_json(status, {"error": {"code": code, "message": message}})


def make_server(port: int, base_path: Optional[str] = None) -> ThreadingHTTPServer:
    """The server, bound and not yet serving. ``base_path`` defaults to EFP_APP_BASE_PATH ("" locally)."""
    # Duty 1: 127.0.0.1, never 0.0.0.0. One thread per request (and they are
    # daemon threads: a request that hangs does not keep the process alive).
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.base_path = about.base_path() if base_path is None else base_path
    return server


def main() -> None:
    logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = make_server(int(os.environ.get("PORT", "8000")))
    log.info("listening on 127.0.0.1:%d, served under %s/", server.server_address[1], server.base_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
