from __future__ import annotations

import json

import pytest

from efp_app_host.manifest import ManifestError, load_manifest, strips_prefix, validate_manifest


def test_defaults_are_filled_in():
    manifest = validate_manifest({"name": " Mind map ", "type": "fastapi", "entry": "main:app", "future_key": 1})
    assert manifest == {
        "name": "Mind map",
        "description": "",
        "icon": None,
        "type": "fastapi",
        "entry": "main:app",
        "command": None,
        "requirements": "requirements.txt",
        "health_path": "/",
        "startup_timeout": 60,
        "strip_prefix": None,
        "owners": [],
    }


def test_the_full_example_of_the_contract():
    manifest = validate_manifest(
        {
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
    )
    assert manifest["owners"] == ["alice"] and manifest["icon"] == "icon.png"


@pytest.mark.parametrize(
    "data, fragment",
    [
        ([], "JSON object"),
        ({"type": "fastapi", "entry": "main:app"}, "name"),
        ({"name": "x" * 81, "type": "fastapi", "entry": "main:app"}, "name"),
        ({"name": "A", "type": "django"}, "type"),
        ({"name": "A", "type": "fastapi", "entry": "main.py"}, "module:attribute"),
        ({"name": "A", "type": "flask", "entry": "app:create()"}, "module:attribute"),
        ({"name": "A", "type": "streamlit"}, "script"),
        ({"name": "A", "type": "streamlit", "entry": "../app.py"}, "script"),
        ({"name": "A", "type": "gradio", "entry": "/srv/app.py"}, "script"),
        ({"name": "A", "type": "static", "entry": "../site"}, "relative directory"),
        ({"name": "A", "type": "command"}, "command is required"),
        ({"name": "A", "type": "command", "command": "a\nb"}, "single line"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "description": "d" * 501}, "description"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "icon": "../icon.png"}, "icon"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "requirements": "/etc/passwd"}, "requirements"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "requirements": "a\\b.txt"}, "requirements"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "health_path": "health"}, "health_path"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "health_path": "/a b"}, "health_path"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "startup_timeout": 301}, "startup_timeout"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "startup_timeout": True}, "startup_timeout"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "strip_prefix": "yes"}, "strip_prefix"),
        ({"name": "A", "type": "fastapi", "entry": "main:app", "owners": "alice"}, "owners"),
    ],
)
def test_rules(data, fragment):
    with pytest.raises(ManifestError) as caught:
        validate_manifest(data)
    assert fragment in str(caught.value)


def test_every_problem_is_reported_at_once():
    with pytest.raises(ManifestError) as caught:
        validate_manifest({"type": "fastapi", "entry": "nope", "startup_timeout": 0})
    assert len(caught.value.problems) == 3


def test_types_and_their_entries():
    assert validate_manifest({"name": "A", "type": "static"})["entry"] == "."
    assert validate_manifest({"name": "A", "type": "static", "entry": "./public/"})["entry"] == "public"
    assert validate_manifest({"name": "A", "type": "streamlit", "entry": "./pages/app.py"})["entry"] == "pages/app.py"
    command = validate_manifest({"name": "A", "type": "command", "command": "python server.py --port $PORT", "entry": "ignored"})
    assert command["entry"] is None and command["command"] == "python server.py --port $PORT"
    assert validate_manifest({"name": "A", "type": "flask", "entry": "pkg.web:app", "startup_timeout": 300})["startup_timeout"] == 300


def test_strip_prefix_follows_the_type_unless_overridden():
    def manifest(app_type, **extra):
        base = {"name": "A", "type": app_type, "entry": "main:app" if app_type in ("fastapi", "flask") else "app.py", "command": "x"}
        return validate_manifest({**base, **extra})

    assert strips_prefix(manifest("fastapi")) is True
    assert strips_prefix(manifest("gradio")) is True
    assert strips_prefix(manifest("static", entry=".")) is True
    assert strips_prefix(manifest("flask")) is False
    assert strips_prefix(manifest("streamlit")) is False
    assert strips_prefix(manifest("command")) is False
    assert strips_prefix(manifest("flask", strip_prefix=True)) is True
    assert strips_prefix(manifest("fastapi", strip_prefix=False)) is False


def test_load_manifest_checks_the_files(tmp_path):
    with pytest.raises(ManifestError, match="missing"):
        load_manifest(tmp_path)

    (tmp_path / "efp-app.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(tmp_path)

    (tmp_path / "efp-app.json").write_text(json.dumps({"name": "A", "type": "fastapi", "entry": "main:app"}), encoding="utf-8")
    with pytest.raises(ManifestError, match="main.py does not exist"):
        load_manifest(tmp_path)
    (tmp_path / "main.py").write_text("app = None\n", encoding="utf-8")
    assert load_manifest(tmp_path)["entry"] == "main:app"

    (tmp_path / "efp-app.json").write_text(json.dumps({"name": "A", "type": "static", "entry": "public"}), encoding="utf-8")
    with pytest.raises(ManifestError, match="not a directory"):
        load_manifest(tmp_path)
    (tmp_path / "public").mkdir()
    assert load_manifest(tmp_path)["type"] == "static"

    (tmp_path / "efp-app.json").write_text(json.dumps({"name": "A", "type": "streamlit", "entry": "app.py", "icon": "icon.png"}), encoding="utf-8")
    with pytest.raises(ManifestError) as caught:
        load_manifest(tmp_path)
    assert len(caught.value.problems) == 2


def test_a_package_module_counts_as_an_entry(tmp_path):
    (tmp_path / "efp-app.json").write_text(json.dumps({"name": "A", "type": "flask", "entry": "web.site:app"}), encoding="utf-8")
    (tmp_path / "web" / "site").mkdir(parents=True)
    (tmp_path / "web" / "site" / "__init__.py").write_text("app = None\n", encoding="utf-8")
    assert load_manifest(tmp_path)["type"] == "flask"


def test_what_the_manifest_names_may_not_lead_out_of_the_entry_directory(tmp_path, links):
    # Runs everywhere: real links on Linux, the model of conftest.Links elsewhere.
    from efp_app_host.manifest import check_files, stays_inside

    code = tmp_path / "store" / "apps" / "app-1" / "releases" / "abc" / "code"
    secrets = tmp_path / "store" / "keys"
    code.mkdir(parents=True)
    secrets.mkdir()
    (secrets / "session.key").write_text("secret", encoding="utf-8")
    # A path of the manifest is plain text without "..", but what it names can be a link.
    links.make(code / "site", "../../../../../keys")
    links.make(code / "icon.png", "../../../../../keys/session.key")
    links.make(code / "requirements.txt", "../../../../../keys/session.key")
    assert not stays_inside(code, "site") and not stays_inside(code, "icon.png")

    manifest = validate_manifest({"name": "A", "type": "static", "entry": "site", "icon": "icon.png"})
    with pytest.raises(ManifestError) as caught:
        check_files(manifest, code)
    text = "; ".join(caught.value.problems)
    # The gateway would serve "entry" without looking at links in that part of the path.
    assert "entry 'site' leads out of the entry directory" in text
    assert "icon 'icon.png' leads out of the entry directory" in text
    assert "requirements 'requirements.txt' leads out of the entry directory" in text

    script = validate_manifest({"name": "A", "type": "streamlit", "entry": "icon.png", "requirements": "none.txt"})
    with pytest.raises(ManifestError, match="entry 'icon.png' leads out"):
        check_files(script, code)

    # A directory of the app's own, and a link that stays inside, are what they always were.
    (code / "public").mkdir()
    assert stays_inside(code, "public") and stays_inside(code, "public/missing.txt") and stays_inside(code, ".")
    if links.real:
        links.make(code / "current", "public")
        check_files(validate_manifest({"name": "A", "type": "static", "entry": "current", "requirements": "none.txt"}), code)


@pytest.mark.parametrize(
    "change",
    [{"entry": "main:app\n"}, {"health_path": "/health\n"}, {"entry": "main:app", "health_path": "/a\nb"}],
)
def test_a_value_may_not_end_in_a_line_break(change):
    with pytest.raises(ManifestError):
        validate_manifest({"name": "A", "type": "fastapi", "entry": "main:app", **change})
