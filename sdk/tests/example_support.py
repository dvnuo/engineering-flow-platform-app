"""What the tests of the example apps share.

``examples/`` is discovered from the disk: a directory with an ``efp-app.json``
is an example, and its type is the manifest's. ``test_examples.py`` holds the
rules every example follows; each example's own behaviour is tested in
``test_examples.py`` (the first seven) or in ``test_example_<name>.py``.

The canned answers are what the Portal sends on the wire for a connector call
(``connector_answer``), a completion (``COMPLETION``) and a streamed
completion (``STREAMED``). The fake Portal itself is
``efp_app.testing.FakePortal``, the one the SDK ships for an app's own tests
(the ``portal`` fixture of ``conftest`` installs it); ``deployed`` (also in
``conftest``) gives a test the environment of an app on a sandbox host.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

from conftest import EXAMPLES
from test_visitor import APP_ID, payload_of, sign

#: What an example of each type may list in requirements.txt, in this order.
#: A "command" example runs on the standard library and the SDK alone.
PACKAGES = {
    "fastapi": ["fastapi", "uvicorn"],
    "flask": ["flask", "gunicorn"],
    "streamlit": ["streamlit"],
    "gradio": ["gradio"],
    "command": [],
}

APP_INFO = {
    "id": APP_ID,
    "slug": "setup",
    "name": "Setup check",
    "url": "https://app.example.com/setup/",
    "owner": {"id": 5, "username": "alice", "display_name": "Alice", "group_id": 3, "group_name": "QA"},
    "llm": {"enabled": True, "default_model": "gpt-5.4", "models": ["gpt-5.4"]},
    "connectors": [{"type": "jira", "write": False}],
    "budget": {"daily_credits": 50.0, "used_credits": 1.2, "daily_tokens": 2000000, "used_tokens": 3400},
}
JIRA_ON = {"connectors": [{"type": "jira", "base_url": "https://jira.example.com/", "write": False}]}
COMPLETION = {
    "text": "Two issues moved.",
    "model": "gpt-5.4",
    "finish_reason": "stop",
    "usage": {"input_tokens": 120, "output_tokens": 40, "credits": 0.004},
}
DONE = {"type": "done", "model": "gpt-5.4", "finish_reason": "stop", "usage": {"input_tokens": 3, "output_tokens": 2, "credits": 0.001}}
STREAMED = ({"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}, DONE)
BUDGET_MESSAGE = "This app has used up its AI budget for today."
CONNECTOR_OFF = "The owner has not turned on this connector for the app."

# Routes of the Portal's SDK API, as FakePortal.on() and sent_to() name them.
LLM = "/llm/complete"
JIRA = "/connectors/jira/request"
GITHUB = "/connectors/github/request"
CONFLUENCE = "/connectors/confluence/request"
JENKINS = "/connectors/jenkins/request"


def example_types():
    """``{name: type}`` of every example on disk, in name order."""
    found = {}
    if EXAMPLES.is_dir():
        for directory in sorted(EXAMPLES.iterdir()):
            manifest = directory / "efp-app.json"
            if directory.is_dir() and manifest.is_file():
                found[directory.name] = json.loads(manifest.read_text(encoding="utf-8")).get("type")
    return found


def example_files():
    """Every file of the examples that belongs to the repository."""
    skipped = {"__pycache__", ".efp-data", ".venv"}
    for path in sorted(EXAMPLES.rglob("*")):
        if path.is_file() and not skipped.intersection(path.parts) and path.name != ".env" and path.suffix != ".pyc":
            yield path


def load_module(name: str, path: Path, register=None):
    """Run a file as a module; ``register`` (a monkeypatch) also puts it in ``sys.modules``."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if register is not None:
        register.setitem(sys.modules, name, module)
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ inside examples/
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


def connector_answer(body=None, status=200, text=None, truncated=False):
    """What the Portal answers to ``POST /connectors/{type}/request``: the other system's status and body."""
    return {
        "status": status,
        "headers": {"content-type": "application/json" if text is None else "text/html"},
        "json": body,
        "body_base64": None if text is None else base64.b64encode(text.encode("utf-8")).decode("ascii"),
        "truncated": truncated,
    }


def sent_to(portal, path):
    """The JSON bodies the app sent to one route of the Portal, in order."""
    return [json.loads(request.content) for request in portal.requests if request.url.path.endswith(path)]


def signed(**changes):
    """The header the gateway adds for a signed-in visitor (Bob, id 12, unless changed)."""
    return {"X-EFP-Visitor": sign(payload_of(**changes))}


def owner():
    """The header of the app's owner (Alice, id 5, as in ``APP_INFO``)."""
    return signed(sub=5, username="alice", name="Alice")


def client_for(app, root_path=""):
    """A test client of a FastAPI app; ``root_path`` is the prefix uvicorn is given on the host."""
    from fastapi.testclient import TestClient

    return TestClient(app, root_path=root_path)
