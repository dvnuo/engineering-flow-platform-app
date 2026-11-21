"""``docs/SDK_REFERENCE.md`` is exact: its examples run, its signatures are the code's, no public name is missing.

The reference marks what can be checked with an HTML comment on the line
before a code fence::

    <!-- runnable -->                          a Python example: it is executed here
    <!-- signature: efp_app.llm.complete -->   the block is the header of that function, as the source has it
    <!-- fields: efp_app.visitor.Visitor -->   the block lists the fields of that dataclass: name, type, default

A line ``<!-- environment: NAME VAR=value ... -->`` above ``<!-- runnable -->``
names the situation the example runs in (``ENVIRONMENTS`` below; without the
line it is ``local``) and may set more environment variables.

Every runnable example talks to the fake Portal of ``conftest`` (the
``portal`` fixture), which ``connect`` fills with the answers the reference
describes under "How to read this file". The one name an example may use
without defining it is ``request_headers``: the headers of the request being
handled.

What "public" means here: a name without a leading underscore in a module
without one. The internal modules (``_check``, ``_sse`` ...) must be named in
the reference, with what they do; their own names need not be.
"""

import argparse
import ast
import dataclasses
import importlib
import inspect
import json
import os
import pkgutil
import re
import textwrap

import pytest

import efp_app
from efp_app import cli, config, errors
from efp_app import client as sdk_client
from conftest import REPO_ROOT, envelope, sse
from example_support import connector_answer
from test_visitor import APP_ID, payload_of, sign

REFERENCE = REPO_ROOT / "docs" / "SDK_REFERENCE.md"
TEXT = REFERENCE.read_text(encoding="utf-8") if REFERENCE.is_file() else ""
LINES = TEXT.splitlines()

pytestmark = pytest.mark.skipif(not REFERENCE.is_file(), reason="needs a checkout of the repository (docs/)")

#: The modules an app calls: every function of these has a runnable example.
APP_FACING = ("app", "client", "config", "connectors", "errors", "llm", "manifest", "storage", "visitor")
#: A Python example that is not marked as runnable must need one of these.
FRAMEWORKS = ("fastapi", "flask", "streamlit", "gradio", "starlette")


# --- The document: its blocks, its headings ------------------------------------------------------

MARKER = re.compile(r"<!--\s*(runnable|environment|signature|fields)\s*(?::\s*(.*?))?\s*-->")
#: What a reader sees: the document without the marker comments.
VISIBLE = "\n".join(line for line in LINES if not MARKER.fullmatch(line))


class Block:
    """A fenced block: the line of its opening fence (from 1), its language, its text, the markers above it."""

    def __init__(self, line, language, code, markers):
        self.line = line
        self.language = language
        self.code = code
        self.markers = markers

    @property
    def id(self):
        return "line-%d" % self.line


def read_blocks(lines):
    found = []
    index = 0
    while index < len(lines):
        opening = re.fullmatch(r"```(\w*)", lines[index])
        if not opening:
            index += 1
            continue
        start = index
        index += 1
        body = []
        while index < len(lines) and lines[index] != "```":
            body.append(lines[index])
            index += 1
        index += 1  # the closing fence
        markers = {}
        above = start - 1
        while above >= 0 and MARKER.fullmatch(lines[above]):
            marker = MARKER.fullmatch(lines[above])
            markers[marker.group(1)] = (marker.group(2) or "").strip()
            above -= 1
        found.append(Block(start + 1, opening.group(1), "\n".join(body) + "\n", markers))
    return found


def read_headings(lines):
    """``(index, level, title)`` of every heading; a ``#`` line inside a fence is code."""
    found = []
    fenced = False
    for index, line in enumerate(lines):
        if line.startswith("```"):
            fenced = not fenced
        elif not fenced:
            heading = re.fullmatch(r"(#{1,6}) (.+)", line)
            if heading:
                found.append((index, len(heading.group(1)), heading.group(2)))
    return found


BLOCKS = read_blocks(LINES)
HEADINGS = read_headings(LINES)
RUNNABLE = [block for block in BLOCKS if "runnable" in block.markers]
SIGNATURES = [block for block in BLOCKS if "signature" in block.markers]
FIELDS = [block for block in BLOCKS if "fields" in block.markers]


def mentions(name):
    """True when a reader finds ``name`` as a whole dotted name (``efp_app.llm.complete`` counts for ``llm.complete``)."""
    return re.search(r"(?<!\w)%s(?!\w)" % re.escape(name), VISIBLE) is not None


def section(title):
    """The text from the first heading that contains ``title`` to the next heading of that level or above."""
    for position, (index, level, text) in enumerate(HEADINGS):
        if title in text:
            later = [other for other, other_level, _ in HEADINGS[position + 1 :] if other_level <= level]
            return "\n".join(LINES[index : later[0] if later else len(LINES)])
    raise AssertionError("the reference has no heading with %s" % title)


# --- The SDK, as the code has it ----------------------------------------------------------------


def sdk_modules():
    """Every module of the package, the internal ones included."""
    found = [efp_app]
    for item in pkgutil.walk_packages(efp_app.__path__, "efp_app."):
        last = item.name.rsplit(".", 1)[-1]
        if not last.startswith("__") and last != "_version":
            found.append(importlib.import_module(item.name))
    return found


def short(module):
    """How the reference names a module: ``llm`` for ``efp_app.llm``, ``efp_app`` for the package."""
    return module.__name__.rsplit(".", 1)[-1]


MODULES = sdk_modules()
PUBLIC_MODULES = [module for module in MODULES if not short(module).startswith("_")]


def functions_of(module):
    return sorted(
        name
        for name, value in vars(module).items()
        if not name.startswith("_") and inspect.isfunction(value) and value.__module__ == module.__name__
    )


def classes_of(module):
    return sorted(
        name
        for name, value in vars(module).items()
        if not name.startswith("_") and inspect.isclass(value) and value.__module__ == module.__name__
    )


def values_of(module):
    """The public names a module assigns at its top level: constants, limits, type aliases."""
    found = []
    for node in ast.parse(inspect.getsource(module)).body:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
        found += [target.id for target in targets if isinstance(target, ast.Name) and not target.id.startswith("_")]
    return sorted(found)


def public_names(module):
    """``__all__`` where a module has one, else every public function, class and value defined in it."""
    listed = getattr(module, "__all__", None)
    if listed is not None:
        return sorted(listed)
    return sorted(set(functions_of(module) + classes_of(module) + values_of(module)))


def resolve(dotted):
    """The object behind ``efp_app.client.Client.get``; a property gives its getter."""
    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            found = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        try:
            for part in parts[cut:]:
                found = inspect.getattr_static(found, part) if inspect.isclass(found) else getattr(found, part)
        except AttributeError:
            break
        if isinstance(found, property):
            found = found.fget
        return getattr(found, "__func__", found)
    raise AssertionError("the reference names %s, which the SDK does not have" % dotted)


def squeeze(text):
    return " ".join(text.split())


def header_of(function):
    """``def name(...) -> X:`` as the source has it: comments dropped, white space squeezed."""
    source = textwrap.dedent(inspect.getsource(function))
    node = next(item for item in ast.parse(source).body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)))
    lines = source.splitlines()[node.lineno - 1 : max(node.body[0].lineno - 1, node.lineno)]
    return squeeze(" ".join(re.sub(r"\s+#.*$", "", line) for line in lines))


def field_lines(cls):
    """``name: type = default`` for every field of a dataclass, in order."""
    lines = []
    for field in dataclasses.fields(cls):
        line = "%s: %s" % (field.name, field.type if isinstance(field.type, str) else field.type.__name__)
        if field.default is not dataclasses.MISSING:
            line += " = %r" % (field.default,)
        elif field.default_factory is not dataclasses.MISSING:
            line += " = %s()" % field.default_factory.__name__
        lines.append(line)
    return lines


# --- The fake Portal the examples talk to -------------------------------------------------------

#: The connectors the owner turned on: type -> (address, whether writes are allowed).
TURNED_ON = {
    "github": ("https://api.github.com", True),
    "jenkins": ("https://jenkins.example.com", False),
    "jira": ("https://jira.example.com", False),
}
APP = {
    "id": APP_ID,
    "slug": "example",
    "name": "Example",
    "url": "https://apps.example.com/example/",
    "owner": {"id": 5, "username": "alice", "display_name": "Alice", "group_id": 3, "group_name": "QA"},
    "llm": {"enabled": True, "default_model": "gpt-5.4", "models": ["gpt-5.4"]},
    "connectors": [{"type": kind, "write": write} for kind, (_address, write) in sorted(TURNED_ON.items())],
    "budget": {"daily_credits": 50.0, "used_credits": 1.2, "daily_tokens": 2000000, "used_tokens": 3400},
}
USAGE = {"input_tokens": 12, "output_tokens": 3, "credits": 0.002}
COMPLETION = {"text": "Hello.", "model": "gpt-5.4", "finish_reason": "stop", "usage": USAGE}
DONE = {"type": "done", "model": "gpt-5.4", "finish_reason": "stop", "usage": USAGE}
STREAMED = ({"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo."}, DONE)
BROKEN = ({"type": "delta", "text": "Hel"}, {"type": "error", "error": {"code": "provider_error", "message": "the provider hung up"}})
ISSUE = {"key": "ABC-1", "fields": {"summary": "Login fails", "status": {"name": "Open"}}}
CONSOLE = "Started by user alice\nFinished: SUCCESS\n"


def other_system(kind, method, path):
    """What Jira, GitHub and Jenkins answer in the examples."""
    path = path.split("?", 1)[0]
    if kind == "jira" and path == "/rest/api/2/issue/ABC-1":
        return connector_answer(ISSUE)
    if kind == "jira" and path == "/rest/api/2/search":
        return connector_answer({"total": 1, "issues": [ISSUE]})
    if kind == "jira":
        return connector_answer({"errorMessages": ["Issue Does Not Exist"]}, status=404)
    if kind == "github" and method == "POST" and path == "/repos/acme/app/issues":
        return connector_answer({"number": 7, "title": "Bug"}, status=201)
    if kind == "github" and path == "/repos/acme/app":
        return connector_answer({"full_name": "acme/app", "default_branch": "main"})
    if kind == "jenkins" and path == "/job/build/1/consoleText":
        return connector_answer(text=CONSOLE)
    return connector_answer({"message": "Not Found"}, status=404)


def connect(portal, budget_used_up=False, stream_fails=False):
    """Give the fake Portal the answers of section 9 of the contract, for the app of the examples."""

    def model(request):
        if budget_used_up:
            return envelope(429, "budget_exceeded", "daily budget of 50 credits used", headers={"Retry-After": "3600"})
        if json.loads(request.content)["stream"]:
            return sse(*(BROKEN if stream_fails else STREAMED))
        return COMPLETION

    def connector(kind):
        def answer(request):
            body = json.loads(request.content)
            if kind not in TURNED_ON:
                return envelope(403, "connector_disabled", "%s is not turned on for this app" % kind)
            if body["method"] not in ("GET", "HEAD") and not TURNED_ON[kind][1]:
                return envelope(403, "method_not_allowed", "%s is read only for this app" % kind)
            return other_system(kind, body["method"], body["path"])

        return answer

    portal.on("GET", "/app", APP).on("POST", "/llm/complete", model)
    listed = [{"type": kind, "base_url": address, "write": write} for kind, (address, write) in sorted(TURNED_ON.items())]
    portal.on("GET", "/connectors", {"connectors": listed})
    for kind in ("jira", "confluence", "github", "jenkins", "nexus", "splunk"):
        portal.on("POST", "/connectors/%s/request" % kind, connector(kind))


def a_developers_machine(request, monkeypatch, **answers):
    connect(request.getfixturevalue("portal"), **answers)  # the token and the Portal's address, nothing else
    return {}


def the_host(request, monkeypatch):
    request.getfixturevalue("deployed")  # an id, a slug, a path prefix, a visitor key, a data directory
    monkeypatch.setenv("EFP_APP_PUBLIC_URL", APP["url"])
    monkeypatch.setenv("PORT", "20003")
    connect(request.getfixturevalue("portal"))
    return {"X-EFP-Visitor": sign(payload_of())}  # what the gateway adds for the signed-in member bob


#: The situations an example can ask for. Each sets one up and returns ``request_headers``.
ENVIRONMENTS = {
    "local": a_developers_machine,
    "deployed": the_host,
    "no-token": lambda request, monkeypatch: {},
    "budget-used-up": lambda request, monkeypatch: a_developers_machine(request, monkeypatch, budget_used_up=True),
    "stream-fails": lambda request, monkeypatch: a_developers_machine(request, monkeypatch, stream_fails=True),
}


# --- (a) The examples run -----------------------------------------------------------------------


def test_the_markers_are_where_the_test_looks_for_them():
    for number, line in enumerate(LINES, 1):
        if not line.lstrip().startswith("<!--"):
            continue
        marker = MARKER.fullmatch(line)
        assert marker, "line %d: not a marker this test knows" % number
        below = LINES[number] if number < len(LINES) else ""
        if marker.group(1) == "environment":
            assert below == "<!-- runnable -->", "line %d: an environment belongs on the line above <!-- runnable -->" % number
        else:
            assert below == "```python", "line %d: the marker belongs on the line before a ```python fence" % number
    for block in BLOCKS:
        assert len([kind for kind in ("runnable", "signature", "fields") if kind in block.markers]) <= 1, block.id
    for block in RUNNABLE:
        assert block.markers["runnable"] == "", block.id
        assert block.markers.get("environment", "local").split()[0] in ENVIRONMENTS, block.id


def test_an_example_that_is_not_marked_needs_a_web_framework():
    for block in BLOCKS:
        if block.language != "python" or block.markers:
            continue
        compile(block.code, "%s:%d" % (REFERENCE.name, block.line), "exec")  # it is Python, at least
        needs = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", block.code, re.MULTILINE)
        assert any(part in FRAMEWORKS for name in needs for part in name.split(".")), (
            "%s: a Python example that needs no web framework must be marked <!-- runnable -->" % block.id
        )


@pytest.mark.parametrize("block", RUNNABLE, ids=[block.id for block in RUNNABLE])
def test_a_runnable_example_runs(block, request, monkeypatch):
    words = block.markers.get("environment", "local").split()
    request_headers = ENVIRONMENTS[words[0]](request, monkeypatch)
    for assignment in words[1:]:
        name, _, value = assignment.partition("=")
        monkeypatch.setenv(name, value)
    # Padded with empty lines, so that a failure names the line of the document.
    code = compile("\n" * block.line + block.code, str(REFERENCE), "exec")
    before = dict(os.environ)
    try:
        exec(code, {"__name__": "sdk_reference_example", "request_headers": request_headers})
    finally:
        os.environ.clear()
        os.environ.update(before)


def test_every_function_an_app_calls_has_a_runnable_example():
    together = "\n".join(block.code for block in RUNNABLE)
    missing = [
        "%s.%s" % (short(module), name)
        for module in MODULES
        if short(module) in APP_FACING
        for name in functions_of(module)
        if not re.search(r"(?<![\w.])%s\.%s\(" % (short(module), name), together)
    ]
    assert missing == []


# --- (b) Nothing public is missing --------------------------------------------------------------


def test_every_public_name_is_in_the_reference():
    assert [module.__name__ for module in MODULES if not mentions(module.__name__)] == []
    missing = [
        "%s.%s" % (short(module), name)
        for module in PUBLIC_MODULES
        for name in public_names(module)
        if not mentions("%s.%s" % (short(module), name))
    ]
    assert missing == []


def test_every_exception_is_in_the_reference():
    table = section("The hierarchy")
    classes = [value for value in vars(errors).values() if inspect.isclass(value) and issubclass(value, errors.EfpError)]
    for cls in classes:
        if cls.__name__.startswith("_"):
            continue
        assert mentions(cls.__name__), cls.__name__
        row = next((line for line in table.splitlines() if "`errors.%s`" % cls.__name__ in line), None)
        assert row, "%s has no row in the table of errors" % cls.__name__
        # The row says what the class says: its code, its status, the sentence for a visitor.
        assert "`%s`" % cls.code in row, cls.__name__
        assert "| %s |" % (cls.default_status or "none") in row, cls.__name__
        assert cls._user_message in row, cls.__name__
    for code in list(errors.PORTAL_ERRORS) + list(errors.STORAGE_ERRORS):
        assert "`%s`" % code in table, code


def test_every_environment_variable_is_in_the_reference():
    table = section("Environment variables")
    constants = {name: value for name, value in vars(config).items() if name.isupper() and isinstance(value, str)}
    assert "EFP_APP_TOKEN" in constants.values()
    for name, value in constants.items():
        row = next((line for line in table.splitlines() if line.startswith("| `%s` |" % value)), None)
        assert row and "`config.%s`" % name in row, value
    # What the HTTP client reads besides: the CA variables, and the proxy variables httpx takes from the environment.
    for value in sdk_client._CA_FILE_VARIABLES + ("SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "NO_PROXY"):
        assert "`%s`" % value in table, value


def test_every_function_has_its_signature_from_the_code():
    shown = set()
    for block in SIGNATURES:
        names = block.markers["signature"].split()
        assert names, block.id
        assert squeeze(block.code) == " ".join(header_of(resolve(name)) for name in names), "%s: not the header in the source" % block.id
        shown.update(names)
    # The functions behind the command are named in the reference, without their signatures: it documents the command.
    missing = [
        "%s.%s" % (module.__name__, name)
        for module in PUBLIC_MODULES
        if module is not cli
        for name in functions_of(module)
        if "%s.%s" % (module.__name__, name) not in shown
    ]
    assert missing == []


def test_every_dataclass_has_its_fields_from_the_code():
    shown = set()
    for block in FIELDS:
        name = block.markers["fields"]
        assert block.code.splitlines() == field_lines(resolve(name)), "%s: not the fields in the source" % block.id
        shown.add(name)
    missing = [
        "%s.%s" % (module.__name__, name)
        for module in PUBLIC_MODULES
        for name in classes_of(module)
        if dataclasses.is_dataclass(getattr(module, name)) and "%s.%s" % (module.__name__, name) not in shown
    ]
    assert missing == []


def test_every_subcommand_and_option_of_the_command_is_in_the_reference():
    commands = next(action for action in cli.build_parser()._actions if isinstance(action, argparse._SubParsersAction))
    for name, command in commands.choices.items():
        text = section("`efp-app %s`" % name)
        for action in command._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            for argument in action.option_strings or [action.dest]:
                assert "`%s`" % argument in text or "`%s " % argument in text, "efp-app %s: %s" % (name, argument)
            for choice in action.choices or ():
                assert "`%s`" % choice in text, "efp-app %s: %s" % (name, choice)
    assert "`--version`" in TEXT and "`efp-app %s`" % efp_app.__version__ in TEXT


def test_the_reference_is_plain_ascii_markdown():
    assert [number for number, line in enumerate(LINES, 1) if not line.isascii()] == []
    assert "\t" not in TEXT
    assert len([line for line in LINES if line.startswith("```")]) % 2 == 0  # every fence is closed
    assert "version %s" % efp_app.__version__ in TEXT
