"""Build hook: a wheel built from a checkout carries the current starter templates.

``templates/`` at the repository root is the single source of the starters.
``efp_app/_templates`` is a mirror kept inside the package so that editable
installs and source distributions have them too; ``python sync_templates.py``
refreshes the mirror and ``tests/test_templates.py`` fails when the two
differ.

When a wheel is built and the top-level directory is there, this hook copies
it over the mirror in the build output, so the wheel never ships a stale
copy. Everything else about the package is declared in ``pyproject.toml``.

The shared browser libraries (``lib/`` at the repository root, served at
``/_efp/lib/``) go into the wheel as ``efp_app/_lib`` the same way, so that
``efp-app dev`` can serve them on a developer's machine. They have no mirror
in the source tree: a checkout or an editable install reads ``lib/`` itself
(``efp_app._devgateway.library_directory``).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "templates"
LIBRARIES = HERE.parent / "lib"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".efp-data", ".env", ".venv")


class BuildWithTemplates(build_py):
    def run(self):
        super().run()
        if getattr(self, "editable_mode", False):
            return
        for source, name in ((SOURCE, "_templates"), (LIBRARIES, "_lib")):
            if not source.is_dir():
                continue
            target = Path(self.build_lib) / "efp_app" / name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, ignore=IGNORE)


setup(cmdclass={"build_py": BuildWithTemplates})
