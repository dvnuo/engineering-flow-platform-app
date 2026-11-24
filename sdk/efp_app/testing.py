"""Test an app without a Portal and without a host.

Deployed, an app gets three things from outside: the environment the host
starts it in, a Portal that answers its model, image and connector calls, and
the signed header by which the gateway names the visitor. This module has a
stand-in for each, so an app can be tested in its own repository with nothing
installed but the SDK and the app's own requirements::

    from efp_app import testing

    def test_the_summary(tmp_path):
        with testing.deployed(tmp_path) as portal:
            portal.on("POST", "/llm/complete", testing.completion("Three bugs are open."))
            from app import app                        # inside: the app reads its settings

            answer = app.test_client().post("/api/summary", headers=testing.visitor_headers("alice"))

        assert answer.get_json() == {"text": "Three bugs are open."}
        assert portal.sent_to("/llm/complete")[0]["messages"][-1]["role"] == "user"

``deployed`` changes the environment of the process and the SDK's shared
clients. When the block ends the environment is as it was before and the
shared clients are reset. A running app never uses anything of this module.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

import httpx

from . import client as _client
from . import config as _config
from . import images as _images
from . import visitor as _visitor

#: The id the stand-in environment gives the app.
APP_ID = "0b0f3a4e-0000-4000-8000-000000000001"
#: The key the stand-in environment signs visitors with.
VISITOR_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii").rstrip("=")
#: The app token of the stand-in environment. No Portal knows it.
TOKEN = "efpa_" + "t" * 43
#: Where the stand-in Portal is said to be. Nothing is ever sent there.
PORTAL_URL = "https://portal.test"
#: The address of the apps in the stand-in environment.
APPS_URL = "https://apps.test"

_ENVIRONMENT = (
    _config.APP_ID,
    _config.APP_SLUG,
    _config.APP_TOKEN,
    _config.APP_BASE_PATH,
    _config.APP_PUBLIC_URL,
    _config.PORTAL_URL,
    _config.VISITOR_KEY,
    _config.APP_DATA_DIR,
    _config.APP_STORAGE_SOCKET,
    _config.DEV_VISITOR,
)


def _json_of(request: httpx.Request) -> Any:
    return json.loads(request.content.decode("utf-8")) if request.content else None


def _copy_of(answer: httpx.Response) -> httpx.Response:
    """A response of its own for one call: the status, the headers and the body of ``answer``.

    httpx binds a response to the client that sent the request. The same
    object handed out again works for a second call of the same kind, and
    fails with a bare ``AssertionError`` when an asynchronous call follows a
    synchronous one (or the other way round). A canned answer is meant for
    any number of calls of either kind, so each call gets a copy.
    """
    try:
        body = answer.content
    except httpx.ResponseNotRead:
        return answer  # built around a stream of the caller's own: it can only be handed on as it is
    # The body is the decoded one, so the headers that describe how it travelled are left to httpx.
    travel = ("content-encoding", "content-length", "transfer-encoding")
    headers = [(name, value) for name, value in answer.headers.multi_items() if name.lower() not in travel]
    return httpx.Response(answer.status_code, headers=headers, content=body, extensions=dict(answer.extensions))


class FakePortal:
    """A Portal for tests: canned answers for the routes an app calls, and a record of what it sent.

    Routes are named as the SDK's API has them, below ``/api/app-sdk/v1``:
    ``("POST", "/llm/complete")``, ``("POST", "/images/inspect")``,
    ``("POST", "/connectors/jira/request")``, ``("GET", "/app")``. A route
    without an answer gets ``404 not_found``, which the SDK raises as an
    ``EfpError`` with the code ``not_found``.
    """

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []
        self.routes: Dict[Tuple[str, str], Any] = {}

    def on(self, method: str, path: str, answer: Any) -> "FakePortal":
        """Answer ``method path`` with ``answer`` from now on.

        ``answer`` is a JSON-able value (sent with status 200), an
        ``httpx.Response`` (every call gets a copy of it, so one response
        answers any number of calls), an exception (raised, as a broken
        connection would), a callable that takes the request and returns one
        of those, or a list of those: one per call, the last one for every
        later call. The list is copied, so the caller's own list stays as it
        is.
        """
        if isinstance(answer, list):
            if not answer:
                raise ValueError("a list of answers needs at least one answer")
            answer = list(answer)
        self.routes[(method.upper(), path)] = answer
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        """What ``httpx.MockTransport`` calls for each request of the SDK."""
        self.requests.append(request)
        prefix = _client.API_PREFIX
        assert request.url.path.startswith(prefix), request.url.path
        key = (request.method, request.url.path[len(prefix) :])
        if key not in self.routes:
            message = "the test gave no answer for %s %s" % key
            return httpx.Response(404, json={"error": {"code": "not_found", "message": message}})
        answer = self.routes[key]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if callable(answer) and not isinstance(answer, httpx.Response):
            answer = answer(request)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, httpx.Response):
            return _copy_of(answer)
        return httpx.Response(200, json=answer)

    @property
    def last(self) -> httpx.Request:
        """The latest request."""
        return self.requests[-1]

    def body(self, index: int = -1) -> Any:
        """The JSON body of one request: the latest unless ``index`` says otherwise. ``None`` when it had none."""
        return _json_of(self.requests[index])

    def count(self, method: str, path: str) -> int:
        """How many times ``method path`` was called."""
        full = _client.API_PREFIX + path
        return sum(1 for request in self.requests if request.method == method.upper() and request.url.path == full)

    def sent_to(self, path: str) -> List[Any]:
        """The JSON bodies the app sent to one route, in order; ``None`` for a request without a body."""
        full = _client.API_PREFIX + path
        return [_json_of(request) for request in self.requests if request.url.path == full]

    def client(self, **options: Any) -> _client.Client:
        """A client of the SDK whose requests come here."""
        return _client.Client(transport=httpx.MockTransport(self.handler), **options)

    def async_client(self, **options: Any) -> _client.AsyncClient:
        """An asynchronous client of the SDK whose requests come here."""
        return _client.AsyncClient(transport=httpx.MockTransport(self.handler), **options)

    def install(self) -> "FakePortal":
        """Make the SDK's shared clients talk to this Portal (``client.reset()`` undoes it)."""
        _client.configure(client=self.client(), async_client=self.async_client())
        return self


# --- What the Portal answers ------------------------------------------------------------------


def app_info(owner: str = "alice", **changes: Any) -> Dict[str, Any]:
    """What ``GET /app`` answers: an app of ``owner``, the model and image analysis on, no connector, budget left.

    ``changes`` replace keys of the answer, for example
    ``connectors=[{"type": "jira", "write": False}]`` or ``llm={"enabled": False}``.
    An app whose owner has not turned image analysis on is
    ``image_analysis={"enabled": False}``; give its route
    ``error(403, "connector_disabled")`` as well, for the Portal here refuses
    nothing by itself.
    """
    found: Dict[str, Any] = {
        "id": APP_ID,
        "slug": "example",
        "name": "Example",
        "url": APPS_URL + "/example/",
        "owner": {
            "id": _visitor.dev_visitor_id(owner),
            "username": owner,
            "display_name": owner.capitalize(),
            "group_id": 3,
            "group_name": "QA",
        },
        "llm": {"enabled": True, "default_model": "gpt-5.4", "models": ["gpt-5.4"]},
        "connectors": [],
        "image_analysis": {
            "enabled": True,
            "model": "gpt-5.4",
            "max_images": _images.MAX_IMAGES,
            "max_image_bytes": _images.MAX_IMAGE_BYTES,
            "max_total_bytes": _images.MAX_TOTAL_BYTES,
        },
        "budget": {"daily_credits": 50.0, "used_credits": 0.0, "daily_tokens": 2000000, "used_tokens": 0},
    }
    found.update(changes)
    return found


def completion(text: str = "Hello.", **changes: Any) -> Dict[str, Any]:
    """What ``POST /llm/complete`` answers to a call that is not streamed."""
    found: Dict[str, Any] = {
        "text": text,
        "model": "gpt-5.4",
        "finish_reason": "stop",
        "usage": {"input_tokens": 12, "output_tokens": 4, "credits": 0.001},
    }
    found.update(changes)
    return found


def inspection(text: str = "A red square.", **changes: Any) -> Dict[str, Any]:
    """What ``POST /images/inspect`` answers: the model's answer about the images.

    ``changes`` replace keys of the answer (``finish_reason="length"``).
    """
    found: Dict[str, Any] = {
        "text": text,
        "model": "gpt-5.4",
        "finish_reason": "stop",
        # No credits: the provider behind image analysis may report none, and an app must cope with that.
        "usage": {"input_tokens": 1530, "output_tokens": 6, "credits": None},
    }
    found.update(changes)
    return found


def stream(*pieces: str, **done: Any) -> httpx.Response:
    """What ``POST /llm/complete`` answers to a streamed call: the pieces, then the closing event.

    ``done`` replaces keys of the closing event (``finish_reason="length"``).
    """
    closing: Dict[str, Any] = {
        "type": "done",
        "model": "gpt-5.4",
        "finish_reason": "stop",
        "usage": {"input_tokens": 12, "output_tokens": len(pieces), "credits": 0.001},
    }
    closing.update(done)
    events = [{"type": "delta", "text": piece} for piece in pieces] + [closing]
    body = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
    return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})


def connector_answer(body: Any = None, *, status: int = 200, text: Optional[str] = None, truncated: bool = False) -> Dict[str, Any]:
    """What ``POST /connectors/{type}/request`` answers: the other system's status and body.

    ``body`` is the other system's JSON; ``text`` is given instead when it
    answered something else (a page, a log).
    """
    return {
        "status": status,
        "headers": {"content-type": "application/json" if text is None else "text/plain"},
        "json": body,
        "body_base64": None if text is None else base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "truncated": truncated,
    }


def error(status: int, code: str, message: str = "refused by the test", headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    """A refusal of the Portal, which the SDK raises as the error class of ``code``.

    ``error(429, "budget_exceeded", headers={"Retry-After": "3600"})`` is a
    used-up budget; ``error(403, "connector_disabled")`` a connector the
    owner has not turned on (on ``/images/inspect``: image analysis).
    """
    return httpx.Response(status, json={"error": {"code": code, "message": message}}, headers=headers)


# --- The visitor ------------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def visitor_headers(
    username: str = "bob",
    *,
    id: Optional[int] = None,
    name: Optional[str] = None,
    group: Optional[str] = "QA",
    group_id: Optional[int] = 3,
    expires_in: int = 60,
) -> Dict[str, str]:
    """The header the gateway adds for a signed-in visitor, for the app of :func:`deployed`.

    Without ``id`` the member gets ``visitor.dev_visitor_id(username)``: the
    id the same name has under ``EFP_DEV_VISITOR``, and the one
    :func:`app_info` gives its owner. A guest of an app open to anyone has no
    header at all: pass no headers.
    """
    payload = {
        "app": os.environ.get(_config.APP_ID) or APP_ID,
        "exp": int(time.time()) + expires_in,
        "group": group,
        "group_id": group_id,
        "name": name if name is not None else username.capitalize(),
        "sub": _visitor.dev_visitor_id(username) if id is None else id,
        "username": username,
    }
    body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    key = os.environ.get(_config.VISITOR_KEY) or VISITOR_KEY
    raw_key = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    signature = _b64url(hmac.new(raw_key, ("v1." + body).encode("ascii"), hashlib.sha256).digest())
    return {_client.VISITOR_HEADER: "v1." + body + "." + signature}


# --- The environment --------------------------------------------------------------------------


def environment(data_dir: Union[str, Path], *, slug: str = "example") -> Dict[str, str]:
    """The variables the host starts an app with, the data directory being ``data_dir``."""
    return {
        _config.APP_ID: APP_ID,
        _config.APP_SLUG: slug,
        _config.APP_TOKEN: TOKEN,
        _config.APP_BASE_PATH: "/" + slug,
        _config.APP_PUBLIC_URL: "%s/%s/" % (APPS_URL, slug),
        _config.PORTAL_URL: PORTAL_URL,
        _config.VISITOR_KEY: VISITOR_KEY,
        _config.APP_DATA_DIR: str(data_dir),
    }


@contextlib.contextmanager
def deployed(data_dir: Union[str, Path], *, slug: str = "example", info: Optional[Dict[str, Any]] = None) -> Iterator[FakePortal]:
    """Run a block as a deployed app would run: the host's environment and a :class:`FakePortal`.

    ``data_dir`` is where the app's storage goes: give each test a fresh
    directory (pytest's ``tmp_path``). ``GET /app`` answers ``info``
    (default: :func:`app_info`); every other route answers what the test
    gives with ``portal.on(...)``. When the block ends the environment is as
    it was before and the SDK's shared clients are reset (a client pinned
    with ``client.configure`` before the block is not restored).

    Import the app inside the block when it reads settings while it is
    imported. Python imports a module once: an app that keeps such settings
    in module variables keeps those of the first test.
    """
    before = {name: os.environ.get(name) for name in _ENVIRONMENT}
    portal = FakePortal().on("GET", "/app", info if info is not None else app_info(slug=slug, url="%s/%s/" % (APPS_URL, slug)))
    try:
        for name in _ENVIRONMENT:
            os.environ.pop(name, None)
        os.environ.update(environment(data_dir, slug=slug))
        _client.reset()
        _visitor.reset_dev_visitor()
        portal.install()
        yield portal
    finally:
        _client.reset()
        _visitor.reset_dev_visitor()
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
