"""Who is using the app right now (docs/CONTRACTS.md, section 6.4).

The gateway adds ``X-EFP-Visitor`` to every request of a signed-in visitor.
The value is signed with a key only the gateway and this app know
(``EFP_VISITOR_KEY``) and is valid for 60 seconds, so an app can trust it
without asking anyone::

    from efp_app import visitor

    who = visitor.current(request.headers)      # a Visitor, or None for an anonymous visitor
    if who:
        print(who.username, who.name, who.group)

On a developer's machine there is no gateway. When no header is present,
``current()`` returns the user named by ``EFP_DEV_VISITOR`` (``anonymous``
stands for nobody), or else the owner of the app as the Portal reports it, so
the same code runs locally. That stand-in exists only where neither
``EFP_APP_ID`` nor ``EFP_VISITOR_KEY`` is set: a host sets both, and behind
its gateway a request without the header is an anonymous visitor.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import logging
import re
import threading
import zlib
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple, Union

from . import config

HEADER = "X-EFP-Visitor"
_VERSION = "v1"
_ANONYMOUS_NAMES = {"anonymous", "anon", "none", "nobody"}
_OWNER_RETRY_SECONDS = 30.0
# Where the ids of visitors named with EFP_DEV_VISITOR start, and how many there are.
_DEV_ID_BASE = 900_000_000
_DEV_ID_RANGE = 100_000_000
_B64URL = re.compile(r"[A-Za-z0-9_-]+")

log = logging.getLogger("efp_app")


@dataclass(frozen=True)
class Visitor:
    """A signed-in visitor.

    ``id``        the Portal's user id
    ``username``  the Portal username
    ``name``      the display name
    ``group``     the name of the visitor's group, or ``None``
    ``group_id``  the id of that group, or ``None``
    ``raw``       the header value this came from; pass the visitor (or this
                  value) as ``visitor=`` to an LLM or connector call so the
                  Portal records who caused it
    """

    id: int
    username: str
    name: str
    group: Optional[str] = None
    group_id: Optional[int] = None
    raw: Optional[str] = field(default=None, repr=False, compare=False)


def _b64decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _key_bytes(key: Union[str, bytes, bytearray, None]) -> Optional[bytes]:
    if key is None:
        return None
    if isinstance(key, (bytes, bytearray)):
        return bytes(key) or None
    try:
        decoded = _b64decode(str(key).strip())
    except (binascii.Error, ValueError):
        return None
    return decoded or None


def parse(
    header_value: Optional[str],
    key: Union[str, bytes, None] = None,
    *,
    now: Optional[float] = None,
) -> Optional[Visitor]:
    """Verify an ``X-EFP-Visitor`` value and return the visitor.

    ``key`` is the app's visitor key: the base64url text of ``EFP_VISITOR_KEY``
    (the default) or the raw bytes. The result is ``None`` for a missing
    value, a bad signature, an expired value, a value made for another app
    (or, in a deployed app, for no app at all) or when there is no key to
    check against: an app must treat all of those as an anonymous visitor.
    """
    if not header_value or not isinstance(header_value, str):
        return None
    settings = config.load()
    secret = _key_bytes(key if key is not None else settings.visitor_key)
    if not secret:
        return None
    parts = header_value.strip().split(".")
    if len(parts) != 3 or parts[0] != _VERSION or not parts[1] or not parts[2]:
        return None
    payload_text, signature_text = parts[1], parts[2]
    if not _B64URL.fullmatch(payload_text) or not _B64URL.fullmatch(signature_text):
        return None
    try:
        signed = (_VERSION + "." + payload_text).encode("ascii")
        signature = _b64decode(signature_text)
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None
    expected = hmac.new(secret, signed, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, signature):
        return None
    try:
        payload = json.loads(_b64decode(payload_text).decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    expires = payload.get("exp")
    if isinstance(expires, bool) or not isinstance(expires, (int, float)):
        return None
    if (time.time() if now is None else now) > expires:
        return None
    # A deployed app takes only a value made for it. The gateway always names the app, so a
    # value that names none is not one of the gateway's: it is refused like one for another app.
    app_id = payload.get("app")
    if settings.app_id and (app_id is None or str(app_id) != settings.app_id):
        return None

    user_id = payload.get("sub")
    username = payload.get("username")
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        return None
    if not isinstance(username, str) or not username:
        return None
    name = payload.get("name")
    group = payload.get("group")
    group_id = payload.get("group_id")
    return Visitor(
        id=user_id,
        username=username,
        name=name if isinstance(name, str) and name else username,
        group=group if isinstance(group, str) and group else None,
        group_id=group_id if isinstance(group_id, int) and not isinstance(group_id, bool) else None,
        raw=header_value.strip(),
    )


def header_value(headers: Any) -> Optional[str]:
    """The ``X-EFP-Visitor`` value in a mapping of request headers, in any letter case.

    A WSGI environment (``HTTP_X_EFP_VISITOR``) works too.
    """
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        for name in ("x-efp-visitor", HEADER, "HTTP_X_EFP_VISITOR"):
            try:
                value = getter(name)
            except Exception:
                value = None
            text = _as_text(value)
            if text:
                return text
    try:
        items = headers.items()
    except Exception:
        return None
    for name, value in items:
        if _as_text(name).lower() == "x-efp-visitor":
            text = _as_text(value)
            if text:
                return text
    return None


def _as_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("latin-1").strip()
    return str(value).strip()


def current(headers: Optional[Mapping[str, Any]] = None) -> Optional[Visitor]:
    """The visitor of the request with these headers, or ``None`` when anonymous.

    ``headers`` is any mapping of the request's headers: ``request.headers``
    in FastAPI, Starlette and Flask, ``st.context.headers`` in Streamlit,
    ``gr.Request.headers`` in Gradio (see ``efp_app.integrations``).
    """
    value = header_value(headers)
    if value:
        return parse(value)
    settings = config.load()
    if settings.deployed:
        return None
    if settings.visitor_key:
        # Only a host gives an app a visitor key. A process that has one is treated as standing
        # behind a gateway even when EFP_APP_ID did not reach it (a command that starts its server
        # with a trimmed environment, say): there a request without the header is an anonymous
        # visitor, and a stand-in would make every such visitor the owner of the app.
        _warn_about_key()
        return None
    return _dev_visitor(settings)


# --- A developer's machine ---------------------------------------------------

# Seconds the one request for the owner of the app may wait. A request of the app waits with it.
_OWNER_TIMEOUT = 5.0

_dev_lock = threading.Lock()
# Held by the one caller that asks the Portal for the owner; the others wait for its answer.
_owner_asking = threading.Lock()
_owner: Tuple[Any, float, Optional[Visitor]] = (None, 0.0, None)
_warned = False
_warned_about_key = False


def _warn_about_key() -> None:
    """Say once per process why there is no local stand-in: a developer who set the key will look for it."""
    global _warned_about_key
    with _dev_lock:
        if _warned_about_key:
            return
        _warned_about_key = True
    log.warning(
        "efp_app: %s is set, so a request without a signed %s header has no visitor: the local "
        "stand-in (%s, or the owner of the app) is not used. Remove %s to get it back.",
        config.VISITOR_KEY,
        HEADER,
        config.DEV_VISITOR,
        config.VISITOR_KEY,
    )


def dev_visitor_id(username: str) -> int:
    """The id of a visitor named with ``EFP_DEV_VISITOR``: made from the name, the same on every run.

    Two names give two ids, so an app that keeps data per ``visitor.id`` can
    be tried as two members on one machine. The numbers start far above the
    ids the Portal gives its members and are nobody's real id.
    """
    return _DEV_ID_BASE + zlib.crc32(username.strip().lower().encode("utf-8")) % _DEV_ID_RANGE


def _dev_visitor(settings: config.Settings) -> Optional[Visitor]:
    name = (settings.dev_visitor or "").strip()
    if name:
        if name.lower() in _ANONYMOUS_NAMES:
            return None
        return Visitor(id=dev_visitor_id(name), username=name, name=name)
    return _owner_visitor(settings)


def _remembered_owner(signature: Tuple[Any, Any]) -> Tuple[bool, Optional[Visitor]]:
    """``(True, the owner or None)`` when the Portal need not be asked now, else ``(False, None)``."""
    with _dev_lock:
        cached_signature, retry_at, cached = _owner
        if cached_signature == signature and (cached is not None or time.monotonic() < retry_at):
            return True, cached
    return False, None


def _owner_visitor(settings: config.Settings) -> Optional[Visitor]:
    """The app's owner as ``GET /app`` reports it; asked once, then remembered."""
    global _owner, _warned
    signature = (settings.token, settings.portal_url)
    known, cached = _remembered_owner(signature)
    if known:
        return cached
    # One caller asks. A request of the app is waiting for this answer, so it is one short
    # attempt (app._info_once), and the requests that arrive meanwhile wait here for that
    # answer instead of each asking a Portal that may be slow or out of reach.
    with _owner_asking:
        known, cached = _remembered_owner(signature)
        if known:
            return cached
        found: Optional[Visitor] = None
        problem = ""
        try:
            from . import app as _app

            owner = _app._info_once(_OWNER_TIMEOUT).owner
            if owner is not None and owner.username:
                found = Visitor(
                    id=owner.id if isinstance(owner.id, int) else 0,
                    username=owner.username,
                    name=owner.display_name or owner.username,
                    group=owner.group_name,
                    group_id=owner.group_id,
                )
            else:
                problem = "the Portal did not name the owner of the app"
        except Exception as exc:  # the visitor of a local run must never break the app
            problem = str(exc)
        with _dev_lock:
            _owner = (signature, time.monotonic() + _OWNER_RETRY_SECONDS, found)
            if found is None and not _warned:
                _warned = True
                log.warning(
                    "efp_app: no visitor for this local run (%s). Set %s to a username to choose one.",
                    problem,
                    config.DEV_VISITOR,
                )
    return found


def reset_dev_visitor() -> None:
    """Forget the remembered owner, and that the warnings were given (tests)."""
    global _owner, _warned, _warned_about_key
    with _dev_lock:
        _owner = (None, 0.0, None)
        _warned = False
        _warned_about_key = False
