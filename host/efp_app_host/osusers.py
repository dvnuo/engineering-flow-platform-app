"""OS user entries for the app uids.

A process only needs a numeric uid, but plenty of libraries look their user
up by it (``getpass.getuser``, ``pwd.getpwuid``) and fail when there is no
entry. The manager therefore keeps one passwd and one group line per app,
named ``efpapp<uid>``. Its own lines are recognisable by that prefix and are
rewritten as a whole; every other line is left alone.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

from .models import AppRecord

USER_PREFIX = "efpapp"


def user_name(uid: int) -> str:
    return f"{USER_PREFIX}{uid}"


def _rewrite(path: Path, own: list[str]) -> bool:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            existing = handle.read().splitlines()
    except FileNotFoundError:
        existing = []
    kept = [line for line in existing if line.strip() and not line.startswith(USER_PREFIX)]
    wanted = kept + own
    if wanted == [line for line in existing if line.strip()]:
        return False
    tmp = path.with_name(f".{path.name}.efp-tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(wanted) + "\n")
    try:
        os.chmod(tmp, 0o644)
    except OSError:
        pass
    os.replace(tmp, path)
    return True


def sync_os_users(records: Iterable[AppRecord], *, passwd: Path = Path("/etc/passwd"), group: Path = Path("/etc/group")) -> bool:
    """Write the entries of every app. Returns whether a file changed."""

    uids = sorted({record.uid for record in records if record.uid > 0})
    passwd_lines = [f"{user_name(uid)}:x:{uid}:{uid}:EFP app:/nonexistent:/usr/sbin/nologin" for uid in uids]
    group_lines = [f"{user_name(uid)}:x:{uid}:" for uid in uids]
    changed = _rewrite(Path(passwd), passwd_lines)
    return _rewrite(Path(group), group_lines) or changed
