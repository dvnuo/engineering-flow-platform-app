"""The gateway's routes, called the way nginx and a browser call them."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from efp_app_host.app import create_app
from efp_app_host.gateway import visitor as visitor_header
from efp_app_host.gateway.routes import safe_next
from efp_app_host.gateway.sessions import COOKIE_NAME
from efp_app_host.models import STATE_CRASHED

from gateway_support import BOB, VISITOR_KEY, add_app, allow, session_cookie

NAVIGATE = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Mode": "navigate"}
FETCH = {"Accept": "application/json", "Sec-Fetch-Mode": "cors"}


@pytest.fixture
def app(settings, registry, fake_runtime, fake_portal):
    return create_app(settings, registry=registry, runtime=fake_runtime, portal=fake_portal, run_background=False)


@pytest.fixture
def gateway(app):
    return app.state.host.gateway


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def ask(client, gateway, uri: str, *, cookie: str = "", headers: dict[str, str] | None = None, source: str = "203.0.113.7"):
    """The access check, as nginx sends it."""

    sent = {
        "X-EFP-Gateway-Secret": gateway.secret,
        "X-EFP-Original-URI": uri,
        "X-EFP-Method": "GET",
        "X-EFP-Source": source,
        **(NAVIGATE if headers is None else headers),
    }
    if cookie:
        sent["Cookie"] = cookie
    return client.get("/_efp/auth", headers=sent)


def turn_away(client, gateway, uri: str, reason: str, *, detail: str = "", headers: dict[str, str] | None = None):
    """The refusal, as nginx asks for it after a 401 / 403 of the access check."""

    sent = {"X-EFP-Gateway-Secret": gateway.secret, "X-EFP-Original-URI": uri, **(NAVIGATE if headers is None else headers)}
    if reason:
        sent["X-EFP-Reason"] = reason
    if detail:
        sent["X-EFP-Detail"] = detail
    return client.get("/_efp/deny", headers=sent)


# ---- the gateway secret ---------------------------------------------------------
@pytest.mark.parametrize("path", ["/_efp/auth", "/_efp/deny"])
@pytest.mark.parametrize("presented", [None, "", "wrong", "x" * 43])
def test_internal_routes_need_the_gateway_secret(client, gateway, registry, fake_runtime, path, presented):
    add_app(registry, fake_runtime, visibility="anyone")
    headers = {"X-EFP-Original-URI": "/mindmap/", "X-EFP-Reason": "app_not_found"}
    if presented is not None:
        headers["X-EFP-Gateway-Secret"] = presented
    response = client.get(path, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert not [name for name in response.headers if name.lower().startswith("x-efp-")]
    assert gateway.secret not in response.text


def test_the_secret_is_the_one_in_the_file_nginx_reads(gateway, settings):
    secret = gateway.secret
    assert (settings.run_dir / "gateway.secret").read_text(encoding="utf-8").strip() == secret


def test_health_needs_nothing(client):
    response = client.get("/_efp/health")
    assert (response.status_code, response.text) == (200, "ok")


# ---- /_efp/auth --------------------------------------------------------------------
def test_auth_lets_a_member_through_and_says_where_to(client, gateway, registry, fake_runtime, fake_portal):
    record = add_app(registry, fake_runtime)
    allow(fake_portal)
    response = ask(client, gateway, "/mindmap/board?id=3", cookie=f"theme=dark; {session_cookie(gateway)}")
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["x-efp-app"] == "app-1"
    assert response.headers["x-efp-slug"] == "mindmap"
    assert response.headers["x-efp-upstream"] == f"127.0.0.1:{record.port}"
    assert response.headers["x-efp-upstream-uri"] == "/board?id=3"
    assert response.headers["x-efp-cookie"] == "theme=dark"
    assert response.headers["x-efp-user"] == str(BOB.id)
    assert visitor_header.verify(response.headers["x-efp-visitor"], VISITOR_KEY, "app-1")["username"] == "bob"
    for absent in ("x-efp-set-cookie", "x-efp-static-root", "x-efp-reason", "set-cookie"):
        assert absent not in response.headers
    assert fake_runtime.touched == ["app-1"]


def test_auth_for_an_anonymous_visitor_of_an_open_app(client, gateway, registry, fake_runtime):
    add_app(registry, fake_runtime, type="flask", visibility="anyone")
    response = ask(client, gateway, "/mindmap/", headers=FETCH)
    assert response.status_code == 200
    assert response.headers["x-efp-upstream-uri"] == "/mindmap/"
    for absent in ("x-efp-visitor", "x-efp-user", "x-efp-cookie", "x-efp-set-cookie"):
        assert absent not in response.headers


def test_auth_names_the_directory_of_a_static_app(client, gateway, settings, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, type="static")
    allow(fake_portal)
    response = ask(client, gateway, "/mindmap/css/site.css", cookie=session_cookie(gateway))
    assert response.status_code == 200
    assert response.headers["x-efp-upstream"] == f"127.0.0.1:{settings.gateway_port}"
    assert response.headers["x-efp-upstream-uri"] == "/mindmap/css/site.css"
    assert response.headers["x-efp-static-root"] == "/static-root"


def test_auth_passes_a_renewed_session_cookie(client, gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    old = session_cookie(gateway, now=gateway.clock() - gateway.sessions.ttl_seconds / 2)
    response = ask(client, gateway, "/mindmap/", cookie=old)
    assert response.status_code == 200
    renewed = response.headers["x-efp-set-cookie"]
    assert renewed.startswith(f"{COOKIE_NAME}=") and "HttpOnly" in renewed and "Secure" in renewed
    # It is for nginx to hand on, not a cookie of this answer.
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "uri, cookie, status, reason",
    [
        ("/mindmap/", False, 401, "sign_in_required"),
        ("/mindmap/", True, 403, "no_access"),
        ("/nothing/", False, 403, "app_not_found"),
        ("/", False, 403, "app_not_found"),
        ("", False, 403, "app_not_found"),
    ],
)
def test_auth_refuses_with_a_status_nginx_understands(client, gateway, registry, fake_runtime, uri, cookie, status, reason):
    add_app(registry, fake_runtime)
    response = ask(client, gateway, uri, cookie=session_cookie(gateway) if cookie else "")
    assert response.status_code == status
    assert response.headers["x-efp-reason"] == reason
    assert response.content == b""
    for absent in ("x-efp-upstream", "x-efp-upstream-uri", "x-efp-app", "x-efp-visitor"):
        assert absent not in response.headers


def test_auth_tells_why_an_app_is_unavailable(client, gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    fake_runtime.set_state("app-1", STATE_CRASHED, "exit:1")
    response = ask(client, gateway, "/mindmap/", cookie=session_cookie(gateway))
    assert (response.status_code, response.headers["x-efp-reason"], response.headers["x-efp-detail"]) == (403, "app_unavailable", "crashed")


def test_auth_starts_a_stopped_app(client, gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, running=False)
    allow(fake_portal)
    response = ask(client, gateway, "/mindmap/", cookie=session_cookie(gateway))
    assert (response.status_code, response.headers["x-efp-reason"]) == (403, "app_starting")
    assert fake_runtime.started == ["app-1"]


def test_auth_limits_by_the_address_nginx_reports(app, client, gateway, registry, fake_runtime):
    add_app(registry, fake_runtime, visibility="anyone")
    gateway.limiter.per_minute = 2
    assert ask(client, gateway, "/mindmap/", source="198.51.100.1").status_code == 200
    assert ask(client, gateway, "/mindmap/", source="198.51.100.1").status_code == 200
    limited = ask(client, gateway, "/mindmap/", source="198.51.100.1")
    assert (limited.status_code, limited.headers["x-efp-reason"]) == (403, "rate_limited")
    assert ask(client, gateway, "/mindmap/", source="198.51.100.2").status_code == 200


# ---- /_efp/deny --------------------------------------------------------------------
def test_deny_sends_a_navigation_to_the_launch_page(client, gateway, registry, fake_runtime):
    add_app(registry, fake_runtime)
    response = turn_away(client, gateway, "/mindmap/board?id=3", "sign_in_required")
    assert response.status_code == 302
    target = urlsplit(response.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == "https://efp.example.com/apps/launch/mindmap"
    assert parse_qs(target.query) == {"next": ["/mindmap/board?id=3"]}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "reason, detail, status, retry_after",
    [
        ("sign_in_required", "", 401, None),
        ("no_access", "", 403, None),
        ("app_disabled", "", 403, None),
        ("app_not_found", "", 404, None),
        ("app_starting", "", 503, "2"),
        ("app_unavailable", "crashed", 503, None),
        ("rate_limited", "", 429, "1"),
    ],
)
def test_deny_answers_a_fetch_with_the_json_error(client, gateway, registry, fake_runtime, reason, detail, status, retry_after):
    add_app(registry, fake_runtime)
    response = turn_away(client, gateway, "/mindmap/api", reason, detail=detail, headers=FETCH)
    assert response.status_code == status
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == reason
    assert body["error"]["message"]
    assert response.headers.get("retry-after") == retry_after
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers


@pytest.mark.parametrize(
    "reason, detail, status, expected",
    [
        ("no_access", "", 403, "You do not have access to Mind map"),
        ("app_disabled", "", 403, "Mind map has been taken down"),
        ("app_not_found", "", 404, "There is no app at this address"),
        ("app_starting", "", 503, "Mind map is starting"),
        ("app_unavailable", "", 503, "Mind map is not available right now"),
        ("app_unavailable", "stopped", 503, "Mind map is not available right now"),
        ("app_unavailable", "not_deployed", 503, "Mind map has not been deployed yet"),
        ("app_unavailable", "crashed", 503, "Mind map stopped unexpectedly"),
        ("app_unavailable", "killed", 503, "Mind map stopped unexpectedly"),
        ("rate_limited", "", 429, "Too many requests"),
    ],
)
def test_deny_answers_a_navigation_with_a_page(client, gateway, registry, fake_runtime, reason, detail, status, expected):
    add_app(registry, fake_runtime, disabled_reason="Policy review")
    response = turn_away(client, gateway, "/mindmap/board", reason, detail=detail)
    assert response.status_code == status
    assert response.headers["content-type"].startswith("text/html")
    assert expected in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_starting_page_refreshes_itself(client, gateway, registry, fake_runtime):
    add_app(registry, fake_runtime)
    response = turn_away(client, gateway, "/mindmap/", "app_starting")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert '<meta http-equiv="refresh" content="2">' in response.text
    other = turn_away(client, gateway, "/mindmap/", "app_unavailable")
    assert "http-equiv" not in other.text and "retry-after" not in other.headers


def test_the_no_access_page_names_the_owner_and_the_disabled_page_the_reason(client, gateway, registry, fake_runtime):
    add_app(registry, fake_runtime, disabled_reason="Leaked <customer> data")
    no_access = turn_away(client, gateway, "/mindmap/", "no_access")
    assert "Ask its owner, alice, to share it with you." in no_access.text
    assert 'href="https://efp.example.com"' in no_access.text
    disabled = turn_away(client, gateway, "/mindmap/", "app_disabled")
    assert "Reason: Leaked &lt;customer&gt; data" in disabled.text
    assert "<customer>" not in disabled.text


def test_deny_without_a_reason_means_the_app_could_not_be_reached(client, gateway, registry, fake_runtime):
    add_app(registry, fake_runtime)
    page = turn_away(client, gateway, "/mindmap/x", "")
    assert page.status_code == 503 and "Mind map is not available right now" in page.text
    assert 'href="/mindmap/x"' in page.text  # "Try again" goes back to where the visitor was
    error = turn_away(client, gateway, "/mindmap/x", "", headers=FETCH)
    assert (error.status_code, error.json()["error"]["code"]) == (503, "app_unavailable")
    unknown = turn_away(client, gateway, "/mindmap/x", "made_up", headers=FETCH)
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (503, "app_unavailable")


def test_the_pages_of_a_refusal_lead_to_the_list_of_apps(client, gateway, registry, fake_runtime):
    # The root of the address is the app center (test_gateway_center.py).
    add_app(registry, fake_runtime)
    for reason in ("no_access", "app_disabled", "app_not_found"):
        page = turn_away(client, gateway, "/mindmap/", reason)
        assert '<a class="button" href="/">All apps</a>' in page.text, reason
        assert 'href="https://efp.example.com"' in page.text
    # "home" was a reason once; it is no longer one, so it means what every unknown reason means.
    assert turn_away(client, gateway, "/", "home", headers=FETCH).status_code == 503


def test_a_path_that_is_no_app_is_not_found(client, gateway):
    page = turn_away(client, gateway, "/favicon.ico", "app_not_found")
    assert page.status_code == 404 and "There is no app at this address" in page.text
    error = turn_away(client, gateway, "/favicon.ico", "app_not_found", headers=FETCH)
    assert (error.status_code, error.json()["error"]["code"]) == (404, "app_not_found")
    # No app to sign in to: there is nowhere to redirect.
    lost = turn_away(client, gateway, "/favicon.ico", "sign_in_required")
    assert lost.status_code == 404


@pytest.mark.parametrize(
    "accept_language, lang, text",
    [
        ("en-GB,en;q=0.9", "en", "You do not have access to Mind map"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh-Hans", "\u4f60\u65e0\u6743\u8bbf\u95ee Mind map"),
        ("zh-HK,zh;q=0.9", "zh-Hant-HK", "\u4f60\u7121\u6b0a\u5b58\u53d6 Mind map"),
    ],
)
def test_pages_follow_accept_language(client, gateway, registry, fake_runtime, accept_language, lang, text):
    add_app(registry, fake_runtime)
    response = turn_away(client, gateway, "/mindmap/", "no_access", headers={**NAVIGATE, "Accept-Language": accept_language})
    assert f'<html lang="{lang}">' in response.text
    assert text in response.text
    assert "charset=utf-8" in response.headers["content-type"]


# ---- /_efp/callback ------------------------------------------------------------------
def handoff(fake_portal, code: str = "c1", slug: str = "mindmap", app_id: str = "app-1") -> None:
    fake_portal.codes[code] = {"app_id": app_id, "slug": slug, "user": BOB.to_json()}


def test_callback_starts_the_session_and_returns_to_the_app(client, gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    handoff(fake_portal)
    response = client.get("/_efp/callback", params={"code": "c1", "next": "/mindmap/board?id=3&x=a+b"})
    assert response.status_code == 302
    assert response.headers["location"] == "/mindmap/board?id=3&x=a+b"
    assert response.headers["cache-control"] == "no-store"

    cookie = response.headers["set-cookie"]
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    assert name_value.startswith("__Host-efp_apps=")
    assert set(attributes) == {"Path=/", f"Max-Age={168 * 3600}", "HttpOnly", "Secure", "SameSite=Lax"}
    assert gateway.sessions.read(name_value.partition("=")[2]).visitor == BOB

    # The session works at once; the Portal just said this user may open the app.
    allowed = ask(client, gateway, "/mindmap/board?id=3", cookie=name_value)
    assert allowed.status_code == 200 and allowed.headers["x-efp-user"] == str(BOB.id)
    assert fake_portal.access_calls == 0


@pytest.mark.parametrize(
    "wanted",
    [
        None,
        "",
        "/",
        "/other/page",
        "/mindmapx/page",
        "/mindmap",
        "https://evil.example.com/mindmap/",
        "//evil.example.com/mindmap/",
        "/mindmap/../other/",
        "/mindmap/a/%2e%2e/%2E%2E/other/",
        "/mindmap/..%2fother/",
        "/mindmap/./x",
        "/mindmap/a\\b",
        "/mindmap/a b",
        "/mindmap/a\r\nSet-Cookie: x=1",
        "/mindmap/\u4e2d",
        "/mindmap/" + "a" * 5000,
    ],
)
def test_callback_only_returns_into_the_same_app(client, registry, fake_runtime, fake_portal, wanted):
    add_app(registry, fake_runtime)
    handoff(fake_portal)
    params = {"code": "c1"}
    if wanted is not None:
        params["next"] = wanted
    response = client.get("/_efp/callback", params=params)
    assert response.status_code == 302
    assert response.headers["location"] == "/mindmap/"
    assert "set-cookie" in response.headers


@pytest.mark.parametrize(
    "wanted",
    ["/mindmap/", "/mindmap/a/b.html", "/mindmap/a%20b?x=%2F..%2F", "/mindmap/?next=/other/", "/mindmap/x?a=1#top", "/mindmap/.hidden/..x"],
)
def test_safe_next_keeps_what_stays_inside_the_app(wanted):
    assert safe_next(wanted, "mindmap") == wanted


def test_callback_with_a_bad_code_answers_400_and_links_back(client, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    response = client.get("/_efp/callback", params={"code": "nope", "next": "/mindmap/board"}, headers=NAVIGATE)
    assert response.status_code == 400
    assert "Sign-in did not finish" in response.text
    assert 'href="/mindmap/"' in response.text
    assert "set-cookie" not in response.headers

    missing = client.get("/_efp/callback", params={"next": "/mindmap/"})
    assert missing.status_code == 400 and "set-cookie" not in missing.headers
    # Without a hint of the app the page still leads somewhere: the Portal.
    nowhere = client.get("/_efp/callback", params={"code": "nope"})
    assert nowhere.status_code == 400
    assert 'href="https://efp.example.com"' in nowhere.text and 'href="/' not in nowhere.text


def test_a_code_works_once(client, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    handoff(fake_portal)
    assert client.get("/_efp/callback", params={"code": "c1"}).status_code == 302
    assert client.get("/_efp/callback", params={"code": "c1"}).status_code == 400


def test_callback_when_the_portal_cannot_be_reached(client, registry, fake_runtime, fake_portal):
    from efp_app_host.portal_client import PortalUnavailable

    async def down(code: str):
        raise PortalUnavailable("portal down")

    fake_portal.redeem_handoff = down
    add_app(registry, fake_runtime)
    response = client.get("/_efp/callback", params={"code": "c1", "next": "/mindmap/"})
    assert response.status_code == 503
    assert 'href="/mindmap/"' in response.text and "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "answer",
    [
        {"app_id": "app-1", "slug": "mindmap"},
        {"app_id": "app-1", "slug": "mindmap", "user": {"username": "bob"}},
        {"app_id": "app-1", "slug": "../evil", "user": {"id": 12, "username": "bob"}},
        {"app_id": "app-1", "slug": "login", "user": {"id": 12, "username": "bob"}},
    ],
)
def test_callback_refuses_an_answer_it_cannot_use(client, fake_portal, answer):
    fake_portal.codes["c1"] = answer
    response = client.get("/_efp/callback", params={"code": "c1", "next": "/mindmap/"})
    assert response.status_code == 400 and "set-cookie" not in response.headers


# ---- /_efp/logout --------------------------------------------------------------------
def test_logout_clears_the_cookie_and_goes_to_the_portal(client, gateway):
    response = client.get("/_efp/logout", headers={"Cookie": session_cookie(gateway)})
    assert response.status_code == 302
    assert response.headers["location"] == "https://efp.example.com"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-efp_apps=;")
    for attribute in ("Max-Age=0", "Path=/", "HttpOnly", "Secure", "SameSite=Lax"):
        assert attribute in cookie


# ---- the whole way ---------------------------------------------------------------------
def test_sign_in_from_the_first_request_to_the_app(client, gateway, registry, fake_runtime, fake_portal):
    record = add_app(registry, fake_runtime, visibility="public")
    first = ask(client, gateway, "/mindmap/board?id=3")
    assert (first.status_code, first.headers["x-efp-reason"]) == (401, "sign_in_required")

    redirect = turn_away(client, gateway, "/mindmap/board?id=3", first.headers["x-efp-reason"])
    wanted = parse_qs(urlsplit(redirect.headers["location"]).query)["next"][0]

    # The Portal signs the visitor in and sends the browser back with a code.
    handoff(fake_portal, code="one-time")
    back = client.get("/_efp/callback", params={"code": "one-time", "next": wanted})
    assert (back.status_code, back.headers["location"]) == (302, "/mindmap/board?id=3")

    session = back.headers["set-cookie"].split(";")[0]
    final = ask(client, gateway, back.headers["location"], cookie=session)
    assert final.status_code == 200
    assert final.headers["x-efp-upstream"] == f"127.0.0.1:{record.port}"
    assert final.headers["x-efp-upstream-uri"] == "/board?id=3"
    assert "x-efp-cookie" not in final.headers
