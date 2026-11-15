"""Landlock through raw system calls (no extra dependency).

Landlock lets an unprivileged process give up filesystem access for itself and
everything it starts. The ``landlock`` sandbox backend uses it when neither
form of bubblewrap works: after dropping to the app's uid and setting
``no_new_privs`` the launcher allows reads below the system directories and
the app's release, writes below the app's data and private tmp, and nothing
else. Unlike a mount namespace this does not hide other paths: they exist but
cannot be opened or listed.

Linux only; every import of ``ctypes`` machinery happens inside functions.
"""

from __future__ import annotations

import errno
import os
import struct
from dataclasses import dataclass, field

SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_CREATE_RULESET_VERSION = 1
LANDLOCK_RULE_PATH_BENEATH = 1
PR_SET_NO_NEW_PRIVS = 38

FS_EXECUTE = 1 << 0
FS_WRITE_FILE = 1 << 1
FS_READ_FILE = 1 << 2
FS_READ_DIR = 1 << 3
FS_REMOVE_DIR = 1 << 4
FS_REMOVE_FILE = 1 << 5
FS_MAKE_CHAR = 1 << 6
FS_MAKE_DIR = 1 << 7
FS_MAKE_REG = 1 << 8
FS_MAKE_SOCK = 1 << 9
FS_MAKE_FIFO = 1 << 10
FS_MAKE_BLOCK = 1 << 11
FS_MAKE_SYM = 1 << 12
FS_REFER = 1 << 13  # ABI 2
FS_TRUNCATE = 1 << 14  # ABI 3
FS_IOCTL_DEV = 1 << 15  # ABI 5

_ABI1 = (1 << 13) - 1
_FILE_RIGHTS = FS_EXECUTE | FS_WRITE_FILE | FS_READ_FILE | FS_TRUNCATE | FS_IOCTL_DEV
READ_EXEC = FS_EXECUTE | FS_READ_FILE | FS_READ_DIR
DEVICE = FS_READ_FILE | FS_WRITE_FILE | FS_READ_DIR | FS_IOCTL_DEV
# Everything except creating device nodes.
READ_WRITE = (
    READ_EXEC
    | FS_WRITE_FILE
    | FS_REMOVE_DIR
    | FS_REMOVE_FILE
    | FS_MAKE_DIR
    | FS_MAKE_REG
    | FS_MAKE_SOCK
    | FS_MAKE_FIFO
    | FS_MAKE_SYM
    | FS_REFER
    | FS_TRUNCATE
)


class LandlockError(OSError):
    """Landlock is missing, disabled or refused a rule."""


@dataclass
class LandlockPlan:
    read_exec: list[str] = field(default_factory=list)
    read_write: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)


def handled_rights(abi: int) -> int:
    """Every filesystem right the kernel's ABI version can restrict."""

    rights = _ABI1
    if abi >= 2:
        rights |= FS_REFER
    if abi >= 3:
        rights |= FS_TRUNCATE
    if abi >= 5:
        rights |= FS_IOCTL_DEV
    return rights


def rule_rights(wanted: int, abi: int, *, is_dir: bool) -> int:
    """The rights of one rule: known to this ABI, and only file rights for a file."""

    rights = wanted & handled_rights(abi)
    if not is_dir:
        rights &= _FILE_RIGHTS
    return rights


def _libc():
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return ctypes, libc


def abi_version() -> int:
    """The Landlock ABI of the running kernel; 0 when Landlock cannot be used."""

    if not hasattr(os, "O_PATH"):
        return 0
    try:
        ctypes, libc = _libc()
        result = libc.syscall(
            ctypes.c_long(SYS_LANDLOCK_CREATE_RULESET),
            ctypes.c_void_p(None),
            ctypes.c_size_t(0),
            ctypes.c_uint(LANDLOCK_CREATE_RULESET_VERSION),
        )
    except (OSError, AttributeError):
        return 0
    return int(result) if result > 0 else 0


def set_no_new_privs() -> None:
    ctypes, libc = _libc()
    if libc.prctl(ctypes.c_int(PR_SET_NO_NEW_PRIVS), ctypes.c_ulong(1), ctypes.c_ulong(0), ctypes.c_ulong(0), ctypes.c_ulong(0)) != 0:
        code = ctypes.get_errno()
        raise LandlockError(code, f"prctl(PR_SET_NO_NEW_PRIVS) failed: {os.strerror(code)}")


def restrict(plan: LandlockPlan) -> int:
    """Apply ``plan`` to this process for good. Returns the ABI version used.

    The caller must have set ``no_new_privs``. Paths that cannot be opened
    are skipped: a rule can only name something that is there, and a missing
    rule means no access.
    """

    abi = abi_version()
    if abi <= 0:
        raise LandlockError(errno.ENOSYS, "Landlock is not available in this kernel (or is disabled at boot)")
    ctypes, libc = _libc()
    attr = ctypes.create_string_buffer(struct.pack("=Q", handled_rights(abi)))
    ruleset = libc.syscall(ctypes.c_long(SYS_LANDLOCK_CREATE_RULESET), attr, ctypes.c_size_t(8), ctypes.c_uint(0))
    if ruleset < 0:
        code = ctypes.get_errno()
        raise LandlockError(code, f"landlock_create_ruleset failed: {os.strerror(code)}")
    try:
        for paths, wanted in ((plan.read_exec, READ_EXEC), (plan.devices, DEVICE), (plan.read_write, READ_WRITE)):
            for path in paths:
                try:
                    handle = os.open(path, os.O_PATH | os.O_CLOEXEC)
                except OSError:
                    # Missing, a dangling link, not reachable: no rule, so no
                    # access. Leaving a path out can only ever close more.
                    continue
                try:
                    rights = rule_rights(wanted, abi, is_dir=os.path.isdir(path))
                    if not rights:
                        continue
                    rule = ctypes.create_string_buffer(struct.pack("=Qi", rights, handle))
                    if libc.syscall(
                        ctypes.c_long(SYS_LANDLOCK_ADD_RULE),
                        ctypes.c_int(ruleset),
                        ctypes.c_int(LANDLOCK_RULE_PATH_BENEATH),
                        rule,
                        ctypes.c_uint(0),
                    ) != 0:
                        code = ctypes.get_errno()
                        raise LandlockError(code, f"landlock_add_rule failed for {path}: {os.strerror(code)}")
                finally:
                    os.close(handle)
        if libc.syscall(ctypes.c_long(SYS_LANDLOCK_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint(0)) != 0:
            code = ctypes.get_errno()
            raise LandlockError(code, f"landlock_restrict_self failed: {os.strerror(code)}")
    finally:
        os.close(ruleset)
    return abi
