from __future__ import annotations

import pytest
from conftest import make_settings

from efp_app_host.launch import LaunchError, app_environment, build_command, health_url, owner_environment, run_dirs, static_root
from efp_app_host.manifest import validate_manifest
from efp_app_host.models import Release
from efp_app_host.paths import HostPaths
from efp_app_host.registry import Registry

PY = "/venv/bin/python"


def _manifest(app_type: str, **extra):
    base = {"name": "A", "type": app_type}
    if app_type in ("fastapi", "flask"):
        base["entry"] = "main:app"
    elif app_type in ("streamlit", "gradio"):
        base["entry"] = "app.py"
    elif app_type == "command":
        base["command"] = "python server.py --port $PORT --prefix $EFP_APP_BASE_PATH"
    return validate_manifest({**base, **extra})


def test_fastapi_command():
    command = build_command(_manifest("fastapi"), python=PY, port=20003, base_path="/mindmap")
    assert command.argv == [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "20003", "--root-path", "/mindmap", "--no-server-header"]
    assert command.env == {}


def test_flask_command():
    command = build_command(_manifest("flask"), python=PY, port=20003, base_path="/mindmap")
    assert command.argv == [PY, "-m", "gunicorn", "main:app", "--bind", "127.0.0.1:20003", "--workers", "1", "--threads", "8", "--timeout", "120"]
    assert command.env == {"SCRIPT_NAME": "/mindmap"}


def test_streamlit_command():
    command = build_command(_manifest("streamlit"), python=PY, port=20003, base_path="/mindmap")
    assert command.argv == [
        PY, "-m", "streamlit", "run", "app.py",
        "--server.port", "20003",
        "--server.address", "127.0.0.1",
        "--server.baseUrlPath", "/mindmap",
        "--server.headless", "true",
        "--browser.gatherUsageStats", "false",
        "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false",
    ]  # fmt: skip
    assert command.env == {}


def test_gradio_command():
    command = build_command(_manifest("gradio"), python=PY, port=20003, base_path="/mindmap")
    assert command.argv == [PY, "app.py"]
    assert command.env == {"GRADIO_SERVER_NAME": "127.0.0.1", "GRADIO_SERVER_PORT": "20003", "GRADIO_ROOT_PATH": "/mindmap"}


def test_command_type_runs_through_the_shell():
    command = build_command(_manifest("command"), python=PY, port=20003, base_path="/mindmap")
    assert command.argv == ["/bin/sh", "-c", "python server.py --port $PORT --prefix $EFP_APP_BASE_PATH"]


def test_an_empty_base_path_drops_the_options_that_carry_it():
    assert "--root-path" not in build_command(_manifest("fastapi"), python=PY, port=1, base_path="").argv
    assert "--server.baseUrlPath" not in build_command(_manifest("streamlit"), python=PY, port=1, base_path="").argv
    assert build_command(_manifest("flask"), python=PY, port=1, base_path="").env == {}
    assert "GRADIO_ROOT_PATH" not in build_command(_manifest("gradio"), python=PY, port=1, base_path="").env


def test_static_has_no_process():
    with pytest.raises(LaunchError):
        build_command(_manifest("static"), python=PY, port=1, base_path="/x")


def test_owner_environment_drops_reserved_names():
    stored = {
        "EFP_APP_TOKEN": "efpa_x",
        "EFP_FAKE": "1",
        "PORT": "1",
        "HOME": "/root",
        "TMPDIR": "/x",
        "PATH": "/evil",
        "bad name": "x",
        "1BAD": "x",
        "MY_SETTING": "value",
        "lower_ok": "v",
    }
    assert owner_environment(stored) == {"MY_SETTING": "value", "lower_ok": "v"}


def _registered(tmp_path, mode):
    settings = make_settings(
        tmp_path,
        isolation_mode=mode,
        passthrough_env={"PIP_INDEX_URL": "https://user:secret@pip.example.com/simple", "HTTPS_PROXY": "http://proxy:3128"},
    )
    registry = Registry(settings)
    record, _ = registry.upsert("app-1", slug="mindmap", visitor_key="vkey")
    release = Release(sha="s1", deployed_at="2026-11-15T00:00:00Z", type="flask", manifest=_manifest("flask", health_path="/healthz"))
    return settings, HostPaths.of(settings), record, release


def test_environment_in_sandbox_mode(tmp_path):
    settings, paths, record, release = _registered(tmp_path, "sandbox")
    stored = {"EFP_APP_TOKEN": "efpa_token", "MY_SETTING": "x", "PATH": "/evil", "EFP_APP_ID": "spoofed", "HTTPS_PROXY": "http://mine:1"}
    env = app_environment(settings, paths, record, release, stored, extra={"SCRIPT_NAME": "/mindmap"})
    dirs = run_dirs(settings, paths, "app-1", "s1")
    assert env["EFP_APP_ID"] == "app-1"
    assert env["EFP_APP_SLUG"] == "mindmap"
    assert env["EFP_APP_TOKEN"] == "efpa_token"
    assert env["EFP_APP_BASE_PATH"] == "/mindmap"
    assert env["EFP_APP_PUBLIC_URL"] == "https://app.example.com/mindmap/"
    assert env["EFP_PORTAL_URL"] == "http://portal.internal"
    assert env["EFP_VISITOR_KEY"] == "vkey"
    assert env["EFP_APP_DATA_DIR"] == paths.data_dir("app-1").as_posix()
    assert "EFP_APP_STORAGE_SOCKET" not in env
    assert env["PORT"] == str(record.port)
    assert env["HOME"] == dirs.home.as_posix() and env["TMPDIR"] == dirs.tmp.as_posix()
    assert env["HOME"].startswith(env["TMPDIR"])
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PATH"].startswith(dirs.venv.as_posix() + "/bin:")
    assert env["MY_SETTING"] == "x"
    assert env["SCRIPT_NAME"] == "/mindmap"
    # The owner may override the host's proxy, but never sees the pip settings.
    assert env["HTTPS_PROXY"] == "http://mine:1"
    assert not any(key.startswith("PIP_") for key in env)
    assert "secret" not in " ".join(env.values())
    assert dirs.code == paths.code_dir("app-1", "s1") and dirs.data == paths.data_dir("app-1")


def test_environment_in_hidden_mode(tmp_path):
    settings, paths, record, release = _registered(tmp_path, "hidden")
    env = app_environment(settings, paths, record, release, {"EFP_APP_TOKEN": "efpa_token"}, port=31000)
    dirs = run_dirs(settings, paths, "app-1", "s1")
    assert env["EFP_APP_STORAGE_SOCKET"] == paths.storage_socket.as_posix()
    assert "EFP_APP_DATA_DIR" not in env
    assert env["PORT"] == "31000"
    assert env["HTTPS_PROXY"] == "http://proxy:3128"
    # Nothing an app runs from is on the volume.
    assert dirs.data is None
    assert settings.store_dir not in dirs.code.parents and settings.runtime_dir in dirs.code.parents
    assert settings.runtime_dir in dirs.venv.parents


def test_health_url_keeps_the_prefix_when_the_type_does(tmp_path):
    settings, paths, record, release = _registered(tmp_path, "sandbox")
    assert health_url(record, release) == f"http://127.0.0.1:{record.port}/mindmap/healthz"
    assert health_url(record, release, port=31000) == "http://127.0.0.1:31000/mindmap/healthz"
    stripped = Release(sha="s2", deployed_at="", type="fastapi", manifest=_manifest("fastapi"))
    assert health_url(record, stripped) == f"http://127.0.0.1:{record.port}/"
    overridden = Release(sha="s3", deployed_at="", type="flask", manifest=_manifest("flask", strip_prefix=True))
    assert health_url(record, overridden) == f"http://127.0.0.1:{record.port}/"


def test_static_root_by_mode(tmp_path):
    release = Release(sha="s1", deployed_at="", type="static", manifest=_manifest("static", entry="public"))
    sandbox = make_settings(tmp_path, isolation_mode="sandbox")
    hidden = make_settings(tmp_path, isolation_mode="hidden")
    assert static_root(sandbox, HostPaths.of(sandbox), "app-1", release) == HostPaths.of(sandbox).code_dir("app-1", "s1") / "public"
    root = static_root(hidden, HostPaths.of(hidden), "app-1", release)
    assert hidden.runtime_dir in root.parents and hidden.store_dir not in root.parents
    whole = Release(sha="s1", deployed_at="", type="static", manifest=_manifest("static"))
    assert static_root(sandbox, HostPaths.of(sandbox), "app-1", whole) == HostPaths.of(sandbox).code_dir("app-1", "s1")
