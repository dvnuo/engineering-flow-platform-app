"""``efp-app dev``: the local stand-in for the gateway (docs/CONTRACTS.md, sections 6.1, 6.2 and 6.6).

Each test starts a tiny app on a loopback port inside the test and talks to
it through the gateway over plain sockets, so nothing but the standard
library takes part. ``run`` puts a time limit on every scenario: a gateway
that holds bytes back shows as a failure, not as a test that never ends.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

from efp_app import _devgateway, cli, config
from test_cli import good_app, run_cli, write_app

LOOPBACK = "127.0.0.1"
WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OUTSIDE = 'outside the app: %s %s  (a URL that starts with "/"?)'


def run(scenario):
    return asyncio.run(asyncio.wait_for(scenario, 30))


# --- A tiny app and a tiny browser ------------------------------------------------------------


class Message:
    """A request as the app got it, or an answer as the browser got it."""

    def __init__(self, first, headers, body=b""):
        self.first = first
        self.headers = headers
        self.body = body

    @property
    def method(self):
        return self.first.split(" ")[0]

    @property
    def target(self):
        return self.first.split(" ")[1]

    @property
    def status(self):
        return int(self.first.split(" ")[1])

    def all(self, name):
        return [value for key, value in self.headers if key == name]

    def get(self, name):
        return (self.all(name) or [None])[0]

    def json(self):
        return json.loads(self.body.decode("utf-8"))


def parse_head(head):
    first, *lines = head.decode("latin-1").split("\r\n")
    pairs = (line.partition(":") for line in lines if line)
    return Message(first, [(name.strip().lower(), value.strip()) for name, _, value in pairs])


async def read_chunked(reader):
    body = b""
    while True:
        size = int((await reader.readline()).strip(), 16)
        body += await reader.readexactly(size)
        await reader.readexactly(2)
        if size == 0:
            return body


def chunk(data):
    return b"%x\r\n%s\r\n" % (len(data), data)


def http_answer(status="200 OK", headers=(), body=b"ok"):
    lines = ["HTTP/1.1 " + status, "Content-Length: %d" % len(body)] + list(headers)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


async def say_ok(request, reader, writer):
    writer.write(http_answer())


class App:
    """The app behind the gateway: it keeps every request and answers as the test says."""

    def __init__(self, answer=say_ok):
        self.answer = answer
        self.requests = []
        self.lines = []  # what the gateway printed
        self.port = 0  # the gateway's
        self.app_port = 0
        self.server = None

    async def listen(self, port=0):
        self.server = await asyncio.start_server(self._handle, LOOPBACK, port)
        self.app_port = self.server.sockets[0].getsockname()[1]

    async def _handle(self, reader, writer):
        try:
            request = parse_head(await reader.readuntil(b"\r\n\r\n"))
            if request.get("transfer-encoding") == "chunked":
                request.body = await read_chunked(reader)
            else:
                request.body = await reader.readexactly(int(request.get("content-length") or 0))
            self.requests.append(request)
            await self.answer(request, reader, writer)
            await writer.drain()
        except (OSError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


@contextlib.asynccontextmanager
async def served(answer=say_ok, listening=True, slug="demo", **options):
    """An app with the gateway of ``/demo`` (or of ``/{slug}``, ``""``: at the top) in front of it."""
    app = App(answer)
    if listening:
        await app.listen()
    else:
        app.app_port = _devgateway.free_port()
    options.setdefault("strip_prefix", True)
    gateway = _devgateway.Gateway(slug, app.app_port, say=app.lines.append, **options)
    server = await gateway.start(0)
    app.port = server.sockets[0].getsockname()[1]
    try:
        yield app
    finally:
        server.close()
        if app.server is not None:
            app.server.close()


async def connect(port, method, target, headers=(), body=b""):
    reader, writer = await asyncio.open_connection(LOOPBACK, port)
    lines = ["%s %s HTTP/1.1" % (method, target), "Host: %s:%d" % (LOOPBACK, port)] + list(headers)
    if body:
        lines.append("Content-Length: %d" % len(body))
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)
    return reader, writer


async def ask(port, method, target, headers=(), body=b""):
    """One request of a browser; the answer is complete when the gateway closes the connection."""
    reader, writer = await connect(port, method, target, headers, body)
    try:
        answer = parse_head(await reader.readuntil(b"\r\n\r\n"))
        answer.body = await reader.read()
        return answer
    finally:
        writer.close()


NAVIGATION = ["Accept: text/html,application/xhtml+xml,*/*;q=0.8", "Sec-Fetch-Mode: navigate"]
FETCH = ["Accept: */*", "Sec-Fetch-Mode: cors"]


# --- Section 6.1: which requests reach the app -------------------------------------------------


def test_the_prefix_is_removed_for_an_app_that_serves_at_its_own_root():
    async def scenario():
        async with served(strip_prefix=True) as app:
            answer = await ask(app.port, "GET", "/demo/api/items?page=2")
            assert (answer.status, answer.body) == (200, b"ok")
            await ask(app.port, "GET", "/demo/")
            await ask(app.port, "GET", "/demo/demo/nested")
            assert [request.target for request in app.requests] == ["/api/items?page=2", "/", "/demo/nested"]
            assert app.lines == []

    run(scenario())


def test_the_prefix_is_kept_for_an_app_that_serves_under_it():
    async def scenario():
        async with served(strip_prefix=False) as app:
            await ask(app.port, "GET", "/demo/api/items?page=2")
            await ask(app.port, "GET", "/demo/")
            assert [request.target for request in app.requests] == ["/demo/api/items?page=2", "/demo/"]

    run(scenario())


def test_the_address_without_the_final_slash_redirects():
    async def scenario():
        async with served() as app:
            plain = await ask(app.port, "GET", "/demo", NAVIGATION)
            assert (plain.status, plain.get("location")) == (301, "/demo/")
            query = await ask(app.port, "GET", "/demo?tab=2")
            assert (query.status, query.get("location")) == (301, "/demo/?tab=2")
            # The next run on this port may be another app: nothing of the gateway is cached.
            assert plain.get("cache-control") == "no-store"
            assert app.requests == [] and app.lines == []

    run(scenario())


def test_the_top_of_the_host_leads_to_the_app_and_is_reported():
    async def scenario():
        async with served() as app:
            answer = await ask(app.port, "GET", "/", NAVIGATION)
            assert (answer.status, answer.get("location")) == (302, "/demo/")
            assert app.lines == ['outside the app: GET /  (sent on to /demo/; once deployed "/" is the app center, not this app)']
            assert app.requests == []

    run(scenario())


def test_an_address_outside_the_app_is_a_page_for_a_page_navigation():
    async def scenario():
        async with served() as app:
            answer = await ask(app.port, "GET", "/reports/2026?month=5", NAVIGATION)
            assert answer.status == 404
            assert answer.get("content-type") == "text/html; charset=utf-8"
            page = answer.body.decode("utf-8")
            assert "<h1>This address is outside the app</h1>" in page
            assert "This address is outside the app: /reports/2026." in page
            assert "The app is at /demo/" in page and '<a href="/demo/">' in page
            assert "A URL that starts with &quot;/&quot; in a page, script or style sheet points here" in page
            assert app.requests == []

    run(scenario())


def test_an_address_outside_the_app_is_json_for_anything_else():
    async def scenario():
        async with served() as app:
            for headers in (FETCH, [], ["Accept: text/html", "Sec-Fetch-Mode: no-cors"]):
                answer = await ask(app.port, "GET", "/static/app.js", headers)
                assert answer.status == 404
                assert answer.get("content-type") == "application/json"
                error = answer.json()["error"]
                assert error["code"] == "app_not_found"
                assert "outside the app: /static/app.js." in error["message"]
                assert "The app is at /demo/" in error["message"]
                assert 'A URL that starts with "/" in a page, script or style sheet points here' in error["message"]
            assert app.requests == []

    run(scenario())


def test_every_request_outside_the_app_is_one_line_on_the_console():
    async def scenario():
        async with served() as app:
            page = "Referer: http://%s:%d/demo/reports" % (LOOPBACK, app.port)
            await ask(app.port, "GET", "/static/app.js", FETCH)
            await ask(app.port, "POST", "/api/ask?stream=1", FETCH, body=b'{"text": "hi"}')
            await ask(app.port, "GET", "/static/style.css", [page])
            await ask(app.port, "GET", "/demolition/plan")  # the prefix is a whole segment
            await ask(app.port, "GET", "/favicon.ico", ["Referer: http://elsewhere.example/"])
            assert app.lines == [
                OUTSIDE % ("GET", "/static/app.js"),
                OUTSIDE % ("POST", "/api/ask?stream=1"),
                'outside the app: GET /static/style.css  (a URL that starts with "/" in /demo/reports?)',
                OUTSIDE % ("GET", "/demolition/plan"),
                "outside the app: GET /favicon.ico  (the browser asks for this by itself)",
            ]
            assert app.requests == []

    run(scenario())


def test_the_page_does_not_repeat_markup_from_the_address():
    async def scenario():
        async with served() as app:
            answer = await ask(app.port, "GET", "/x<script>alert(1)</script>", NAVIGATION)
            assert answer.status == 404
            assert b"<script>" not in answer.body and b"/x&lt;script&gt;alert(1)&lt;/script&gt;" in answer.body

    run(scenario())


def test_an_upload_to_an_address_outside_the_app_still_gets_its_answer():
    async def scenario():
        async with served() as app:
            answer = await ask(app.port, "POST", "/api/upload", FETCH, body=b"x" * (2 * 1024 * 1024))
            assert answer.status == 404 and answer.json()["error"]["code"] == "app_not_found"

    run(scenario())


def test_what_is_not_a_request_is_refused():
    async def scenario():
        async with served() as app:
            reader, writer = await asyncio.open_connection(LOOPBACK, app.port)
            writer.write(b"GET http://elsewhere.example/ HTTP/1.1\r\n\r\n")
            assert (await reader.read()).startswith(b"HTTP/1.1 400 Bad Request\r\n")
            writer.close()
            assert app.requests == []

    run(scenario())


# --- Section 6.2: what reaches the app ---------------------------------------------------------


def test_the_app_gets_the_forwarded_headers():
    async def scenario():
        async with served() as app:
            sent = [
                "X-Forwarded-For: 203.0.113.9",
                "X-Forwarded-Host: forged.example",
                "X-Forwarded-Prefix: /forged",
                "Cookie: theme=dark",
                "Accept-Language: en",
                "Connection: keep-alive",
            ]
            await ask(app.port, "GET", "/demo/", sent)
            request = app.requests[0]
            public = "%s:%d" % (LOOPBACK, app.port)
            assert request.all("x-forwarded-for") == ["203.0.113.9, 127.0.0.1"]
            assert request.all("x-forwarded-proto") == ["http"]
            assert request.all("x-forwarded-host") == [public]
            assert request.all("x-forwarded-prefix") == ["/demo"]
            assert request.all("host") == [public]  # the address the browser used, not the app's port
            assert request.all("cookie") == ["theme=dark"] and request.all("accept-language") == ["en"]
            assert request.all("connection") == ["close"]

    run(scenario())


def test_a_request_with_an_x_efp_header_is_refused():
    async def scenario():
        async with served() as app:
            inside = await ask(app.port, "GET", "/demo/api/me", ["x-efp-visitor: v1.forged.forged"])
            outside = await ask(app.port, "GET", "/elsewhere", ["X-EFP-Anything: 1"])
            assert inside.status == 400 and outside.status == 400
            assert "x-efp-visitor" in inside.json()["error"]["message"]
            assert app.lines == [
                "refused with 400: GET /demo/api/me  (the request carries x-efp-visitor; only the gateway may add X-EFP-* headers)",
                "refused with 400: GET /elsewhere  (the request carries X-EFP-Anything; only the gateway may add X-EFP-* headers)",
            ]
            assert app.requests == []

    run(scenario())


async def echo_body(request, reader, writer):
    writer.write(http_answer(body=request.body))


def test_a_request_body_reaches_the_app():
    async def scenario():
        async with served(echo_body) as app:
            small = json.dumps({"text": "What is the path prefix?"}).encode("utf-8")
            answer = await ask(app.port, "POST", "/demo/api/ask", ["Content-Type: application/json"], body=small)
            assert (answer.status, answer.body) == (200, small)
            assert app.requests[0].method == "POST" and app.requests[0].get("content-type") == "application/json"
            large = bytes(range(256)) * 4096  # 1 MiB: more than any buffer on the way
            answer = await ask(app.port, "PUT", "/demo/api/file", body=large)
            assert answer.body == large

    run(scenario())


def test_a_chunked_request_body_reaches_the_app():
    async def scenario():
        async with served(echo_body) as app:
            reader, writer = await connect(app.port, "POST", "/demo/api/ask", ["Transfer-Encoding: chunked"])
            writer.write(chunk(b"first,") + chunk(b"second") + b"0\r\n\r\n")
            answer = parse_head(await reader.readuntil(b"\r\n\r\n"))
            assert (answer.status, await reader.read()) == (200, b"first,second")
            writer.close()
            assert app.requests[0].get("transfer-encoding") == "chunked"

    run(scenario())


def test_expect_100_continue_is_answered_by_the_gateway():
    async def scenario():
        async with served(echo_body) as app:
            head = ["Expect: 100-continue", "Content-Length: 5"]
            reader, writer = await connect(app.port, "POST", "/demo/api/ask", head)
            assert await reader.readuntil(b"\r\n\r\n") == b"HTTP/1.1 100 Continue\r\n\r\n"
            writer.write(b"hello")
            answer = parse_head(await reader.readuntil(b"\r\n\r\n"))
            assert (answer.status, await reader.read()) == (200, b"hello")
            writer.close()
            assert app.requests[0].get("expect") is None

    run(scenario())


def test_a_streamed_answer_arrives_piece_by_piece():
    async def scenario():
        release = asyncio.Event()
        sent_last = []

        async def stream(request, reader, writer):
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
            writer.write(chunk(b"data: first\n\n"))
            await writer.drain()
            await release.wait()
            sent_last.append(True)
            writer.write(chunk(b"data: last\n\n") + b"0\r\n\r\n")

        async with served(stream) as app:
            reader, writer = await connect(app.port, "GET", "/demo/events", ["Accept: text/event-stream"])
            answer = parse_head(await reader.readuntil(b"\r\n\r\n"))
            assert answer.status == 200
            assert answer.get("content-type") == "text/event-stream" and answer.get("transfer-encoding") == "chunked"
            received = b""
            while b"data: first\n\n" not in received:
                received += await reader.read(4096)
            # The first piece is here while the app has not yet sent the last one.
            assert sent_last == [] and b"data: last" not in received
            release.set()
            received += await reader.read()
            assert received == chunk(b"data: first\n\n") + chunk(b"data: last\n\n") + b"0\r\n\r\n"
            writer.close()

    run(scenario())


def frame(payload, mask=None):
    """A WebSocket text frame of up to 125 bytes; the browser's frames are masked."""
    if mask is None:
        return bytes([0x81, len(payload)]) + payload
    return bytes([0x81, 0x80 | len(payload)]) + mask + bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))


async def read_frame(reader):
    first = await reader.readexactly(2)
    mask = await reader.readexactly(4) if first[1] & 0x80 else None
    payload = await reader.readexactly(first[1] & 0x7F)
    return payload if mask is None else bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))


def test_a_websocket_is_tunnelled():
    async def scenario():
        async def echo(request, reader, writer):
            digest = hashlib.sha1((request.get("sec-websocket-key") + WEBSOCKET_GUID).encode("ascii")).digest()
            accept = base64.b64encode(digest).decode("ascii")
            head = ["Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Accept: " + accept, "Set-Cookie: ws=1; Path=/"]
            writer.write(("HTTP/1.1 101 Switching Protocols\r\n" + "\r\n".join(head) + "\r\n\r\n").encode("ascii"))
            while True:
                writer.write(frame(b"echo: " + await read_frame(reader)))
                await writer.drain()

        async with served(echo, strip_prefix=False) as app:
            key = base64.b64encode(b"0123456789abcdef").decode("ascii")
            head = ["Upgrade: websocket", "Connection: Upgrade", "Sec-WebSocket-Key: " + key, "Sec-WebSocket-Version: 13"]
            reader, writer = await connect(app.port, "GET", "/demo/_stcore/stream", head)
            answer = parse_head(await reader.readuntil(b"\r\n\r\n"))
            assert answer.status == 101
            assert answer.get("upgrade") == "websocket" and answer.get("connection") == "Upgrade"
            expected = base64.b64encode(hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()).decode("ascii")
            assert answer.get("sec-websocket-accept") == expected
            assert answer.all("set-cookie") == ["ws=1; Path=/demo/"]
            # Two rounds: both directions stay open after the first answer.
            for text in (b"one", b"two"):
                writer.write(frame(text, mask=b"\x01\x02\x03\x04"))
                assert await read_frame(reader) == b"echo: " + text
            writer.close()

            request = app.requests[0]
            assert request.target == "/demo/_stcore/stream"
            assert request.get("upgrade") == "websocket" and request.get("connection") == "upgrade"
            assert request.get("sec-websocket-key") == key and request.get("x-forwarded-prefix") == "/demo"

    run(scenario())


# --- Section 6.2: what the gateway does to the app's answer -------------------------------------


@pytest.mark.parametrize(
    "cookie, expected",
    [
        ("session=abc", "session=abc; Path=/demo/"),
        ("session=abc; Path=/; HttpOnly; SameSite=Lax", "session=abc; HttpOnly; SameSite=Lax; Path=/demo/"),
        ("session=abc; path=/somewhere/else; Max-Age=60", "session=abc; Max-Age=60; Path=/demo/"),
        ("empty=", "empty=; Path=/demo/"),
        ("session=abc; Domain=example.com", None),
        ("session=abc; Path=/; domain=.example.com; Secure", None),
        ("__Host-efp_apps=forged; Path=/; Secure", None),
        ("__Secure-efp-x=1", None),
        ("=no-name", None),
        ("no-value", None),
    ],
)
def test_the_cookie_rule(cookie, expected):
    assert _devgateway.rewrite_set_cookie(cookie, "demo") == expected


def test_the_apps_cookies_are_confined_to_the_app():
    async def with_cookies(request, reader, writer):
        cookies = [
            "Set-Cookie: session=abc; Path=/; HttpOnly",
            "Set-Cookie: tracker=1; Domain=example.com",
            "Set-Cookie: __Host-efp_apps=forged; Path=/; Secure",
            "set-cookie: theme=dark",
        ]
        writer.write(http_answer(headers=cookies))

    async def scenario():
        async with served(with_cookies) as app:
            answer = await ask(app.port, "GET", "/demo/")
            assert answer.all("set-cookie") == ["session=abc; HttpOnly; Path=/demo/", "theme=dark; Path=/demo/"]

    run(scenario())


def test_the_answer_gets_the_headers_of_the_gateway():
    async def with_headers(request, reader, writer):
        headers = [
            "Content-Type: text/javascript",
            "Service-Worker-Allowed: /",
            "X-Content-Type-Options: none",
            "Referrer-Policy: unsafe-url",
            "Permissions-Policy: camera=*",
            "Connection: keep-alive",
            "X-App: kept",
        ]
        writer.write(http_answer("201 Created", headers, b"made"))

    async def scenario():
        async with served(with_headers) as app:
            answer = await ask(app.port, "GET", "/demo/")
            assert (answer.first, answer.body) == ("HTTP/1.1 201 Created", b"made")
            assert answer.all("service-worker-allowed") == []
            assert answer.all("x-content-type-options") == ["nosniff"]
            assert answer.all("x-robots-tag") == ["noindex"]
            assert answer.all("referrer-policy") == ["same-origin"]
            assert answer.all("permissions-policy") == ["camera=(), microphone=(), geolocation=(), display-capture=()"]
            assert answer.all("connection") == ["close"]
            assert answer.get("content-type") == "text/javascript" and answer.get("x-app") == "kept"

    run(scenario())


def test_a_redirect_of_the_app_that_leaves_the_app_is_reported():
    async def scenario():
        async def redirect(request, reader, writer):
            where = {
                "/save": "/done",
                "/same-host": "http://%s/done?x=1" % request.get("host"),
                "/fine": "/demo/done",
                "/home": "/demo",
                "/relative": "done",
                "/other-host": "https://portal.example.com/done",
            }[request.target]
            writer.write(http_answer("303 See Other", ["Location: " + where], b""))

        async with served(redirect) as app:
            public = "%s:%d" % (LOOPBACK, app.port)
            expected = {
                "/save": "/done",
                "/same-host": "http://%s/done?x=1" % public,
                "/fine": "/demo/done",
                "/home": "/demo",
                "/relative": "done",
                "/other-host": "https://portal.example.com/done",
            }
            for path, location in expected.items():
                answer = await ask(app.port, "POST", "/demo" + path)
                # As on the host, the address goes to the browser as the app wrote it.
                assert (answer.status, answer.get("location")) == (303, location)
            assert app.lines == [
                'outside the app: POST /demo/save answers with a redirect to /done  (a "Location" without the prefix?)',
                'outside the app: POST /demo/same-host answers with a redirect to http://%s/done?x=1  (a "Location" without the prefix?)'
                % public,
            ]

    run(scenario())


def test_the_redirect_of_the_local_file_server_gets_the_prefix():
    async def to_the_directory(request, reader, writer):
        writer.write(http_answer("301 Moved Permanently", ["Location: " + request.target + "/"], b""))

    async def scenario():
        async with served(to_the_directory, local_file_server=True) as app:
            answer = await ask(app.port, "GET", "/demo/docs")
            # nginx, which serves a static app on the host, redirects under the public path.
            assert (answer.status, answer.get("location")) == (301, "/demo/docs/")
            assert app.lines == []

    run(scenario())


# --- An app that is not there -------------------------------------------------------------------


def test_an_app_that_does_not_listen_is_a_503():
    async def scenario():
        async with served(listening=False) as app:
            fetched, opened = await asyncio.gather(
                ask(app.port, "GET", "/demo/api/me", FETCH), ask(app.port, "GET", "/demo/", NAVIGATION)
            )
            assert fetched.status == 503 and fetched.get("retry-after") == "2"
            assert fetched.json()["error"]["code"] == "app_starting"
            assert opened.status == 503 and b'<meta http-equiv="refresh" content="2">' in opened.body
            await ask(app.port, "GET", "/demo/again")
            # One line, not one for every refresh of the page.
            assert len(app.lines) == 1
            assert app.lines[0].startswith("no answer from the app on 127.0.0.1:%d: GET /demo/" % app.app_port)

    run(scenario())


def test_a_request_waits_for_an_app_that_is_still_starting():
    async def scenario():
        async with served(listening=False, startup_timeout=20) as app:
            waiting = asyncio.ensure_future(ask(app.port, "GET", "/demo/"))
            await asyncio.sleep(0.5)
            assert not waiting.done()
            await app.listen(app.app_port)
            answer = await waiting
            assert (answer.status, answer.body) == (200, b"ok")
            assert app.lines == []

    run(scenario())


def test_an_app_that_hangs_up_is_a_503():
    async def hang_up(request, reader, writer):
        pass

    async def scenario():
        async with served(hang_up) as app:
            answer = await ask(app.port, "GET", "/demo/crash", FETCH)
            assert answer.status == 503 and answer.json()["error"]["code"] == "app_unavailable"
            assert answer.get("retry-after") is None
            assert app.lines == ["the app closed the connection without an answer: GET /demo/crash"]

    run(scenario())


# --- The --prefix argument ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, slug",
    [("demo", "demo"), ("/demo", "demo"), ("my-app-2", "my-app-2"), ("a1b", "a1b"), ("9" * 40, "9" * 40)],
)
def test_a_prefix_is_a_slug_with_or_without_the_slash(text, slug):
    assert _devgateway.parse_prefix(text) == slug


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("", "is not a slug"),
        ("ab", "is not a slug"),
        ("a" * 41, "is not a slug"),
        ("Demo", "is not a slug"),
        ("my_app", "is not a slug"),
        ("-demo", "is not a slug"),
        ("demo-", "is not a slug"),
        ("/demo/", "is not a slug"),
        ("//demo", "is not a slug"),
        ("demo/reports", "is not a slug"),
        ("C:/Program Files/Git/demo", 'This shell rewrote a leading "/"'),
        ("static", '"static" is a reserved name: no app is ever served at /static/'),
        ("/api", '"api" is a reserved name'),
    ],
)
def test_anything_else_is_refused_with_the_reason(text, fragment):
    with pytest.raises(ValueError) as caught:
        _devgateway.parse_prefix(text)
    assert fragment in str(caught.value)


def test_the_reserved_names_are_those_of_the_contract():
    assert _devgateway.RESERVED_SLUGS == {
        "_efp", "api", "app", "apps", "admin", "assets", "auth", "efp", "health", "login", "logout", "portal", "static", "www",
    }  # fmt: skip


@pytest.mark.parametrize("prefix, fragment", [("My_App", '"My_App" is not a slug'), ("static", "reserved name")])
def test_dev_refuses_a_bad_prefix(tmp_path, capsys, prefix, fragment):
    with pytest.raises(SystemExit) as caught:
        cli.main(["dev", str(good_app(tmp_path / "app")), "--prefix", prefix])
    assert caught.value.code == 2
    err = capsys.readouterr().err
    assert "argument --prefix: " in err and fragment in err


# --- efp-app dev --prefix: what is started ------------------------------------------------------

APP_PORT = 54321


class FakeProcess:
    """An app process that ends with exit code 7 at the second look, unless ``fate`` says otherwise.

    ``ctrl-c``: the developer presses Ctrl+C, and the app is slow to leave.
    ``kill``: somebody sends SIGTERM to the command; the app ends when it is asked to.
    """

    started = []
    fate = "ends"

    def __init__(self, argv, cwd=None, env=None):
        self.argv, self.cwd, self.env = list(argv), cwd, dict(env)
        self.returncode = None
        self.looks = 0
        self.asked = []
        FakeProcess.started.append(self)

    def poll(self):
        self.looks += 1
        if self.looks < 2:
            return None
        if FakeProcess.fate == "ctrl-c":
            raise KeyboardInterrupt
        if FakeProcess.fate == "kill" and "terminate" not in self.asked:
            signal.raise_signal(signal.SIGTERM)
            return None
        self.returncode = 7
        return self.returncode

    def wait(self, timeout=None):
        self.asked.append("wait")
        if FakeProcess.fate == "ctrl-c" and "terminate" not in self.asked:
            raise subprocess.TimeoutExpired(self.argv, timeout)
        return 0

    def terminate(self):
        self.asked.append("terminate")

    def kill(self):
        self.asked.append("kill")


@pytest.fixture
def prefixed(monkeypatch):
    """Record what `efp-app dev --prefix` would start instead of starting it; the app's port is APP_PORT."""
    monkeypatch.setattr(FakeProcess, "started", [])
    monkeypatch.setattr(FakeProcess, "fate", "ends")
    monkeypatch.setattr(cli.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(cli.subprocess, "call", lambda *a, **k: pytest.fail("the app is started behind the gateway"))
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(_devgateway, "free_port", lambda: APP_PORT)
    return FakeProcess.started


def test_dev_dry_run_with_a_prefix_shows_the_gateway_and_the_command(tmp_path, capsys, prefixed):
    code, out, err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")), "--port", "8123", "--prefix", "/demo", "--dry-run")
    assert code == 0 and err == "" and prefixed == []
    assert "address    http://127.0.0.1:8123/demo/  (under /demo, as the app is addressed once deployed)" in out
    assert "gateway    127.0.0.1:8123 -> the app on 127.0.0.1:54321, with /demo removed from the path" in out
    assert "a request outside /demo/ gets 404 and a line here" in out
    assert "open the address above, not the one the app's own server prints" in out
    assert "-m uvicorn main:app --host 127.0.0.1 --port 54321 --root-path /demo --no-server-header" in out
    assert "sets       EFP_APP_BASE_PATH=/demo" in out
    assert "sets       EFP_APP_PUBLIC_URL=http://127.0.0.1:8123/demo/" in out
    assert "sets       EFP_APP_SLUG=demo" in out


FLASK_ON_THIS_SYSTEM = (
    "-m flask --app efp_app._devgateway:flask_app('app:app') run --port 54321"
    if sys.platform == "win32"
    else "-m gunicorn app:app --bind 127.0.0.1:54321"
)


@pytest.mark.parametrize(
    "manifest, files, expected",
    [
        (
            {"type": "flask", "entry": "app:app"},
            {"app.py": "app = None\n"},
            ["/demo kept in the path", FLASK_ON_THIS_SYSTEM, "sets       SCRIPT_NAME=/demo"],
        ),
        (
            {"type": "streamlit", "entry": "app.py"},
            {"app.py": ""},
            ["/demo kept in the path", "--server.port 54321 --server.address 127.0.0.1 --server.baseUrlPath /demo"],
        ),
        (
            {"type": "gradio", "entry": "app.py"},
            {"app.py": ""},
            ["/demo removed from the path", "sets       GRADIO_ROOT_PATH=/demo", "sets       GRADIO_SERVER_PORT=54321"],
        ),
        (
            {"type": "static", "entry": "public"},
            {"public/index.html": "hi"},
            ["/demo removed from the path", "-m http.server 54321 --bind 127.0.0.1 --directory public", "local preview"],
        ),
        # "strip_prefix" in the manifest wins over the default of the type.
        ({"type": "flask", "entry": "app:app", "strip_prefix": True}, {"app.py": "app = None\n"}, ["/demo removed from the path"]),
        ({"type": "fastapi", "entry": "main:app", "strip_prefix": False}, {"main.py": "app = None\n"}, ["/demo kept in the path"]),
    ],
)
def test_dev_dry_run_with_a_prefix_for_each_type(tmp_path, capsys, prefixed, manifest, files, expected):
    app = write_app(tmp_path / "app", dict({"name": "x"}, **manifest), files)
    code, out, _err = run_cli(capsys, "dev", str(app), "--port", "8123", "--prefix", "demo", "--dry-run")
    assert code == 0 and prefixed == []
    assert "http://127.0.0.1:8123/demo/" in out
    for fragment in expected:
        assert fragment in out, fragment


def test_dev_with_a_prefix_starts_the_app_behind_the_gateway(tmp_path, capsys, prefixed, monkeypatch):
    monkeypatch.setenv("EFP_APP_BASE_PATH", "/left-over")
    monkeypatch.setenv("EFP_APP_PUBLIC_URL", "https://left-over.example/")
    app = good_app(tmp_path / "app")
    port = socket_free_port()
    code, out, err = run_cli(capsys, "dev", str(app), "--port", str(port), "--prefix", "demo")
    # The command ends when the app's process ends, with its exit code.
    assert code == 7 and err == ""
    (process,) = prefixed
    assert process.argv == [
        sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "54321",
        "--root-path", "/demo", "--no-server-header",
    ]  # fmt: skip
    assert process.cwd == str(app.resolve())
    env = process.env
    assert env["PORT"] == "54321"
    assert env["EFP_APP_BASE_PATH"] == "/demo"
    assert env["EFP_APP_SLUG"] == "demo"
    assert env["EFP_APP_PUBLIC_URL"] == "http://127.0.0.1:%d/demo/" % port
    assert "http://127.0.0.1:%d/demo/" % port in out

    # Identity and storage stay those of a developer's machine: nothing says "deployed".
    for name in ("EFP_APP_ID", "EFP_VISITOR_KEY", "EFP_APP_DATA_DIR", "EFP_APP_STORAGE_SOCKET"):
        assert name not in env
    settings = config.load(env)
    assert settings.deployed is False
    assert settings.base_path == "/demo" and settings.slug == "demo"


def test_ctrl_c_ends_the_gateway_and_then_the_app(tmp_path, capsys, prefixed, monkeypatch):
    monkeypatch.setattr(FakeProcess, "fate", "ctrl-c")
    code, _out, _err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")), "--port", str(socket_free_port()), "--prefix", "demo")
    assert code == 130
    # The app got the Ctrl+C too: it is waited for first and asked to end only when it stays.
    assert prefixed[0].asked == ["wait", "terminate", "wait"]


def test_sigterm_for_the_command_ends_the_app_as_well(tmp_path, capsys, prefixed, monkeypatch):
    before = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr(FakeProcess, "fate", "kill")
    code, _out, _err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")), "--port", str(socket_free_port()), "--prefix", "demo")
    # The signal went on to the app, and the command ended with the app.
    assert prefixed[0].asked[0] == "terminate"
    assert code == 7
    assert signal.getsignal(signal.SIGTERM) is before


def test_the_app_is_not_started_when_the_port_is_taken(tmp_path, capsys, prefixed):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as taken:
        taken.bind((LOOPBACK, 0))
        taken.listen(1)
        port = taken.getsockname()[1]
        code, _out, err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")), "--port", str(port), "--prefix", "demo")
    assert code == 1 and prefixed == []
    assert "efp-app dev: could not listen on 127.0.0.1:%d" % port in err


def test_without_a_prefix_the_app_is_behind_the_gateway_at_the_top(tmp_path, capsys, prefixed):
    port = socket_free_port()
    code, out, _err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")), "--port", str(port))
    assert code == 7  # the exit code of the (fake) app
    process = prefixed[0]
    assert process.argv == [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(APP_PORT), "--no-server-header"]
    assert process.env["PORT"] == str(APP_PORT)
    assert "EFP_APP_BASE_PATH" not in process.env and "EFP_APP_SLUG" not in process.env
    # The address is the gateway's, so that app.public_url() names the port that answers /_efp/lib/.
    assert process.env["EFP_APP_PUBLIC_URL"] == "http://127.0.0.1:%d/" % port
    assert "address    http://127.0.0.1:%d/\n" % port in out
    assert "gateway    127.0.0.1:%d -> the app on 127.0.0.1:%d, paths unchanged" % (port, APP_PORT) in out


def test_without_a_prefix_every_path_and_cookie_reaches_the_app_unchanged():
    async def say_cookie(request, reader, writer):
        writer.write(http_answer(headers=["Set-Cookie: theme=dark; Path=/settings; Domain=example.com"]))

    async def scenario():
        async with served(say_cookie, slug="") as app:
            deep = await ask(app.port, "GET", "/settings/x?y=1", FETCH)
            top = await ask(app.port, "GET", "/", NAVIGATION)
            return app, deep, top

    app, deep, top = run(scenario())
    assert [request.target for request in app.requests] == ["/settings/x?y=1", "/"]
    assert deep.get("set-cookie") == "theme=dark; Path=/settings; Domain=example.com"
    assert app.requests[0].get("x-forwarded-prefix") is None
    assert app.requests[0].get("x-forwarded-proto") == "http"
    assert top.status == 200 and app.lines == []


# --- Section 6.6: the shared browser libraries ----------------------------------------------------


@pytest.fixture
def libraries(tmp_path):
    """A lib/ directory with one library in it, and a file next to it that must stay out of reach."""
    lib = tmp_path / "lib"
    (lib / "kit@1.0.0").mkdir(parents=True)
    (lib / "kit@1.0.0" / "kit.css").write_bytes(b"body{}")
    (lib / "kit@1.0.0" / "LICENSE").write_bytes(b"MIT")
    (lib / "MANIFEST.json").write_bytes(b"{}")
    (tmp_path / "secret.txt").write_bytes(b"no")
    return lib


@pytest.mark.parametrize("slug", ["demo", ""])
def test_the_shared_libraries_are_answered_by_the_gateway(libraries, slug):
    async def scenario():
        async with served(slug=slug, lib_dir=libraries) as app:
            css = await ask(app.port, "GET", "/_efp/lib/kit@1.0.0/kit.css", FETCH)
            head = await ask(app.port, "HEAD", "/_efp/lib/kit@1.0.0/kit.css?v=2", FETCH)
            notice = await ask(app.port, "GET", "/_efp/lib/kit@1.0.0/LICENSE", FETCH)
            return app, css, head, notice

    app, css, head, notice = run(scenario())
    assert (css.status, css.body) == (200, b"body{}")
    assert css.get("content-type") == "text/css; charset=utf-8"
    assert css.get("x-content-type-options") == "nosniff"
    assert (head.status, head.get("content-length"), head.body) == (200, "6", b"")
    assert notice.get("content-type") == "text/plain; charset=utf-8"
    assert app.requests == [] and app.lines == []  # the app never sees them


def test_what_is_not_a_library_file_is_a_404_that_names_the_versions_there_are(libraries):
    targets = [
        "/_efp/lib/kit@1.0.1/kit.css",
        "/_efp/lib/kit@1.0.0/../../secret.txt",
        "/_efp/lib/kit@1.0.0/%2e%2e/%2e%2e/secret.txt",
        "/_efp/lib/.hidden",
        "/_efp/lib/kit@1.0.0",
        "/_efp/lib/",
    ]

    async def scenario():
        async with served(lib_dir=libraries) as app:
            return app, [await ask(app.port, "GET", target, FETCH) for target in targets]

    app, answers = run(scenario())
    assert [answer.status for answer in answers] == [404] * len(targets)
    assert {answer.json()["error"]["code"] for answer in answers} == {"not_found"}
    assert all(b"no" != answer.body for answer in answers)
    assert app.lines[0] == "not in the shared libraries: GET /_efp/lib/kit@1.0.1/kit.css  (there is kit@1.0.0)"
    assert app.requests == []


def test_the_shared_libraries_are_read_only(libraries):
    async def scenario():
        async with served(lib_dir=libraries) as app:
            return app, await ask(app.port, "POST", "/_efp/lib/kit@1.0.0/kit.css", FETCH, b"x")

    app, answer = run(scenario())
    assert answer.status == 405 and app.requests == []


def test_an_sdk_without_the_libraries_says_so(tmp_path):
    async def scenario():
        async with served(lib_dir=tmp_path / "nowhere") as app:
            return app, await ask(app.port, "GET", "/_efp/lib/kit@1.0.0/kit.css", NAVIGATION)

    app, answer = run(scenario())
    assert answer.status == 404 and b"not one of the shared libraries" in answer.body
    assert app.lines == ["not in the shared libraries: GET /_efp/lib/kit@1.0.0/kit.css  (this installation of the SDK has no shared libraries)"]


def test_a_checkout_finds_the_libraries_of_the_repository():
    found = _devgateway.library_directory()
    assert found is not None and found.name == "lib" and (found / "MANIFEST.json").is_file()


def socket_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK, 0))
        return probe.getsockname()[1]


# --- Flask on Windows ----------------------------------------------------------------------------


def test_flask_command_line_on_windows_under_a_prefix():
    found = cli._manifest.parse({"name": "x", "type": "flask", "entry": "shop.web:app"})
    launch = cli.build_launch(found, 5001, base_path="/demo", python="python", platform="win32")
    assert launch.argv == ["python", "-m", "flask", "--app", "efp_app._devgateway:flask_app('shop.web:app')", "run", "--port", "5001"]
    assert launch.env == {"SCRIPT_NAME": "/demo"}
    assert launch.needs == "flask" and "gunicorn" in launch.note


def test_script_name_is_applied_the_way_gunicorn_applies_it(tmp_path, monkeypatch):
    (tmp_path / "efp_prefix_probe.py").write_text(
        "class App:\n"
        "    def wsgi_app(self, environ, start_response):\n"
        "        return environ.get('SCRIPT_NAME'), environ['PATH_INFO']\n"
        "\n"
        "class holder:\n"
        "    app = App()\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("SCRIPT_NAME", "/demo")
    try:
        app = _devgateway.flask_app("efp_prefix_probe:holder.app")
    finally:
        sys.modules.pop("efp_prefix_probe", None)
    assert app.wsgi_app({"PATH_INFO": "/demo/static/app.js", "SCRIPT_NAME": ""}, None) == ("/demo", "/static/app.js")
    assert app.wsgi_app({"PATH_INFO": "/demo/"}, None) == ("/demo", "/")


def test_a_flask_app_builds_its_urls_under_the_prefix(tmp_path, monkeypatch):
    pytest.importorskip("flask")
    (tmp_path / "efp_prefix_flask.py").write_text(
        "from flask import Flask, redirect, url_for\n"
        "\n"
        "app = Flask(__name__)\n"
        "\n"
        "@app.get('/')\n"
        "def index():\n"
        "    return url_for('static', filename='app.js')\n"
        "\n"
        "@app.post('/save')\n"
        "def save():\n"
        "    return redirect(url_for('index'))\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("SCRIPT_NAME", "/demo")
    try:
        app = _devgateway.flask_app("efp_prefix_flask:app")
    finally:
        sys.modules.pop("efp_prefix_flask", None)
    browser = app.test_client()
    assert browser.get("/demo/").get_data(as_text=True) == "/demo/static/app.js"
    assert browser.post("/demo/save").headers["Location"] == "/demo/"


# --- The whole command, for real ------------------------------------------------------------------


def fetch(port, target, attempts=60):
    """GET over a plain socket; the gateway may not be listening yet."""
    for _ in range(attempts):
        try:
            connection = socket.create_connection((LOOPBACK, port), timeout=90)
            break
        except OSError:
            time.sleep(0.25)
    else:
        raise AssertionError("nothing listens on port %d" % port)
    with connection:
        connection.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\n\r\n" % (target, LOOPBACK, port)).encode("ascii"))
        received = b""
        while True:
            data = connection.recv(65536)
            if not data:
                break
            received += data
    head, _, body = received.partition(b"\r\n\r\n")
    answer = parse_head(head + b"\r\n\r\n")
    answer.body = body
    return answer


def test_a_static_app_under_a_prefix_for_real(tmp_path, capsys, monkeypatch):
    app = write_app(
        tmp_path / "site",
        {"name": "Site", "type": "static", "entry": "public"},
        {
            "public/index.html": '<script src="app.js"></script>',
            "public/app.js": 'fetch("data.json");',
            "public/docs/index.html": "docs",
        },
    )
    port = socket_free_port()
    started, codes = [], []
    popen = subprocess.Popen

    def recording(*arguments, **options):
        started.append(popen(*arguments, **options))
        return started[-1]

    monkeypatch.setattr(cli.subprocess, "Popen", recording)
    command = threading.Thread(
        target=lambda: codes.append(cli.main(["dev", str(app), "--port", str(port), "--prefix", "demo"])), daemon=True
    )
    command.start()
    try:
        page = fetch(port, "/demo/")  # waits for the file server to start
        assert (page.status, page.body) == (200, b'<script src="app.js"></script>')
        assert page.get("x-content-type-options") == "nosniff"
        assert fetch(port, "/demo/app.js").body == b'fetch("data.json");'
        # A directory without the final slash: the redirect stays inside the app, as with nginx.
        assert fetch(port, "/demo/docs").get("location") == "/demo/docs/"
        assert fetch(port, "/demo/docs/").body == b"docs"
        assert fetch(port, "/demo").get("location") == "/demo/"
        outside = fetch(port, "/app.js")
        assert outside.status == 404 and outside.json()["error"]["code"] == "app_not_found"
    finally:
        for process in started:
            process.terminate()
        command.join(30)
    # The command ended because the app's process ended, and with its exit code.
    assert not command.is_alive()
    assert codes == [started[0].returncode]
    out = capsys.readouterr().out
    assert "http://127.0.0.1:%d/demo/" % port in out
    assert OUTSIDE % ("GET", "/app.js") in out
