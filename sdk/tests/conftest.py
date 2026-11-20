"""Shared fixtures: a clean environment and the fake Portal of ``efp_app.testing`` behind httpx.MockTransport."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
import pytest

SDK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SDK_ROOT.parent
TEMPLATES = REPO_ROOT / "templates"
EXAMPLES = REPO_ROOT / "examples"

if str(SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(SDK_ROOT))

from efp_app import client as sdk_client  # noqa: E402
from efp_app import visitor as sdk_visitor  # noqa: E402
from efp_app.testing import FakePortal  # noqa: E402  (the one an app's own tests use)

TOKEN = "efpa_" + "t" * 43
PORTAL = "https://portal.test"
PREFIX = "/api/app-sdk/v1"

_SCRUBBED = (
    "PORT",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SCRIPT_NAME",
    "GRADIO_ROOT_PATH",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    """No EFP or proxy settings leak in from the machine; the work directory is a fresh one."""
    for name in list(os.environ):
        if name.upper().startswith("EFP_") or name.upper() in _SCRUBBED:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.chdir(tmp_path)
    sdk_client.reset()
    sdk_visitor.reset_dev_visitor()
    yield
    sdk_client.reset()
    sdk_visitor.reset_dev_visitor()


def envelope(status: int, code: str, message: str = "nope", headers: Optional[Dict[str, str]] = None) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message}}, headers=headers)


def sse(*events: Any, blank_lines: bool = True) -> httpx.Response:
    """A text/event-stream response; dict events are JSON-encoded, strings go out as they are."""
    lines = []
    for event in events:
        lines.append("data: " + json.dumps(event) if isinstance(event, dict) else event)
        if blank_lines:
            lines.append("")
    body = "\n".join(lines) + "\n"
    return httpx.Response(200, content=body.encode("utf-8"), headers={"content-type": "text/event-stream"})


@pytest.fixture
def credentials(monkeypatch):
    """The two settings a developer's machine needs."""
    monkeypatch.setenv("EFP_APP_TOKEN", TOKEN)
    monkeypatch.setenv("EFP_PORTAL_URL", PORTAL)


@pytest.fixture
def portal(credentials) -> FakePortal:
    """A fake Portal that the shared clients of the SDK talk to."""
    fake = FakePortal()
    sdk_client.configure(client=fake.client(), async_client=fake.async_client())
    return fake


@pytest.fixture
def deployed(monkeypatch, tmp_path):
    """The environment of an app on a sandbox host: an id, a slug, a path prefix, a visitor key, a data directory."""
    from test_visitor import APP_ID, VISITOR_KEY

    monkeypatch.setenv("EFP_APP_ID", APP_ID)
    monkeypatch.setenv("EFP_APP_SLUG", "example")
    monkeypatch.setenv("EFP_APP_BASE_PATH", "/example")
    monkeypatch.setenv("EFP_VISITOR_KEY", VISITOR_KEY)
    monkeypatch.setenv("EFP_APP_DATA_DIR", str(tmp_path / "data"))


@pytest.fixture
def no_sleep(monkeypatch):
    """Retries without the waiting."""
    monkeypatch.setattr(sdk_client, "RETRY_DELAY", 0.0)
