"""Where the gateway's non-Python files live.

``host/nginx`` (the nginx template and the njs script) and ``host/pages``
(the page layout and its catalogues) sit next to the ``efp_app_host``
package. An image that installs the package somewhere else points
``EFP_HOST_RESOURCE_DIR`` at the directory that holds both.
"""

from __future__ import annotations

import os
from pathlib import Path

RESOURCE_DIR_ENV = "EFP_HOST_RESOURCE_DIR"


def resource_root() -> Path:
    override = (os.environ.get(RESOURCE_DIR_ENV) or "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def nginx_dir() -> Path:
    return resource_root() / "nginx"


def pages_dir() -> Path:
    return resource_root() / "pages"
