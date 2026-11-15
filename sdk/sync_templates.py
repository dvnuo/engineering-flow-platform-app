"""Refresh ``efp_app/_templates`` from the top-level ``templates/`` directory.

    python sync_templates.py            copy templates/ over the mirror
    python sync_templates.py --check    exit 1 when the mirror is out of date

``templates/`` is the single source: edit the starters there, then run this
script. ``tests/test_templates.py`` runs the same comparison.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "templates"
MIRROR = HERE / "efp_app" / "_templates"
SKIP_DIRS = {"__pycache__", ".efp-data", ".venv"}
SKIP_FILES = {".env"}


def tree(root: Path) -> Dict[str, bytes]:
    """Relative path -> content, with line endings normalised."""
    found: Dict[str, bytes] = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file():
            continue
        if SKIP_DIRS.intersection(relative.parts) or path.name in SKIP_FILES or path.suffix == ".pyc":
            continue
        found[relative.as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    return found


def differences() -> List[str]:
    source, mirror = tree(SOURCE), tree(MIRROR)
    problems = []
    for name in sorted(set(source) | set(mirror)):
        if name not in mirror:
            problems.append("missing in the mirror: " + name)
        elif name not in source:
            problems.append("only in the mirror: " + name)
        elif source[name] != mirror[name]:
            problems.append("differs: " + name)
    return problems


def sync() -> None:
    if not SOURCE.is_dir():
        raise SystemExit("templates/ not found next to sdk/: run this from a checkout of the repository")
    if MIRROR.exists():
        shutil.rmtree(MIRROR)
    for name, content in tree(SOURCE).items():
        target = MIRROR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def main(argv: List[str]) -> int:
    if "--check" in argv:
        problems = differences()
        for line in problems:
            print(line)
        if problems:
            print("efp_app/_templates is out of date: run python sync_templates.py")
            return 1
        print("efp_app/_templates matches templates/")
        return 0
    sync()
    print("copied %d files to %s" % (len(tree(MIRROR)), MIRROR))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
