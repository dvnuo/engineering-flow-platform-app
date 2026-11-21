"""Gateway sessions: one signed cookie for the whole apps hostname.

The cookie holds who the visitor is, when it was issued and when it ends::

    s1.<payload>.<signature>
    payload   = base64url(no padding) of {"exp": ..., "iat": ..., "u": {visitor}}
    signature = base64url(no padding) of HMAC-SHA256(session key, "s1." + payload)

Nothing is stored on the host: a session ends when it expires, or when the
Portal revokes the user's sessions (``Registry.revoke_user``), which makes
every cookie issued before that moment invalid.

A second, short-lived cookie holds the "state" of a sign-in that is under way
(docs/CONTRACTS.md, section 6.3): a random value the gateway also puts into
the address it sends the browser to. The Portal hands it back with the code,
and the code is redeemed only in the browser that holds the same value, so
that nobody can finish a sign-in of their own in somebody else's browser.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from ..models import Visitor

COOKIE_NAME = "__Host-efp_apps"
# Cookie names no app may use (docs/CONTRACTS.md, section 6.2), lower case.
RESERVED_COOKIE_PREFIXES = ("__host-efp", "__secure-efp")
TOKEN_VERSION = "s1"
MAX_TOKEN_LENGTH = 4096
_B64URL = re.compile(r"[A-Za-z0-9_-]+")

# The sign-in under way. An hour: the Portal's own sign-in (company SSO, a
# second factor) happens in between.
STATE_COOKIE_NAME = "__Host-efp_apps_state"
STATE_TTL_SECONDS = 3600
_STATE = re.compile(r"[A-Za-z0-9_-]{22,64}")


def new_state() -> str:
    return secrets.token_urlsafe(24)


def read_state(header: str | None) -> str | None:
    """The state of a sign-in under way, from a ``Cookie`` header; ``None`` without a well-formed one."""

    for part in (header or "").split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name.strip() == STATE_COOKIE_NAME and _STATE.fullmatch(value.strip()):
            return value.strip()
    return None


def state_matches(presented: str | None, held: str | None) -> bool:
    """True when the address and the cookie carry the same well-formed value."""

    if not presented or not held or not _STATE.fullmatch(presented):
        return False
    return hmac.compare_digest(presented.encode("ascii"), held.encode("ascii"))


def state_cookie(value: str) -> str:
    return f"{STATE_COOKIE_NAME}={value}; Path=/; Max-Age={STATE_TTL_SECONDS}; HttpOnly; Secure; SameSite=Lax"


def clear_state_cookie() -> str:
    return f"{STATE_COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"


def b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64decode(text: str) -> bytes:
    """Strict: anything outside the base64url alphabet is an error, not skipped."""

    if not _B64URL.fullmatch(text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text.encode("ascii") + b"=" * (-len(text) % 4))


@dataclass(frozen=True)
class Session:
    visitor: Visitor
    issued_at: float
    expires_at: float


def split_cookie_header(header: str | None) -> tuple[list[str], str]:
    """Session tokens found in a ``Cookie`` header, and the header an app may see.

    The second value has neither the session cookie nor any other cookie with
    a reserved name; it is empty when nothing is left.
    """

    tokens: list[str] = []
    kept: list[str] = []
    for part in (header or "").split(";"):
        item = part.strip()
        if not item:
            continue
        name, separator, value = item.partition("=")
        name = name.strip()
        if separator and name == COOKIE_NAME:
            tokens.append(value.strip())
            continue
        if name.lower().startswith(RESERVED_COOKIE_PREFIXES):
            continue
        kept.append(item)
    return tokens, "; ".join(kept)


class SessionCodec:
    def __init__(
        self,
        key: bytes | Callable[[], bytes],
        ttl_seconds: int,
        *,
        clock: Callable[[], float] = time.time,
        revoked_before: Callable[[int], float] | None = None,
    ):
        self._key_source = key
        self._key: bytes | None = None
        self.ttl_seconds = int(ttl_seconds)
        self._clock = clock
        self._revoked_before = revoked_before or (lambda _user_id: 0.0)

    @property
    def key(self) -> bytes:
        if self._key is None:
            source = self._key_source
            self._key = source() if callable(source) else bytes(source)
            if not self._key:
                raise RuntimeError("the session key is empty")
        return self._key

    def _sign(self, payload: str) -> str:
        signed = f"{TOKEN_VERSION}.{payload}".encode("ascii")
        return b64encode(hmac.new(self.key, signed, hashlib.sha256).digest())

    # ---- tokens -------------------------------------------------------------
    def issue(self, visitor: Visitor, *, now: float | None = None) -> str:
        issued = self._clock() if now is None else now
        body = {"exp": int(issued) + self.ttl_seconds, "iat": round(float(issued), 3), "u": visitor.to_json()}
        payload = b64encode(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        return f"{TOKEN_VERSION}.{payload}.{self._sign(payload)}"

    def read(self, token: str | None, *, now: float | None = None) -> Session | None:
        """The session a token stands for, or ``None`` when it is not valid now."""

        if not token or len(token) > MAX_TOKEN_LENGTH:
            return None
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != TOKEN_VERSION or not parts[1] or not parts[2]:
            return None
        try:
            presented = b64decode(parts[2])
            expected = hmac.new(self.key, f"{TOKEN_VERSION}.{parts[1]}".encode("ascii"), hashlib.sha256).digest()
        except (UnicodeEncodeError, binascii.Error, ValueError):
            return None
        if not hmac.compare_digest(presented, expected):
            return None
        try:
            body = json.loads(b64decode(parts[1]).decode("utf-8"))
            issued = body["iat"]
            expires = body["exp"]
            if isinstance(issued, bool) or isinstance(expires, bool):
                return None
            issued = float(issued)
            expires = float(expires)
            visitor = Visitor.from_json(body["u"])
        except (binascii.Error, ValueError, UnicodeDecodeError, KeyError, TypeError):
            return None
        current = self._clock() if now is None else now
        if expires <= current:
            return None
        if issued < self._revoked_before(visitor.id):
            return None
        return Session(visitor=visitor, issued_at=issued, expires_at=expires)

    def read_cookie_header(self, header: str | None, *, now: float | None = None) -> tuple[Session | None, str]:
        """The valid session in a ``Cookie`` header (if any) and the header for the app."""

        tokens, forwarded = split_cookie_header(header)
        for token in tokens:
            session = self.read(token, now=now)
            if session is not None:
                return session, forwarded
        return None, forwarded

    def needs_refresh(self, session: Session, *, now: float | None = None) -> bool:
        """Sliding lifetime: a new cookie once a tenth of the lifetime is used up."""

        current = self._clock() if now is None else now
        return current - session.issued_at > self.ttl_seconds / 10

    # ---- cookies ------------------------------------------------------------
    def cookie(self, token: str) -> str:
        """The ``Set-Cookie`` value that stores ``token``."""

        return f"{COOKIE_NAME}={token}; Path=/; Max-Age={self.ttl_seconds}; HttpOnly; Secure; SameSite=Lax"

    def clear_cookie(self) -> str:
        return f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax"
