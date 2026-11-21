"""A stand-in for the Portal, for the container smoke test (standard library only).

    python stub_portal.py --port 8000 --token ci-token [--apps apps.json]

It answers docs/CONTRACTS.md, section 5, under ``/api/app-host/v1`` with
canned data and remembers what the host sent:

- ``POST /handoff/redeem``  a code ``ok.<app id>.<slug>.<user id>`` is good
  once, and so is ``center.<user id>``, the code of the app center (it names
  no app); anything else is ``400 invalid_code``.
- ``POST /access/check``    allowed, unless the pair was put on the deny list.
- ``GET  /apps``            the desired state (``--apps`` or ``PUT /_stub/apps``).
- ``POST /metrics/ingest``, ``POST /events``  recorded.
- ``POST /catalog``         what the app center shows: the list given with
  ``PUT /_stub/catalog``, for a known user.
- ``GET  /apps/{id}/icon``  a small PNG for the ids named there, else 404.

The test drives and inspects it through ``/_stub/...`` (same bearer token):
``GET /_stub/state`` returns events, metrics and call counts;
``PUT /_stub/apps``, ``PUT /_stub/deny`` and ``PUT /_stub/catalog`` change
the canned answers.
"""

from __future__ import annotations

import argparse
import hmac
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFIX = "/api/app-host/v1"
USERS = {
    12: {"id": 12, "username": "bob", "display_name": "Bob", "group_id": 3, "group_name": "QA"},
    13: {"id": 13, "username": "carol", "display_name": "Carol", "group_id": 4, "group_name": "Ops"},
}
# What GET /apps/{id}/icon answers: the PNG signature and a few bytes.
ICON = b"\x89PNG\r\n\x1a\n" + bytes(range(16))


class State:
    def __init__(self, token: str, apps: list):
        self.token = token
        self.lock = threading.Lock()
        self.apps = apps
        self.denied: set[tuple[str, int]] = set()
        self.catalog: list = []
        self.icons: set[str] = set()
        self.used_codes: set[str] = set()
        self.events: list[dict] = []
        self.metrics: list[dict] = []
        self.calls: dict[str, int] = {}
        self.unauthorized = 0

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "events": list(self.events),
                "metrics_posts": len(self.metrics),
                "last_metrics": self.metrics[-1] if self.metrics else None,
                "calls": dict(self.calls),
                "unauthorized": self.unauthorized,
                "apps": list(self.apps),
            }


class Handler(BaseHTTPRequestHandler):
    server_version = "efp-stub-portal"
    protocol_version = "HTTP/1.1"
    state: State

    def log_message(self, fmt: str, *args) -> None:  # one line per request on stdout
        sys.stdout.write("portal %s %s\n" % (self.command, fmt % args))
        sys.stdout.flush()

    # ---- plumbing -----------------------------------------------------------
    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return None

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization") or ""
        scheme, _, token = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(token.strip(), self.state.token)

    def _count(self, name: str) -> None:
        with self.state.lock:
            self.state.calls[name] = self.state.calls.get(name, 0) + 1

    def _route(self) -> None:
        state = self.state
        path = self.path.split("?", 1)[0]
        body = self._body() if self.command in ("POST", "PUT") else {}
        if path == "/_stub/health":
            self._json(200, {"ok": True})
            return
        # Where the gateway sends a browser that has to sign in (not part of
        # section 5; it lets a test follow the redirect).
        if path.startswith("/apps/launch/"):
            self._json(200, {"launch": path.rsplit("/", 1)[-1]})
            return
        if path == "/apps/launch":  # the app center's sign-in (section 6.5)
            self._json(200, {"launch": None})
            return
        if not self._authorized():
            with state.lock:
                state.unauthorized += 1
            self._json(401, {"error": {"code": "unauthorized", "message": "the host API token is required"}})
            return
        if body is None:
            self._json(400, {"error": {"code": "invalid_request", "message": "the body is not JSON"}})
            return

        if path == "/_stub/state" and self.command == "GET":
            self._json(200, state.snapshot())
        elif path == "/_stub/apps" and self.command == "PUT":
            with state.lock:
                state.apps = list(body.get("apps") or [])
            self._json(200, {"ok": True})
        elif path == "/_stub/deny" and self.command == "PUT":
            with state.lock:
                state.denied = {(str(app_id), int(user_id)) for app_id, user_id in body.get("pairs") or []}
            self._json(200, {"ok": True})
        elif path == PREFIX + "/handoff/redeem" and self.command == "POST":
            self._count("redeem")
            code = str(body.get("code") or "")
            parts = code.split(".")
            with state.lock:
                fresh = code not in state.used_codes
                state.used_codes.add(code)
            if len(parts) == 4 and parts[0] == "ok" and parts[3].isdigit() and int(parts[3]) in USERS and fresh:
                self._json(200, {"app_id": parts[1], "slug": parts[2], "user": USERS[int(parts[3])]})
            elif len(parts) == 2 and parts[0] == "center" and parts[1].isdigit() and int(parts[1]) in USERS and fresh:
                self._json(200, {"app_id": None, "slug": None, "user": USERS[int(parts[1])]})
            else:
                self._json(400, {"error": {"code": "invalid_code", "message": "the code is unknown, used or expired"}})
        elif path == PREFIX + "/access/check" and self.command == "POST":
            self._count("access_check")
            app_id, user_id = str(body.get("app_id") or ""), int(body.get("user_id") or 0)
            with state.lock:
                denied = (app_id, user_id) in state.denied
            answer = {"allowed": not denied and user_id in USERS, "reason": "no_access" if denied else (None if user_id in USERS else "user_inactive")}
            if user_id in USERS:
                answer["user"] = USERS[user_id]
            self._json(200, answer)
        elif path == "/_stub/catalog" and self.command == "PUT":
            with state.lock:
                state.catalog = list(body.get("apps") or [])
                state.icons = {str(app_id) for app_id in body.get("icons") or []}
            self._json(200, {"ok": True})
        elif path == PREFIX + "/catalog" and self.command == "POST":
            self._count("catalog")
            user_id = int(body.get("user_id") or 0)
            with state.lock:
                apps = list(state.catalog)
            if user_id in USERS:
                self._json(200, {"allowed": True, "reason": None, "user": USERS[user_id], "apps": apps})
            else:
                self._json(200, {"allowed": False, "reason": "user_inactive", "apps": []})
        elif path.startswith(PREFIX + "/apps/") and path.endswith("/icon") and self.command == "GET":
            self._count("icon")
            app_id = path[len(PREFIX + "/apps/") : -len("/icon")]
            with state.lock:
                known = app_id in state.icons
            if known:
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(ICON)))
                self.end_headers()
                self.wfile.write(ICON)
            else:
                self._json(404, {"error": {"code": "icon_not_found", "message": "this app has no icon"}})
        elif path == PREFIX + "/apps" and self.command == "GET":
            self._count("apps")
            with state.lock:
                apps = list(state.apps)
            self._json(200, {"apps": apps})
        elif path == PREFIX + "/metrics/ingest" and self.command == "POST":
            self._count("metrics")
            with state.lock:
                state.metrics.append(body)
                del state.metrics[:-20]
            self._json(200, {"ok": True})
        elif path == PREFIX + "/events" and self.command == "POST":
            self._count("events")
            with state.lock:
                state.events.append(body)
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": {"code": "not_found", "message": f"the stub has no {self.command} {path}"}})

    do_GET = do_POST = do_PUT = _route


def main() -> int:
    parser = argparse.ArgumentParser(description="A stand-in for the Portal (docs/CONTRACTS.md, section 5).")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--token", required=True)
    parser.add_argument("--apps", help="a JSON file with the desired apps (the list of GET /apps)")
    args = parser.parse_args()
    apps: list = []
    if args.apps:
        with open(args.apps, "r", encoding="utf-8") as handle:
            apps = json.load(handle)
    Handler.state = State(args.token, apps)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    print(f"stub portal on {args.host}:{args.port}, {len(apps)} desired app(s)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
