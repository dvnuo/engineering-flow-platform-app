"""Secrets the gateway makes for itself and keeps in a file.

Two of them exist:

- the gateway secret (``paths.gateway_secret_file``): known to nginx and the
  manager only. nginx proves with it that a request to an internal route
  really comes from the gateway, not from an app that connected to a
  loopback port by itself;
- the session key (``paths.session_key_file``): signs the session cookie.

Both are created on first use and never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

SECRET_HEADER = "X-EFP-Gateway-Secret"


def _read(path: Path) -> str:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return ""


def load_or_create(path: Path, *, nbytes: int = 32) -> str:
    """The secret in ``path``; a new random one (mode 0600) when there is none.

    Safe when two processes start at once (the nginx renderer and the
    manager): the file is created exclusively, the loser reads the winner's.
    """

    for attempt in range(20):
        value = _read(path)
        if value:
            return value
        path.parent.mkdir(parents=True, exist_ok=True)
        candidate = secrets.token_urlsafe(nbytes)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if attempt >= 10:
            # Another process fills the file within microseconds of making
            # it. One that is still empty now is the left-over of a crash.
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            time.sleep(0.02)
            continue
        try:
            os.write(descriptor, candidate.encode("ascii"))
        finally:
            os.close(descriptor)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return candidate
    raise RuntimeError(f"could not read or create the secret file {path}")


def access_log_key(gateway_secret: str) -> str:
    """What nginx writes into every access-log line so the manager can tell
    its lines from anything else that writes to the socket.

    Derived, so a copy of a log line never reveals the gateway secret.
    """

    return hmac.new(gateway_secret.encode("utf-8"), b"efp-access-log", hashlib.sha256).hexdigest()[:32]


def matches(presented: str | None, expected: str) -> bool:
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode("latin-1", "replace"), expected.encode("latin-1", "replace"))
