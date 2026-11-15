from __future__ import annotations

from pathlib import Path

from efp_app_host import pyenv
from efp_app_host.manifest import validate_manifest


def test_requirement_names():
    text = """
    # comment
    FastAPI>=0.110   # trailing comment
    uvicorn[standard]==0.29.0
    my_pkg.core ; python_version >= "3.9"
    --index-url https://pip.example.com/simple
    -r extra/more.txt
    --requirement=other.txt
    requests @ https://files.example.com/requests-2.31.0-py3-none-any.whl
    https://files.example.com/vendor/Internal_Tool-1.2-py3-none-any.whl
    ./vendor/efp_app_sdk-0.1.0-py3-none-any.whl
    git+https://github.com/acme/thing.git#egg=acme-thing
    long-line \\
        ==1.0
    """
    names, includes = pyenv.requirement_names(text)
    assert names == {"fastapi", "uvicorn", "my-pkg-core", "requests", "internal-tool", "efp-app-sdk", "acme-thing", "long-line"}
    assert includes == ["extra/more.txt", "other.txt"]


def test_includes_are_followed_but_never_out_of_the_code(tmp_path):
    code = tmp_path / "code"
    (code / "reqs").mkdir(parents=True)
    (code / "requirements.txt").write_text("flask\n-r reqs/web.txt\n-r ../outside.txt\n", encoding="utf-8")
    (code / "reqs" / "web.txt").write_text("gunicorn\n-r ../requirements.txt\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("stolen\n", encoding="utf-8")
    assert pyenv.collect_requirement_names(code, "requirements.txt") == {"flask", "gunicorn"}
    assert pyenv.collect_requirement_names(code, "missing.txt") is None


def _plan(tmp_path, app_type, requirements, *, sdk_wheel=True):
    code = tmp_path / "code"
    code.mkdir(exist_ok=True)
    if requirements is not None:
        (code / "requirements.txt").write_text(requirements, encoding="utf-8")
    wheels = tmp_path / "wheels"
    wheels.mkdir(exist_ok=True)
    if sdk_wheel:
        (wheels / "efp_app_sdk-0.1.0-py3-none-any.whl").write_bytes(b"")
    entry = {"fastapi": "main:app", "flask": "main:app", "streamlit": "app.py", "gradio": "app.py"}.get(app_type)
    manifest = validate_manifest({"name": "A", "type": app_type, "entry": entry, "command": "x"})
    return pyenv.plan_install(code, manifest, wheels_dir=wheels), code, wheels


def test_the_runner_and_the_sdk_are_added_when_not_named(tmp_path):
    plan, code, wheels = _plan(tmp_path, "fastapi", "fastapi\n")
    assert plan.requirements_file == code / "requirements.txt"
    assert plan.extras == ["uvicorn", "efp-app-sdk"]
    assert plan.find_links == [wheels.as_posix()]


def test_named_packages_are_left_to_the_requirements(tmp_path):
    plan, _, _ = _plan(tmp_path, "fastapi", "fastapi\nuvicorn[standard]>=0.29\nEFP_App.SDK==0.1.0\n")
    assert plan.extras == [] and plan.find_links == []
    plan, _, _ = _plan(tmp_path, "flask", "flask\n")
    assert plan.extras == ["gunicorn", "efp-app-sdk"]
    plan, _, _ = _plan(tmp_path, "streamlit", "streamlit\n")
    assert plan.extras == ["efp-app-sdk"]


def test_no_requirements_file_and_no_bundled_sdk(tmp_path):
    plan, _, _ = _plan(tmp_path, "command", None, sdk_wheel=False)
    assert plan.requirements_file is None and plan.extras == [] and plan.empty
    assert any("not found" in note for note in plan.notes)
    plan, _, _ = _plan(tmp_path, "fastapi", None, sdk_wheel=False)
    assert plan.extras == ["uvicorn"] and not plan.empty


def test_install_is_wheels_only_unless_sdists_are_allowed(tmp_path):
    plan, code, wheels = _plan(tmp_path, "fastapi", "fastapi\n")
    command = pyenv.install_command("/v/bin/python", plan, allow_sdist=False)
    assert command[:6] == ["/v/bin/python", "-m", "pip", "--disable-pip-version-check", "--no-input", "--no-cache-dir"]
    assert command[6:] == [
        "install",
        "--only-binary",
        ":all:",
        "--find-links",
        wheels.as_posix(),
        "-r",
        (code / "requirements.txt").as_posix(),
        "uvicorn",
        "efp-app-sdk",
    ]
    assert "--only-binary" not in pyenv.install_command("/v/bin/python", plan, allow_sdist=True)


def test_hidden_mode_commands(tmp_path):
    plan, code, wheels = _plan(tmp_path, "fastapi", "fastapi\n")
    cache = Path("/build/wheels")
    command = pyenv.wheel_command("/v/bin/python", plan, cache, allow_sdist=False)
    assert command[6:11] == ["wheel", "--wheel-dir", "/build/wheels", "--only-binary", ":all:"]
    assert command[-2:] == ["uvicorn", "efp-app-sdk"]
    offline = pyenv.offline_install_command("/v/bin/python", [Path("/build/wheels/a-1-py3-none-any.whl"), Path("/build/wheels/b-2-py3-none-any.whl")])
    assert offline[6:] == ["install", "--no-index", "--no-deps", "/build/wheels/a-1-py3-none-any.whl", "/build/wheels/b-2-py3-none-any.whl"]
    assert pyenv.freeze_command("/v/bin/python")[-1] == "freeze"
    assert pyenv.venv_command(Path("/r/venv"), python="/usr/local/bin/python3.11") == ["/usr/local/bin/python3.11", "-m", "venv", "/r/venv"]


def test_pip_environment_and_files():
    passthrough = {"PIP_INDEX_URL": "https://pip.example.com/simple", "PIP_CERT": "/etc/ssl/corp.pem", "HTTPS_PROXY": "http://proxy:3128", "SSL_CERT_FILE": "relative.pem"}
    env = pyenv.pip_environment(passthrough, home="/t/home", tmp="/t", venv_bin="/v/bin")
    assert env["PIP_INDEX_URL"] == "https://pip.example.com/simple" and env["HTTPS_PROXY"] == "http://proxy:3128"
    assert env["HOME"] == "/t/home" and env["TMPDIR"] == "/t" and env["PATH"].startswith("/v/bin:")
    assert env["PIP_NO_INPUT"] == "1"
    assert pyenv.pip_readable_files(passthrough) == ["/etc/ssl/corp.pem"]


def test_cached_wheels(tmp_path):
    (tmp_path / "b-1-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "a-1-py3-none-any.whl").write_bytes(b"")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert [path.name for path in pyenv.cached_wheels(tmp_path)] == ["a-1-py3-none-any.whl", "b-1-py3-none-any.whl"]
    assert pyenv.cached_wheels(tmp_path / "missing") == []
    assert pyenv.bundled_sdk_wheel(tmp_path) is None
