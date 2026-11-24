"""HTTP access to the Portal's SDK API (docs/CONTRACTS.md, section 9).

A small synchronous client and an asynchronous twin over httpx. Both

- send ``Authorization: Bearer <app token>`` and nothing else that identifies
  the caller (no cookies);
- turn the error envelope ``{"error": {"code", "message"}}`` into the typed
  exceptions of :mod:`efp_app.errors`;
- encode a body as JSON themselves, and refuse one that JSON or UTF-8 cannot
  carry with ``InvalidRequestError`` before anything is sent;
- never repeat a ``POST``; a ``GET`` is tried again when the connection
  could not be made;
- honour the usual CA variables (``SSL_CERT_FILE``, ``REQUESTS_CA_BUNDLE``,
  ``CURL_CA_BUNDLE``, ``SSL_CERT_DIR``) everywhere and the proxy variables
  (``HTTPS_PROXY``, ``HTTP_PROXY``, ``ALL_PROXY``, ``NO_PROXY``) only on a
  developer's machine: a deployed app talks to the Portal inside the cluster
  and must not go through a proxy.

The modules ``llm``, ``images``, ``connectors`` and ``app`` use one shared
client per process (:func:`get_client`, :func:`get_async_client`); pass
``client=`` to any of their functions to use your own. The token is never
logged or put in an error message.
"""

from __future__ import annotations

import asyncio
import json as _json
import os
import re
import ssl
import threading
import time
import weakref
from dataclasses import replace
from typing import Any, Dict, Mapping, Optional, Tuple, Union
from urllib.parse import urlsplit

import httpx

from . import config, errors
from ._version import __version__

API_PREFIX = "/api/app-sdk/v1"
VISITOR_HEADER = "X-EFP-Visitor"
USER_AGENT = "efp-app-sdk/" + __version__

#: Seconds for a whole exchange unless a call says otherwise.
DEFAULT_TIMEOUT = 120.0
#: Seconds to open the connection.
CONNECT_TIMEOUT = 10.0
#: How often a GET is tried when the connection fails.
GET_ATTEMPTS = 3
RETRY_DELAY = 0.2

_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError)
_VISITOR_VALUE = re.compile(r"v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_CA_FILE_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")

TimeoutLike = Union[None, int, float, httpx.Timeout]


def as_timeout(value: TimeoutLike) -> httpx.Timeout:
    """Seconds (or an ``httpx.Timeout``) to the timeout of one request."""
    if isinstance(value, httpx.Timeout):
        return value
    seconds = DEFAULT_TIMEOUT if value is None else float(value)
    return httpx.Timeout(seconds, connect=min(seconds, CONNECT_TIMEOUT))


_contexts: Dict[Tuple[Optional[str], Optional[str]], Union[bool, ssl.SSLContext]] = {}


def ca_bundle(env: Optional[Mapping[str, str]] = None) -> Union[bool, ssl.SSLContext]:
    """The TLS trust for the Portal: the CA named by the environment, else the default.

    Building a context reads a whole certificate bundle, which is slow on
    some systems, so each one is built once per process and shared.
    """
    env = os.environ if env is None else env
    cafile = None
    for name in _CA_FILE_VARIABLES:
        candidate = (env.get(name) or "").strip()
        if candidate and os.path.isfile(candidate):
            cafile = candidate
            break
    capath = (env.get("SSL_CERT_DIR") or "").strip()
    capath = capath if capath and os.path.isdir(capath) else None
    key = (cafile, capath)
    if key in _contexts:
        return _contexts[key]
    if not cafile and not capath:
        try:
            context: Union[bool, ssl.SSLContext] = httpx.create_ssl_context(trust_env=False)
        except Exception:
            context = True
    else:
        try:
            context = ssl.create_default_context(cafile=cafile, capath=capath)
        except (ssl.SSLError, OSError) as exc:
            raise errors.ConfigurationError(
                "The CA bundle named by %s or SSL_CERT_DIR could not be loaded: %s"
                % (" / ".join(_CA_FILE_VARIABLES), exc)
            ) from exc
    _contexts[key] = context
    return context


def visitor_header_value(visitor: Any) -> Optional[str]:
    """The ``X-EFP-Visitor`` value to forward: from a ``Visitor`` or a header string."""
    raw = getattr(visitor, "raw", visitor)
    if isinstance(raw, str) and _VISITOR_VALUE.fullmatch(raw.strip()):
        return raw.strip()
    return None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return "%s://%s" % (parts.scheme, parts.netloc) if parts.netloc else url


def _check_token(token: str) -> str:
    """The token when every character of it can be one of a token, else ``ConfigurationError``.

    Checked here and by name: httpx refuses such a header with an error whose
    ``repr`` carries the whole value, and that error is not an ``EfpError``.
    The message says which character is wrong and where, never the token.
    """
    for position, character in enumerate(token):
        if not "\x21" <= character <= "\x7e":
            raise errors.ConfigurationError(
                "%s holds a character that no app token has (U+%04X at position %d): a blank, a control "
                "character or a character outside ASCII, which a copy and paste can bring along unseen. "
                "Copy the token again from the app's page in the Portal." % (config.APP_TOKEN, ord(character), position + 1)
            )
    return token


def _encode_body(body: Any) -> bytes:
    """The JSON of a request body, as UTF-8.

    Encoded here and not by httpx, whose encoder changed between releases, so
    that what JSON or UTF-8 cannot carry is refused the same way everywhere
    and as an error an app already catches. A value of a type JSON does not
    know stays a ``TypeError``: that is a slip in the app's code, not in what
    a visitor sent.
    """
    try:
        return _json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError as exc:
        # Not chained: the original error holds the whole text of the request.
        raise errors.InvalidRequestError(
            "the request holds text that cannot be sent: half of a surrogate pair (U+%04X), "
            "which is what cutting a string in the middle of an emoji leaves" % ord(exc.object[exc.start])
        ) from None
    except ValueError as exc:  # NaN or an infinity, or a value that contains itself
        raise errors.InvalidRequestError("the request cannot be sent as JSON: %s" % exc) from None


class _Core:
    """What the two clients share: settings, request building, answer checking."""

    def __init__(
        self,
        token: Optional[str],
        portal_url: Optional[str],
        timeout: TimeoutLike,
        settings: Optional[config.Settings],
    ) -> None:
        settings = settings if settings is not None else config.load()
        if token or portal_url:
            settings = replace(
                settings,
                token=token or settings.token,
                portal_url=(portal_url.rstrip("/") if portal_url else settings.portal_url),
            )
        self.token = _check_token(settings.require_token())
        self.portal_url = settings.require_portal_url()
        self.base_url = self.portal_url + API_PREFIX
        self.deployed = settings.deployed
        self.timeout = as_timeout(timeout)

    def http_options(self, transport: Any) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "headers": {
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            "timeout": self.timeout,
            "follow_redirects": False,
        }
        if transport is not None:
            # A caller-supplied transport is used as it is: no proxy from the environment.
            options["transport"] = transport
            options["trust_env"] = False
        else:
            options["trust_env"] = not self.deployed
            options["verify"] = ca_bundle()
        return options

    def build(
        self,
        http: Any,
        method: str,
        path: str,
        body: Any,
        params: Optional[Mapping[str, Any]],
        visitor: Any,
        timeout: TimeoutLike,
    ) -> httpx.Request:
        headers: Dict[str, str] = {}
        forwarded = visitor_header_value(visitor)
        if forwarded:
            headers[VISITOR_HEADER] = forwarded
        options: Dict[str, Any] = {"headers": headers}
        if params:
            options["params"] = params
        if body is not None:
            options["content"] = _encode_body(body)
            headers["Content-Type"] = "application/json"
        if timeout is not None:
            options["timeout"] = as_timeout(timeout)
        try:
            return http.build_request(method, self.base_url + path, **options)
        except httpx.InvalidURL as exc:
            raise errors.ConfigurationError("%s is not a usable URL: %s" % (config.PORTAL_URL, exc)) from exc

    def transport_error(self, exc: Exception) -> errors.EfpError:
        where = _origin(self.portal_url)
        if isinstance(exc, httpx.TimeoutException):
            return errors.PortalTimeoutError(
                "The Portal at %s did not answer in time (%s)." % (where, type(exc).__name__)
            )
        if isinstance(exc, httpx.TransportError):
            return errors.PortalUnreachableError(
                "Cannot reach the Portal at %s: %s" % (where, str(exc) or type(exc).__name__)
            )
        return errors.UnexpectedResponseError(
            "The exchange with the Portal at %s failed: %s" % (where, str(exc) or type(exc).__name__)
        )

    def check(self, status: int, headers: Mapping[str, Any], body: bytes) -> None:
        """Raise the typed error of a response that is not a success."""
        if 300 <= status < 400:
            raise errors.UnexpectedResponseError(
                "The Portal answered HTTP %d, a redirect to %s. %s must be the address of the Portal itself."
                % (status, headers.get("location") or "another address", config.PORTAL_URL),
                status=status,
            )
        if status >= 400:
            raise errors.from_response(status, body, headers)

    def parse(self, status: int, body: bytes) -> Any:
        try:
            return _json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise errors.UnexpectedResponseError(
                "The Portal answered HTTP %d with a body that is not JSON. Check %s." % (status, config.PORTAL_URL),
                status=status,
            ) from None

    def signature(self) -> Tuple[str, str, bool]:
        return (self.token, self.portal_url, self.deployed)


class Client:
    """Synchronous client of the SDK API.

    ``token`` and ``portal_url`` default to ``EFP_APP_TOKEN`` and
    ``EFP_PORTAL_URL``; a missing one raises ``ConfigurationError`` naming the
    variable. ``timeout`` is in seconds. ``transport`` is an httpx transport,
    for tests.
    """

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        portal_url: Optional[str] = None,
        timeout: TimeoutLike = None,
        transport: Optional[httpx.BaseTransport] = None,
        settings: Optional[config.Settings] = None,
    ) -> None:
        self._core = _Core(token, portal_url, timeout, settings)
        self._http = httpx.Client(**self._core.http_options(transport))
        #: The last answer of ``GET /app`` (see ``efp_app.app.info``).
        self.cached_app_info: Any = None

    def get(
        self,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        visitor: Any = None,
        timeout: TimeoutLike = None,
    ) -> Any:
        """``GET {prefix}{path}`` and the decoded JSON answer."""
        response = self._send("GET", path, None, params, visitor, timeout, stream=False)
        self._core.check(response.status_code, response.headers, response.content)
        return self._core.parse(response.status_code, response.content)

    def post(self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None) -> Any:
        """``POST {prefix}{path}`` with a JSON body and the decoded JSON answer. Never repeated."""
        response = self._send("POST", path, body, None, visitor, timeout, stream=False)
        self._core.check(response.status_code, response.headers, response.content)
        return self._core.parse(response.status_code, response.content)

    def stream(self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None) -> httpx.Response:
        """``POST`` and return the open response once its status is a success.

        The caller reads the body (``iter_lines``) and closes the response.
        """
        response = self._send("POST", path, body, None, visitor, timeout, stream=True)
        if response.status_code >= 300:
            try:
                content = response.read()
            except httpx.HTTPError as exc:
                raise self._core.transport_error(exc) from exc
            finally:
                response.close()
            self._core.check(response.status_code, response.headers, content)
        return response

    def transport_error(self, exc: Exception) -> errors.EfpError:
        return self._core.transport_error(exc)

    def _get_once(self, path: str, *, timeout: TimeoutLike) -> Any:
        """``get`` without the further attempts, for a caller that must not wait long.

        ``visitor.current`` asks for the owner of the app while a request of a
        developer's machine waits for it; three attempts at the default
        timeout would hold that request for half a minute.
        """
        response = self._send("GET", path, None, None, None, timeout, stream=False, attempts=1)
        self._core.check(response.status_code, response.headers, response.content)
        return self._core.parse(response.status_code, response.content)

    def _send(
        self,
        method: str,
        path: str,
        body: Any,
        params: Optional[Mapping[str, Any]],
        visitor: Any,
        timeout: TimeoutLike,
        *,
        stream: bool,
        attempts: Optional[int] = None,
    ) -> httpx.Response:
        if attempts is None:
            attempts = GET_ATTEMPTS if method == "GET" else 1
        attempt = 0
        while True:
            attempt += 1
            request = self._core.build(self._http, method, path, body, params, visitor, timeout)
            try:
                return self._http.send(request, stream=stream)
            except httpx.HTTPError as exc:
                if attempt < attempts and isinstance(exc, _RETRYABLE):
                    time.sleep(RETRY_DELAY * attempt)
                    continue
                raise self._core.transport_error(exc) from exc

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class AsyncClient:
    """Asynchronous client of the SDK API; the same behaviour as :class:`Client`."""

    def __init__(
        self,
        *,
        token: Optional[str] = None,
        portal_url: Optional[str] = None,
        timeout: TimeoutLike = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        settings: Optional[config.Settings] = None,
    ) -> None:
        self._core = _Core(token, portal_url, timeout, settings)
        self._http = httpx.AsyncClient(**self._core.http_options(transport))
        self.cached_app_info: Any = None

    async def get(
        self,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        visitor: Any = None,
        timeout: TimeoutLike = None,
    ) -> Any:
        response = await self._send("GET", path, None, params, visitor, timeout, stream=False)
        self._core.check(response.status_code, response.headers, response.content)
        return self._core.parse(response.status_code, response.content)

    async def post(self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None) -> Any:
        response = await self._send("POST", path, body, None, visitor, timeout, stream=False)
        self._core.check(response.status_code, response.headers, response.content)
        return self._core.parse(response.status_code, response.content)

    async def stream(
        self, path: str, body: Any, *, visitor: Any = None, timeout: TimeoutLike = None
    ) -> httpx.Response:
        response = await self._send("POST", path, body, None, visitor, timeout, stream=True)
        if response.status_code >= 300:
            try:
                content = await response.aread()
            except httpx.HTTPError as exc:
                raise self._core.transport_error(exc) from exc
            finally:
                await response.aclose()
            self._core.check(response.status_code, response.headers, content)
        return response

    def transport_error(self, exc: Exception) -> errors.EfpError:
        return self._core.transport_error(exc)

    async def _send(
        self,
        method: str,
        path: str,
        body: Any,
        params: Optional[Mapping[str, Any]],
        visitor: Any,
        timeout: TimeoutLike,
        *,
        stream: bool,
    ) -> httpx.Response:
        attempts = GET_ATTEMPTS if method == "GET" else 1
        attempt = 0
        while True:
            attempt += 1
            request = self._core.build(self._http, method, path, body, params, visitor, timeout)
            try:
                return await self._http.send(request, stream=stream)
            except httpx.HTTPError as exc:
                if attempt < attempts and isinstance(exc, _RETRYABLE):
                    await asyncio.sleep(RETRY_DELAY * attempt)
                    continue
                raise self._core.transport_error(exc) from exc

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()


# --- The shared clients of the process ---------------------------------------

_lock = threading.Lock()
_sync: Optional[Client] = None
_sync_pinned = False
_async: Optional[AsyncClient] = None
_async_pinned = False
_async_loop: Any = None


def _current_signature() -> Tuple[config.Settings, Tuple[Optional[str], Optional[str], bool]]:
    settings = config.load()
    return settings, (settings.token, settings.portal_url, settings.deployed)


def get_client() -> Client:
    """The shared synchronous client, rebuilt when the token or the Portal address changes."""
    global _sync
    with _lock:
        if _sync is not None and _sync_pinned:
            return _sync
        settings, signature = _current_signature()
        if _sync is None or _sync._core.signature() != signature:
            previous = _sync
            _sync = Client(settings=settings)
            if previous is not None:
                previous.close()
        return _sync


def _running_loop() -> Any:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def get_async_client() -> AsyncClient:
    """The shared asynchronous client of the running event loop."""
    global _async, _async_loop
    with _lock:
        if _async is not None and _async_pinned:
            return _async
        settings, signature = _current_signature()
        loop = _running_loop()
        same_loop = _async_loop is not None and _async_loop() is loop
        if _async is None or not same_loop or _async._core.signature() != signature:
            # A client belongs to the loop it was first used on; the old one is
            # dropped (its connections close with their loop).
            _async = AsyncClient(settings=settings)
            _async_loop = weakref.ref(loop) if loop is not None else None
        return _async


def configure(*, client: Optional[Client] = None, async_client: Optional[AsyncClient] = None) -> None:
    """Use these clients as the shared ones (tests, custom timeouts or transports)."""
    global _sync, _sync_pinned, _async, _async_pinned, _async_loop
    with _lock:
        if client is not None:
            _sync, _sync_pinned = client, True
        if async_client is not None:
            _async, _async_pinned, _async_loop = async_client, True, None


def reset() -> None:
    """Forget the shared clients; the next call builds them from the environment."""
    global _sync, _sync_pinned, _async, _async_pinned, _async_loop
    with _lock:
        if _sync is not None:
            try:
                _sync.close()
            except Exception:
                pass
        _sync, _sync_pinned = None, False
        _async, _async_pinned, _async_loop = None, False, None
