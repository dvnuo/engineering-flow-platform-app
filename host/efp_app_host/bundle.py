"""Safe extraction of a deployment bundle (docs/CONTRACTS.md, section 4.2).

A bundle is a tar archive (normally ``.tar.gz``) that holds either the entry
directory itself or a GitHub tarball: one top-level directory, with the entry
directory somewhere below it. Nothing in the archive is trusted:

- a member with an absolute name, a ``..`` segment or a backslash rejects the
  whole bundle, wherever in the archive it sits;
- devices, FIFOs and other special members reject the bundle;
- a symbolic or hard link must point inside the entry directory;
- symbolic links are created last, so that no file is ever written through
  a link, and when all of them exist every one is resolved on the real
  filesystem: a link whose target walks through another link ("a/up/../..")
  can only be judged once that other link is there.

Only gzip is read: the other formats ``tarfile`` knows bring decoders whose
memory the archive itself chooses.
"""

from __future__ import annotations

import os
import re
import stat
import tarfile
from dataclasses import dataclass
from pathlib import Path

from .manifest import MANIFEST_NAME, is_safe_relative_path, normalize_relative_path

MAX_UNPACKED_BYTES = 1024 * 1024 * 1024
MAX_MEMBERS = 50_000
MAX_PATH_LENGTH = 1024
_COPY_CHUNK = 1024 * 1024
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class BundleError(ValueError):
    """The bundle cannot be unpacked safely."""


@dataclass
class Unpacked:
    entry_dir: str  # the directory inside the repository that became the code directory ("" = its root)
    files: int
    bytes: int
    skipped_prefix: str  # the GitHub top-level directory that was dropped ("" when there was none)


def _clean_name(raw: str) -> str:
    """The member's path as a list of plain segments joined by ``/``; ``""`` is the archive root."""

    if "\x00" in raw or "\\" in raw:
        raise BundleError(f"unsafe path in the bundle: {raw!r}")
    if raw.startswith("/") or _WINDOWS_DRIVE_RE.match(raw):
        raise BundleError(f"absolute path in the bundle: {raw!r}")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise BundleError(f"path with '..' in the bundle: {raw!r}")
    name = "/".join(parts)
    if len(name) > MAX_PATH_LENGTH or any(len(part.encode("utf-8", "surrogateescape")) > 255 for part in parts):
        raise BundleError(f"path too long in the bundle: {raw[:80]!r}...")
    return name


def _resolve_inside(link_dir: str, target: str) -> str | None:
    """Where a link lands, as a path relative to the entry directory; ``None`` when outside."""

    if not target or "\x00" in target or "\\" in target or target.startswith("/") or _WINDOWS_DRIVE_RE.match(target):
        return None
    stack = [part for part in link_dir.split("/") if part]
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not stack:
                return None
            stack.pop()
        else:
            stack.append(part)
    return "/".join(stack)


def choose_base(names: set[str], directories: set[str], entry_dir: str) -> tuple[str, str, str]:
    """Pick the entry directory inside the archive.

    Returns ``(base, skipped_prefix, entry_dir)`` where ``base`` is the archive
    prefix (``""`` or ending in ``/``) whose content becomes the code directory.
    """

    entry_dir = normalize_relative_path(entry_dir or "")
    top_level = {name.split("/", 1)[0] for name in names if name}
    root = ""
    if MANIFEST_NAME not in names and len(top_level) == 1:
        only = next(iter(top_level))
        if only in directories or any(name.startswith(only + "/") for name in names):
            root = only + "/"
    if entry_dir and f"{root}{entry_dir}/{MANIFEST_NAME}" in names:
        return f"{root}{entry_dir}/", root.rstrip("/"), entry_dir
    if f"{root}{MANIFEST_NAME}" in names:
        return root, root.rstrip("/"), ""
    where = f"'{entry_dir}'" if entry_dir else "the root of the bundle"
    raise BundleError(f"{MANIFEST_NAME} was not found in {where}")


def unpack_bundle(
    bundle: Path,
    dest: Path,
    *,
    entry_dir: str = "",
    max_bytes: int = MAX_UNPACKED_BYTES,
    max_members: int = MAX_MEMBERS,
) -> Unpacked:
    """Extract the entry directory of ``bundle`` into ``dest`` (created, must not hold files)."""

    if entry_dir and not is_safe_relative_path(entry_dir):
        raise BundleError(f"entry_dir {entry_dir!r} must be a relative path without '..'")
    try:
        archive = tarfile.open(bundle, mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BundleError(f"the bundle is not a readable tar archive (gzip): {exc}") from None

    with archive:
        try:
            members = []
            for member in archive:
                members.append(member)
                if len(members) > max_members:
                    raise BundleError(f"the bundle holds more than {max_members} entries")
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise BundleError(f"the bundle is damaged: {exc}") from None

        cleaned: list[tuple[tarfile.TarInfo, str]] = []
        names: set[str] = set()
        directories: set[str] = set()
        for member in members:
            name = _clean_name(member.name)
            if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
                raise BundleError(f"special file in the bundle (device, FIFO ...): {member.name!r}")
            cleaned.append((member, name))
            if name:
                names.add(name)
                if member.isdir():
                    directories.add(name)

        base, skipped, chosen = choose_base(names, directories, entry_dir)
        dest.mkdir(parents=True, exist_ok=True)
        # Whatever the umask: the code is readable (the gateway serves static
        # apps straight from it) and writable by its owner only.
        os.chmod(dest, 0o755)
        dest_real = os.path.realpath(dest)

        total = 0
        files = 0
        symlinks: list[tuple[str, str]] = []
        for member, name in cleaned:
            if base and not name.startswith(base):
                continue
            rel = name[len(base):]
            if not rel:
                continue
            target = os.path.join(dest_real, *rel.split("/"))
            if member.isdir():
                _make_dir(target, dest_real)
                continue
            if member.issym():
                landing = _resolve_inside(rel.rpartition("/")[0], member.linkname)
                if landing is None:
                    raise BundleError(f"link pointing outside the entry directory: {member.name!r} -> {member.linkname!r}")
                symlinks.append((rel, member.linkname))
                continue
            if member.islnk():
                # A hard link names its target by archive path.
                link_target = _clean_name(member.linkname)
                if base and not link_target.startswith(base):
                    raise BundleError(f"hard link pointing outside the entry directory: {member.name!r} -> {member.linkname!r}")
            _make_dir(os.path.dirname(target), dest_real)
            try:
                source = archive.extractfile(member)
            except (KeyError, tarfile.TarError) as exc:
                raise BundleError(f"cannot read {member.name!r} from the bundle: {exc}") from None
            if source is None:
                raise BundleError(f"cannot read {member.name!r} from the bundle")
            if os.path.lexists(target):
                if os.path.isdir(target) and not os.path.islink(target):
                    raise BundleError(f"the bundle holds {member.name!r} both as a file and as a directory")
                os.unlink(target)
            with source, open(target, "wb") as handle:
                while True:
                    chunk = source.read(_COPY_CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise BundleError(f"the bundle unpacks to more than {max_bytes // (1024 * 1024)} MiB")
                    handle.write(chunk)
            os.chmod(target, 0o755 if member.mode & stat.S_IXUSR else 0o644)
            files += 1

        created: list[str] = []
        try:
            for rel, linkname in symlinks:
                target = os.path.join(dest_real, *rel.split("/"))
                _make_dir(os.path.dirname(target), dest_real)
                if os.path.lexists(target):
                    raise BundleError(f"the bundle holds {rel!r} both as a link and as something else")
                try:
                    os.symlink(linkname, target)
                except (OSError, NotImplementedError) as exc:
                    raise BundleError(f"cannot create the link {rel!r}: {exc}") from None
                created.append(target)
                if not _lands_inside(target, dest_real):
                    raise BundleError(f"link pointing outside the entry directory: {rel!r} -> {linkname!r}")
            # Once more, now that every link exists: the first look resolved a
            # target that passes through a later link as plain text, and
            # "later/../.." is inside as text and outside on the disk.
            for (rel, linkname), target in zip(symlinks, created):
                if not _lands_inside(target, dest_real):
                    raise BundleError(f"link pointing outside the entry directory: {rel!r} -> {linkname!r}")
        except BundleError:
            # Leave no link behind that leads out, whatever the caller does with the rest.
            for target in created:
                try:
                    os.unlink(target)
                except OSError:
                    pass
            raise

    return Unpacked(entry_dir=chosen, files=files, bytes=total, skipped_prefix=skipped)


def _lands_inside(path: str, dest_real: str) -> bool:
    """Whether ``path``, with every link in it followed, is ``dest_real`` or below it."""

    real = os.path.realpath(path)
    return real == dest_real or real.startswith(dest_real + os.sep)


def _make_dir(path: str, dest_real: str) -> None:
    """Create ``path`` (inside ``dest_real``), refusing to walk through a link."""

    if path == dest_real:
        return
    relative = os.path.relpath(path, dest_real)
    current = dest_real
    for part in relative.split(os.sep):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise BundleError(f"the bundle writes through a link: {os.path.relpath(current, dest_real)!r}")
        if os.path.isdir(current):
            continue
        if os.path.lexists(current):
            raise BundleError(f"the bundle holds {os.path.relpath(current, dest_real)!r} both as a file and as a directory")
        os.mkdir(current)
        os.chmod(current, 0o755)  # not left to the umask
