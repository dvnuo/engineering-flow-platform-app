"""Fetch the shared browser libraries into ``lib/``, which the host serves at ``/_efp/lib/``.

    python scripts/vendor_libs.py            download the pinned packages and rewrite lib/
    python scripts/vendor_libs.py --check    exit 1 when lib/ is not what lib/MANIFEST.json says

Every library is pinned to one version and lands in ``lib/<name>@<version>/``.
A page links a file there by its full path, for example
``/_efp/lib/daisyui@5.8.2/daisyui.css``: a path never changes its content, so
browsers keep each file for a year, and an app keeps the version it was
written against when a later host adds a newer one. docs/CONTRACTS.md,
section 6.6, says how the gateway serves these files; docs/UI_KIT.md says
what each one is for.

Packages come from the npm registry (``NPM_REGISTRY`` names another one, such
as an internal mirror). Each tarball is checked against the integrity value
the registry publishes, and only the files listed below are taken out of it.
``lib/MANIFEST.json`` records the SHA-256 of every file written; ``--check``
compares lib/ with it without the network, and sdk/tests/test_lib.py runs
the same check.

To move a library to a new version: change its ``version`` below, run this
script, and keep the old directory if a published app may still link it.
``efp-kit@*`` directories are written by hand and are not touched here.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tarfile
import urllib.request
from pathlib import Path
from typing import Dict, List, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
MANIFEST = LIB / "MANIFEST.json"
REGISTRY = os.environ.get("NPM_REGISTRY", "https://registry.npmjs.org").rstrip("/")


class Package(NamedTuple):
    name: str  # the directory: lib/<name>@<version>/
    npm: str  # the package on the registry
    version: str
    license: str
    purpose: str
    files: Dict[str, str]  # file in lib/<name>@<version>/ -> path in the tarball (below "package/")


PACKAGES: List[Package] = [
    Package(
        "daisyui", "daisyui", "5.8.2", "MIT",
        "components and themes (CSS only)",
        {"daisyui.css": "daisyui.css", "themes.css": "themes.css", "LICENSE": "LICENSE"},
    ),
    Package(
        "tailwindcss-browser", "@tailwindcss/browser", "4.3.5", "MIT",
        "Tailwind utility classes, generated in the browser",
        {"tailwindcss.js": "dist/index.global.js", "LICENSE": "LICENSE"},
    ),
    Package(
        "motion", "motion", "14.14.0", "MIT",
        "animations: springs, stagger, scroll and in-view (global Motion)",
        {"motion.js": "dist/motion.js", "LICENSE.md": "LICENSE.md"},
    ),
    Package(
        "auto-animate", "@formkit/auto-animate", "0.10.2", "MIT",
        "lists that animate their own additions, removals and moves (ES module)",
        {"auto-animate.js": "index.min.js", "LICENSE": "LICENSE"},
    ),
    Package(
        "sortablejs", "sortablejs", "1.15.7", "MIT",
        "drag and drop to reorder (global Sortable)",
        {"Sortable.min.js": "Sortable.min.js", "LICENSE": "LICENSE"},
    ),
    Package(
        "canvas-confetti", "canvas-confetti", "1.9.4", "ISC",
        "confetti (global confetti)",
        {"confetti.js": "dist/confetti.browser.js", "LICENSE": "LICENSE"},
    ),
    Package(
        "echarts", "echarts", "6.1.0", "Apache-2.0",
        "charts (global echarts)",
        {"echarts.min.js": "dist/echarts.min.js", "LICENSE": "LICENSE", "LICENSE-d3": "licenses/LICENSE-d3"},
    ),
    Package(
        "lucide", "lucide", "1.77.0", "ISC",
        "icons: <i data-lucide=\"name\"> (global lucide)",
        {"lucide.min.js": "dist/umd/lucide.min.js", "LICENSE": "LICENSE"},
    ),
    Package(
        "inter", "@fontsource-variable/inter", "5.3.0", "OFL-1.1",
        "the Inter typeface, Latin, variable weight",
        {"inter-latin-wght-normal.woff2": "files/inter-latin-wght-normal.woff2", "LICENSE": "LICENSE"},
    ),
    Package(
        "jetbrains-mono", "@fontsource-variable/jetbrains-mono", "5.3.0", "OFL-1.1",
        "the JetBrains Mono typeface, Latin, variable weight",
        {"jetbrains-mono-latin-wght-normal.woff2": "files/jetbrains-mono-latin-wght-normal.woff2", "LICENSE": "LICENSE"},
    ),
]


def directory(package: Package) -> str:
    return "%s@%s" % (package.name, package.version)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str, attempts: int = 3) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "efp-vendor-libs"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as answer:
                return answer.read()
        except OSError as exc:  # URLError and dropped connections
            if attempt == attempts:
                raise SystemExit("could not fetch %s: %s" % (url, exc))
            print("retrying %s (%s)" % (url, exc))
    raise AssertionError("unreachable")


def check_integrity(data: bytes, integrity: str, what: str) -> None:
    """The registry's ``dist.integrity`` (``sha512-<base64>``) must match the tarball."""
    algorithm, _, expected = integrity.partition("-")
    if algorithm not in ("sha512", "sha384", "sha256"):
        raise SystemExit("%s: unknown integrity algorithm %r" % (what, algorithm))
    actual = base64.b64encode(hashlib.new(algorithm, data).digest()).decode("ascii")
    if actual != expected:
        raise SystemExit("%s: the tarball does not match the integrity value of the registry" % what)


def download(package: Package) -> Dict[str, bytes]:
    """The listed files of one package, checked."""
    what = "%s@%s" % (package.npm, package.version)
    meta = json.loads(fetch("%s/%s/%s" % (REGISTRY, package.npm.replace("/", "%2f"), package.version)))
    data = fetch(meta["dist"]["tarball"])
    check_integrity(data, meta["dist"]["integrity"], what)
    found: Dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for target, source in package.files.items():
            try:
                member = archive.getmember("package/" + source)
            except KeyError:
                raise SystemExit("%s: the tarball has no %s" % (what, source))
            handle = archive.extractfile(member) if member.isfile() else None
            if handle is None:
                raise SystemExit("%s: %s is not a regular file" % (what, source))
            found[target] = handle.read()
    return found


def vendor() -> None:
    LIB.mkdir(exist_ok=True)
    entries = []
    for package in PACKAGES:
        files = download(package)
        target = LIB / directory(package)
        if target.exists():
            shutil.rmtree(target)
        target.mkdir()
        for name, content in files.items():
            (target / name).write_bytes(content)
        entries.append(
            {
                "directory": directory(package),
                "package": package.npm,
                "version": package.version,
                "license": package.license,
                "purpose": package.purpose,
                "files": {name: sha256(content) for name, content in sorted(files.items())},
            }
        )
        print("lib/%s: %d files" % (directory(package), len(files)))
    wanted = {directory(package) for package in PACKAGES}
    for child in sorted(LIB.iterdir()):
        if child.is_dir() and child.name not in wanted and not child.name.startswith("efp-kit@"):
            print("lib/%s is not pinned any more: delete it once no published app links it" % child.name)
    MANIFEST.write_text(json.dumps({"libraries": entries}, indent=2) + "\n", encoding="utf-8", newline="\n")


def problems() -> List[str]:
    """What differs between lib/ and lib/MANIFEST.json (and the pins above)."""
    if not MANIFEST.is_file():
        return ["lib/MANIFEST.json is missing: run python scripts/vendor_libs.py"]
    found = []
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    listed = {entry["directory"]: entry for entry in manifest["libraries"]}
    for package in PACKAGES:
        entry = listed.get(directory(package))
        if entry is None:
            found.append("lib/%s is pinned but not in lib/MANIFEST.json: run the script" % directory(package))
            continue
        if sorted(entry["files"]) != sorted(package.files):
            found.append("lib/%s: the manifest lists other files than the pins" % directory(package))
    for name, entry in sorted(listed.items()):
        folder = LIB / name
        for file_name, digest in sorted(entry["files"].items()):
            path = folder / file_name
            if not path.is_file():
                found.append("lib/%s/%s is missing" % (name, file_name))
            elif sha256(path.read_bytes()) != digest:
                found.append("lib/%s/%s differs from the downloaded file" % (name, file_name))
        if folder.is_dir():
            extra = sorted(p.name for p in folder.iterdir() if p.name not in entry["files"])
            found += ["lib/%s/%s is not in the manifest" % (name, extra_name) for extra_name in extra]
    return found


def main(argv: List[str]) -> int:
    if "--check" in argv:
        found = problems()
        for line in found:
            print(line)
        print("lib/ is out of date" if found else "lib/ matches lib/MANIFEST.json")
        return 1 if found else 0
    vendor()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
