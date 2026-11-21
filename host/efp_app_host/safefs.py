"""File operations of the manager (root) on directories an app can write.

An app owns its private tmp, in hidden mode its virtual environment and the
directory pip collects its wheels in, and nothing stops it from putting a
symbolic link where the manager is about to work. A call that takes a path
follows such a link: ``chown`` and ``chmod`` then land on whatever the link
names, a copy reads or overwrites it. So the manager never trusts the last
component of a path inside such a directory:

- a directory is opened with ``O_NOFOLLOW`` and changed through the
  descriptor (``fchmod`` / ``fchown``), so the change lands on what was
  opened or not at all;
- a directory that is handed to an app is made by root, filled by root and
  given away last (``fresh_dir``);
- a file an app wrote is opened with ``O_NOFOLLOW`` and must be a plain file
  with one name (``copy_from_app``).

Only the last component needs this care: the directories above are root's,
and an app cannot swap an entry of a directory it may not write.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
from pathlib import Path
from typing import Iterable

_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
# Windows (where only the tests run) has neither the flags nor the calls.
_BY_DESCRIPTOR = bool(_O_NOFOLLOW and _O_DIRECTORY and hasattr(os, "fchmod") and hasattr(os, "fchown"))
_EMPTY_ATTEMPTS = 3
_COPY_CHUNK = 1024 * 1024


class UnsafePath(OSError):
    """Something other than what the manager put there sits at the path: a link, or content that keeps coming back."""


def _quiet(call, *args) -> None:
    if call is None:  # Windows has no chown
        return
    try:
        call(*args)
    except OSError:
        # EFS behind an access point owns every file itself.
        pass


def _not_a_link(path: Path | str, exc: OSError) -> None:
    if exc.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
        raise UnsafePath(exc.errno, f"{path} is a link or not what the host made there; refusing to follow it") from None


def own(path: Path | str, mode: int, *, uid: int | None = None, gid: int | None = None, directory: bool = True, follow: bool = False) -> None:
    """Set mode (and owner) of ``path`` itself, never of what a link there points at.

    ``follow`` is for the few paths an operator names (the volume, the run
    directory): those may be links, and no app can reach their parents.
    """

    group = uid if gid is None else gid
    if follow:
        _quiet(os.chmod, path, mode)
        if uid is not None:
            _quiet(getattr(os, "chown", None), path, uid, group)
        return
    if not _BY_DESCRIPTOR:
        if os.path.islink(path):
            raise UnsafePath(errno.ELOOP, f"{path} is a link; refusing to follow it")
        _quiet(os.chmod, path, mode)
        if uid is not None:
            _quiet(getattr(os, "chown", None), path, uid, group)
        return
    flags = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | (_O_DIRECTORY if directory else getattr(os, "O_NONBLOCK", 0))
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        _not_a_link(path, exc)
        raise
    try:
        _quiet(os.fchmod, descriptor, mode)
        if uid is not None:
            _quiet(os.fchown, descriptor, uid, group)
    finally:
        os.close(descriptor)


def make_dir(path: Path, mode: int, *, uid: int | None = None, gid: int | None = None, follow: bool = False) -> None:
    """Create ``path`` with ``mode`` (and owner). Directories that had to be
    created above it are open for passing through, whatever the umask: what
    must be closed is closed where it is named.

    A link at ``path`` is refused (``UnsafePath``), not followed.
    """

    path = Path(path)
    missing = []
    current = path
    while not os.path.lexists(current) and current != current.parent:
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        try:
            os.mkdir(directory)
        except FileExistsError:
            continue
        if directory != path:
            own(directory, 0o755, follow=follow)
    own(path, mode, uid=uid, gid=gid, follow=follow)


def remove_tree(path: Path | str) -> None:
    shutil.rmtree(path, ignore_errors=True)


def fresh_dir(path: Path, mode: int, *, uid: int | None = None, gid: int | None = None, children: Iterable[str] = ()) -> None:
    """Replace ``path`` by an empty directory of the given owner, with empty ``children``.

    Whatever was there is removed first. The new directory is made by the
    caller (root), gets its children while it is still root's and closed, and
    is given to its owner last: nothing the owner's processes do in the
    meantime can make root follow a link. When the old content cannot be
    removed because something keeps writing there, nothing is handed over.
    """

    path = Path(path)
    for _ in range(_EMPTY_ATTEMPTS):
        remove_tree(path)
        if os.path.lexists(path):
            # A link or a file where the directory was: rmtree leaves those.
            try:
                os.unlink(path)
            except OSError:
                pass
        if not os.path.lexists(path):
            break
    else:
        raise UnsafePath(errno.ENOTEMPTY, f"{path} could not be emptied: a process keeps writing there")
    if not path.parent.exists():
        make_dir(path.parent, 0o755)
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        raise UnsafePath(errno.EEXIST, f"{path} came back while it was being replaced") from None
    for name in children:
        child = path / name
        os.mkdir(child, 0o700)
        own(child, mode, uid=uid, gid=gid)
    own(path, mode, uid=uid, gid=gid)


def copy_from_app(source: Path, target: Path) -> int:
    """Copy a file an app wrote to ``target`` (a place of root's own). Returns its size.

    The source is opened without following a link and must be a plain file
    with a single name: a link (symbolic or hard) to something the app
    cannot read itself would otherwise be read for it, as root.
    """

    flags = os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    if not _O_NOFOLLOW and os.path.islink(source):
        raise UnsafePath(errno.ELOOP, f"{source} is a link; refusing to follow it")
    try:
        descriptor = os.open(source, flags)
    except OSError as exc:
        _not_a_link(source, exc)
        raise
    with os.fdopen(descriptor, "rb") as reader:
        info = os.fstat(reader.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            raise UnsafePath(errno.EINVAL, f"{source} is not a plain file of its own; refusing to copy it")
        copied = 0
        with open(target, "wb") as writer:
            while True:
                chunk = reader.read(_COPY_CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                copied += len(chunk)
    return copied
