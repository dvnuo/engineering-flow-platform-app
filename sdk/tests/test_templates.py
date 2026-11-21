"""The starter templates: one source, valid manifests, and code that runs.

``templates/`` at the repository root is the single source; ``efp_app/_templates``
is the copy that ships in the package. The first test fails when the two
differ (``python sync_templates.py`` refreshes the copy).

The FastAPI starter runs for real. Flask runs when Flask is installed.
Streamlit and Gradio run against small stand-ins for the two libraries, so
their own code is executed without installing them.
"""

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

import pytest

import efp_app
from efp_app import _check, cli, storage
from efp_app import manifest as sdk_manifest
from conftest import REPO_ROOT, SDK_ROOT, TEMPLATES, envelope, sse
from test_visitor import APP_ID, VISITOR_KEY, payload_of, sign

MIRROR = SDK_ROOT / "efp_app" / "_templates"
TYPES = ("fastapi", "flask", "streamlit", "gradio", "static")
DONE = {"type": "done", "model": "gpt-5.4", "finish_reason": "stop", "usage": {"input_tokens": 3, "output_tokens": 2, "credits": 0.001}}
STREAMED = ({"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}, DONE)
BUDGET_MESSAGE = "This app has used up its AI budget for today."

pytestmark = pytest.mark.skipif(not TEMPLATES.is_dir(), reason="needs a checkout of the repository (templates/)")


def tree(root: Path):
    found = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and "__pycache__" not in relative.parts and ".efp-data" not in relative.parts and path.suffix != ".pyc":
            found[relative.as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    return found


def load_module(name: str, path: Path, register=None):
    """Run a file as a module; ``register`` (a monkeypatch) also puts it in ``sys.modules``."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if register is not None:
        register.setitem(sys.modules, name, module)
    before = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # no __pycache__ inside templates/
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = before
    return module


@pytest.fixture
def deployed(monkeypatch, tmp_path):
    """The environment of an app on a sandbox host."""
    monkeypatch.setenv("EFP_APP_ID", APP_ID)
    monkeypatch.setenv("EFP_APP_SLUG", "mindmap")
    monkeypatch.setenv("EFP_APP_BASE_PATH", "/mindmap")
    monkeypatch.setenv("EFP_VISITOR_KEY", VISITOR_KEY)
    monkeypatch.setenv("EFP_APP_DATA_DIR", str(tmp_path / "data"))


# --- One source ---------------------------------------------------------------------------


def test_the_packaged_templates_are_the_top_level_ones():
    source, mirror = tree(TEMPLATES), tree(MIRROR)
    assert sorted(source) == sorted(mirror), "run: python sync_templates.py"
    different = [name for name in source if source[name] != mirror[name]]
    assert different == [], "run: python sync_templates.py"


def test_the_sync_script_agrees():
    sync = load_module("sync_templates", SDK_ROOT / "sync_templates.py")
    assert sync.differences() == []


def test_the_command_line_serves_the_packaged_copy():
    assert cli.templates_dir() == MIRROR
    assert sorted(path.name for path in MIRROR.iterdir() if path.is_dir()) == sorted(TYPES)
    assert cli.TEMPLATE_TYPES == TYPES


def test_the_package_data_patterns_cover_every_template_file():
    text = (SDK_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"_templates/**/*"' in text and '"_templates/**/.*"' in text
    assert 'version = "%s"' % efp_app.__version__ in text
    assert re.search(r'dependencies = \[\s*"httpx[^"]*",\s*\]', text), "httpx is the only runtime dependency"


# --- Every starter ---------------------------------------------------------------------------


@pytest.mark.parametrize("app_type", TYPES)
def test_a_starter_is_complete_and_valid(app_type):
    directory = TEMPLATES / app_type
    assert (directory / "README.md").is_file()
    data = json.loads((directory / "efp-app.json").read_text(encoding="utf-8"))
    assert data["type"] == app_type
    assert "owners" not in data  # a starter limits nobody: any member may register it

    # Nothing is left to fill in before it can be registered.
    assert [problem.render() for problem in _check.check_directory(directory)] == []
    assert sdk_manifest.validate(data) == []
    assert _check.check_files(directory, data) == []
    assert _check.scan_urls(directory, app_type) == []  # correct under a path prefix


@pytest.mark.parametrize("app_type", [name for name in TYPES if name != "static"])
def test_a_python_starter_asks_for_nothing_exotic(app_type):
    directory = TEMPLATES / app_type
    lines = [
        line.strip()
        for line in (directory / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    names = [re.match(r"[A-Za-z0-9._-]+", line).group(0).lower() for line in lines]
    allowed = {"fastapi": ["fastapi", "uvicorn"], "flask": ["flask", "gunicorn"], "streamlit": ["streamlit"], "gradio": ["gradio"]}
    assert names == allowed[app_type]
    assert all("==" not in line and "@" not in line and not line.startswith("-") for line in lines)
    env_example = (directory / ".env.example").read_text(encoding="utf-8")
    assert "EFP_APP_TOKEN=\n" in env_example and "EFP_PORTAL_URL=\n" in env_example
    assert ".env\n" in (directory / ".gitignore").read_text(encoding="utf-8")
    for source in directory.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        compile(text, str(source), "exec")
        assert "BudgetExceededError" in text and "EfpError" in text and "user_message" in text
        assert "storage." in text and "llm." in text and "get_visitor" in text


@pytest.mark.parametrize("app_type", [name for name in TYPES if name != "static"])
def test_a_python_starter_bounds_and_attributes_every_model_call(app_type):
    # What members copy must follow the rule they are given: a capped input, a max_output_tokens,
    # visitor=. Every call is paid from the app's daily budget, whoever asks.
    import ast

    calls = []
    for source in (TEMPLATES / app_type).rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            function = getattr(node, "func", None)
            if isinstance(node, ast.Call) and isinstance(function, ast.Attribute) and getattr(function.value, "id", None) == "llm":
                calls.append((function.attr, [keyword.arg for keyword in node.keywords]))
    assert calls, "a starter shows a model call"
    for name, keywords in calls:
        assert "max_output_tokens" in keywords and "visitor" in keywords, name


def test_the_static_starter_says_it_has_no_backend():
    directory = TEMPLATES / "static"
    assert not list(directory.rglob("*.py")) and not (directory / "requirements.txt").exists()
    page = (directory / "public" / "index.html").read_text(encoding="utf-8")
    assert "no backend" in page and "no SDK" in page
    assert 'href="style.css"' in page and 'src="app.js"' in page
    assert json.loads((directory / "efp-app.json").read_text(encoding="utf-8"))["entry"] == "public"
    assert 'fetch("data.json")' in (directory / "public" / "app.js").read_text(encoding="utf-8")
    assert json.loads((directory / "public" / "data.json").read_text(encoding="utf-8"))["items"]


def test_sources_are_ascii():
    roots = [SDK_ROOT / "efp_app", SDK_ROOT / "tests", TEMPLATES, REPO_ROOT / "docs" / "DEVELOPING_APPS.md"]
    files = []
    for root in roots:
        files += [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    files += [SDK_ROOT / name for name in ("pyproject.toml", "setup.py", "sync_templates.py", "README.md", "MANIFEST.in")]
    offenders = []
    for path in files:
        if path.suffix in (".pyc", ".png", ".ico"):
            continue
        data = path.read_bytes()
        if any(byte > 127 for byte in data):
            offenders.append(path.relative_to(REPO_ROOT).as_posix())
    assert offenders == []


# --- FastAPI: the real thing ---------------------------------------------------------------------


@pytest.fixture
def fastapi_app():
    pytest.importorskip("fastapi")
    return load_module("starter_fastapi_main", TEMPLATES / "fastapi" / "main.py").app


def client_for(app, root_path=""):
    from fastapi.testclient import TestClient

    return TestClient(app, root_path=root_path)


def test_fastapi_starter_knows_the_visitor(deployed, fastapi_app):
    client = client_for(fastapi_app)
    assert client.get("/api/me").json() == {"signed_in": False}
    headers = {"X-EFP-Visitor": sign(payload_of())}
    assert client.get("/api/me", headers=headers).json() == {"signed_in": True, "username": "bob", "name": "Bob", "group": "QA"}


def test_fastapi_starter_page_uses_relative_urls(deployed, fastapi_app):
    client = client_for(fastapi_app)
    page = client.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert 'href="static/style.css"' in page.text and 'src="static/app.js"' in page.text
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert 'fetch("api/me")' in script.text and 'fetch("api/ask"' in script.text and 'fetch("api/note"' in script.text
    assert client.get("/static/style.css").status_code == 200


def test_fastapi_starter_works_the_way_uvicorn_presents_a_prefix(deployed, fastapi_app):
    # uvicorn --root-path /mindmap: the path carries the prefix and root_path names it.
    client = client_for(fastapi_app, root_path="/mindmap")
    assert client.get("/mindmap/").status_code == 200
    assert client.get("/mindmap/api/me").json() == {"signed_in": False}
    assert client.get("/mindmap/static/app.js").status_code == 200


def test_fastapi_starter_streams_the_answer(deployed, fastapi_app, portal):
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    value = sign(payload_of())
    answer = client_for(fastapi_app).post("/api/ask", json={"text": "Say hello"}, headers={"X-EFP-Visitor": value})
    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/plain")
    assert answer.text == "Hello"
    body = portal.body()
    assert body["messages"] == [{"role": "user", "content": "Say hello"}]
    assert body["stream"] is True and body["system"]
    assert portal.last.headers["x-efp-visitor"] == value  # the Portal learns who asked


def test_fastapi_starter_says_when_the_budget_is_used_up(deployed, fastapi_app, portal):
    portal.on("POST", "/llm/complete", envelope(429, "budget_exceeded", "daily budget of 50 credits used", headers={"Retry-After": "3600"}))
    answer = client_for(fastapi_app).post("/api/ask", json={"text": "Hi"})
    assert answer.status_code == 429
    error = answer.json()["error"]
    assert error["code"] == "budget_exceeded"
    assert error["message"].startswith(BUDGET_MESSAGE)
    assert "50 credits" not in error["message"]  # the detail stays in the log


def test_fastapi_starter_reports_other_failures_kindly(deployed, fastapi_app, portal):
    portal.on("POST", "/llm/complete", envelope(403, "llm_disabled", "llm is off for app 0b0f"))
    answer = client_for(fastapi_app).post("/api/ask", json={"text": "Hi"})
    assert answer.status_code == 502
    assert answer.json()["error"] == {"code": "llm_disabled", "message": "The owner has not turned on the AI model for this app."}

    failure = {"type": "error", "error": {"code": "provider_error", "message": "boom"}}
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}, failure))
    answer = client_for(fastapi_app).post("/api/ask", json={"text": "Hi"})
    assert answer.status_code == 200
    assert answer.text == "Hel\n\n[The AI provider returned an error. Please try again.]"

    assert client_for(fastapi_app).post("/api/ask", json={"text": "   "}).status_code == 400


def test_fastapi_starter_bounds_the_question_and_the_answer(deployed, fastapi_app, portal):
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    client = client_for(fastapi_app)
    assert client.post("/api/ask", json={"text": "x" * 8000}).status_code == 200
    assert portal.body()["max_output_tokens"] == 1000
    assert len(portal.body()["messages"][0]["content"]) == 8000
    assert client.post("/api/ask", json={"text": "x" * 8001}).status_code == 422  # refused, not sent
    assert portal.count("POST", "/llm/complete") == 1


def test_fastapi_starter_without_a_token_still_serves_the_page(monkeypatch, fastapi_app):
    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    client = client_for(fastapi_app)
    assert client.get("/").status_code == 200
    assert client.get("/api/me").json()["username"] == "carol"
    answer = client.post("/api/ask", json={"text": "Hi"})
    assert answer.status_code == 502
    assert answer.json()["error"]["code"] == "not_configured"


def test_fastapi_starter_keeps_a_note_per_visitor(deployed, fastapi_app, tmp_path):
    client = client_for(fastapi_app)
    bob = {"X-EFP-Visitor": sign(payload_of())}
    eve = {"X-EFP-Visitor": sign(payload_of(sub=13, username="eve", name="Eve"))}
    assert client.get("/api/note", headers=bob).json() == {"text": ""}
    assert client.put("/api/note", json={"text": "bob's note \u2713"}, headers=bob).json() == {"saved": True}
    assert client.put("/api/note", json={"text": "guest note"}).json() == {"saved": True}
    assert client.get("/api/note", headers=bob).json() == {"text": "bob's note \u2713"}
    assert client.get("/api/note", headers=eve).json() == {"text": ""}
    assert client.get("/api/note").json() == {"text": "guest note"}
    assert sorted(item.key for item in storage.list("notes/")) == ["notes/12.txt", "notes/guest.txt"]
    assert (tmp_path / "data" / "objects" / "notes" / "12.txt").is_file()


# --- Flask: when it is installed ---------------------------------------------------------------------


@pytest.fixture
def flask_app(monkeypatch):
    pytest.importorskip("flask")
    # Flask finds its templates and static files through the module's place in sys.modules.
    return load_module("starter_flask_app", TEMPLATES / "flask" / "app.py", register=monkeypatch).app


def test_flask_starter_builds_its_urls_with_the_prefix(deployed, flask_app):
    client = flask_app.test_client()
    local = client.get("/", headers={"X-EFP-Visitor": sign(payload_of())})
    assert local.status_code == 200
    page = local.get_data(as_text=True)
    assert "Hello, Bob (QA)." in page
    assert 'href="/static/style.css"' in page and 'data-ask-url="/ask"' in page and 'data-note-url="/note"' in page

    # On the host gunicorn hands Flask SCRIPT_NAME=/mindmap and the path below it.
    hosted = client.get("/", environ_overrides={"SCRIPT_NAME": "/mindmap"}).get_data(as_text=True)
    assert "Hello, guest." in hosted
    assert 'href="/mindmap/static/style.css"' in hosted
    assert 'src="/mindmap/static/app.js"' in hosted
    assert 'data-ask-url="/mindmap/ask"' in hosted and 'data-note-url="/mindmap/note"' in hosted


def test_flask_starter_streams_and_reports_errors(deployed, flask_app, portal):
    client = flask_app.test_client()
    value = sign(payload_of())
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    answer = client.post("/ask", json={"text": "Say hello"}, headers={"X-EFP-Visitor": value})
    assert answer.status_code == 200 and answer.get_data(as_text=True) == "Hello"
    assert portal.body()["messages"] == [{"role": "user", "content": "Say hello"}]
    assert portal.last.headers["x-efp-visitor"] == value

    portal.on("POST", "/llm/complete", envelope(429, "budget_exceeded", headers={"Retry-After": "3600"}))
    refused = client.post("/ask", json={"text": "Hi"})
    assert refused.status_code == 429
    assert refused.get_json()["error"]["message"].startswith(BUDGET_MESSAGE)
    assert client.post("/ask", json={"text": ""}).status_code == 400


def test_flask_starter_bounds_the_question_and_the_answer(deployed, flask_app, portal):
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    answer = flask_app.test_client().post("/ask", json={"text": "x" * 9000})
    assert answer.status_code == 200
    assert portal.body()["max_output_tokens"] == 1000
    assert portal.body()["messages"] == [{"role": "user", "content": "x" * 8000}]  # cut, as before


def test_flask_starter_answers_half_an_emoji_with_a_sentence(deployed, flask_app, portal):
    # A page that cuts a text in the middle of an emoji sends half of a surrogate pair. The SDK
    # refuses it with an EfpError, so the starter's "except EfpError" answers; it used to be a 500.
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    client = flask_app.test_client()
    body = b'{"text": "half an emoji \\ud83d"}'
    asked = client.post("/ask", data=body, content_type="application/json")
    assert asked.status_code == 502
    assert asked.get_json()["error"] == {"code": "invalid_request", "message": "The app sent a request the EFP Portal could not accept."}
    saved = client.post("/note", data=body, content_type="application/json")
    assert saved.status_code == 502 and saved.get_json()["error"]["code"] == "invalid_text"
    assert portal.requests == [] and storage.list() == []


def test_flask_starter_keeps_a_note(deployed, flask_app):
    client = flask_app.test_client()
    bob = {"X-EFP-Visitor": sign(payload_of())}
    assert client.post("/note", json={"text": "remember <this>"}, headers=bob).get_json() == {"saved": True}
    assert storage.load("notes/12.txt") == b"remember <this>"
    page = client.get("/", headers=bob).get_data(as_text=True)
    assert "remember &lt;this&gt;</textarea>" in page


# --- Streamlit: a stand-in that runs the script ---------------------------------------------------------


class Block:
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class SessionState(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


class FakeStreamlit(types.ModuleType):
    """The few Streamlit calls the starter makes; what it shows is recorded in ``shown``."""

    def __init__(self, headers, prompt=None, pressed=(), typed=None):
        super().__init__("streamlit")
        self.context = types.SimpleNamespace(headers=headers)
        self.session_state = SessionState()
        self.sidebar = Block()
        self.shown = []
        self._prompt = prompt
        self._pressed = set(pressed)
        self._typed = typed

    def _show(self, kind):
        return lambda text, *args, **kwargs: self.shown.append((kind, text))

    def __getattr__(self, name):
        if name in ("title", "write", "subheader", "markdown", "success", "error", "warning"):
            return self._show(name)
        raise AttributeError(name)

    def set_page_config(self, **options):
        self.shown.append(("config", options))

    def text_area(self, label, key=None, height=None):
        if self._typed is not None:
            self.session_state[key] = self._typed
        return self.session_state.get(key, "")

    def button(self, label):
        return label in self._pressed

    def chat_message(self, role):
        self.shown.append(("chat", role))
        return Block()

    def chat_input(self, placeholder, **options):
        self.chat_input_options = options
        return self._prompt

    def write_stream(self, stream):
        text = "".join(stream)
        self.shown.append(("stream", text))
        return text


def run_streamlit(monkeypatch, fake):
    import runpy

    monkeypatch.setitem(sys.modules, "streamlit", fake)
    runpy.run_path(str(TEMPLATES / "streamlit" / "app.py"), run_name="__main__")
    return fake


def long_chat(exchanges):
    """A conversation of that many questions and answers, the oldest first."""
    found = []
    for number in range(exchanges):
        found += [{"role": "user", "content": "question %d" % number}, {"role": "assistant", "content": "answer %d" % number}]
    return found


def test_streamlit_starter_bounds_what_it_sends(deployed, portal, monkeypatch):
    # Every question sends the conversation again: only its end goes, and the answer is capped.
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    fake = FakeStreamlit({}, prompt="the newest question")
    fake.session_state["messages"] = long_chat(30) + [{"role": "user", "content": "y" * 9000}, {"role": "assistant", "content": "ok"}]
    run_streamlit(monkeypatch, fake)
    assert fake.chat_input_options == {"max_chars": 8000}
    sent = portal.body()
    assert sent["max_output_tokens"] == 1000
    assert len(sent["messages"]) == 21 and sent["messages"][0]["role"] == "user"
    assert sent["messages"][-1] == {"role": "user", "content": "the newest question"}
    assert sent["messages"][-3] == {"role": "user", "content": "y" * 8000}  # an old message is cut as well
    assert sent["messages"][0] == {"role": "user", "content": "question 21"}
    assert len(fake.session_state.messages) == 64  # the page keeps the whole conversation; the model gets its end


def test_streamlit_starter_greets_and_chats(deployed, portal, monkeypatch):
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    value = sign(payload_of())
    fake = run_streamlit(monkeypatch, FakeStreamlit({"X-Efp-Visitor": value}, prompt="Say hello"))
    assert ("write", "Hello, Bob (QA).") in fake.shown
    assert ("stream", "Hello") in fake.shown
    assert fake.session_state.messages == [{"role": "user", "content": "Say hello"}, {"role": "assistant", "content": "Hello"}]
    assert portal.body()["messages"] == [{"role": "user", "content": "Say hello"}]
    assert portal.last.headers["x-efp-visitor"] == value
    assert fake.shown[0][0] == "config"  # set_page_config comes first


def test_streamlit_starter_for_a_guest_without_a_question(deployed, portal, monkeypatch):
    fake = run_streamlit(monkeypatch, FakeStreamlit({}))
    assert ("write", "Hello, guest. You are not signed in.") in fake.shown
    assert portal.requests == []
    assert fake.session_state.messages == []


def test_streamlit_starter_says_when_the_budget_is_used_up(deployed, portal, monkeypatch):
    portal.on("POST", "/llm/complete", envelope(429, "budget_exceeded", headers={"Retry-After": "7200"}))
    fake = run_streamlit(monkeypatch, FakeStreamlit({}, prompt="Hi"))
    assert ("warning", BUDGET_MESSAGE + " Try again in about 2 hours.") in fake.shown
    assert fake.session_state.messages == [{"role": "user", "content": "Hi"}]

    portal.on("POST", "/llm/complete", envelope(424, "provider_auth_failed", "token expired for alice"))
    fake = run_streamlit(monkeypatch, FakeStreamlit({}, prompt="Hi"))
    errors = [text for kind, text in fake.shown if kind == "error"]
    assert errors == ["The owner's sign-in to the AI provider has expired. The owner needs to reconnect it in the Portal."]


def test_streamlit_starter_saves_and_reloads_the_note(deployed, portal, monkeypatch):
    value = sign(payload_of())
    fake = run_streamlit(monkeypatch, FakeStreamlit({"X-Efp-Visitor": value}, pressed={"Save note"}, typed="buy milk"))
    assert ("success", "Saved.") in fake.shown
    assert storage.load("notes/12.txt") == b"buy milk"
    again = run_streamlit(monkeypatch, FakeStreamlit({"X-Efp-Visitor": value}))
    assert again.session_state.note == "buy milk"


# --- Gradio: a stand-in that loads the script -------------------------------------------------------------


class FakeGradio(types.ModuleType):
    def __init__(self):
        super().__init__("gradio")
        self.built = []
        outer = self

        class Request:
            def __init__(self, headers=None):
                self.headers = headers or {}

        class Component:
            def __init__(self, *args, **options):
                outer.built.append((type(self).__name__, args, options))

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def load(self, *args, **options):
                outer.built.append(("load", args, options))

            def click(self, *args, **options):
                outer.built.append(("click", args, options))

            def launch(self, *args, **options):
                outer.built.append(("launch", args, options))

        self.Request = Request
        for name in ("Blocks", "Markdown", "ChatInterface", "Accordion", "Textbox", "Button"):
            setattr(self, name, type(name, (Component,), {}))


@pytest.fixture
def gradio_app(monkeypatch):
    fake = FakeGradio()
    monkeypatch.setitem(sys.modules, "gradio", fake)
    module = load_module("starter_gradio_app", TEMPLATES / "gradio" / "app.py")
    return module, fake


def test_gradio_starter_builds_its_page_and_does_not_launch_on_import(gradio_app):
    module, fake = gradio_app
    kinds = [kind for kind, _args, _options in fake.built]
    assert kinds.count("ChatInterface") == 1 and kinds.count("load") == 2 and kinds.count("click") == 1
    assert "launch" not in kinds
    chat = next(args for kind, args, _options in fake.built if kind == "ChatInterface")
    assert chat == (module.reply,)


def test_gradio_starter_launches_without_arguments(monkeypatch):
    import runpy

    fake = FakeGradio()
    monkeypatch.setitem(sys.modules, "gradio", fake)
    runpy.run_path(str(TEMPLATES / "gradio" / "app.py"), run_name="__main__")
    launches = [(args, options) for kind, args, options in fake.built if kind == "launch"]
    assert launches == [((), {})]  # the host's GRADIO_* variables decide where it listens


def test_gradio_starter_greets_the_visitor(deployed, gradio_app):
    module, fake = gradio_app
    assert module.greet(fake.Request({"x-efp-visitor": sign(payload_of())})) == "Hello, Bob (QA)."
    assert module.greet(fake.Request({"x-efp-visitor": sign(payload_of(group=None))})) == "Hello, Bob."
    assert module.greet(fake.Request()) == "Hello, guest. You are not signed in."


def test_gradio_starter_streams_with_any_history_shape(deployed, gradio_app, portal):
    module, fake = gradio_app
    value = sign(payload_of())
    request = fake.Request({"x-efp-visitor": value})
    portal.on("POST", "/llm/complete", sse(*STREAMED))

    messages_format = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}]
    assert list(module.reply("three", messages_format, request)) == ["Hel", "Hello"]
    expected = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}, {"role": "user", "content": "three"}]
    assert portal.body()["messages"] == expected
    assert portal.last.headers["x-efp-visitor"] == value

    pairs_format = [["one", "two"]]
    assert list(module.reply("three", pairs_format, request))[-1] == "Hello"
    assert portal.body()["messages"] == expected

    parts_format = [
        {"role": "user", "content": [{"type": "text", "text": "one"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
        {"role": "user", "content": {"path": "/tmp/file.png"}},
    ]
    assert list(module.reply({"text": "three", "files": []}, parts_format, request))[-1] == "Hello"
    assert portal.body()["messages"] == expected

    assert list(module.reply("first", [], request))[-1] == "Hello"
    assert list(module.reply("first", None, request))[-1] == "Hello"


def test_gradio_starter_bounds_what_it_sends(deployed, gradio_app, portal):
    # Every question sends the conversation again: only its end goes, and the answer is capped.
    module, fake = gradio_app
    portal.on("POST", "/llm/complete", sse(*STREAMED))
    history = long_chat(30) + [{"role": "user", "content": "y" * 9000}, {"role": "assistant", "content": "ok"}]
    assert list(module.reply("z" * 9000, history, fake.Request()))[-1] == "Hello"
    sent = portal.body()
    assert sent["max_output_tokens"] == 1000
    assert len(sent["messages"]) == 21 and sent["messages"][0] == {"role": "user", "content": "question 21"}
    assert sent["messages"][-1] == {"role": "user", "content": "z" * 8000}
    assert sent["messages"][-3] == {"role": "user", "content": "y" * 8000}

    # What is kept starts with a question: an answer whose question fell away goes too.
    odd = [{"role": "assistant", "content": "a greeting of the app"}] + long_chat(10)
    assert list(module.reply("next", odd, fake.Request()))[-1] == "Hello"
    assert [message["role"] for message in portal.body()["messages"]][:2] == ["user", "assistant"]
    assert len(portal.body()["messages"]) == 21
    assert module.recent([{"role": "assistant", "content": "only an answer"}]) == []


def test_gradio_starter_says_when_the_budget_is_used_up(deployed, gradio_app, portal):
    module, fake = gradio_app
    portal.on("POST", "/llm/complete", envelope(429, "budget_exceeded", headers={"Retry-After": "7200"}))
    assert list(module.reply("Hi", [], fake.Request())) == [BUDGET_MESSAGE + " Try again in about 2 hours."]

    failure = {"type": "error", "error": {"code": "provider_error", "message": "boom"}}
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}, failure))
    assert list(module.reply("Hi", [], fake.Request())) == ["Hel", "Hel\n\nThe AI provider returned an error. Please try again."]


def test_gradio_starter_keeps_a_note_per_visitor(deployed, gradio_app):
    module, fake = gradio_app
    bob = fake.Request({"x-efp-visitor": sign(payload_of())})
    assert module.load_note(bob) == ""
    assert module.save_note("call Alice", bob) == "Saved."
    assert module.save_note(None, fake.Request()) == "Saved."
    assert module.load_note(bob) == "call Alice"
    assert module.load_note(fake.Request()) == ""
    assert storage.load("notes/12.txt") == b"call Alice"
