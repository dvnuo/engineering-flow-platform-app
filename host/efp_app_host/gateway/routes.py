"""The gateway's routes on the manager (docs/CONTRACTS.md, section 6).

Internal, reachable only with the gateway secret (nginx adds it):

- ``GET /_efp/auth``: nginx asks this about every request for ``/{slug}/...``
  (``auth_request``). nginx only understands 2xx, 401 and 403 here, so the
  answer is ``200`` with ``X-EFP-*`` headers that say where the request goes,
  or ``401`` / ``403`` with ``X-EFP-Reason``.
- ``GET /_efp/deny``: turns a reason into the response the browser gets: a
  redirect to the Portal's launch page, a page, or the JSON error.

Reached by browsers through nginx: ``/_efp/callback`` and ``/_efp/logout``
(section 6.3). ``/_efp/health`` tells that the manager is up.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import unquote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from ..models import Visitor, is_valid_slug
from .decision import (
    APP_STARTING,
    APP_UNAVAILABLE,
    DETAIL_GATEWAY,
    RATE_LIMITED,
    SIGN_IN_REQUIRED,
    GatewayRequest,
    Refusal,
    decide,
    parse_original_uri,
    refuse,
)
from .pages import pick_locale
from .secret import SECRET_HEADER, matches
from .services import GatewayServices

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

# A reason only nginx gives: the root path of the apps hostname.
REASON_HOME = "home"

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
        retry_url=retry_url,
        refresh_seconds=refresh_seconds,
    )
    return HTMLResponse(body, status_code=status, headers={**_PAGE_HEADERS, **(headers or {})})


def refusal_response(gateway: GatewayServices, request: Request, refusal: Refusal, *, original_uri: str = "") -> Response:
    headers = dict(_NO_STORE)
    if refusal.retry_after:
        headers["Retry-After"] = str(refusal.retry_after)
    if refusal.redirect_to:
        return RedirectResponse(refusal.redirect_to, status_code=302, headers=headers)
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
    if reason == REASON_HOME:
        return _page(gateway, request, "home", status=200)
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


def _signin_failed(gateway: GatewayServices, request: Request, *, slug: str, status: int) -> Response:
    return _page(gateway, request, "signin_failed", status=status, slug=slug)


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
    try:
        redeemed = await gateway.portal.redeem_handoff(code)
    except Exception as exc:
        logger.warning("the sign-in code could not be redeemed: %s", exc.__class__.__name__)
        return _signin_failed(gateway, request, slug=hinted_slug, status=503)
    if not redeemed:
        return _signin_failed(gateway, request, slug=hinted_slug, status=400)
    try:
        slug = str(redeemed["slug"])
        app_id = str(redeemed["app_id"])
        visitor = Visitor.from_json(redeemed["user"])
    except (KeyError, TypeError, ValueError):
        logger.warning("the Portal's answer to a sign-in code is incomplete")
        return _signin_failed(gateway, request, slug=hinted_slug, status=400)
    if not is_valid_slug(slug):
        return _signin_failed(gateway, request, slug="", status=400)

    # The Portal hands out a code only to somebody who may open the app.
    gateway.access.remember(app_id, visitor.id, True, None, visitor)
    response = RedirectResponse(safe_next(wanted, slug), status_code=302, headers={**_NO_STORE, "Referrer-Policy": "no-referrer"})
    response.headers.append("Set-Cookie", gateway.sessions.cookie(gateway.sessions.issue(visitor, now=gateway.clock())))
    return response


@router.get("/logout")
async def logout(request: Request) -> Response:
    gateway = _services(request)
    response = RedirectResponse(gateway.settings.portal_public_url or "/", status_code=302, headers=_NO_STORE)
    response.headers.append("Set-Cookie", gateway.sessions.clear_cookie())
    return response
