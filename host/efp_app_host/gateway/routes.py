"""The gateway's routes on the manager (docs/CONTRACTS.md, section 6).

Internal, reachable only with the gateway secret (nginx adds it):

- ``GET /_efp/auth``: nginx asks this about every request for ``/{slug}/...``
  (``auth_request``). nginx only understands 2xx, 401 and 403 here, so the
  answer is ``200`` with ``X-EFP-*`` headers that say where the request goes,
  or ``401`` / ``403`` with ``X-EFP-Reason``.
- ``GET /_efp/deny``: turns a reason into the response the browser gets: a
  redirect to the Portal's launch page, a page, or the JSON error.
- ``GET /_efp/home``: the app center, what nginx answers ``/`` with (section
  6.5): the apps the visitor may open, or the way to sign in.
- ``GET /_efp/icon/{slug}``: the icon of an app on that page.

Reached by browsers through nginx: ``/_efp/callback`` and ``/_efp/logout``
(section 6.3). ``/_efp/health`` tells that the manager is up.
"""

from __future__ import annotations

import logging
import re
import secrets
from urllib.parse import parse_qs, unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from ..models import VISIBILITY_ANYONE, Visitor, is_valid_slug
from .access import AccessUnavailable
from .center import CatalogError, parse_catalog
from .decision import (
    APP_NOT_FOUND,
    APP_STARTING,
    APP_UNAVAILABLE,
    DETAIL_GATEWAY,
    RATE_LIMITED,
    SIGN_IN_REQUIRED,
    GatewayRequest,
    Refusal,
    decide,
    is_navigation,
    parse_original_uri,
    refuse,
)
from .pages import pick_locale
from .secret import SECRET_HEADER, matches
from .services import GatewayServices
from .sessions import clear_state_cookie, new_state, read_state, state_cookie, state_matches

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/_efp")

# ---- what nginx sends ---------------------------------------------------------
ORIGINAL_URI_HEADER = "X-EFP-Original-URI"  # the request target as the client sent it
METHOD_HEADER = "X-EFP-Method"
SOURCE_HEADER = "X-EFP-Source"  # the client's address
REASON_HEADER = "X-EFP-Reason"  # /deny: the reason /auth gave, or a fixed one
DETAIL_HEADER = "X-EFP-Detail"

# ---- what /auth answers -------------------------------------------------------
APP_HEADER = "X-EFP-App"  # app id, for the access log
SLUG_HEADER = "X-EFP-Slug"  # for X-Forwarded-Prefix and the cookie paths
UPSTREAM_HEADER = "X-EFP-Upstream"  # host:port
UPSTREAM_URI_HEADER = "X-EFP-Upstream-URI"  # path and query for the upstream
COOKIE_HEADER = "X-EFP-Cookie"  # the Cookie header the app gets
VISITOR_HEADER = "X-EFP-Visitor"
SET_COOKIE_HEADER = "X-EFP-Set-Cookie"  # a renewed session cookie for the browser
USER_HEADER = "X-EFP-User"  # user id, for the access log
STATIC_ROOT_HEADER = "X-EFP-Static-Root"  # static apps: the directory nginx serves

# The app center (section 6.5).
CENTER_PATH = "/"
SIGNED_IN_MARK = "signed_in"  # /?signed_in=1: the sign-in just finished, do not start it again
CENTER_AFTER_SIGN_IN = f"{CENTER_PATH}?{SIGNED_IN_MARK}=1"
ICON_MAX_AGE = 86400

_NO_STORE = {"Cache-Control": "no-store"}
_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "same-origin",
}
_NEXT_CHARACTERS = re.compile(r"[\x21-\x7e]+")
MAX_NEXT_LENGTH = 4096


def _services(request: Request) -> GatewayServices:
    return request.app.state.host.gateway


def _internal_only(request: Request, gateway: GatewayServices) -> Response | None:
    """``None`` for nginx; a refusal for anything else that found the port."""

    if matches(request.headers.get(SECRET_HEADER), gateway.secret):
        return None
    return JSONResponse(
        {"error": {"code": "forbidden", "message": "This route is for the gateway only."}},
        status_code=403,
        headers=_NO_STORE,
    )


def safe_next(value: str | None, slug: str) -> str:
    """Where the callback sends the browser: ``value`` when it stays inside
    the app ``slug``, else the app's front page (section 6.3, step 3)."""

    home = f"/{slug}/"
    if not value or len(value) > MAX_NEXT_LENGTH or not value.startswith(home):
        return home
    if not _NEXT_CHARACTERS.fullmatch(value) or "\\" in value:
        return home
    path = value.partition("?")[0].partition("#")[0]
    if any(segment in (".", "..") for segment in unquote(path).split("/")):
        return home
    return value


def _page(
    gateway: GatewayServices,
    request: Request,
    page: str,
    *,
    status: int,
    slug: str = "",
    retry_url: str = "",
    refresh_seconds: int | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    record = gateway.registry.find_by_slug(slug) if slug else None
    body = gateway.pages.render(
        page,
        pick_locale(request.headers.get("accept-language")),
        app=(record.name or record.slug) if record is not None else "",
        owner=record.owner_username if record is not None else "",
        reason=(record.disabled_reason or "") if record is not None else "",
        portal_url=gateway.settings.portal_public_url,
        app_url=f"/{slug}/" if slug else "",
        center_url=CENTER_PATH,
        retry_url=retry_url,
        refresh_seconds=refresh_seconds,
    )
    return HTMLResponse(body, status_code=status, headers={**_PAGE_HEADERS, **(headers or {})})


def sign_in_redirect(request: Request, launch: str, headers: dict[str, str] | None = None) -> Response:
    """Send the browser to the Portal's launch page, bound to this browser (section 6.3, step 1).

    The state goes into the address and into a cookie. Tabs that start a
    sign-in at the same time share one value: the cookie of the first is
    used again, so the second does not make the first one's answer invalid.
    """

    state = read_state("; ".join(request.headers.getlist("cookie"))) or new_state()
    separator = "&" if "?" in launch else "?"
    response = RedirectResponse(f"{launch}{separator}state={state}", status_code=302, headers=headers or dict(_NO_STORE))
    response.headers.append("Set-Cookie", state_cookie(state))
    return response


def refusal_response(gateway: GatewayServices, request: Request, refusal: Refusal, *, original_uri: str = "") -> Response:
    headers = dict(_NO_STORE)
    if refusal.retry_after:
        headers["Retry-After"] = str(refusal.retry_after)
    if refusal.redirect_to:
        return sign_in_redirect(request, refusal.redirect_to, headers)
    if not refusal.navigation or refusal.page is None:
        return JSONResponse(
            {"error": {"code": refusal.code, "message": refusal.message}},
            status_code=refusal.status,
            headers=headers,
        )
    return _page(
        gateway,
        request,
        refusal.page,
        status=refusal.status,
        slug=refusal.slug,
        retry_url=original_uri,
        refresh_seconds=refusal.retry_after if refusal.code == APP_STARTING else None,
        headers=headers,
    )


@router.get("/health")
async def health() -> Response:
    return PlainTextResponse("ok")


@router.get("/auth")
async def auth(request: Request) -> Response:
    gateway = _services(request)
    blocked = _internal_only(request, gateway)
    if blocked is not None:
        return blocked

    slug, path, query = parse_original_uri(request.headers.get(ORIGINAL_URI_HEADER))
    result = await decide(
        gateway,
        GatewayRequest(
            slug=slug,
            path=path,
            query=query,
            method=(request.headers.get(METHOD_HEADER) or "GET").upper(),
            cookies="; ".join(request.headers.getlist("cookie")),
            headers={
                "accept": request.headers.get("accept") or "",
                "sec-fetch-mode": request.headers.get("sec-fetch-mode") or "",
            },
            source=request.headers.get(SOURCE_HEADER) or "",
        ),
    )

    if isinstance(result, Refusal):
        answer = {REASON_HEADER: result.code}
        if result.detail:
            answer[DETAIL_HEADER] = result.detail
        return Response(status_code=401 if result.code == SIGN_IN_REQUIRED else 403, headers=answer)

    answer = {
        APP_HEADER: result.app_id,
        SLUG_HEADER: result.slug,
        UPSTREAM_HEADER: result.upstream,
        UPSTREAM_URI_HEADER: result.uri,
    }
    if result.cookie:
        answer[COOKIE_HEADER] = result.cookie
    if result.visitor_header:
        answer[VISITOR_HEADER] = result.visitor_header
    if result.set_cookie:
        answer[SET_COOKIE_HEADER] = result.set_cookie
    if result.user_id is not None:
        answer[USER_HEADER] = str(result.user_id)
    if result.static_root:
        answer[STATIC_ROOT_HEADER] = result.static_root
    return Response(status_code=200, headers=answer)


@router.get("/deny")
async def deny(request: Request) -> Response:
    gateway = _services(request)
    blocked = _internal_only(request, gateway)
    if blocked is not None:
        return blocked

    original_uri = request.headers.get(ORIGINAL_URI_HEADER) or ""
    slug, path, query = parse_original_uri(original_uri)
    reason = (request.headers.get(REASON_HEADER) or "").strip()
    detail = (request.headers.get(DETAIL_HEADER) or "").strip()
    if not reason:
        # No reason from /auth: nginx itself could not get an answer for a
        # request that was let through (the app did not accept the connection).
        reason, detail = APP_UNAVAILABLE, DETAIL_GATEWAY
    refusal = refuse(
        reason,
        portal_public_url=gateway.settings.portal_public_url,
        slug=slug,
        path=path,
        query=query,
        headers=request.headers,
        detail=detail,
        retry_after=gateway.limiter.retry_after if reason == RATE_LIMITED else None,
    )
    return refusal_response(gateway, request, refusal, original_uri=original_uri)


def _signin_failed(gateway: GatewayServices, request: Request, *, slug: str, status: int, retry_url: str = "") -> Response:
    return _page(gateway, request, "signin_failed", status=status, slug=slug, retry_url=retry_url)


# ---- the app center ---------------------------------------------------------------
def center_launch_url(portal_public_url: str) -> str:
    """Where a visitor of the app center signs in (section 6.5, step 1)."""

    return f"{portal_public_url}/apps/launch"


def _center_json(code: str, status: int, message: str) -> Response:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=_NO_STORE)


def _cookies(request: Request) -> str:
    return "; ".join(request.headers.getlist("cookie"))


@router.get("/home")
async def home(request: Request) -> Response:
    """Section 6.5: the apps this visitor may open, or the way to sign in."""

    gateway = _services(request)
    blocked = _internal_only(request, gateway)
    if blocked is not None:
        return blocked

    navigation = is_navigation(request.headers)
    session, _forwarded = gateway.sessions.read_cookie_header(_cookies(request), now=gateway.clock())
    if session is None:
        if not navigation:
            return _center_json(SIGN_IN_REQUIRED, 401, "Sign in to see your apps.")
        query = (request.headers.get(ORIGINAL_URI_HEADER) or "").partition("?")[2]
        if parse_qs(query).get(SIGNED_IN_MARK):
            # The sign-in just finished and there is still no session: the
            # browser did not keep the cookie. Another redirect would only loop.
            return _signin_failed(gateway, request, slug="", status=400, retry_url=CENTER_PATH)
        return sign_in_redirect(request, center_launch_url(gateway.settings.portal_public_url))
    if not navigation:
        # The list is a page, not an API.
        return _center_json(APP_NOT_FOUND, 404, "There is no app at this address.")

    try:
        catalog = parse_catalog(await gateway.portal.catalog(session.visitor.id))
    except CatalogError as exc:
        logger.error("the Portal's catalog cannot be used: %s", exc)
        return _page(gateway, request, "center_unavailable", status=503, retry_url=CENTER_PATH)
    except Exception as exc:
        logger.warning("the catalog could not be read: %s", exc.__class__.__name__)
        return _page(gateway, request, "center_unavailable", status=503, retry_url=CENTER_PATH)

    if not catalog.allowed or catalog.visitor is None or catalog.visitor.id != session.visitor.id:
        # The member is no longer one: the session ends, and the Portal says why when they sign in.
        response = sign_in_redirect(request, center_launch_url(gateway.settings.portal_public_url))
        response.headers.append("Set-Cookie", gateway.sessions.clear_cookie())
        return response

    # The Portal just said that this member may open each of these.
    for app in catalog.apps:
        gateway.access.remember(app.id, catalog.visitor.id, True, None, catalog.visitor)

    nonce = secrets.token_urlsafe(18)
    body = gateway.center.render(
        catalog,
        pick_locale(request.headers.get("accept-language")),
        portal_url=gateway.settings.portal_public_url,
        nonce=nonce,
    )
    headers = {
        **_PAGE_HEADERS,
        "Content-Security-Policy": (
            f"default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
            "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        ),
    }
    response = HTMLResponse(body, status_code=200, headers=headers)
    now = gateway.clock()
    if catalog.visitor != session.visitor or gateway.sessions.needs_refresh(session, now=now):
        response.headers.append("Set-Cookie", gateway.sessions.cookie(gateway.sessions.issue(catalog.visitor, now=now)))
    return response


@router.get("/icon/{slug}")
async def icon(slug: str, request: Request) -> Response:
    """An app's icon for a visitor with a session who may open the app; 404 for everything else."""

    gateway = _services(request)
    blocked = _internal_only(request, gateway)
    if blocked is not None:
        return blocked
    missing = Response(status_code=404, headers=_NO_STORE)

    session, _forwarded = gateway.sessions.read_cookie_header(_cookies(request), now=gateway.clock())
    record = gateway.registry.find_by_slug(slug) if is_valid_slug(slug) else None
    if session is None or record is None or not record.enabled:
        return missing
    if record.visibility != VISIBILITY_ANYONE:
        try:
            answer = await gateway.access.check(record.id, session.visitor.id)
        except AccessUnavailable:
            return missing
        if not answer.allowed:
            return missing
    try:
        found = await gateway.icons.get(record.id, request.query_params.get("v") or "", gateway.portal.app_icon)
    except Exception as exc:
        logger.warning("the icon of %s could not be read: %s", record.id, exc.__class__.__name__)
        return missing
    if found is None:
        return missing
    data, content_type = found
    return Response(
        content=data,
        media_type=content_type,
        headers={
            "Cache-Control": f"private, max-age={ICON_MAX_AGE}",
            "Content-Security-Policy": "default-src 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/callback")
async def callback(request: Request) -> Response:
    """Section 6.3, step 3: redeem the Portal's code, start the session."""

    gateway = _services(request)
    code = request.query_params.get("code") or ""
    wanted = request.query_params.get("next") or ""
    # Until the code is redeemed the only hint of the app is ``next``.
    hinted_slug = parse_original_uri(wanted)[0]
    if not code:
        return _signin_failed(gateway, request, slug=hinted_slug, status=400)
    if not state_matches(request.query_params.get("state"), read_state(_cookies(request))):
        # This browser did not start the sign-in the code belongs to (a link
        # somebody else made, to sign the visitor in as themselves), or it
        # lost the cookie on the way. The code is left alone.
        back = safe_next(wanted, hinted_slug) if hinted_slug else CENTER_PATH
        session, _forwarded = gateway.sessions.read_cookie_header(_cookies(request), now=gateway.clock())
        if session is not None:
            # Signed in already: another tab finished the same sign-in first.
            return RedirectResponse(back, status_code=302, headers=_NO_STORE)
        return _signin_failed(gateway, request, slug=hinted_slug, status=400, retry_url=back)
    try:
        redeemed = await gateway.portal.redeem_handoff(code)
    except Exception as exc:
        logger.warning("the sign-in code could not be redeemed: %s", exc.__class__.__name__)
        return _signin_failed(gateway, request, slug=hinted_slug, status=503)
    if not redeemed:
        return _signin_failed(gateway, request, slug=hinted_slug, status=400)
    try:
        visitor = Visitor.from_json(redeemed["user"])
        for_center = redeemed["app_id"] is None and redeemed.get("slug") is None
        slug = "" if for_center else str(redeemed["slug"])
        app_id = "" if for_center else str(redeemed["app_id"])
    except (KeyError, TypeError, ValueError):
        logger.warning("the Portal's answer to a sign-in code is incomplete")
        return _signin_failed(gateway, request, slug=hinted_slug, status=400)
    if for_center:
        # A code of the app center names a member and no app (section 6.5).
        target = CENTER_AFTER_SIGN_IN
    elif not is_valid_slug(slug):
        return _signin_failed(gateway, request, slug="", status=400)
    else:
        # The Portal hands out a code only to somebody who may open the app.
        gateway.access.remember(app_id, visitor.id, True, None, visitor)
        target = safe_next(wanted, slug)
    response = RedirectResponse(target, status_code=302, headers={**_NO_STORE, "Referrer-Policy": "no-referrer"})
    response.headers.append("Set-Cookie", gateway.sessions.cookie(gateway.sessions.issue(visitor, now=gateway.clock())))
    response.headers.append("Set-Cookie", clear_state_cookie())
    return response


@router.get("/logout")
async def logout(request: Request) -> Response:
    gateway = _services(request)
    response = RedirectResponse(gateway.settings.portal_public_url or "/", status_code=302, headers=_NO_STORE)
    response.headers.append("Set-Cookie", gateway.sessions.clear_cookie())
    return response
