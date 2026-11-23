"""Print the gateway's nginx.conf::

    python -m efp_app_host.gateway.render_nginx > /etc/nginx/nginx.conf

The template is ``host/nginx/nginx.conf.tmpl``; the values come from the host
settings (``Settings.from_env(require_urls=False)``) and from the gateway
secret, which is created when it does not exist yet. The output holds that
secret: the file it is written to must be readable by root only.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from ..config import Settings, SettingsError
from ..paths import HostPaths
from .pages import PageRenderer, PagesError
from .resources import lib_dir, nginx_dir
from .secret import access_log_key, load_or_create

TEMPLATE_NAME = "nginx.conf.tmpl"
SCRIPT_NAME = "efp_gateway.js"
# The Host by which the gateway reaches its own static-file server.
STATIC_HOST = "efp-static.internal"

_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
# Characters that end or change an nginx configuration token.
_UNSAFE = re.compile(r"[\s;{}\"'$\\#]")


class RenderError(RuntimeError):
    """The configuration cannot be produced."""


def _plain(name: str, value: str) -> str:
    if not value or _UNSAFE.search(value):
        raise RenderError(f"{name} cannot be written into nginx.conf: {value!r}")
    return value


def values_for(settings: Settings, *, resources: Path | None = None) -> dict[str, str]:
    paths = HostPaths.of(settings)
    directory = resources or nginx_dir()
    script = directory / SCRIPT_NAME
    if not script.is_file():
        raise RenderError(f"the njs script {script} is missing")
    libraries = lib_dir()
    if not (libraries / "MANIFEST.json").is_file():
        # Every page that links /_efp/lib/ would lose its look: say so where the image is built.
        raise RenderError(f"the shared browser libraries are missing: no MANIFEST.json in {libraries}")
    secret = load_or_create(paths.gateway_secret_file)
    return {
        "GATEWAY_PORT": str(int(settings.gateway_port)),
        "MANAGER_ADDR": f"127.0.0.1:{int(settings.admin_port)}",
        "GATEWAY_SECRET": _plain("the gateway secret", secret),
        "ACCESS_LOG_KEY": _plain("the access-log key", access_log_key(secret)),
        "ACCESS_LOG_SOCKET": _plain("the access-log socket", paths.access_log_socket.as_posix()),
        "PID_FILE": _plain("the pid file", (paths.run / "nginx.pid").as_posix()),
        "NJS_SCRIPT": _plain("the njs script", script.resolve().as_posix()),
        "LIB_DIR": _plain("the shared browser libraries", libraries.resolve().as_posix()),
        "STATIC_HOST": STATIC_HOST,
    }


def render(settings: Settings, *, resources: Path | None = None) -> str:
    """The final nginx.conf for these settings."""

    directory = resources or nginx_dir()
    template = directory / TEMPLATE_NAME
    try:
        with open(template, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise RenderError(f"the nginx template {template} cannot be read: {exc}") from exc
    values = values_for(settings, resources=directory)

    def fill(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise RenderError(f"{template} uses the unknown placeholder {name}")
        return values[name]

    rendered = _PLACEHOLDER.sub(fill, text)
    left = re.search(r"\{\{|\}\}", rendered)
    if left is not None:
        line = rendered.count("\n", 0, left.start()) + 1
        raise RenderError(f"{template} has an unresolved placeholder near line {line}")
    return rendered


def main() -> int:
    try:
        settings = Settings.from_env(require_urls=False)
        text = render(settings)
        # The manager cannot start without its pages; say so where the image
        # is built rather than when the first visitor is turned away.
        PageRenderer()
    except (SettingsError, RenderError, PagesError) as exc:
        sys.stderr.write(f"render_nginx: {exc}\n")
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
