"""The shared browser libraries in lib/ (docs/CONTRACTS.md, section 6.6; docs/UI_KIT.md).

The files are served at /_efp/lib/ for a year without being asked for again,
so what is in lib/ must be exactly what scripts/vendor_libs.py downloaded,
and every page that links a file there must name one that exists.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from conftest import EXAMPLES

from efp_app import _devgateway

REPO = Path(__file__).resolve().parents[2]
LIB = REPO / "lib"
KIT = "efp-kit@1.0.0"


def vendor_script():
    spec = importlib.util.spec_from_file_location("vendor_libs", REPO / "scripts" / "vendor_libs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lib_is_what_the_vendor_script_downloaded():
    assert vendor_script().problems() == []


def test_every_directory_in_lib_is_pinned_or_the_kit():
    pinned = {entry["directory"] for entry in json.loads((LIB / "MANIFEST.json").read_text(encoding="utf-8"))["libraries"]}
    for child in LIB.iterdir():
        if child.is_dir():
            assert child.name in pinned or child.name.startswith("efp-kit@"), child.name


def test_the_kit_names_only_files_that_are_there():
    kit = (LIB / KIT / "efp-kit.js").read_text(encoding="utf-8")
    style = (LIB / KIT / "efp-kit.css").read_text(encoding="utf-8")
    named = re.findall(r'"\.\./([^"]+)"', kit) + re.findall(r'url\("\.\./([^"]+)"\)', style)
    assert len(named) >= 8
    for relative in named:
        assert (LIB / relative).is_file(), relative
    assert 'VERSION = "%s"' % KIT.split("@")[1] in kit  # the directory and the module agree


def test_the_kit_is_ascii():
    for path in (LIB / KIT).iterdir():
        assert path.read_bytes().isascii(), path.name


def test_every_page_of_an_example_links_library_files_that_exist():
    lib_dir = _devgateway.library_directory()
    found = 0
    for page in EXAMPLES.rglob("*"):
        if page.suffix not in (".html", ".js") or "__pycache__" in page.parts:
            continue
        for path in re.findall(r"""["'](/_efp/lib/[^"'?#]+)""", page.read_text(encoding="utf-8")):
            found += 1
            assert _devgateway.library_file(lib_dir, path[len(_devgateway.LIB_PREFIX) :]) is not None, (page, path)
    assert found > 0
