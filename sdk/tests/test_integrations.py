"""The framework helpers: FastAPI for real, Flask / Streamlit / Gradio through small fakes.

No ``from __future__ import annotations`` here: FastAPI reads the annotations
of the route functions below, and they name objects that only exist inside
``make_fastapi_app``.
"""

import subprocess
import sys
import time
import types

import pytest

from efp_app import visitor
from efp_app.visitor import Visitor
from conftest import SDK_ROOT
from test_visitor import APP_ID, VISITOR_KEY, payload_of, sign


@pytest.fixture
def deployed(monkeypatch):
    monkeypatch.setenv("EFP_APP_ID", APP_ID)
    monkeypatch.setenv("EFP_VISITOR_KEY", VISITOR_KEY)


# --- The SDK needs none of the frameworks --------------------------------------------------


def test_the_sdk_imports_without_any_framework():
    script = """
import importlib.abc, sys

BLOCKED = ("fastapi", "starlette", "flask", "streamlit", "gradio", "uvicorn", "gunicorn")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError("blocked for the test: " + name)
        return None

sys.meta_path.insert(0, Block())
import efp_app
import efp_app.cli
import efp_app.integrations
import efp_app.integrations.fastapi
import efp_app.integrations.flask
import efp_app.integrations.streamlit
import efp_app.integrations.gradio
loaded = sorted(name for name in sys.modules if name.split(".")[0] in BLOCKED)
assert loaded == [], loaded
third_party = sorted({name.split(".")[0] for name in sys.modules} & {"httpx", "requests", "pydantic", "yaml"})
assert third_party == ["httpx"], third_party
print("ok")
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=str(SDK_ROOT), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


# --- FastAPI / Starlette -------------------------------------------------------------------


def make_fastapi_app():
    fastapi = pytest.importorskip("fastapi")
    from typing import Optional

    from efp_app.integrations.fastapi import get_visitor, require_visitor

    app = fastapi.FastAPI()

    @app.get("/me")
    def me(who: Optional[Visitor] = fastapi.Depends(get_visitor)):
        return {"username": who.username if who else None, "group": who.group if who else None}

    @app.get("/private")
    def private(who: Visitor = fastapi.Depends(require_visitor)):
        return {"id": who.id}

    @app.websocket("/ws")
    async def ws(socket: fastapi.WebSocket, who: Optional[Visitor] = fastapi.Depends(get_visitor)):
        await socket.accept()
        await socket.send_json({"username": who.username if who else None})
        await socket.close()

    return app


def test_fastapi_dependencies(deployed):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    client = TestClient(make_fastapi_app())
    headers = {"X-EFP-Visitor": sign(payload_of())}
    assert client.get("/me", headers=headers).json() == {"username": "bob", "group": "QA"}
    assert client.get("/me").json() == {"username": None, "group": None}
    assert client.get("/me", headers={"X-EFP-Visitor": "v1.forged.forged"}).json()["username"] is None
    assert client.get("/private", headers=headers).json() == {"id": 12}
    refused = client.get("/private")
    assert refused.status_code == 401
    assert refused.json() == {"detail": "Sign in to use this app."}
    expired = {"X-EFP-Visitor": sign(payload_of(exp=int(time.time()) - 5))}
    assert client.get("/private", headers=expired).status_code == 401


def test_fastapi_dependency_on_a_websocket(deployed):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    client = TestClient(make_fastapi_app())
    with client.websocket_connect("/ws", headers={"X-EFP-Visitor": sign(payload_of())}) as socket:
        assert socket.receive_json() == {"username": "bob"}
    with client.websocket_connect("/ws") as socket:
        assert socket.receive_json() == {"username": None}


def test_fastapi_on_a_developers_machine_uses_the_dev_visitor(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    client = TestClient(make_fastapi_app())
    assert client.get("/me").json() == {"username": "carol", "group": None}
    assert client.get("/private").json() == {"id": 0}


def test_starlette_request_works_directly(deployed):
    pytest.importorskip("starlette")
    from starlette.requests import Request

    from efp_app.integrations.fastapi import get_visitor

    scope = {"type": "http", "headers": [(b"x-efp-visitor", sign(payload_of()).encode("ascii"))]}
    assert get_visitor(Request(scope)).username == "bob"


# --- Flask (a stand-in for the module) -------------------------------------------------------


class Aborted(Exception):
    def __init__(self, status, description):
        super().__init__(description)
        self.status = status
        self.description = description


@pytest.fixture
def fake_flask(monkeypatch):
    module = types.ModuleType("flask")
    module.request = types.SimpleNamespace(headers={})

    def abort(status, description=None):
        raise Aborted(status, description)

    module.abort = abort
    monkeypatch.setitem(sys.modules, "flask", module)
    return module


def test_flask_helpers(deployed, fake_flask):
    from efp_app.integrations.flask import get_visitor, require_visitor

    assert get_visitor() is None
    with pytest.raises(Aborted) as caught:
        require_visitor()
    assert caught.value.status == 401
    assert caught.value.description == "Sign in to use this app."

    fake_flask.request.headers = {"X-Efp-Visitor": sign(payload_of())}
    assert get_visitor().username == "bob"
    assert require_visitor().id == 12


def test_flask_helper_accepts_a_wsgi_environment(deployed, fake_flask):
    from efp_app.integrations.flask import get_visitor

    fake_flask.request.headers = {"HTTP_X_EFP_VISITOR": sign(payload_of()), "REQUEST_METHOD": "GET"}
    assert get_visitor().username == "bob"


# --- Streamlit (a stand-in for the module) ------------------------------------------------------


class SessionState(dict):
    """Item and attribute access, like st.session_state."""

    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


@pytest.fixture
def fake_streamlit(monkeypatch):
    module = types.ModuleType("streamlit")
    module.session_state = SessionState()
    module.context = types.SimpleNamespace(headers={})
    monkeypatch.setitem(sys.modules, "streamlit", module)
    return module


def test_streamlit_reads_the_headers_of_the_session(deployed, fake_streamlit):
    from efp_app.integrations.streamlit import SESSION_KEY, get_visitor

    assert get_visitor() is None
    assert SESSION_KEY not in fake_streamlit.session_state  # an anonymous visitor is looked up again

    fake_streamlit.context.headers = {"X-Efp-Visitor": sign(payload_of())}
    assert get_visitor().username == "bob"
    assert fake_streamlit.session_state[SESSION_KEY].username == "bob"


def test_streamlit_remembers_the_visitor_after_the_header_expired(deployed, fake_streamlit):
    from efp_app.integrations.streamlit import get_visitor

    fake_streamlit.context.headers = {"X-Efp-Visitor": sign(payload_of(exp=int(time.time()) + 1))}
    first = get_visitor()
    assert first.username == "bob"
    # The session lives on; the header of its first request is long expired.
    fake_streamlit.context.headers = {"X-Efp-Visitor": sign(payload_of(exp=int(time.time()) - 600))}
    assert visitor.current(fake_streamlit.context.headers) is None
    assert get_visitor() == first
    # Another browser session starts empty.
    fake_streamlit.session_state.clear()
    assert get_visitor() is None


def test_streamlit_before_st_context(deployed, monkeypatch):
    module = types.ModuleType("streamlit")
    module.session_state = SessionState()
    web = types.ModuleType("streamlit.web")
    server = types.ModuleType("streamlit.web.server")
    headers = types.ModuleType("streamlit.web.server.websocket_headers")
    headers._get_websocket_headers = lambda: {"X-Efp-Visitor": sign(payload_of())}
    for name, item in [
        ("streamlit", module),
        ("streamlit.web", web),
        ("streamlit.web.server", server),
        ("streamlit.web.server.websocket_headers", headers),
    ]:
        monkeypatch.setitem(sys.modules, name, item)

    from efp_app.integrations.streamlit import get_visitor

    assert get_visitor().username == "bob"


def test_streamlit_on_a_developers_machine(monkeypatch, fake_streamlit):
    from efp_app.integrations.streamlit import get_visitor

    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    assert get_visitor() == Visitor(id=0, username="carol", name="carol")


# --- Gradio (any object with headers) ------------------------------------------------------------


class Obj:
    """How Gradio hands over the headers of a queued event: an object around a dict."""

    def __init__(self, items):
        self.__dict__.update(items)

    def get(self, name, default=None):
        return self.__dict__.get(name, default)

    def items(self):
        return self.__dict__.items()


def test_gradio_request(deployed):
    from efp_app.integrations.gradio import get_visitor

    value = sign(payload_of())
    assert get_visitor(types.SimpleNamespace(headers={"x-efp-visitor": value})).username == "bob"
    assert get_visitor(types.SimpleNamespace(headers=Obj({"x-efp-visitor": value, "host": "app.example.com"}))).username == "bob"
    assert get_visitor(types.SimpleNamespace(headers={})) is None
    assert get_visitor(None) is None
    assert get_visitor() is None
    assert get_visitor(object()) is None


def test_gradio_on_a_developers_machine(monkeypatch):
    from efp_app.integrations.gradio import get_visitor

    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    assert get_visitor(types.SimpleNamespace(headers={"host": "127.0.0.1:8000"})).username == "carol"
    assert get_visitor(None).username == "carol"
