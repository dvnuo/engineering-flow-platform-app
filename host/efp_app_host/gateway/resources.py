"""Where the gateway's non-Python files live.

``host/nginx`` (the nginx template and the njs script) and ``host/pages``
(the page layout and its catalogues) sit next to the ``efp_app_host``
package. An image that installs the package somewhere else points
``EFP_HOST_RESOURCE_DIR`` at the directory that holds both, and ``lib``
(the shared browser libraries) as well.
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


def lib_dir() -> Path:
    """The shared browser libraries (docs/CONTRACTS.md, section 6.6).

    ``lib`` in the resource directory of the image; in a checkout, the
    repository's top-level ``lib/``, next to ``host/``.
    """
    root = resource_root()
    if (root / "lib").is_dir() or (os.environ.get(RESOURCE_DIR_ENV) or "").strip():
        return root / "lib"
    return root.parent / "lib"
