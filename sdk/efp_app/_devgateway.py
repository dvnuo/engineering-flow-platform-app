"""A local stand-in for the EFP gateway: ``efp-app dev --prefix NAME``.

Once deployed, an app is served at ``https://<apps host>/<slug>/`` behind the
host's gateway; ``efp-app dev`` alone serves it at ``/``. That difference
hides root-absolute URLs, redirects that leave the app and a framework that
does not know its prefix until the app is deployed. This module puts a small
gateway in front of ONE app on a developer's machine and treats it the way
the real one does (docs/CONTRACTS.md, sections 6.1 and 6.2):

- ``/NAME`` redirects to ``/NAME/``; ``/NAME/...`` goes to the app, with the
  prefix removed or kept as the app's type says;
- the app gets ``X-Forwarded-For``, ``-Proto``, ``-Host`` and ``-Prefix``; a
  request that carries an ``X-EFP-*`` header is refused with 400;
- request bodies, streamed answers and WebSockets pass through as they come;
- the app's cookies are confined to ``/NAME/`` and its answer gets the
  headers the gateway adds;
- every other address answers 404 and is printed on the console: a request
  that leaves the app is what this mode exists to show.

- ``/_efp/lib/...`` is answered by this gateway from the shared browser
  libraries (section 6.6), as the host answers it, and never reaches the app.

Without ``--prefix`` the same gateway stands in front of the app at ``/``
(``slug=""``): it answers ``/_efp/lib/`` and passes everything else on with
its path and cookies unchanged, so that a page linking the shared libraries
looks the same as it will once deployed.

Not reproduced: sign-in and the visitor header, the app center, the pages for
an app that is stopped or switched off, rate limits. Identity and storage
stay those of a developer's machine.

Standard library only. Each connection carries one request, as nginx talks to
an app: once the head has been rewritten, bytes are copied both ways until
one side closes, which is all that streaming and WebSockets need.
"""

from __future__ import annotations

import asyncio
import html
import importlib
import json
import os
import re
import socket
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple
from urllib.parse import urlsplit

HOST = "127.0.0.1"

# docs/CONTRACTS.md, section 6.6: the shared browser libraries.
LIB_PREFIX = "/_efp/lib/"
# Path segments of a library file: no leading dot, so never "." or "..".
_LIB_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9@._+-]*(?:/[A-Za-z0-9_][A-Za-z0-9@._+-]*)*")
_LIB_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".png": "image/png",
}


def library_directory() -> Optional[Path]:
    """Where the shared libraries are: package data in a wheel, ``lib/`` in a checkout of the repository."""
    here = Path(__file__).resolve().parent
    for candidate in (here / "_lib", here.parents[1] / "lib"):
        if (candidate / "MANIFEST.json").is_file():
            return candidate
    return None


def library_file(lib_dir: Path, relative: str) -> Optional[Path]:
    """The file ``/_efp/lib/<relative>`` names, or ``None``: never a directory, never outside ``lib_dir``."""
    if not _LIB_PATH.fullmatch(relative):
        return None
    candidate = lib_dir.joinpath(*relative.split("/"))
    return candidate if candidate.is_file() else None


def library_versions(lib_dir: Path, relative: str) -> List[str]:
    """The directories in ``lib_dir`` of the library ``relative`` starts with, such as ``["daisyui@5.8.2"]``."""
    name = relative.partition("/")[0].partition("@")[0]
    if not name or not lib_dir.is_dir():
        return []
    return sorted(p.name for p in lib_dir.iterdir() if p.is_dir() and p.name.partition("@")[0] == name)

# docs/CONTRACTS.md, section 1.
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{1,38}[a-z0-9]")
RESERVED_SLUGS = frozenset(
    {"_efp", "api", "app", "apps", "admin", "assets", "auth", "efp", "health", "login", "logout", "portal", "static", "www"}
)

# An origin-form request target without blanks or control characters.
_TARGET = re.compile(r"/[\x21-\x7e\x80-\xff]*")
# nginx sets these itself or never passes them on to an app.
_NOT_PASSED_ON = frozenset(
    {"connection", "upgrade", "keep-alive", "te", "expect"}
    | {"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-forwarded-prefix"}
)
# Section 6.2: taken from the app's answer; the lines below then go in with the gateway's values.
_TAKEN_FROM_ANSWER = frozenset(
    {"service-worker-allowed", "x-content-type-options", "x-robots-tag", "referrer-policy", "permissions-policy"}
)
_ADDED_TO_ANSWER = [
    "X-Content-Type-Options: nosniff",
    "X-Robots-Tag: noindex",
    "Referrer-Policy: same-origin",
    "Permissions-Policy: camera=(), microphone=(), geolocation=(), display-capture=()",
]
# Cookie names (lower case) that belong to the gateway: an app may not set them.
_GATEWAY_COOKIES = ("__host-efp", "__secure-efp")
# Addresses a browser asks for without any page naming them.
_ASKED_BY_THE_BROWSER = ("/favicon.ico", "/.well-known/")

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
%(refresh)s<title>%(title)s</title>
<style>
body { font: 16px/1.5 system-ui, sans-serif; max-width: 40rem; margin: 15vh auto 0; padding: 0 1.5rem; color: #1f2933; }
h1 { font-size: 1.4rem; }
p.note { color: #52606d; font-size: 0.9rem; }
</style>
</head>
<body>
<h1>%(title)s</h1>
<p>%(message)s</p>
<p><a href="%(base)s/">Open the app at %(base)s/</a></p>
<p class="note">efp-app dev: a local stand-in for the EFP gateway.</p>
</body>
</html>
"""

Headers = List[Tuple[str, str]]


def parse_prefix(text: str) -> str:
    """The slug of ``--prefix NAME`` (``NAME`` or ``/NAME``); ``ValueError`` says what is wrong."""
    slug = text[1:] if text.startswith("/") else text
    if not _SLUG.fullmatch(slug):
        # Git Bash on Windows turns an argument such as /demo into C:/Program Files/Git/demo.
        hint = '. This shell rewrote a leading "/": write the name without it' if ":" in text else ""
        raise ValueError(
            '"%s" is not a slug: 3 to 40 characters, lower-case letters, digits and "-", '
            "with a letter or a digit at both ends (for example: my-app)%s" % (text, hint)
        )
    if slug in RESERVED_SLUGS:
        raise ValueError('"%s" is a reserved name: no app is ever served at /%s/' % (slug, slug))
    return slug


def free_port() -> int:
    """A loopback port nothing listens on right now."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        return int(probe.getsockname()[1])


def rewrite_set_cookie(value: str, slug: str) -> Optional[str]:
    """One ``Set-Cookie`` of the app as the browser gets it; ``None`` when the gateway drops it.

    The rule of host/nginx/efp_gateway.js: a cookie without a name, with a
    name of the gateway or with ``Domain=`` is dropped; any ``Path`` becomes
    ``/{slug}/``, so the cookie stays with this app.
    """
    pair, *attributes = [part.strip() for part in value.split(";")]
    name = pair.partition("=")[0].strip().lower()
    if "=" not in pair or not name or name.startswith(_GATEWAY_COOKIES):
        return None
    kept = [pair]
    for attribute in attributes:
        key = attribute.partition("=")[0].strip().lower()
        if key == "domain":
            return None
        if attribute and key != "path":
            kept.append(attribute)
    return "; ".join(kept + ["Path=/%s/" % slug])


def _parse_head(head: bytes) -> Tuple[str, Headers]:
    """The first line and the headers of a request or of an answer."""
    first, *lines = head.decode("latin-1").split("\r\n")
    headers = []
    for line in lines:
        name, colon, value = line.partition(":")
        if colon and name.strip():
            headers.append((name.strip(), value.strip()))
    return first, headers


def _get(headers: Headers, name: str) -> str:
    """The first value of the header ``name`` (lower case), or ``""``."""
    return next((value for key, value in headers if key.lower() == name), "")


def _is_navigation(headers: Headers) -> bool:
    """Section 6.1, item 4: a page the browser is about to show gets a page, anything else JSON."""
    mode = _get(headers, "sec-fetch-mode").lower()
    if mode and mode != "navigate":
        return False
    return "text/html" in _get(headers, "accept").lower()


def _path_here(url: str, host: str) -> Optional[str]:
    """The path of ``url`` when it is an address of this host, else ``None``."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return parts.path if parts.netloc in ("", host) and parts.path.startswith("/") else None


def _send(writer: asyncio.StreamWriter, status: int, headers: List[str], body: bytes = b"") -> None:
    """An answer of the gateway itself. Never cached: the next run on this port may be another app."""
    lines = ["HTTP/1.1 %d %s" % (status, HTTPStatus(status).phrase)] + headers
    lines += ["Content-Length: %d" % len(body), "Cache-Control: no-store", "Connection: close"]
    writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body)


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes as they arrive, holding none back, until the reading side ends."""
    while True:
        data = await reader.read(65536)
        if not data:
            return
        writer.write(data)
        await writer.drain()


async def _finish(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Send what is left and let the browser hang up first.

    Closing while a request body is still arriving resets the connection, and
    the browser then reports a network error instead of the answer.
    """
    await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()

    async def swallow() -> None:
        while await reader.read(65536):
            pass

    try:
        await asyncio.wait_for(swallow(), 2)
    except asyncio.TimeoutError:
        pass


class Gateway:
    """The gateway of one app: ``/{slug}/...`` goes to the app on ``127.0.0.1:app_port``.

    ``say`` prints one line on the console. A request that arrives while the
    app is still starting waits for it, for up to ``startup_timeout`` seconds
    after ``start``. ``local_file_server`` marks a static app, which the host
    serves from nginx and ``efp-app dev`` from Python's file server: see
    ``_location``. ``slug=""`` puts the gateway in front of an app served at
    ``/``. ``lib_dir`` is where ``/_efp/lib/`` is read from (by default
    ``library_directory()``).
    """

    def __init__(
        self,
        slug: str,
        app_port: int,
        *,
        strip_prefix: bool,
        say: Callable[[str], None] = print,
        startup_timeout: float = 0.0,
        local_file_server: bool = False,
        lib_dir: Optional[Path] = None,
    ) -> None:
        self.slug = slug
        self.base = "/" + slug if slug else ""
        self.lib_dir = lib_dir if lib_dir is not None else library_directory()
        self.app_port = app_port
        self.strip_prefix = strip_prefix
        self.say = say
        self.startup_timeout = startup_timeout
        self.local_file_server = local_file_server
        self._patience_ends = 0.0
        self._app_has_answered = False
        self._silence_reported = False

    async def start(self, port: int) -> asyncio.AbstractServer:
        """Listen on the loopback ``port``; ``OSError`` when it is taken."""
        self._patience_ends = time.monotonic() + self.startup_timeout
        return await asyncio.start_server(self.handle, HOST, port)

    async def _connect(self) -> Tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """A connection to the app; until its first one the app may still be starting, so this waits for it."""
        while True:
            try:
                connection = await asyncio.open_connection(HOST, self.app_port)
            except OSError:
                if self._app_has_answered or time.monotonic() >= self._patience_ends:
                    raise
                await asyncio.sleep(0.2)
            else:
                self._app_has_answered = True
                return connection

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One connection of a browser: one request, one answer."""
        try:
            await self._serve(reader, writer)
            await _finish(reader, writer)
        except (OSError, asyncio.IncompleteReadError):
            pass  # the browser went away
        finally:
            writer.close()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Section 6.1 for the one app there is."""
        try:
            first, headers = _parse_head(await reader.readuntil(b"\r\n\r\n"))
        except asyncio.IncompleteReadError:
            return  # closed without a request: browsers open spare connections
        except asyncio.LimitOverrunError:
            first, headers = "", []
        method, _, rest = first.partition(" ")
        target = rest.rpartition(" ")[0]
        navigation = _is_navigation(headers)
        if not method or not _TARGET.fullmatch(target):
            return self._reply(writer, 400, "bad_request", "Bad request", "The gateway could not read this request.", navigation)

        reserved = [name for name, _ in headers if name.lower().startswith("x-efp-")]
        if reserved:
            self.say(
                "refused with 400: %s %s  (the request carries %s; only the gateway may add X-EFP-* headers)"
                % (method, target, reserved[0])
            )
            message = "The request carries the header %s. Only the gateway may add X-EFP-* headers, so it refuses such a request."
            return self._reply(writer, 400, "bad_request", "Bad request", message % reserved[0], navigation)

        path = target.partition("?")[0]
        if path.startswith(LIB_PREFIX):
            return self._library(writer, method, target, path, navigation)
        if path == self.base:
            return _send(writer, 301, ["Location: %s/%s" % (self.base, target[len(path) :])])
        if path.startswith(self.base + "/"):
            return await self._forward(reader, writer, method, target, headers, navigation)
        if path == "/":
            # A convenience of this mode, and still a request that left the app.
            self.say(
                'outside the app: %s %s  (sent on to %s/; once deployed "/" is the app center, not this app)'
                % (method, target, self.base)
            )
            return _send(writer, 302, ["Location: %s/" % self.base])
        self.say(self._outside_line(method, target, headers))
        message = (
            "This address is outside the app: %s. The app is at %s/, and only addresses below it reach the app. "
            'A URL that starts with "/" in a page, script or style sheet points here, to the top of the host.'
        )
        self._reply(writer, 404, "app_not_found", "This address is outside the app", message % (path, self.base), navigation)

    def _library(self, writer: asyncio.StreamWriter, method: str, target: str, path: str, navigation: bool) -> None:
        """Section 6.6: a file of the shared browser libraries, the same for every app."""
        if method not in ("GET", "HEAD"):
            self.say("refused with 405: %s %s  (the shared libraries are read-only)" % (method, target))
            return self._reply(writer, 405, "method_not_allowed", "Not allowed", "The shared libraries can only be read.", navigation)
        relative = path[len(LIB_PREFIX) :]
        found = library_file(self.lib_dir, relative) if self.lib_dir is not None else None
        if found is None:
            self.say("not in the shared libraries: %s %s%s" % (method, target, self._library_hint(relative)))
            message = "%s is not one of the shared libraries. docs/UI_KIT.md lists them with their versions."
            return self._reply(writer, 404, "not_found", "No such library file", message % path, navigation)
        body = found.read_bytes()
        kind = _LIB_TYPES.get(found.suffix.lower(), "text/plain; charset=utf-8")
        lines = ["HTTP/1.1 200 OK", "Content-Type: " + kind, "Content-Length: %d" % len(body)]
        # Not cached here: the next run on this port may serve another checkout. The host caches for a year.
        lines += ["Cache-Control: no-cache"] + _ADDED_TO_ANSWER + ["Connection: close"]
        # Two writes, not one joined copy: ECharts alone is a megabyte.
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        if method != "HEAD":
            writer.write(body)

    def _library_hint(self, relative: str) -> str:
        """For a missing file: the versions there are of the same library, or where the libraries are not."""
        if self.lib_dir is None or not self.lib_dir.is_dir():
            return "  (this installation of the SDK has no shared libraries)"
        versions = library_versions(self.lib_dir, relative)
        return "  (there is %s)" % ", ".join(versions) if versions else ""

    def _outside_line(self, method: str, target: str, headers: Headers) -> str:
        """The console line for a request that is not under the prefix."""
        if target.startswith(_ASKED_BY_THE_BROWSER):
            hint = "the browser asks for this by itself"
        else:
            page = _path_here(_get(headers, "referer"), _get(headers, "host"))
            hint = 'a URL that starts with "/"%s?' % (" in " + page if page else "")
        return "outside the app: %s %s  (%s)" % (method, target, hint)

    def _reply(self, writer: asyncio.StreamWriter, status: int, code: str, title: str, message: str, navigation: bool) -> None:
        """An answer of the gateway itself: a page for a page navigation, JSON for anything else."""
        retry = code == "app_starting"
        headers = ["Retry-After: 2"] if retry else []
        if navigation:
            page = _PAGE % {
                "title": html.escape(title),
                "message": html.escape(message),
                "base": self.base,
                "refresh": '<meta http-equiv="refresh" content="2">\n' if retry else "",
            }
            return _send(writer, status, headers + ["Content-Type: text/html; charset=utf-8"], page.encode("utf-8"))
        body = json.dumps({"error": {"code": code, "message": message}})
        _send(writer, status, headers + ["Content-Type: application/json"], body.encode("utf-8"))

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        headers: Headers,
        navigation: bool,
    ) -> None:
        """Send the request on to the app (section 6.2) and its answer back."""
        try:
            app_reader, app_writer = await self._connect()
        except OSError:
            if not self._silence_reported:  # once, not for every refresh of the page below
                self.say(
                    "no answer from the app on %s:%d: %s %s  (it must listen on $PORT; see its own output)"
                    % (HOST, self.app_port, method, target)
                )
            self._silence_reported = True
            message = "The app is not answering on its port. This page tries again by itself."
            return self._reply(writer, 503, "app_starting", "The app is not answering", message, navigation)
        self._silence_reported = False

        host = _get(headers, "host") or HOST
        upgrade = _get(headers, "upgrade")
        proto = _get(headers, "x-forwarded-proto").lower()
        peer = (writer.get_extra_info("peername") or (HOST,))[0]
        seen = [value for name, value in headers if name.lower() == "x-forwarded-for"]
        lines = ["%s %s HTTP/1.1" % (method, target[len(self.base) :] if self.strip_prefix else target)]
        lines += ["%s: %s" % header for header in headers if header[0].lower() not in _NOT_PASSED_ON]
        lines += [
            "X-Forwarded-For: " + ", ".join(seen + [peer]),
            "X-Forwarded-Proto: " + (proto if proto in ("http", "https") else "http"),
            "X-Forwarded-Host: " + host,
        ]
        if self.base:
            lines.append("X-Forwarded-Prefix: " + self.base)
        # One request per connection, unless the browser asks to switch protocols (a WebSocket).
        lines += ["Upgrade: " + upgrade, "Connection: upgrade"] if upgrade else ["Connection: close"]
        if _get(headers, "expect").lower() == "100-continue":
            writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")  # as nginx does: the app never sees Expect
        app_writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))

        # The body (and, after an upgrade, the browser's frames) one way, the answer the other.
        sending = asyncio.ensure_future(_copy(reader, app_writer))
        answering = asyncio.ensure_future(self._relay_answer(app_reader, writer, method, target, host, navigation))
        try:
            await asyncio.wait([sending, answering], return_when=asyncio.FIRST_COMPLETED)
            if sending.done() and sending.exception() is not None:
                await answering  # the app stopped reading (an early answer): let the answer through
            # Otherwise the answer is complete, or the browser went away and the app is to notice.
        finally:
            for task in (sending, answering):
                task.cancel()
            await asyncio.gather(sending, answering, return_exceptions=True)
            app_writer.close()

    async def _relay_answer(
        self,
        app_reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        method: str,
        target: str,
        host: str,
        navigation: bool,
    ) -> None:
        """The app's answer: its head as the gateway changes it (section 6.2), its body as it comes."""
        try:
            while True:
                head = await app_reader.readuntil(b"\r\n\r\n")
                first, headers = _parse_head(head)
                status = int(first.split(" ")[1])
                if status == 101 or not 100 <= status < 200:
                    break
                writer.write(head)  # an interim answer goes on as it is
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, IndexError):
            self.say("the app closed the connection without an answer: %s %s" % (method, target))
            message = "The app closed the connection without an answer. Its output on the console may say why."
            return self._reply(writer, 503, "app_unavailable", "The app did not answer", message, navigation)

        switched = status == 101
        lines = ["HTTP/1.1 " + first.partition(" ")[2]]
        for name, value in headers:
            lower = name.lower()
            if lower in _TAKEN_FROM_ANSWER or (lower in ("connection", "keep-alive") and not switched):
                continue
            if lower == "set-cookie" and self.slug:  # an app at "/" keeps its cookies as they are
                cookie = rewrite_set_cookie(value, self.slug)
                if cookie is None:
                    continue
                value = cookie
            elif lower == "location":
                value = self._location(value, method, target, host)
            lines.append("%s: %s" % (name, value))
        lines += _ADDED_TO_ANSWER + ([] if switched else ["Connection: close"])
        writer.write(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        await writer.drain()
        await _copy(app_reader, writer)

    def _location(self, value: str, method: str, target: str, host: str) -> str:
        """A ``Location`` of the app goes to the browser as it is, as on the host; one that leaves the app is reported."""
        path = _path_here(value, host)
        if path is None:
            return value  # relative to the page, or another host
        if self.local_file_server and value.startswith("/"):
            # nginx serves a static app under its public path, so its redirect for a directory
            # without the final slash carries the prefix. Python's file server never saw the prefix.
            return self.base + value
        if path != self.base and not path.startswith(self.base + "/"):
            self.say(
                'outside the app: %s %s answers with a redirect to %s  (a "Location" without the prefix?)'
                % (method, target, value)
            )
        return value


# --- Flask on Windows ---------------------------------------------------------
#
# On the host gunicorn reads SCRIPT_NAME from the environment and takes that
# prefix off every path before Flask sees it. gunicorn needs a POSIX system, and
# Flask's own server ignores SCRIPT_NAME, so on Windows `efp-app dev --prefix`
# starts it with
#     python -m flask --app "efp_app._devgateway:flask_app('app:app')" run --port N
# and the function below does gunicorn's part.


def flask_app(entry: str) -> Any:
    """The Flask app of ``module:attribute``, served under ``SCRIPT_NAME`` the way gunicorn serves it."""
    module_name, _, attribute = entry.partition(":")
    app: Any = importlib.import_module(module_name)
    for name in attribute.split("."):
        app = getattr(app, name)
    base = os.environ.get("SCRIPT_NAME", "").rstrip("/")
    inner = app.wsgi_app

    def under_script_name(environ: Any, start_response: Any) -> Any:
        path = environ.get("PATH_INFO", "")
        if base and path.startswith(base):
            environ["SCRIPT_NAME"] = base
            environ["PATH_INFO"] = path[len(base) :]
        return inner(environ, start_response)

    app.wsgi_app = under_script_name
    return app
