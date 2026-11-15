"""Build hook: a wheel built from a checkout carries the current starter templates.

``templates/`` at the repository root is the single source of the starters.
``efp_app/_templates`` is a mirror kept inside the package so that editable
installs and source distributions have them too; ``python sync_templates.py``
refreshes the mirror and ``tests/test_templates.py`` fails when the two
differ.

When a wheel is built and the top-level directory is there, this hook copies
it over the mirror in the build output, so the wheel never ships a stale
copy. Everything else about the package is declared in ``pyproject.toml``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "templates"
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".efp-data", ".env", ".venv")


class BuildWithTemplates(build_py):
    def run(self):
        super().run()
        if getattr(self, "editable_mode", False) or not SOURCE.is_dir():
            return
        target = Path(self.build_lib) / "efp_app" / "_templates"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(SOURCE, target, ignore=IGNORE)


setup(cmdclass={"build_py": BuildWithTemplates})
