from __future__ import annotations

import io
import os
import sys
import tarfile

import pytest
from runtime_helpers import make_bundle

from efp_app_host.bundle import BundleError, choose_base, unpack_bundle

MANIFEST = '{"name": "A", "type": "static"}'


def _add(archive: tarfile.TarFile, name: str, *, kind: bytes = tarfile.REGTYPE, data: bytes = b"", linkname: str = "", mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    info.linkname = linkname
    info.mode = mode
    archive.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


def _tree(root) -> dict[str, str]:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            found[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return found


def test_the_entry_directory_itself(tmp_path):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST, "public/index.html": "hi"})
    result = unpack_bundle(bundle, tmp_path / "code", entry_dir="apps/docs")
    assert _tree(tmp_path / "code") == {"efp-app.json": MANIFEST, "public/index.html": "hi"}
    assert (result.entry_dir, result.skipped_prefix, result.files) == ("", "", 2)


def test_a_github_tarball_loses_its_top_level_directory(tmp_path):
    files = {
        "README.md": "repo",
        "apps/docs/efp-app.json": MANIFEST,
        "apps/docs/index.html": "docs",
        "apps/other/efp-app.json": "{}",
    }
    bundle = make_bundle(tmp_path / "b.tar.gz", files, prefix="acme-tools-0123abc/")
    result = unpack_bundle(bundle, tmp_path / "code", entry_dir="apps/docs")
    assert _tree(tmp_path / "code") == {"efp-app.json": MANIFEST, "index.html": "docs"}
    assert (result.entry_dir, result.skipped_prefix) == ("apps/docs", "acme-tools-0123abc")


def test_a_github_tarball_with_the_app_at_its_root(tmp_path):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST, "index.html": "x"}, prefix="repo-sha/")
    result = unpack_bundle(bundle, tmp_path / "code")
    assert sorted(_tree(tmp_path / "code")) == ["efp-app.json", "index.html"]
    assert result.entry_dir == ""


def test_the_named_entry_directory_wins_over_a_manifest_at_the_root(tmp_path):
    files = {"efp-app.json": '{"name": "root"}', "apps/docs/efp-app.json": MANIFEST}
    bundle = make_bundle(tmp_path / "b.tar.gz", files, prefix="repo-sha/")
    result = unpack_bundle(bundle, tmp_path / "code", entry_dir="./apps/docs/")
    assert _tree(tmp_path / "code") == {"efp-app.json": MANIFEST}
    assert result.entry_dir == "apps/docs"


def test_a_missing_manifest_is_reported_with_the_place(tmp_path):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"index.html": "x"}, prefix="repo-sha/")
    with pytest.raises(BundleError, match="efp-app.json was not found in 'apps/docs'"):
        unpack_bundle(bundle, tmp_path / "code", entry_dir="apps/docs")
    with pytest.raises(BundleError, match="the root of the bundle"):
        unpack_bundle(bundle, tmp_path / "code2")


@pytest.mark.parametrize("name", ["/etc/passwd", "../outside.txt", "a/../../outside.txt", "C:/windows/x", "a\\b.txt"])
def test_unsafe_names_reject_the_whole_bundle(tmp_path, name):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST}, extra=lambda archive: _add(archive, name, data=b"x"))
    with pytest.raises(BundleError):
        unpack_bundle(bundle, tmp_path / "code")
    assert not (tmp_path / "outside.txt").exists()


def test_unsafe_names_outside_the_entry_directory_also_reject(tmp_path):
    files = {"apps/docs/efp-app.json": MANIFEST}
    bundle = make_bundle(tmp_path / "b.tar.gz", files, prefix="repo/", extra=lambda archive: _add(archive, "repo/../../evil", data=b"x"))
    with pytest.raises(BundleError, match=r"'\.\.'"):
        unpack_bundle(bundle, tmp_path / "code", entry_dir="apps/docs")


@pytest.mark.parametrize("kind", [tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_devices_and_fifos_are_rejected(tmp_path, kind):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST}, extra=lambda archive: _add(archive, "dev/null", kind=kind))
    with pytest.raises(BundleError, match="special file"):
        unpack_bundle(bundle, tmp_path / "code")


@pytest.mark.parametrize("target", ["/etc/passwd", "../../outside", "../outside", "a/../../outside"])
def test_links_pointing_outside_are_rejected(tmp_path, target):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST}, extra=lambda archive: _add(archive, "link", kind=tarfile.SYMTYPE, linkname=target))
    with pytest.raises(BundleError, match="outside"):
        unpack_bundle(bundle, tmp_path / "code")


def test_a_hard_link_to_something_outside_the_entry_directory_is_rejected(tmp_path):
    files = {"secret.txt": "s", "apps/docs/efp-app.json": MANIFEST}
    bundle = make_bundle(
        tmp_path / "b.tar.gz",
        files,
        prefix="repo/",
        extra=lambda archive: _add(archive, "repo/apps/docs/copy.txt", kind=tarfile.LNKTYPE, linkname="repo/secret.txt"),
    )
    with pytest.raises(BundleError, match="hard link"):
        unpack_bundle(bundle, tmp_path / "code", entry_dir="apps/docs")


def test_a_hard_link_inside_becomes_a_copy(tmp_path):
    bundle = make_bundle(
        tmp_path / "b.tar.gz",
        {"efp-app.json": MANIFEST, "a.txt": "same"},
        extra=lambda archive: _add(archive, "b.txt", kind=tarfile.LNKTYPE, linkname="a.txt"),
    )
    unpack_bundle(bundle, tmp_path / "code")
    assert (tmp_path / "code" / "b.txt").read_text(encoding="utf-8") == "same"


def test_size_and_count_limits(tmp_path):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST, "big.bin": b"x" * 5000})
    with pytest.raises(BundleError, match="unpacks to more than"):
        unpack_bundle(bundle, tmp_path / "code", max_bytes=1000)
    with pytest.raises(BundleError, match="more than 1 entries"):
        unpack_bundle(bundle, tmp_path / "code2", max_members=1)


def test_not_an_archive(tmp_path):
    (tmp_path / "b.tar.gz").write_bytes(b"this is not a tarball")
    with pytest.raises(BundleError, match="not a readable tar archive"):
        unpack_bundle(tmp_path / "b.tar.gz", tmp_path / "code")


def test_entry_dir_must_be_relative(tmp_path):
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST})
    with pytest.raises(BundleError, match="entry_dir"):
        unpack_bundle(bundle, tmp_path / "code", entry_dir="../x")


def test_modes_keep_only_the_executable_bit(tmp_path):
    bundle = make_bundle(
        tmp_path / "b.tar.gz",
        {"efp-app.json": MANIFEST},
        extra=lambda archive: _add(archive, "run.sh", data=b"#!/bin/sh\n", mode=0o4755),
    )
    unpack_bundle(bundle, tmp_path / "code")
    assert (tmp_path / "code" / "run.sh").is_file()
    if sys.platform != "win32":
        assert (tmp_path / "code" / "run.sh").stat().st_mode & 0o7777 == 0o755
        assert (tmp_path / "code" / "efp-app.json").stat().st_mode & 0o7777 == 0o644


def test_a_file_and_a_directory_of_the_same_name(tmp_path):
    bundle = make_bundle(
        tmp_path / "b.tar.gz",
        {"efp-app.json": MANIFEST, "thing": "file"},
        extra=lambda archive: _add(archive, "thing/inside.txt", data=b"x"),
    )
    with pytest.raises(BundleError, match="both as a file and as a directory"):
        unpack_bundle(bundle, tmp_path / "code")


def test_choose_base_needs_one_top_level_directory():
    names = {"a/efp-app.json", "b/readme"}
    with pytest.raises(BundleError):
        choose_base(names, {"a", "b"}, "")
    assert choose_base({"repo/efp-app.json", "repo/x"}, {"repo"}, "") == ("repo/", "repo", "")
    assert choose_base({"efp-app.json", "sub/efp-app.json"}, {"sub"}, "sub") == ("sub/", "", "sub")


@pytest.mark.skipif(sys.platform != "linux", reason="symbolic links need Linux here")
def test_links_that_stay_inside_are_kept(tmp_path):
    def links(archive: tarfile.TarFile) -> None:
        _add(archive, "current", kind=tarfile.SYMTYPE, linkname="assets/v2")
        _add(archive, "assets/latest.css", kind=tarfile.SYMTYPE, linkname="v2/site.css")

    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST, "assets/v2/site.css": "body{}"}, extra=links)
    unpack_bundle(bundle, tmp_path / "code")
    assert os.readlink(tmp_path / "code" / "current") == "assets/v2"
    assert (tmp_path / "code" / "assets" / "latest.css").read_text(encoding="utf-8") == "body{}"


@pytest.mark.skipif(sys.platform != "linux", reason="symbolic links need Linux here")
def test_a_chain_of_links_cannot_climb_out(tmp_path):
    # "here" points at the entry directory, so "here/.." is its parent although
    # the path looks harmless when it is only read as text.
    def links(archive: tarfile.TarFile) -> None:
        _add(archive, "here", kind=tarfile.SYMTYPE, linkname=".")
        _add(archive, "up", kind=tarfile.SYMTYPE, linkname="here/..")

    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST}, extra=links)
    with pytest.raises(BundleError, match="outside"):
        unpack_bundle(bundle, tmp_path / "work" / "code")
    assert not (tmp_path / "work" / "code" / "up").exists()


@pytest.mark.skipif(sys.platform != "linux", reason="symbolic links need Linux here")
def test_nothing_is_written_through_a_link(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()

    def links(archive: tarfile.TarFile) -> None:
        _add(archive, "data", kind=tarfile.SYMTYPE, linkname="sub")
        _add(archive, "data/file.txt", data=b"x")

    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST, "sub/keep.txt": "k"}, extra=links)
    with pytest.raises(BundleError, match="both as a link and as something else"):
        unpack_bundle(bundle, tmp_path / "code")
    assert list(outside.iterdir()) == []


def _link_through_a_later_link(archive: tarfile.TarFile) -> None:
    # The order of a sorted archive (git archive, a GitHub tarball): "site" comes
    # before "z/...". Read as text, "z/a/up/../../.." is the entry directory itself.
    # With "z/a/up" in place (a link to the entry directory) it is three levels above.
    _add(archive, "site", kind=tarfile.SYMTYPE, linkname="z/a/up/../../..")
    _add(archive, "z", kind=tarfile.DIRTYPE, mode=0o755)
    _add(archive, "z/a", kind=tarfile.DIRTYPE, mode=0o755)
    _add(archive, "z/a/up", kind=tarfile.SYMTYPE, linkname="../..")


def test_a_link_through_a_link_that_comes_later_cannot_climb_out(tmp_path, links):
    # Runs everywhere: with real links on Linux, with the model of conftest.Links elsewhere.
    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": '{"name": "A", "type": "static", "entry": "site"}'}, extra=_link_through_a_later_link)
    code = tmp_path / "store" / "apps" / "app-1" / "releases" / "abc" / "code"
    with pytest.raises(BundleError, match="outside"):
        unpack_bundle(bundle, code)
    # Neither link is left behind: nothing in the directory leads out of it,
    # whatever the caller does with what was unpacked so far.
    assert not links.is_link(code / "site") and not links.is_link(code / "z" / "a" / "up")
    assert (code / "efp-app.json").is_file() and not (code / "site").exists()


def test_the_same_two_links_in_the_other_order_are_refused_too(tmp_path, links):
    def members(archive: tarfile.TarFile) -> None:
        _add(archive, "a/b/up", kind=tarfile.SYMTYPE, linkname="../..")
        _add(archive, "site", kind=tarfile.SYMTYPE, linkname="a/b/up/../../..")

    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST}, extra=members)
    with pytest.raises(BundleError, match="outside"):
        unpack_bundle(bundle, tmp_path / "work" / "code")
    assert not links.is_link(tmp_path / "work" / "code" / "site") and not links.is_link(tmp_path / "work" / "code" / "a" / "b" / "up")


def test_links_inside_through_other_links_are_still_fine(tmp_path, links):
    def members(archive: tarfile.TarFile) -> None:
        _add(archive, "current", kind=tarfile.SYMTYPE, linkname="releases/latest/site")
        _add(archive, "releases/latest", kind=tarfile.SYMTYPE, linkname="v2")

    bundle = make_bundle(tmp_path / "b.tar.gz", {"efp-app.json": MANIFEST, "releases/v2/site/index.html": "two"}, extra=members)
    unpack_bundle(bundle, tmp_path / "code")
    assert links.is_link(tmp_path / "code" / "current") and links.is_link(tmp_path / "code" / "releases" / "latest")


def test_only_gzip_is_read(tmp_path):
    # tarfile would open these too; their decoders use as much memory as the archive asks for.
    for mode, name in (("w:xz", "b.tar.xz"), ("w:bz2", "b.tar.bz2"), ("w", "b.tar")):
        with tarfile.open(tmp_path / name, mode) as archive:
            _add(archive, "efp-app.json", data=MANIFEST.encode("utf-8"))
        with pytest.raises(BundleError, match="not a readable tar archive"):
            unpack_bundle(tmp_path / name, tmp_path / ("code-" + name))
