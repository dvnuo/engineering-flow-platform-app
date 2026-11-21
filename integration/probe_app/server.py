"""The isolation probe: an app that tells what it can see and reach.

Deployed twice by the container smoke test (a "victim" and an "attacker").
The test asks the attacker to read the victim's files, environment and
stored objects, and expects every attempt to fail. It is deliberately the
kind of app nobody should host: it reads whatever path it is told to. It
exists in CI only.

Standard library only. Type ``command``: the gateway keeps the ``/{slug}``
prefix, so the handler removes ``EFP_APP_BASE_PATH`` itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = os.environ.get("EFP_APP_BASE_PATH", "")
HERE = Path(__file__).resolve().parent
VERSION = (HERE / "version.txt").read_text(encoding="utf-8").strip()
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def status_field(name: str) -> str:
    with open("/proc/self/status", "r", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith(name + ":"):
                return line.split(":", 1)[1].strip()
    return ""


def try_read(path: str) -> dict:
    """What happens when this app touches ``path``."""

    result = {"path": path, "ok": False, "kind": None, "error": None, "sample": None}
    try:
        if os.path.isdir(path):
            result["kind"] = "dir"
            result["sample"] = sorted(os.listdir(path))[:20]
        else:
            result["kind"] = "file"
            with open(path, "rb") as handle:
                result["sample"] = handle.read(200).decode("utf-8", "replace")
        result["ok"] = True
    except OSError as exc:
        result["error"] = exc.__class__.__name__
    return result


def try_write(path: str, content: str) -> dict:
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return {"path": path, "ok": True, "error": None}
    except OSError as exc:
        return {"path": path, "ok": False, "error": exc.__class__.__name__}


def try_connect(path: str) -> dict:
    """Can this app open a connection to the unix socket at ``path``?"""

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(path)
        return {"path": path, "ok": True, "error": None}
    except OSError as exc:
        return {"path": path, "ok": False, "error": exc.__class__.__name__}


def scan_environments(needles: list) -> dict:
    """Look for the given strings in the environment of every process this app can open."""

    readable, found = 0, []
    pids = [name for name in os.listdir("/proc") if name.isdigit()]
    for pid in pids:
        try:
            with open(f"/proc/{pid}/environ", "rb") as handle:
                data = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        if int(pid) == os.getpid():
            continue
        readable += 1
        found += [needle for needle in needles if needle and needle in data]
    return {"processes_visible": len(pids), "foreign_environments_readable": readable, "found": sorted(set(found))}


def storage_call(header: dict, body: bytes = b"") -> dict:
    """One request to the storage service of hidden mode (docs/CONTRACTS.md, 7.1)."""

    path = os.environ.get("EFP_APP_STORAGE_SOCKET")
    if not path:
        return {"ok": False, "error": {"code": "no_socket", "message": "EFP_APP_STORAGE_SOCKET is not set"}}
    raw = json.dumps(header).encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(20)
        client.connect(path)
        client.sendall(struct.pack(">I", len(raw)) + raw + body)

        def exactly(count: int) -> bytes:
            data = b""
            while len(data) < count:
                chunk = client.recv(count - len(data))
                if not chunk:
                    raise ConnectionError("the storage service closed the connection")
                data += chunk
            return data

        (length,) = struct.unpack(">I", exactly(4))
        answer = json.loads(exactly(length))
        if answer.get("ok") and "size" in answer:
            answer["body"] = exactly(int(answer["size"])).decode("utf-8", "replace")
        return answer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print("probe %s" % (fmt % args), flush=True)

    def _send(self, status: int, payload, *, extra: list | None = None) -> None:
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8" if isinstance(payload, bytes) else "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in extra or []:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _path(self) -> str:
        path = self.path.split("?", 1)[0]
        if BASE and (path == BASE or path.startswith(BASE + "/")):
            path = path[len(BASE):]
        return path or "/"

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:
        path = self._path()
        if path in ("/", "/healthz"):
            self._send(200, f"probe app {os.environ.get('EFP_APP_SLUG', '?')} version {VERSION}\n".encode("utf-8"))
        elif path == "/whoami":
            visitor = self.headers.get("X-EFP-Visitor") or ""
            claims = None
            if visitor.count(".") == 2:
                payload = visitor.split(".")[1]
                claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            self._send(
                200,
                {
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                    "groups": os.getgroups(),
                    "cwd": os.getcwd(),
                    "no_new_privs": status_field("NoNewPrivs"),
                    "cap_eff": status_field("CapEff"),
                    "env_names": sorted(os.environ),
                    "app_id": os.environ.get("EFP_APP_ID"),
                    "data_dir": os.environ.get("EFP_APP_DATA_DIR"),
                    "storage_socket": os.environ.get("EFP_APP_STORAGE_SOCKET"),
                    "home": os.environ.get("HOME"),
                    "tmpdir": os.environ.get("TMPDIR"),
                    "request_path": self.path,
                    "forwarded_prefix": self.headers.get("X-Forwarded-Prefix"),
                    "cookie": self.headers.get("Cookie"),
                    "efp_headers": sorted(name for name in self.headers if name.lower().startswith("x-efp-")),
                    "visitor": claims,
                },
            )
        elif path == "/cookies":
            self._send(
                200,
                {"ok": True},
                extra=[
                    ("Set-Cookie", "good=1; Path=/"),
                    ("Set-Cookie", "scoped=2; Path=/somewhere; HttpOnly"),
                    ("Set-Cookie", "wide=3; Domain=example.com; Path=/"),
                    ("Set-Cookie", "__Host-efp_apps=forged; Path=/; Secure"),
                    ("Service-Worker-Allowed", "/"),
                    ("NEL", '{"report_to":"x","max_age":600,"success_fraction":1}'),
                    ("Report-To", '{"group":"x","max_age":600,"endpoints":[{"url":"https://collector.example.com/"}]}'),
                    ("Reporting-Endpoints", 'x="https://collector.example.com/"'),
                    ("Clear-Site-Data", '"cookies"'),
                ],
            )
        elif path == "/accel":
            # What an app would answer to make nginx hand it another location's answer.
            self._send(200, {"own": True}, extra=[("X-Accel-Redirect", "/_efp_internal/auth")])
        elif path == "/ws":
            self._websocket_echo()
        else:
            self._send(404, {"error": "no such path", "path": path})

    def do_POST(self) -> None:
        path = self._path()
        body = self._json_body()
        if path == "/read":
            self._send(200, {"results": [try_read(item) for item in body.get("paths", [])]})
        elif path == "/write":
            self._send(200, try_write(body["path"], body.get("content", "written by the probe\n")))
        elif path == "/connect":
            self._send(200, {"results": [try_connect(item) for item in body.get("paths", [])]})
        elif path == "/environ-scan":
            self._send(200, scan_environments(body.get("needles", [])))
        elif path == "/storage":
            content = body.pop("content", None)
            payload = content.encode("utf-8") if content is not None else b""
            if content is not None:
                body["size"] = len(payload)
            try:
                self._send(200, storage_call(body, payload))
            except OSError as exc:
                self._send(200, {"ok": False, "error": {"code": "socket", "message": f"{exc.__class__.__name__}: {exc}"}})
        else:
            self._send(404, {"error": "no such path", "path": path})

    def _websocket_echo(self) -> None:
        """Answer the upgrade, echo text frames until the client closes."""

        key = self.headers.get("Sec-WebSocket-Key")
        if (self.headers.get("Upgrade") or "").lower() != "websocket" or not key:
            self._send(400, {"error": "not a WebSocket upgrade", "upgrade": self.headers.get("Upgrade"), "connection": self.headers.get("Connection")})
            return
        accept = base64.b64encode(hashlib.sha1((key + WS_MAGIC).encode("ascii")).digest()).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.close_connection = True
        while True:
            head = self.rfile.read(2)
            if len(head) < 2:
                return
            opcode, length = head[0] & 0x0F, head[1] & 0x7F
            if length == 126:
                (length,) = struct.unpack(">H", self.rfile.read(2))
            elif length == 127:
                (length,) = struct.unpack(">Q", self.rfile.read(8))
            mask = self.rfile.read(4) if head[1] & 0x80 else b""
            data = self.rfile.read(length)
            if mask:
                data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
            if opcode == 0x8:  # close
                self.wfile.write(b"\x88\x00")
                return
            if opcode == 0x1:  # text: echo it back, unmasked
                reply = b"echo:" + data
                self.wfile.write(bytes([0x81, len(reply)]) + reply if len(reply) < 126 else bytes([0x81, 126]) + struct.pack(">H", len(reply)) + reply)
                self.wfile.flush()


def main() -> None:
    port = int(os.environ["PORT"])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"probe app version {VERSION} on 127.0.0.1:{port}, base path {BASE!r}, uid {os.getuid()}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
