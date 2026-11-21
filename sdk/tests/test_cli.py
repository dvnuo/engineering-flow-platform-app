"""The manifest rules (section 2) and the ``efp-app`` command (sections 8 and 8.1)."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from efp_app import _check, cli
from efp_app import manifest as sdk_manifest
from conftest import TOKEN

GOOD = {
    "name": "Mind map",
    "description": "Draw and share mind maps.",
    "icon": "icon.png",
    "type": "fastapi",
    "entry": "main:app",
    "command": None,
    "requirements": "requirements.txt",
    "health_path": "/",
    "startup_timeout": 60,
    "strip_prefix": None,
    "owners": ["alice"],
}


def manifest_of(**changes):
    data = dict(GOOD)
    data.update(changes)
    return {key: value for key, value in data.items() if value is not ...}


def errors_of(data, **options):
    return [problem.message for problem in sdk_manifest.validate(data, **options) if problem.level == "error"]


def warnings_of(data):
    return [problem.message for problem in sdk_manifest.validate(data) if problem.level == "warning"]


def write_app(directory: Path, data=None, files=None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if data is not None:
        (directory / "efp-app.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    for name, content in (files or {}).items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
    return directory


def good_app(directory: Path, **changes) -> Path:
    return write_app(
        directory,
        manifest_of(**changes),
        {
            "main.py": "from fastapi import FastAPI\n\napp = FastAPI()\n",
            "requirements.txt": "fastapi>=0.110\nuvicorn\n",
            "icon.png": b"\x89PNG",
            "static/index.html": '<link rel="stylesheet" href="static/style.css">\n<script src="static/app.js"></script>\n',
            "static/app.js": 'fetch("api/items");\n',
            "static/style.css": "body { background: url(img/bg.png); }\n",
        },
    )


def run_cli(capsys, *arguments):
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- The manifest: section 2 ---------------------------------------------------------------


def test_the_example_of_the_contract_is_valid():
    assert sdk_manifest.validate(GOOD) == []


def test_only_name_type_and_entry_are_needed():
    assert sdk_manifest.validate({"name": "x", "type": "streamlit", "entry": "app.py"}) == []
    assert sdk_manifest.validate({"name": "x", "type": "static"}) == []
    assert sdk_manifest.validate({"name": "x", "type": "command", "command": "python run.py --port $PORT"}) == []


@pytest.mark.parametrize(
    "changes, fragment",
    [
        ({"name": ...}, '"name" is required'),
        ({"name": ""}, '"name" is required'),
        ({"name": "   "}, '"name" is required'),
        ({"name": 5}, '"name" is required'),
        ({"name": "x" * 81}, '"name" is longer than 80'),
        ({"description": "x" * 501}, '"description" is longer than 500'),
        ({"description": 5}, '"description" must be text'),
        ({"type": ...}, '"type" is required'),
        ({"type": "django"}, '"type" is required'),
        ({"icon": "/etc/icon.png"}, '"icon" may not be an absolute path'),
        ({"icon": "../icon.png"}, '"icon" may not contain ".."'),
        ({"icon": "C:/icon.png"}, '"icon" may not be an absolute path'),
        ({"icon": "img\\icon.png"}, '"icon" must use forward slashes'),
        ({"entry": ...}, '"entry" must be module:attribute'),
        ({"entry": "main.py"}, '"entry" must be module:attribute'),
        ({"entry": "main:"}, '"entry" must be module:attribute'),
        ({"entry": "my-module:app"}, '"entry" must be module:attribute'),
        ({"type": "streamlit", "entry": ...}, '"entry" must be the path of the script'),
        ({"type": "gradio", "entry": "../app.py"}, '"entry" may not contain ".."'),
        ({"type": "static", "entry": "/var/www"}, '"entry" may not be an absolute path'),
        ({"type": "command", "command": None}, '"command" is required'),
        ({"type": "command", "command": "  "}, '"command" is required'),
        ({"type": "command", "command": "a\nb"}, '"command" must be a single line'),
        ({"requirements": "/abs/requirements.txt"}, '"requirements" may not be an absolute path'),
        ({"requirements": "a/../../r.txt"}, '"requirements" may not contain ".."'),
        ({"requirements": 5}, '"requirements" must be the relative path'),
        ({"health_path": "healthz"}, '"health_path" must start with "/"'),
        ({"health_path": "/a b"}, '"health_path" must start with "/"'),
        ({"health_path": 5}, '"health_path" must start with "/"'),
        ({"startup_timeout": 301}, '"startup_timeout" must be between 1 and 300'),
        ({"startup_timeout": 0}, '"startup_timeout" must be between 1 and 300'),
        ({"startup_timeout": "60"}, '"startup_timeout" must be a whole number'),
        ({"startup_timeout": True}, '"startup_timeout" must be a whole number'),
        ({"startup_timeout": 1.5}, '"startup_timeout" must be a whole number'),
        ({"strip_prefix": "yes"}, '"strip_prefix" must be true, false or null'),
        ({"owners": "alice"}, '"owners" must be a list of Portal usernames'),
        ({"owners": ["alice", ""]}, '"owners" must be a list of Portal usernames'),
        ({"owners": ["alice", "  "]}, '"owners" must be a list of Portal usernames'),
        ({"owners": ["alice", 5]}, '"owners" must be a list of Portal usernames'),
    ],
)
def test_each_rule_of_section_2(changes, fragment):
    found = errors_of(manifest_of(**changes))
    assert len(found) == 1, found
    assert fragment in found[0]


@pytest.mark.parametrize(
    "changes",
    [
        {"entry": "pkg.web.main:app"},
        {"entry": "main:server.app"},
        {"type": "flask", "entry": "app:app"},
        {"type": "static", "entry": ...},
        {"type": "static", "entry": "public"},
        {"type": "static", "entry": "."},
        {"type": "command", "entry": "ignored:really", "command": "sh run.sh"},
        {"health_path": "/healthz?full=1"},
        {"startup_timeout": 300},
        {"strip_prefix": True},
        {"strip_prefix": False},
        {"icon": None, "description": None, "requirements": None, "health_path": None, "startup_timeout": None},
        {"owners": ["alice", "bob"]},
        # Optional: without the list any member may register the app.
        {"owners": ...},
        {"owners": None},
        {"owners": []},
    ],
)
def test_values_the_contract_allows(changes):
    assert errors_of(manifest_of(**changes)) == []


def test_unknown_keys_are_ignored_with_a_warning():
    data = manifest_of(enrty="main:app", _comment="fine", **{"$schema": "fine"})
    assert errors_of(data) == []
    assert warnings_of(data) == ['unknown key "enrty" is ignored']


def test_a_manifest_that_is_not_an_object():
    assert errors_of(["name"]) == ["the manifest must be a JSON object"]


def test_a_slip_in_owners_does_not_stop_a_launch():
    assert errors_of(manifest_of(owners="alice")) != []
    assert errors_of(manifest_of(owners="alice"), for_launch=True) == []
    assert errors_of(manifest_of(entry=...), for_launch=True) != []


def test_parse_fills_in_the_defaults():
    found = sdk_manifest.parse({"name": "x", "type": "flask", "entry": "app:app", "owners": ["alice"]})
    assert found.requirements == "requirements.txt" and found.requirements_named is False
    assert found.health_path == "/"
    assert found.startup_timeout == 60
    assert found.strip_prefix is False
    assert found.owners == ("alice",)
    assert sdk_manifest.parse({"name": "x", "type": "static"}).entry == "."
    assert sdk_manifest.parse({"name": "x", "type": "static"}).strip_prefix is True
    assert sdk_manifest.parse({"name": "x", "type": "fastapi", "strip_prefix": False}).strip_prefix is False
    assert sdk_manifest.parse({"name": "x", "type": "gradio", "requirements": "deps.txt"}).requirements_named is True


def test_the_prefix_defaults_of_section_6_2():
    assert sdk_manifest.STRIP_PREFIX_BY_TYPE == {
        "fastapi": True,
        "gradio": True,
        "static": True,
        "flask": False,
        "streamlit": False,
        "command": False,
    }
    assert sdk_manifest.TYPES == ("fastapi", "flask", "streamlit", "gradio", "command", "static")


def test_reading_a_manifest_file(tmp_path):
    data, problems = sdk_manifest.read(tmp_path)
    assert data is None and "not found" in problems[0].message

    (tmp_path / "efp-app.json").write_text('{"name": "x",\n "type": }', encoding="utf-8")
    data, problems = sdk_manifest.read(tmp_path)
    assert data is None
    assert "not valid JSON" in problems[0].message and problems[0].line == 2

    (tmp_path / "efp-app.json").write_text("\ufeff" + json.dumps(GOOD), encoding="utf-8")
    data, problems = sdk_manifest.read(tmp_path)
    assert data == GOOD and problems == []


# --- efp-app check ----------------------------------------------------------------------------


def test_check_passes_a_good_app(tmp_path, capsys):
    code, out, err = run_cli(capsys, "check", str(good_app(tmp_path / "app")))
    assert code == 0
    assert "no problems found" in out
    assert err == ""


def test_check_defaults_to_the_current_directory(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(good_app(tmp_path / "app"))
    code, out, _err = run_cli(capsys, "check")
    assert code == 0 and "no problems found" in out


def test_check_without_a_manifest(tmp_path, capsys):
    code, out, _err = run_cli(capsys, "check", str(tmp_path))
    assert code == 1
    assert "efp-app.json" in out and "not found" in out
    assert "1 error, 0 warnings" in out


def test_check_on_a_missing_directory(tmp_path, capsys):
    code, _out, err = run_cli(capsys, "check", str(tmp_path / "nope"))
    assert code == 1 and "is not a directory" in err


@pytest.mark.parametrize("owners", [..., []])
def test_check_passes_an_app_without_owners(tmp_path, capsys, owners):
    code, out, _err = run_cli(capsys, "check", str(good_app(tmp_path / "app", owners=owners)))
    assert code == 0 and "no problems found" in out


def test_check_flags_owners_that_are_not_a_list(tmp_path, capsys):
    code, out, _err = run_cli(capsys, "check", str(good_app(tmp_path / "app", owners="alice")))
    assert code == 1
    assert 'error   efp-app.json: "owners" must be a list of Portal usernames' in out
    assert "leave it out to let any member register this app" in out


def test_check_needs_the_entry_module(tmp_path, capsys):
    app = good_app(tmp_path / "app", entry="server:app")
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 1
    assert '"entry" is "server:app", but server.py does not exist' in out


def test_check_accepts_a_package_as_the_entry_module(tmp_path, capsys):
    app = good_app(tmp_path / "app", entry="web.main:app")
    write_app(app, None, {"web/__init__.py": "", "web/main/__init__.py": "app = object()\n"})
    assert run_cli(capsys, "check", str(app))[0] == 0


def test_check_warns_when_the_module_does_not_mention_the_attribute(tmp_path, capsys):
    app = good_app(tmp_path / "app", entry="main:application")
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    assert 'warning efp-app.json: "entry" is "main:application", but main.py does not mention "application"' in out


@pytest.mark.parametrize("app_type", ["streamlit", "gradio"])
def test_check_needs_the_entry_script(tmp_path, capsys, app_type):
    app = write_app(
        tmp_path / "app",
        {"name": "x", "type": app_type, "entry": "app.py", "owners": ["alice"]},
        {"requirements.txt": app_type + "\n"},
    )
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 1 and '"entry" is "app.py", which does not exist' in out
    (app / "app.py").write_text("print('hi')\n", encoding="utf-8")
    assert run_cli(capsys, "check", str(app))[0] == 0


def test_check_a_static_app(tmp_path, capsys):
    app = write_app(tmp_path / "app", {"name": "x", "type": "static", "entry": "public", "owners": ["alice"]})
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 1 and '"entry" is "public", which is not a directory' in out
    (app / "public").mkdir()
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0 and "there is no index.html" in out
    (app / "public" / "index.html").write_text('<script src="app.js"></script>', encoding="utf-8")
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0 and "no problems found" in out


def test_check_needs_a_named_requirements_file(tmp_path, capsys):
    app = good_app(tmp_path / "app", requirements="deps/prod.txt")
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 1 and '"requirements" is "deps/prod.txt", which does not exist' in out


def test_check_only_warns_when_the_default_requirements_file_is_missing(tmp_path, capsys):
    app = good_app(tmp_path / "app", requirements=...)
    (app / "requirements.txt").unlink()
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    assert "warning efp-app.json: there is no requirements.txt" in out
    assert "0 errors, 1 warning" in out


def test_check_needs_the_icon_file(tmp_path, capsys):
    app = good_app(tmp_path / "app")
    (app / "icon.png").unlink()
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 1 and '"icon" is "icon.png", which does not exist' in out


def test_check_reads_the_requirements(tmp_path, capsys):
    app = good_app(tmp_path / "app")
    (app / "requirements.txt").write_text(
        "# comment\n"
        "requests==2.32.0\n"
        "git+https://github.com/acme/tool.git\n"
        "-e ./local-package\n"
        "tool @ https://example.com/tool-1.0.tar.gz\n"
        "--extra-index-url https://pypi.example.com/simple\n"
        "./wheels/thing.whl\n",
        encoding="utf-8",
    )
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    for line in (3, 4, 5, 7):
        assert "warning requirements.txt:%d: the host installs wheels from its package index only" % line in out
    assert "warning requirements.txt:6: the host uses its own package index" in out
    assert 'warning requirements.txt: does not list "fastapi", which a fastapi app needs' in out
    assert "requirements.txt:2" not in out


def test_check_trusts_included_requirement_files(tmp_path, capsys):
    app = good_app(tmp_path / "app")
    (app / "requirements.txt").write_text("-r base.txt\n", encoding="utf-8")
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0 and "does not list" not in out


ROOT_ABSOLUTE = {
    "static/index.html": (
        "<!doctype html>\n"
        '<link rel="stylesheet" href="/static/style.css">\n'
        "<script src='/static/app.js'></script>\n"
        '<a href="/">Home</a> <img SRC = "/img/logo.png">\n'
        '<form action="/submit"></form>\n'
        '<a href="//cdn.example.com/x.js">protocol-relative is fine</a>\n'
        '<a href="https://example.com/">absolute is fine</a> <a href="page/2">relative is fine</a>\n'
    ),
    "static/app.js": (
        'const a = await fetch("/api/items");\n'
        "fetch('/api/more', { method: 'POST' });\n"
        "fetch(`/api/${id}`);\n"
        'new EventSource("/events");\n'
        'fetch("api/fine"); fetch(url); fetch("//other.example.com/x");\n'
    ),
    "static/style.css": (
        "body { background: url(/img/bg.png); }\n"
        '.a { background: url("/img/a.png"); }\n'
        ".b { background: url('img/fine.png'); }\n"
        ".c { background: url(//cdn.example.com/c.png); }\n"
    ),
    "templates/page.jinja2": '<a href="/{{ slug }}/x">x</a>\n<a href="{{ url_for(\'index\') }}">fine</a>\n',
}


def test_check_finds_root_absolute_urls(tmp_path, capsys):
    app = write_app(good_app(tmp_path / "app"), None, ROOT_ABSOLUTE)
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0  # warnings only
    expected = [
        'warning static/index.html:2: root-absolute URL href="/static/style.css": use a relative URL: href="static/style.css"',
        'warning static/index.html:3: root-absolute URL src="/static/app.js": use a relative URL: src="static/app.js"',
        'warning static/index.html:4: root-absolute URL href="/": use a relative URL: href="./"',
        'warning static/index.html:4: root-absolute URL SRC="/img/logo.png": use a relative URL: SRC="img/logo.png"',
        'warning static/index.html:5: root-absolute URL action="/submit": use a relative URL: action="submit"',
        'warning static/app.js:1: root-absolute URL fetch("/api/items"): use a relative URL: fetch("api/items")',
        'warning static/app.js:2: root-absolute URL fetch("/api/more"): use a relative URL: fetch("api/more")',
        'warning static/app.js:3: root-absolute URL fetch("/api/${id}"): use a relative URL: fetch("api/${id}")',
        'warning static/app.js:4: root-absolute URL EventSource("/events"): use a relative URL: EventSource("events")',
        "warning static/style.css:1: root-absolute URL url(/img/bg.png): use a path relative to the style sheet: url(../img/bg.png)",
        "warning static/style.css:2: root-absolute URL url(/img/a.png): use a path relative to the style sheet: url(../img/a.png)",
        'warning templates/page.jinja2:1: root-absolute URL href="/{{ slug }}/x": use a relative URL: href="{{ slug }}/x"',
    ]
    for line in expected:
        assert line in out, line
    assert "0 errors, 12 warnings" in out
    assert "index.html:6" not in out and "index.html:7" not in out
    assert "app.js:5" not in out
    assert "style.css:3" not in out and "style.css:4" not in out
    assert "page.jinja2:2" not in out


def test_check_strict_fails_on_warnings(tmp_path, capsys):
    app = write_app(good_app(tmp_path / "app"), None, {"static/app.js": 'fetch("/api/items");\n'})
    assert run_cli(capsys, "check", str(app))[0] == 0
    assert run_cli(capsys, "check", "--strict", str(app))[0] == 1


def test_check_suggests_url_for_in_a_flask_app(tmp_path, capsys):
    app = write_app(
        tmp_path / "app",
        {"name": "x", "type": "flask", "entry": "app:app", "owners": ["alice"]},
        {
            "app.py": "app = None\n",
            "requirements.txt": "flask\n",
            "templates/index.html": '<script src="/static/app.js"></script>\n',
        },
    )
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    assert 'use a relative URL: src="static/app.js" or {{ url_for(...) }}' in out


def test_check_skips_dependencies_and_hidden_folders(tmp_path, capsys):
    app = write_app(
        good_app(tmp_path / "app"),
        None,
        {
            "node_modules/pkg/index.js": 'fetch("/x");\n',
            ".venv/lib/site.html": '<a href="/x">x</a>\n',
            ".efp-data/objects/page.html": '<a href="/x">x</a>\n',
            "__pycache__/x.js": 'fetch("/x");\n',
            "notes.txt": 'href="/not-a-page"\n',
            "main.py": 'app = None  # href="/python-is-not-scanned"\n',
        },
    )
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0 and "no problems found" in out


def test_check_caps_the_findings_of_one_file(tmp_path, capsys):
    lines = "".join('fetch("/api/%d");\n' % number for number in range(30))
    app = write_app(good_app(tmp_path / "app"), None, {"static/bundle.js": lines})
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    assert len(re.findall(r"static/bundle\.js:[0-9]+: ", out)) == _check.MAX_FINDINGS_PER_FILE
    assert "warning static/bundle.js: 10 more root-absolute URLs in this file" in out


def test_errors_are_printed_before_warnings(tmp_path, capsys):
    app = write_app(good_app(tmp_path / "app", owners="alice"), None, {"static/app.js": 'fetch("/api/items");\n'})
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 1
    assert out.index("error ") < out.index("warning ")
    assert "1 error, 1 warning" in out


@pytest.mark.parametrize(
    "sheet, url, advice",
    [
        # Beside the sheet: the directory's name is not repeated.
        ("static/style.css", "/static/bg.png", "bg.png"),
        ("static/css/site.css", "/static/img/bg.png", "../img/bg.png"),
        ("static/css/site.css", "/static/css/fonts/a.woff2", "fonts/a.woff2"),
        # Outside the sheet's directory.
        ("static/style.css", "/img/bg.png", "../img/bg.png"),
        ("style.css", "/img/bg.png", "img/bg.png"),
        # What follows the path stays as written.
        ("static/style.css", "/static/font.woff2?v=3#iefix", "font.woff2?v=3#iefix"),
        ("static/style.css", "/static/", "./"),
        ("static/style.css", "/img/", "../img/"),
        ("static/style.css", "/", "../"),
    ],
)
def test_check_writes_a_style_sheet_url_as_the_sheet_sees_it(tmp_path, capsys, sheet, url, advice):
    app = write_app(good_app(tmp_path / "app"), None, {sheet: "body { background: url(%s); }\n" % url})
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    assert "warning %s:1: root-absolute URL url(%s): use a path relative to the style sheet: url(%s)" % (sheet, url, advice) in out


@pytest.mark.parametrize(
    "entry, sheet, advice",
    [
        # The served folder is the top of the app: the sheet is at /style.css, not /public/style.css.
        ("public", "public/style.css", "img/bg.png"),
        ("public/", "public/css/site.css", "../img/bg.png"),
        ("./public", "public/style.css", "img/bg.png"),
        (".", "css/site.css", "../img/bg.png"),
        (..., "style.css", "img/bg.png"),
        # A sheet outside the served folder is not served at all; the hint is the plain one.
        ("public", "other/style.css", "../img/bg.png"),
    ],
)
def test_check_starts_from_the_served_folder_of_a_static_app(tmp_path, capsys, entry, sheet, advice):
    data = {"name": "Links", "type": "static", "entry": entry}
    data = {key: value for key, value in data.items() if value is not ...}
    files = {"public/index.html": "<p>x</p>\n", "index.html": "<p>x</p>\n", sheet: "body { background: url(/img/bg.png); }\n"}
    app = write_app(tmp_path / "app", data, files)
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    assert "warning %s:1: root-absolute URL url(/img/bg.png): use a path relative to the style sheet: url(%s)" % (sheet, advice) in out


def test_check_writes_a_style_in_a_page_as_the_page_sees_it(tmp_path, capsys):
    page = '<div style="background: url(/img/bg.png)">x</div>\n'
    app = write_app(good_app(tmp_path / "app"), None, {"templates/page.html": page})
    code, out, _err = run_cli(capsys, "check", str(app))
    assert code == 0
    assert "warning templates/page.html:1: root-absolute URL url(/img/bg.png): use a relative URL: url(img/bg.png)" in out


# --- efp-app init ------------------------------------------------------------------------------


@pytest.mark.parametrize("app_type", cli.TEMPLATE_TYPES)
def test_init_creates_a_starter(tmp_path, capsys, app_type):
    target = tmp_path / "new-app"
    code, out, err = run_cli(capsys, "init", app_type, str(target))
    assert code == 0 and err == ""
    created = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
    assert created == sorted(path.as_posix() for path in cli.template_files(app_type))
    assert "efp-app.json" in created and "README.md" in created
    data = json.loads((target / "efp-app.json").read_text(encoding="utf-8"))
    assert data["type"] == app_type
    # Any member may register a fresh starter: it limits nobody.
    assert "owners" not in data
    assert "Created a %s app" % app_type in out
    assert "efp-app dev" in out and '"owners"' not in out
    if app_type != "static":
        assert ".env.example" in created and ".gitignore" in created and "requirements.txt" in created
        assert "pip install -r requirements.txt" in out

    # A fresh starter has nothing left to fill in before it can be registered.
    code, out, _err = run_cli(capsys, "check", str(target))
    assert code == 0 and "no problems found" in out


@pytest.mark.parametrize("folder", [".venv", "__pycache__", ".efp-data"])
def test_init_works_when_the_sdk_is_installed_below_a_skipped_name(tmp_path, capsys, monkeypatch, folder):
    # pip puts the package under .venv/Lib/site-packages: only names below the starter are skipped.
    installed = tmp_path / folder / "Lib" / "site-packages" / "efp_app" / "_templates"
    shutil.copytree(cli.templates_dir(), installed)
    (installed / "flask" / "__pycache__").mkdir()
    (installed / "flask" / "__pycache__" / "app.cpython-311.pyc").write_bytes(b"x")
    (installed / "flask" / ".efp-data").mkdir()
    (installed / "flask" / ".efp-data" / "left-over.json").write_text("{}", encoding="utf-8")
    expected = sorted(path.as_posix() for path in cli.template_files("flask"))
    monkeypatch.setattr(cli, "templates_dir", lambda: installed)

    assert sorted(path.as_posix() for path in cli.template_files("flask")) == expected
    assert "efp-app.json" in expected and "app.py" in expected

    target = tmp_path / "new-app"
    code, _out, err = run_cli(capsys, "init", "flask", str(target))
    assert code == 0 and err == ""
    created = sorted(path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file())
    assert created == expected


def test_init_fills_in_owner_and_name(tmp_path, capsys):
    target = tmp_path / "mindmap"
    code, out, _err = run_cli(capsys, "init", "fastapi", str(target), "--owner", "alice", "--owner", "bob", "--name", "Mind map")
    assert code == 0
    data = json.loads((target / "efp-app.json").read_text(encoding="utf-8"))
    assert data["owners"] == ["alice", "bob"]
    assert data["name"] == "Mind map"
    assert data["type"] == "fastapi" and data["entry"] == "main:app"
    assert '"owners"' not in out
    code, out, _err = run_cli(capsys, "check", str(target))
    assert code == 0 and "no problems found" in out


def test_init_into_the_current_directory(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, out, _err = run_cli(capsys, "init", "static")
    assert code == 0
    assert (tmp_path / "efp-app.json").is_file() and (tmp_path / "public" / "index.html").is_file()
    assert "cd " not in out


def test_init_never_overwrites_without_force(tmp_path, capsys):
    target = tmp_path / "app"
    target.mkdir()
    (target / "main.py").write_text("# mine\n", encoding="utf-8")
    (target / "unrelated.txt").write_text("keep\n", encoding="utf-8")
    code, _out, err = run_cli(capsys, "init", "fastapi", str(target))
    assert code == 1
    assert "main.py" in err and "Nothing was written" in err
    assert (target / "main.py").read_text(encoding="utf-8") == "# mine\n"
    assert not (target / "efp-app.json").exists()

    code, _out, _err = run_cli(capsys, "init", "fastapi", str(target), "--force")
    assert code == 0
    assert "FastAPI" in (target / "main.py").read_text(encoding="utf-8")
    assert (target / "unrelated.txt").read_text(encoding="utf-8") == "keep\n"


def test_init_refuses_a_file_as_the_target(tmp_path, capsys):
    (tmp_path / "taken").write_text("x", encoding="utf-8")
    code, _out, err = run_cli(capsys, "init", "flask", str(tmp_path / "taken"))
    assert code == 1 and "is not a directory" in err


def test_init_rejects_an_unknown_type(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["init", "django"])
    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_no_command_prints_the_help(capsys):
    code, out, _err = run_cli(capsys)
    assert code == 2
    assert "init" in out and "dev" in out and "check" in out


# --- efp-app dev: the command lines of section 8.1 -----------------------------------------------

PY = "/venv/bin/python"


def launch_of(platform="linux", base_path="", port=8000, **fields):
    found = sdk_manifest.parse(dict({"name": "x", "owners": ["alice"]}, **fields))
    return cli.build_launch(found, port, base_path=base_path, python=PY, platform=platform)


def test_fastapi_command_line():
    launch = launch_of(type="fastapi", entry="main:app")
    assert launch.argv == [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000", "--no-server-header"]
    assert launch.env == {}
    assert launch.needs == "uvicorn"
    on_host = launch_of(type="fastapi", entry="main:app", base_path="/mindmap", port=20003)
    assert on_host.argv == [
        PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "20003",
        "--root-path", "/mindmap", "--no-server-header",
    ]  # fmt: skip


def test_flask_command_line():
    launch = launch_of(type="flask", entry="app:app")
    assert launch.argv == [
        PY, "-m", "gunicorn", "app:app", "--bind", "127.0.0.1:8000",
        "--workers", "1", "--threads", "8", "--timeout", "120",
    ]  # fmt: skip
    assert launch.env == {}
    assert launch.needs == "gunicorn"
    on_host = launch_of(type="flask", entry="app:app", base_path="/mindmap", port=20003)
    assert on_host.argv[:6] == [PY, "-m", "gunicorn", "app:app", "--bind", "127.0.0.1:20003"]
    assert on_host.env == {"SCRIPT_NAME": "/mindmap"}


def test_flask_command_line_on_windows():
    launch = launch_of(platform="win32", type="flask", entry="app:app", port=5001)
    assert launch.argv == [PY, "-m", "flask", "--app", "app:app", "run", "--port", "5001"]
    assert launch.env == {}
    assert launch.needs == "flask"
    assert "gunicorn" in launch.note


def test_streamlit_command_line():
    tail = [
        "--server.headless", "true", "--browser.gatherUsageStats", "false",
        "--server.enableCORS", "false", "--server.enableXsrfProtection", "false",
    ]  # fmt: skip
    launch = launch_of(type="streamlit", entry="app.py")
    assert launch.argv == [PY, "-m", "streamlit", "run", "app.py", "--server.port", "8000", "--server.address", "127.0.0.1"] + tail
    assert launch.env == {}
    on_host = launch_of(type="streamlit", entry="app.py", base_path="/mindmap")
    assert on_host.argv == [
        PY, "-m", "streamlit", "run", "app.py", "--server.port", "8000", "--server.address", "127.0.0.1",
        "--server.baseUrlPath", "/mindmap",
    ] + tail  # fmt: skip


def test_gradio_command_line():
    launch = launch_of(type="gradio", entry="app.py")
    assert launch.argv == [PY, "app.py"]
    assert launch.env == {"GRADIO_SERVER_NAME": "127.0.0.1", "GRADIO_SERVER_PORT": "8000"}
    on_host = launch_of(type="gradio", entry="app.py", base_path="/mindmap", port=20003)
    assert on_host.env == {"GRADIO_SERVER_NAME": "127.0.0.1", "GRADIO_SERVER_PORT": "20003", "GRADIO_ROOT_PATH": "/mindmap"}


def test_command_command_line():
    launch = launch_of(type="command", command="python server.py --port $PORT --prefix $EFP_APP_BASE_PATH")
    assert launch.argv == ["sh", "-c", "python server.py --port $PORT --prefix $EFP_APP_BASE_PATH"]
    assert launch.env == {} and launch.needs is None


def test_static_has_a_local_preview_only():
    launch = launch_of(type="static", entry="public")
    assert launch.argv == [PY, "-m", "http.server", "8000", "--bind", "127.0.0.1", "--directory", "public"]
    assert "no process on the host" in launch.note
    assert launch_of(type="static").argv[-1] == "."


def test_the_default_interpreter_is_the_one_running_the_tool():
    found = sdk_manifest.parse({"name": "x", "type": "gradio", "entry": "app.py"})
    assert cli.build_launch(found, 8000).argv[0] == sys.executable


# --- efp-app dev: environment and .env --------------------------------------------------------


def test_env_file_parsing(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "\ufeff# a comment\n"
        "\n"
        "EFP_APP_TOKEN=" + TOKEN + "\n"
        "export EFP_PORTAL_URL = https://efp.example.com  # the Portal\n"
        "QUOTED=\"two words # not a comment\"\n"
        "SINGLE='x y'\n"
        "EMPTY=\n"
        "not a variable line\n"
        "1BAD=x\n",
        encoding="utf-8",
    )
    assert cli.read_env_file(path) == {
        "EFP_APP_TOKEN": TOKEN,
        "EFP_PORTAL_URL": "https://efp.example.com",
        "QUOTED": "two words # not a comment",
        "SINGLE": "x y",
        "EMPTY": "",
    }
    assert cli.read_env_file(tmp_path / "missing.env") == {}


def test_the_child_environment():
    base = {
        "PATH": "/bin",
        "EFP_PORTAL_URL": "https://from-the-shell.example.com",
        "EFP_APP_BASE_PATH": "/left-over",
        "SCRIPT_NAME": "/left-over",
        "GRADIO_ROOT_PATH": "/left-over",
        "PORT": "1",
    }
    env_file = {"EFP_APP_TOKEN": TOKEN, "EFP_PORTAL_URL": "https://from-the-file.example.com", "MY_SETTING": "x"}
    env = cli.child_environment(base, env_file, 8123)
    assert env["PORT"] == "8123"
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["EFP_APP_TOKEN"] == TOKEN  # from .env
    assert env["EFP_PORTAL_URL"] == "https://from-the-shell.example.com"  # the environment wins
    assert env["MY_SETTING"] == "x"
    assert env["PATH"] == "/bin"
    for name in ("EFP_APP_BASE_PATH", "SCRIPT_NAME", "GRADIO_ROOT_PATH"):
        assert name not in env  # an empty base path: the options that carry it are left out
    assert base["PORT"] == "1"


@pytest.fixture
def launched(monkeypatch):
    """Record what `efp-app dev` would start instead of starting it."""
    calls = []

    def fake_call(argv, cwd=None, env=None):
        calls.append({"argv": list(argv), "cwd": cwd, "env": dict(env)})
        return 0

    monkeypatch.setattr(cli.subprocess, "call", fake_call)
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: object())
    return calls


def test_dev_starts_the_app_with_the_token_from_dot_env(tmp_path, capsys, launched):
    app = good_app(tmp_path / "app", owners=...)
    (app / ".env").write_text("EFP_APP_TOKEN=%s\nEFP_PORTAL_URL=https://efp.example.com\n" % TOKEN, encoding="utf-8")
    code, out, err = run_cli(capsys, "dev", str(app), "--port", "8123")
    assert code == 0 and err == ""
    call = launched[0]
    assert call["argv"] == [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8123", "--no-server-header"]
    assert Path(call["cwd"]) == app.resolve()
    assert call["env"]["PORT"] == "8123"
    assert call["env"]["EFP_APP_TOKEN"] == TOKEN
    assert call["env"]["EFP_PORTAL_URL"] == "https://efp.example.com"
    assert call["env"]["PYTHONUNBUFFERED"] == "1"
    assert "EFP_APP_BASE_PATH" not in call["env"] and "EFP_APP_ID" not in call["env"]
    assert "http://127.0.0.1:8123/" in out
    assert "token      set" in out
    assert "https://efp.example.com" in out
    assert TOKEN not in out and TOKEN not in err  # the token is never printed


def test_dev_prefers_the_environment_and_defaults_the_port(tmp_path, capsys, launched, monkeypatch):
    app = good_app(tmp_path / "app")
    (app / ".env").write_text("EFP_APP_TOKEN=from-the-file\nPORT=9100\n", encoding="utf-8")
    monkeypatch.setenv("EFP_APP_TOKEN", TOKEN)
    monkeypatch.chdir(app)
    assert run_cli(capsys, "dev")[0] == 0
    assert launched[0]["env"]["EFP_APP_TOKEN"] == TOKEN
    assert launched[0]["env"]["PORT"] == "9100"
    monkeypatch.setenv("PORT", "9200")
    assert run_cli(capsys, "dev")[0] == 0
    assert launched[1]["env"]["PORT"] == "9200"
    (app / ".env").unlink()
    monkeypatch.delenv("PORT")
    assert run_cli(capsys, "dev")[0] == 0
    assert launched[2]["env"]["PORT"] == "8000"
    assert "--port" in launched[2]["argv"] and "8000" in launched[2]["argv"]


def test_dev_warns_when_the_token_is_missing_but_still_starts(tmp_path, capsys, launched):
    code, out, _err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")))
    assert code == 0 and len(launched) == 1
    assert "token      not set" in out
    assert "EFP_APP_TOKEN and EFP_PORTAL_URL not set" in out
    assert ".env" in out


def test_dev_says_when_a_setting_of_the_host_takes_the_local_visitor_away(tmp_path, capsys, launched, monkeypatch):
    app = good_app(tmp_path / "app")
    out = run_cli(capsys, "dev", str(app), "--dry-run")[1]
    assert "EFP_VISITOR_KEY" not in out and "EFP_APP_ID" not in out

    # Only a host sets the visitor key: with it the SDK expects a gateway and uses no stand-in.
    (app / ".env").write_text("EFP_VISITOR_KEY=a2V5LWZyb20tZG90LWVudg\nEFP_DEV_VISITOR=carol\n", encoding="utf-8")
    out = run_cli(capsys, "dev", str(app), "--dry-run")[1]
    assert "warning    EFP_VISITOR_KEY is set, so a request without a signed header has no visitor (EFP_DEV_VISITOR is not used)." in out
    assert "a2V5LWZyb20tZG90LWVudg" not in out  # the key itself is never printed

    monkeypatch.setenv("EFP_APP_ID", "0b0f3a4e")
    out = run_cli(capsys, "dev", str(app), "--dry-run")[1]
    assert "warning    EFP_APP_ID is set, so the SDK behaves as on the host (no local visitor)." in out
    assert "EFP_VISITOR_KEY is set" not in out  # said once: the first warning covers it


def test_dev_passes_gradio_its_settings(tmp_path, capsys, launched, monkeypatch):
    monkeypatch.setenv("GRADIO_ROOT_PATH", "/left-over")
    app = write_app(tmp_path / "app", {"name": "x", "type": "gradio", "entry": "app.py", "owners": ["alice"]}, {"app.py": ""})
    assert run_cli(capsys, "dev", str(app), "--port", "7000")[0] == 0
    call = launched[0]
    assert call["argv"] == [sys.executable, "app.py"]
    assert call["env"]["GRADIO_SERVER_NAME"] == "127.0.0.1"
    assert call["env"]["GRADIO_SERVER_PORT"] == "7000"
    assert "GRADIO_ROOT_PATH" not in call["env"]


def test_dev_starts_flask_the_right_way_for_the_platform(tmp_path, capsys, launched, monkeypatch):
    monkeypatch.setenv("SCRIPT_NAME", "/left-over")
    app = write_app(tmp_path / "app", {"name": "x", "type": "flask", "entry": "app:app", "owners": ["alice"]}, {"app.py": "app = None\n"})
    assert run_cli(capsys, "dev", str(app))[0] == 0
    argv = launched[0]["argv"]
    if sys.platform == "win32":
        assert argv == [sys.executable, "-m", "flask", "--app", "app:app", "run", "--port", "8000"]
    else:
        assert argv[:4] == [sys.executable, "-m", "gunicorn", "app:app"]
    assert "SCRIPT_NAME" not in launched[0]["env"]


def test_dev_previews_a_static_app_without_asking_for_a_token(tmp_path, capsys, launched):
    app = write_app(tmp_path / "app", {"name": "x", "type": "static", "entry": "public", "owners": ["alice"]}, {"public/index.html": "hi"})
    code, out, _err = run_cli(capsys, "dev", str(app))
    assert code == 0
    assert launched[0]["argv"] == [sys.executable, "-m", "http.server", "8000", "--bind", "127.0.0.1", "--directory", "public"]
    assert "token" not in out and "local preview" in out


def test_dev_dry_run_shows_the_command_and_starts_nothing(tmp_path, capsys, launched):
    code, out, _err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")), "--dry-run")
    assert code == 0 and launched == []
    assert "-m uvicorn main:app --host 127.0.0.1 --port 8000 --no-server-header" in out


def test_dev_needs_a_usable_manifest(tmp_path, capsys, launched):
    code, _out, err = run_cli(capsys, "dev", str(tmp_path))
    assert code == 1 and "efp-app.json" in err and "not found" in err
    app = good_app(tmp_path / "app", entry="main.py")
    code, _out, err = run_cli(capsys, "dev", str(app))
    assert code == 1 and '"entry" must be module:attribute' in err
    assert launched == []


def test_dev_says_when_the_runner_is_not_installed(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(cli.subprocess, "call", lambda *a, **k: pytest.fail("must not start"))
    code, _out, err = run_cli(capsys, "dev", str(good_app(tmp_path / "app")))
    assert code == 1
    assert "uvicorn is not installed" in err and "pip install -r requirements.txt" in err


def test_dev_needs_the_entry_script(tmp_path, capsys, launched):
    app = write_app(tmp_path / "app", {"name": "x", "type": "streamlit", "entry": "app.py", "owners": ["alice"]})
    code, _out, err = run_cli(capsys, "dev", str(app))
    assert code == 1 and '"entry" is "app.py", which does not exist' in err and launched == []


def test_dev_prints_names_a_console_cannot_show(tmp_path, capsys, launched, monkeypatch):
    class Latin1:
        encoding = "latin-1"

        def __init__(self):
            self.text = ""

        def write(self, text):
            text.encode(self.encoding)
            self.text += text

    console = Latin1()
    monkeypatch.setattr(cli.sys, "stdout", console)
    app = good_app(tmp_path / "app", name="\u601d\u7ef4\u5bfc\u56fe")
    assert cli.main(["dev", str(app), "--dry-run"]) == 0
    assert "\\u601d\\u7ef4\\u5bfc\\u56fe" in console.text


def test_python_dash_m_runs_the_tool():
    import subprocess

    from conftest import SDK_ROOT

    result = subprocess.run([sys.executable, "-m", "efp_app", "--version"], cwd=str(SDK_ROOT), capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stdout.strip().startswith("efp-app 0.1.0")


# --- what efp-app dev protects the app's environment from --------------------------------------


def test_the_apps_own_address_stays_off_a_company_proxy():
    # Gradio asks its own port while it starts; through a proxy that fails and the app never comes up.
    env = cli.child_environment({"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": ".corp.example"}, {}, 8000)
    assert env["NO_PROXY"] == env["no_proxy"] == ".corp.example,127.0.0.1,localhost,::1"
    already = cli.child_environment({"http_proxy": "http://proxy:3128", "no_proxy": "LOCALHOST, 127.0.0.1"}, {}, 8000)
    assert already["no_proxy"] == already["NO_PROXY"] == "LOCALHOST,127.0.0.1,::1"
    everything = {"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "*"}
    cli.exempt_loopback_from_proxy(everything)
    assert everything == {"HTTPS_PROXY": "http://proxy:3128", "NO_PROXY": "*"}
    # No proxy: nothing to exempt, nothing is added.
    plain = cli.child_environment({"NO_PROXY": ".corp.example"}, {}, 8000)
    assert plain["NO_PROXY"] == ".corp.example" and "no_proxy" not in plain
    # A proxy that only .env names counts as well.
    from_file = cli.child_environment({}, {"HTTP_PROXY": "http://proxy:3128"}, 8000)
    assert from_file["NO_PROXY"] == "127.0.0.1,localhost,::1"


def test_git_bash_is_told_to_leave_the_prefix_alone():
    env = {}
    cli.keep_paths_from_msys(env)
    assert env == {
        "MSYS2_ENV_CONV_EXCL": "EFP_APP_BASE_PATH;SCRIPT_NAME;GRADIO_ROOT_PATH",
        # Arguments are left alone as well: "python server.py $EFP_APP_BASE_PATH" gets /my-app, as on the host.
        "MSYS_NO_PATHCONV": "1",
        "MSYS2_ARG_CONV_EXCL": "*",
    }
    kept = {"MSYS2_ENV_CONV_EXCL": "MY_PATH;SCRIPT_NAME", "MSYS2_ARG_CONV_EXCL": "/mine"}
    cli.keep_paths_from_msys(kept)
    assert kept["MSYS2_ENV_CONV_EXCL"] == "MY_PATH;SCRIPT_NAME;EFP_APP_BASE_PATH;GRADIO_ROOT_PATH"
    assert kept["MSYS2_ARG_CONV_EXCL"] == "/mine"  # what the developer chose stays
    everything = {"MSYS2_ENV_CONV_EXCL": "*"}
    cli.keep_paths_from_msys(everything)
    assert everything["MSYS2_ENV_CONV_EXCL"] == "*"
    # Only on Windows does the dev command set it.
    assert ("MSYS2_ENV_CONV_EXCL" in cli.child_environment({}, {}, 8000)) is (sys.platform == "win32")


def test_a_proxy_of_the_systems_own_settings_is_handed_to_the_app_with_its_own_port_exempt():
    # Windows keeps the proxy in its settings; httpx reads it there and ignores the bypass list beside it.
    system = ({"http": "http://proxy.corp.example:8080", "https": "http://proxy.corp.example:8080"}, [".corp.example", "intranet"])
    env = cli.child_environment({}, {}, 8000, system_proxy=system)
    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"] == "http://proxy.corp.example:8080"
    assert env["NO_PROXY"] == env["no_proxy"] == ".corp.example,intranet,127.0.0.1,localhost,::1"

    # A proxy named in the environment (or in .env) wins: the system's settings are not looked at.
    named = cli.child_environment({"HTTPS_PROXY": "http://mine:1"}, {}, 8000, system_proxy=system)
    assert named["HTTPS_PROXY"] == "http://mine:1" and "HTTP_PROXY" not in named
    assert named["NO_PROXY"] == "127.0.0.1,localhost,::1"
    # A machine without a system proxy: nothing is added.
    assert not [name for name in cli.child_environment({}, {}, 8000, system_proxy=({}, [])) if "proxy" in name.lower()]

    proxies, bypass = cli.system_proxy_settings()  # whatever this machine has, in the promised shape
    assert set(proxies) <= {"http", "https"} and all("://" in address for address in proxies.values())
    assert all("*" not in host and host != "<local>" for host in bypass)
