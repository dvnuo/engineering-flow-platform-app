"""What happens to one request for ``/{slug}/...`` (docs/CONTRACTS.md, section 6.1).

``decide`` is the whole rule set in one place. It knows nothing about HTTP
frameworks: it gets the facts of a request and answers either ``Proxy``
(send it on, and how) or ``Refusal`` (which error, and in which form).
``routes.py`` translates both for nginx.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping
from urllib.parse import quote

from ..models import (
    STATE_DISABLED,
    STATE_RUNNING,
    STATE_STARTING,
    STATE_UNDEPLOYED,
    VISIBILITY_ANYONE,
    AppRecord,
    Upstream,
    Visitor,
    is_valid_slug,
)
from . import visitor as visitor_header
from .access import AccessDecision, AccessUnavailable
from .sessions import Session

if TYPE_CHECKING:  # pragma: no cover
    from .services import GatewayServices

logger = logging.getLogger(__name__)

# ---- the codes of section 6.1, item 4 and 5 ----------------------------------
SIGN_IN_REQUIRED = "sign_in_required"
NO_ACCESS = "no_access"
APP_DISABLED = "app_disabled"
APP_NOT_FOUND = "app_not_found"
APP_STARTING = "app_starting"
APP_UNAVAILABLE = "app_unavailable"
RATE_LIMITED = "rate_limited"

STATUS = {
    SIGN_IN_REQUIRED: 401,
    NO_ACCESS: 403,
    APP_DISABLED: 403,
    APP_NOT_FOUND: 404,
    APP_STARTING: 503,
    APP_UNAVAILABLE: 503,
    RATE_LIMITED: 429,
}

MESSAGES = {
    SIGN_IN_REQUIRED: "Sign in to use this app.",
    NO_ACCESS: "You do not have access to this app.",
    APP_DISABLED: "This app has been taken down.",
    APP_NOT_FOUND: "There is no app at this address.",
    APP_STARTING: "The app is starting. Try again in a moment.",
    APP_UNAVAILABLE: "The app is not available right now.",
    RATE_LIMITED: "Too many requests. Try again in a moment.",
}

# ---- why an app is unavailable (chooses the page; never shown as is) ---------
DETAIL_NOT_DEPLOYED = "not_deployed"
DETAIL_PORTAL = "portal"
DETAIL_UPSTREAM = "upstream"
DETAIL_GATEWAY = "gateway"
_CRASH_DETAILS = frozenset({"crashed", "killed", "failed"})

STARTING_RETRY_SECONDS = 2

PAGE_STARTING = "starting"
PAGE_UNAVAILABLE = "unavailable"
PAGE_NOT_DEPLOYED = "not_deployed"
PAGE_CRASHED = "crashed"
PAGE_NO_ACCESS = "no_access"
PAGE_DISABLED = "disabled"
PAGE_NOT_FOUND = "not_found"
PAGE_RATE_LIMITED = "rate_limited"

_DETAIL_UNSAFE = re.compile(r"[^A-Za-z0-9_.:-]")
_HEADER_TOKEN = re.compile(r"[\x21-\x7e]+")
_HOST = re.compile(r"[A-Za-z0-9.-]+")
_warned_missing_key: set[str] = set()


@dataclass(frozen=True)
class GatewayRequest:
    """The facts of one request, as nginx reports them."""

    slug: str
    # The path exactly as the client sent it (still percent-encoded); it
    # starts with "/{slug}/". ``query`` is what followed "?", or None.
    path: str
    query: str | None = None
    # Carried for rules that depend on it; none does today.
    method: str = "GET"
    # The raw Cookie header.
    cookies: str = ""
    # Request headers, names in lower case. Read: accept, sec-fetch-mode.
    headers: Mapping[str, str] = field(default_factory=dict)
    source: str = ""


@dataclass(frozen=True)
class Proxy:
    """Send the request on."""

    app_id: str
    slug: str
    kind: str  # "proxy": the app's own process; "static": nginx serves static_root
    upstream: str  # host:port nginx connects to
    uri: str  # path and query the upstream is asked for
    cookie: str  # the Cookie header for the app; "" when nothing is left
    visitor_header: str | None = None
    set_cookie: str | None = None  # a renewed session cookie for the browser
    user_id: int | None = None  # for the access log
    static_root: str | None = None


@dataclass(frozen=True)
class Refusal:
    """Do not send the request on."""

    code: str
    # What the browser gets: 302 for a sign-in redirect, else the code's status.
    status: int
    # A page navigation gets a page or a redirect, anything else gets JSON.
    navigation: bool
    detail: str = ""
    redirect_to: str | None = None
    page: str | None = None
    retry_after: int | None = None
    slug: str = ""

    @property
    def kind(self) -> str:
        if self.redirect_to:
            return "redirect"
        return "page" if self.navigation else "json"

    @property
    def message(self) -> str:
        return MESSAGES[self.code]


def is_navigation(headers: Mapping[str, str]) -> bool:
    """Section 6.1, item 4: is this a page the browser is about to show?"""

    mode = (headers.get("sec-fetch-mode") or "").strip().lower()
    if mode and mode != "navigate":
        return False
    return "text/html" in (headers.get("accept") or "").lower()


def parse_original_uri(uri: str | None) -> tuple[str, str, str | None]:
    """(slug, path, query) of a request target such as ``/mindmap/a?b=1``.

    The slug is empty when the first segment is not a possible slug or
    nothing follows it.
    """

    path, separator, query = (uri or "").partition("?")
    slug = ""
    if path.startswith("/"):
        first, slash, _rest = path[1:].partition("/")
        if slash and is_valid_slug(first):
            slug = first
    return slug, path, (query if separator else None)


def launch_url(portal_public_url: str, slug: str, path: str, query: str | None) -> str:
    """Where a visitor signs in (section 6.3, step 1)."""

    target = path if query is None else f"{path}?{query}"
    return f"{portal_public_url}/apps/launch/{slug}?next={quote(target, safe='')}"


def page_for(code: str, detail: str = "") -> str:
    if code == NO_ACCESS:
        return PAGE_NO_ACCESS
    if code == APP_DISABLED:
        return PAGE_DISABLED
    if code == APP_NOT_FOUND:
        return PAGE_NOT_FOUND
    if code == APP_STARTING:
        return PAGE_STARTING
    if code == RATE_LIMITED:
        return PAGE_RATE_LIMITED
    if detail in _CRASH_DETAILS:
        return PAGE_CRASHED
    if detail == DETAIL_NOT_DEPLOYED:
        return PAGE_NOT_DEPLOYED
    return PAGE_UNAVAILABLE


def refuse(
    code: str,
    *,
    portal_public_url: str,
    slug: str = "",
    path: str = "",
    query: str | None = None,
    headers: Mapping[str, str] | None = None,
    detail: str = "",
    retry_after: int | None = None,
) -> Refusal:
    """The refusal for ``code``, in the form this request should get."""

    if code not in STATUS:
        code, detail = APP_UNAVAILABLE, DETAIL_GATEWAY
    if code == SIGN_IN_REQUIRED and not slug:
        code = APP_NOT_FOUND
    detail = _DETAIL_UNSAFE.sub("", detail or "")[:64]
    navigation = is_navigation(headers or {})
    status = STATUS[code]
    redirect_to = None
    page = None
    if navigation:
        if code == SIGN_IN_REQUIRED:
            redirect_to = launch_url(portal_public_url, slug, path, query)
            status = 302
        else:
            page = page_for(code, detail)
    if code == APP_STARTING and retry_after is None:
        retry_after = STARTING_RETRY_SECONDS
    return Refusal(
        code=code,
        status=status,
        navigation=navigation,
        detail=detail,
        redirect_to=redirect_to,
        page=page,
        retry_after=retry_after,
        slug=slug,
    )


def upstream_uri(slug: str, path: str, query: str | None, *, strip_prefix: bool) -> str:
    target = path[len(slug) + 1 :] if strip_prefix else path
    return target if query is None else f"{target}?{query}"


def _code_for_denial(reason: str | None) -> str:
    if reason == APP_DISABLED:
        return APP_DISABLED
    if reason == APP_NOT_FOUND:
        return APP_NOT_FOUND
    return NO_ACCESS


def _identity(session: Session, answer: AccessDecision) -> Visitor:
    """Name and group as the Portal knows them now, not as of sign-in."""

    fresh = answer.visitor
    if fresh is not None and fresh.id == session.visitor.id:
        return fresh
    return session.visitor


def _static_root(value: str | None) -> str | None:
    """A directory nginx may be pointed at: absolute, plain, never ``/`` itself."""

    text = (value or "").rstrip("/")
    if not text.startswith("/") or not all(" " <= char <= "~" for char in text):
        return None
    if ".." in text.split("/"):
        return None
    return text


def _target(gateway: "GatewayServices", record: AppRecord, upstream: Upstream, request: GatewayRequest) -> tuple[str, str, str | None] | None:
    """(address, uri, static root) of the upstream, or None when it cannot be used."""

    if upstream.kind == "static":
        root = _static_root(upstream.static_root)
        if root is None:
            return None
        # nginx serves the directory from a virtual host of its own on the
        # gateway port. That host takes the public path (it drops the first
        # segment itself), so that its redirects name the right address.
        address = f"127.0.0.1:{gateway.settings.gateway_port}"
        return address, upstream_uri(record.slug, request.path, request.query, strip_prefix=False), root
    if upstream.port <= 0 or not _HOST.fullmatch(upstream.host or ""):
        return None
    address = f"{upstream.host}:{upstream.port}"
    return address, upstream_uri(record.slug, request.path, request.query, strip_prefix=upstream.strip_prefix), None


async def decide(gateway: "GatewayServices", request: GatewayRequest) -> Proxy | Refusal:
    """Section 6.1, item 3, in the order written there."""

    settings = gateway.settings

    def no(code: str, *, detail: str = "", retry_after: int | None = None) -> Refusal:
        return refuse(
            code,
            portal_public_url=settings.portal_public_url,
            slug=request.slug,
            path=request.path,
            query=request.query,
            headers=request.headers,
            detail=detail,
            retry_after=retry_after,
        )

    slug = request.slug
    if not is_valid_slug(slug) or not request.path.startswith(f"/{slug}/"):
        return no(APP_NOT_FOUND)
    record = gateway.registry.find_by_slug(slug)
    if record is None or not _HEADER_TOKEN.fullmatch(record.id):
        return no(APP_NOT_FOUND)
    if not record.enabled:
        return no(APP_DISABLED)

    now = gateway.clock()
    session, forwarded_cookie = gateway.sessions.read_cookie_header(request.cookies, now=now)
    who: Visitor | None = None

    if record.visibility == VISIBILITY_ANYONE:
        # The limit is for visitors nobody knows. Signed-in members often
        # share one source address (an office, a proxy in front of the
        # gateway) and one page of an app is many requests.
        if session is None and not gateway.limiter.allow(request.source):
            return no(RATE_LIMITED, retry_after=gateway.limiter.retry_after)
        if session is not None:
            # Nobody is turned away from such an app; the check only tells
            # whether the visitor's identity may still be vouched for.
            try:
                answer = await gateway.access.check(record.id, session.visitor.id)
            except AccessUnavailable:
                answer = None
            if answer is not None and answer.allowed:
                who = _identity(session, answer)
    else:
        if session is None:
            return no(SIGN_IN_REQUIRED)
        try:
            answer = await gateway.access.check(record.id, session.visitor.id)
        except AccessUnavailable:
            return no(APP_UNAVAILABLE, detail=DETAIL_PORTAL)
        if not answer.allowed:
            return no(_code_for_denial(answer.reason), detail=answer.reason or "")
        who = _identity(session, answer)

    upstream = gateway.runtime.upstream(record.id)
    if upstream is None:
        return no(APP_UNAVAILABLE, detail=DETAIL_NOT_DEPLOYED)
    status = await gateway.runtime.ensure_running(record.id)
    if status.state == STATE_STARTING:
        return no(APP_STARTING)
    if status.state == STATE_DISABLED:
        return no(APP_DISABLED)
    if status.state != STATE_RUNNING:
        return no(APP_UNAVAILABLE, detail=DETAIL_NOT_DEPLOYED if status.state == STATE_UNDEPLOYED else status.state)
    target = _target(gateway, record, upstream, request)
    if target is None:
        logger.error("app %s has an upstream the gateway cannot use", record.id)
        return no(APP_UNAVAILABLE, detail=DETAIL_UPSTREAM)
    address, uri, static_root = target
    gateway.runtime.touch(record.id)

    header = None
    set_cookie = None
    if who is not None and session is not None:
        header = visitor_header.sign(record.id, record.visitor_key, who, now=now)
        if header is None and record.id not in _warned_missing_key:
            _warned_missing_key.add(record.id)
            logger.warning("app %s has no usable visitor key: its visitors look anonymous to it", record.id)
        if who != session.visitor or gateway.sessions.needs_refresh(session, now=now):
            set_cookie = gateway.sessions.cookie(gateway.sessions.issue(who, now=now))

    return Proxy(
        app_id=record.id,
        slug=record.slug,
        kind="static" if static_root else "proxy",
        upstream=address,
        uri=uri,
        cookie=forwarded_cookie,
        visitor_header=header,
        set_cookie=set_cookie,
        user_id=who.id if who is not None else None,
        static_root=static_root,
    )
