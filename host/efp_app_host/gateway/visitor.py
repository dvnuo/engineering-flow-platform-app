"""The visitor header an app receives (docs/CONTRACTS.md, section 6.4)::

    X-EFP-Visitor: v1.<payload>.<signature>
    payload   = base64url(no padding) of the JSON below, keys sorted, no spaces
    signature = base64url(no padding) of HMAC-SHA256(key, "v1." + payload)
    key       = base64url-decoded visitor_key of the app

    {"app": "<app id>", "exp": 1763200000, "group": "QA", "group_id": 3,
     "name": "Bob", "sub": 12, "username": "bob"}

``verify`` is the reference check; the SDK (``efp_app.visitor``) mirrors it.
"""

from __future__ import annotations

import binascii
import hashlib
import hmac
import json
import time
from typing import Any

from ..models import Visitor
from .sessions import b64decode, b64encode

HEADER = "X-EFP-Visitor"
VERSION = "v1"
LIFETIME_SECONDS = 60


def decode_key(visitor_key: str | bytes | None) -> bytes | None:
    """The raw key behind an app's ``visitor_key``, or ``None`` when it is unusable."""

    if not visitor_key:
        return None
    if isinstance(visitor_key, (bytes, bytearray)):
        return bytes(visitor_key)
    try:
        decoded = b64decode(visitor_key.strip().rstrip("="))
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None
    return decoded or None


def payload_for(app_id: str, visitor: Visitor, *, expires_at: int) -> dict[str, Any]:
    return {
        "app": app_id,
        "exp": int(expires_at),
        "group": visitor.group_name or None,
        "group_id": visitor.group_id,
        "name": visitor.display_name or visitor.username,
        "sub": int(visitor.id),
        "username": visitor.username,
    }


def sign(app_id: str, visitor_key: str | bytes | None, visitor: Visitor, *, now: float | None = None) -> str | None:
    """The header value for ``visitor`` on ``app_id``; ``None`` without a usable key."""

    key = decode_key(visitor_key)
    if key is None:
        return None
    current = time.time() if now is None else now
    body = payload_for(app_id, visitor, expires_at=int(current) + LIFETIME_SECONDS)
    payload = b64encode(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = b64encode(hmac.new(key, f"{VERSION}.{payload}".encode("ascii"), hashlib.sha256).digest())
    return f"{VERSION}.{payload}.{signature}"


def verify(
    header_value: str | None,
    visitor_key: str | bytes | None,
    app_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """The payload of a header value when it is genuine, current and made for ``app_id``."""

    key = decode_key(visitor_key)
    if key is None or not header_value:
        return None
    parts = header_value.strip().split(".")
    if len(parts) != 3 or parts[0] != VERSION or not parts[1] or not parts[2]:
        return None
    try:
        presented = b64decode(parts[2])
        expected = hmac.new(key, f"{VERSION}.{parts[1]}".encode("ascii"), hashlib.sha256).digest()
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None
    if not hmac.compare_digest(presented, expected):
        return None
    try:
        body = json.loads(b64decode(parts[1]).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict):
        return None
    expires = body.get("exp")
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return None
    if (time.time() if now is None else now) > expires:
        return None
    if body.get("app") != app_id:
        return None
    subject = body.get("sub")
    if isinstance(subject, bool) or not isinstance(subject, int):
        return None
    return body
