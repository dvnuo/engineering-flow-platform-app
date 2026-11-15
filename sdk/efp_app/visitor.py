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
the same code runs locally.
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
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple, Union

from . import config

HEADER = "X-EFP-Visitor"
_VERSION = "v1"
_ANONYMOUS_NAMES = {"anonymous", "anon", "none", "nobody"}
_OWNER_RETRY_SECONDS = 30.0
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
    value, a bad signature, an expired value, a value made for another app or
    when there is no key to check against: an app must treat all of those as
    an anonymous visitor.
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
    app_id = payload.get("app")
    if settings.app_id and app_id is not None and str(app_id) != settings.app_id:
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
    return _dev_visitor(settings)


# --- A developer's machine ---------------------------------------------------

_dev_lock = threading.Lock()
_owner: Tuple[Any, float, Optional[Visitor]] = (None, 0.0, None)
_warned = False


def _dev_visitor(settings: config.Settings) -> Optional[Visitor]:
    name = (settings.dev_visitor or "").strip()
    if name:
        if name.lower() in _ANONYMOUS_NAMES:
            return None
        return Visitor(id=0, username=name, name=name)
    return _owner_visitor(settings)


def _owner_visitor(settings: config.Settings) -> Optional[Visitor]:
    """The app's owner as ``GET /app`` reports it; asked once, then remembered."""
    global _owner, _warned
    signature = (settings.token, settings.portal_url)
    with _dev_lock:
        cached_signature, retry_at, cached = _owner
        if cached_signature == signature and (cached is not None or time.monotonic() < retry_at):
            return cached
    found: Optional[Visitor] = None
    problem = ""
    try:
        from . import app as _app

        owner = _app.info(cached=True).owner
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
    """Forget the remembered owner (tests)."""
    global _owner, _warned
    with _dev_lock:
        _owner = (None, 0.0, None)
        _warned = False
