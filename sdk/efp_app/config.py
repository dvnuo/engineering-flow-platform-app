"""Settings of an app process, read from the environment (docs/CONTRACTS.md, section 8).

The host sets every variable for a deployed app. On a developer's machine
only ``EFP_APP_TOKEN`` and ``EFP_PORTAL_URL`` are needed (``efp-app dev``
reads them from the environment or from a ``.env`` file).

"Deployed" means ``EFP_APP_ID`` is set; anything else is a developer's
machine. The environment is read on every call, so a test or a launcher can
change it at any time.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .errors import ConfigurationError

APP_ID = "EFP_APP_ID"
APP_SLUG = "EFP_APP_SLUG"
APP_TOKEN = "EFP_APP_TOKEN"
APP_BASE_PATH = "EFP_APP_BASE_PATH"
APP_PUBLIC_URL = "EFP_APP_PUBLIC_URL"
PORTAL_URL = "EFP_PORTAL_URL"
VISITOR_KEY = "EFP_VISITOR_KEY"
APP_DATA_DIR = "EFP_APP_DATA_DIR"
APP_STORAGE_SOCKET = "EFP_APP_STORAGE_SOCKET"
DEV_VISITOR = "EFP_DEV_VISITOR"
PORT = "PORT"


@dataclass(frozen=True)
class Settings:
    """What the environment says. The token and the visitor key never appear in ``repr``."""

    app_id: Optional[str] = None
    slug: Optional[str] = None
    token: Optional[str] = field(default=None, repr=False)
    portal_url: Optional[str] = None
    base_path: str = ""
    public_url: Optional[str] = None
    visitor_key: Optional[str] = field(default=None, repr=False)
    data_dir: Optional[str] = None
    storage_socket: Optional[str] = None
    port: Optional[int] = None
    dev_visitor: Optional[str] = None

    @property
    def deployed(self) -> bool:
        """True inside the EFP host, false on a developer's machine."""
        return bool(self.app_id)

    def require_token(self) -> str:
        if self.token:
            return self.token
        if self.deployed:
            raise ConfigurationError(
                "%s is not set. The host sets it for a deployed app: redeploy the app, "
                "or reset its token in the Portal." % APP_TOKEN
            )
        raise ConfigurationError(
            "%s is not set. Copy the app token from the app's page in the Portal and export it, "
            "or put it in a .env file next to efp-app.json and start the app with `efp-app dev`." % APP_TOKEN
        )

    def require_portal_url(self) -> str:
        if not self.portal_url:
            raise ConfigurationError(
                "%s is not set. Set it to the address of the EFP Portal, for example "
                "https://efp.example.com (in the environment, or in .env for `efp-app dev`)." % PORTAL_URL
            )
        if not self.portal_url.startswith(("http://", "https://")):
            raise ConfigurationError("%s must start with http:// or https://." % PORTAL_URL)
        return self.portal_url


def _text(env: Mapping[str, str], name: str) -> Optional[str]:
    value = (env.get(name) or "").strip()
    return value or None


def _base_path(value: Optional[str]) -> str:
    path = (value or "").strip().rstrip("/")
    if path and not path.startswith("/"):
        path = "/" + path
    return path


def _port(value: Optional[str]) -> Optional[int]:
    try:
        port = int(value) if value else None
    except ValueError:
        return None
    return port if port and 0 < port < 65536 else None


def load(env: Optional[Mapping[str, str]] = None) -> Settings:
    """Read the settings from ``env`` (default: the process environment)."""
    env = os.environ if env is None else env
    portal_url = _text(env, PORTAL_URL)
    return Settings(
        app_id=_text(env, APP_ID),
        slug=_text(env, APP_SLUG),
        token=_text(env, APP_TOKEN),
        portal_url=portal_url.rstrip("/") if portal_url else None,
        base_path=_base_path(env.get(APP_BASE_PATH)),
        public_url=_text(env, APP_PUBLIC_URL),
        visitor_key=_text(env, VISITOR_KEY),
        data_dir=_text(env, APP_DATA_DIR),
        storage_socket=_text(env, APP_STORAGE_SOCKET),
        port=_port(_text(env, PORT)),
        dev_visitor=_text(env, DEV_VISITOR),
    )


def is_deployed(env: Optional[Mapping[str, str]] = None) -> bool:
    """True inside the EFP host (``EFP_APP_ID`` is set)."""
    return load(env).deployed
