"""docs/CONTRACTS.md, section 6.1, row by row, for the one function that decides."""

from __future__ import annotations

import dataclasses
from urllib.parse import parse_qs, urlsplit

import pytest

from efp_app_host.gateway import visitor as visitor_header
from efp_app_host.gateway.decision import (
    Proxy,
    Refusal,
    decide,
    is_navigation,
    page_for,
    parse_original_uri,
    refuse,
    upstream_uri,
)
from efp_app_host.gateway.sessions import COOKIE_NAME
from efp_app_host.models import (
    STATE_CRASHED,
    STATE_DISABLED,
    STATE_FAILED,
    STATE_KILLED,
    STATE_STARTING,
    STATE_STOPPED,
    STATE_UNDEPLOYED,
    Visitor,
)

from gateway_support import BOB, FETCH, NAVIGATION, VISITOR_KEY, Clock, add_app, allow, make_gateway, request, run, session_cookie

FORMS = pytest.mark.parametrize("headers, navigation", [(NAVIGATION, True), (FETCH, False)], ids=["navigation", "fetch"])


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def gateway(settings, registry, fake_runtime, fake_portal, clock):
    return make_gateway(settings, registry, fake_runtime, fake_portal, clock=clock)


def refused(result, *, code: str, navigation: bool, status: int, page: str | None) -> Refusal:
    """Check the form of a refusal: a page for a navigation, JSON otherwise."""

    assert isinstance(result, Refusal), result
    assert result.code == code
    assert result.navigation is navigation
    assert result.status == status
    assert result.redirect_to is None
    if navigation:
        assert (result.kind, result.page) == ("page", page)
    else:
        assert (result.kind, result.page) == ("json", None)
    return result


# ---- unknown slug -------------------------------------------------------------
@FORMS
def test_unknown_slug_is_not_found(gateway, fake_runtime, headers, navigation):
    result = run(decide(gateway, request("/nothing/", slug="nothing", headers=headers)))
    refused(result, code="app_not_found", navigation=navigation, status=404, page="not_found")
    assert fake_runtime.started == [] and fake_runtime.touched == []


@pytest.mark.parametrize(
    "slug, path",
    [
        ("", "/"),
        ("ab", "/ab/"),  # too short to be a slug
        ("login", "/login/"),  # reserved
        ("Mindmap", "/Mindmap/"),
        ("mindmap", "/other/x"),  # the path is not under the slug
        ("mindmap", "/mindmap"),  # nothing follows the slug
        ("mindmap", "/%6dindmap/x"),
    ],
)
def test_a_path_that_does_not_name_an_app_is_not_found(gateway, registry, fake_runtime, slug, path):
    add_app(registry, fake_runtime, visibility="anyone")
    result = run(decide(gateway, request(path, slug=slug)))
    refused(result, code="app_not_found", navigation=True, status=404, page="not_found")


# ---- disabled -----------------------------------------------------------------
@FORMS
def test_a_disabled_app_is_refused_before_anything_else(gateway, registry, fake_runtime, fake_portal, headers, navigation):
    add_app(registry, fake_runtime, enabled=False, disabled_reason="Leaked customer data")
    # Neither a missing session nor a valid one changes the answer.
    for cookies in ("", session_cookie(gateway)):
        result = run(decide(gateway, request(cookies=cookies, headers=headers)))
        refused(result, code="app_disabled", navigation=navigation, status=403, page="disabled")
    assert fake_portal.access_calls == 0
    assert fake_runtime.started == [] and fake_runtime.touched == []


def test_a_disabled_app_that_anyone_may_open_is_refused_too(gateway, registry, fake_runtime):
    add_app(registry, fake_runtime, visibility="anyone", enabled=False)
    refused(run(decide(gateway, request())), code="app_disabled", navigation=True, status=403, page="disabled")


# ---- sign-in ------------------------------------------------------------------
@pytest.mark.parametrize("visibility", ["private", "public", "platform"])
def test_a_navigation_without_session_goes_to_the_launch_page(gateway, registry, fake_runtime, fake_portal, visibility):
    add_app(registry, fake_runtime, visibility=visibility)
    result = run(decide(gateway, request("/mindmap/board%20one", query="id=3&x=a+b")))
    assert isinstance(result, Refusal)
    assert (result.code, result.kind, result.status, result.page) == ("sign_in_required", "redirect", 302, None)
    target = urlsplit(result.redirect_to)
    assert f"{target.scheme}://{target.netloc}{target.path}" == "https://efp.example.com/apps/launch/mindmap"
    # ``next`` is the path and query, url-encoded once: decoding gives them back.
    assert parse_qs(target.query, keep_blank_values=True) == {"next": ["/mindmap/board%20one?id=3&x=a+b"]}
    assert "next=%2Fmindmap%2Fboard%2520one%3Fid%3D3%26x%3Da%2Bb" in result.redirect_to
    assert fake_portal.access_calls == 0 and fake_runtime.touched == []


@pytest.mark.parametrize("visibility", ["private", "public", "platform"])
def test_a_fetch_without_session_gets_401(gateway, registry, fake_runtime, visibility):
    add_app(registry, fake_runtime, visibility=visibility)
    result = run(decide(gateway, request(headers=FETCH)))
    refused(result, code="sign_in_required", navigation=False, status=401, page=None)


@pytest.mark.parametrize("kind", ["garbage", "expired", "revoked", "other_key"])
def test_a_session_that_is_not_valid_counts_as_none(gateway, registry, fake_runtime, fake_portal, clock, kind, tmp_path):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    cookies = session_cookie(gateway)
    if kind == "garbage":
        cookies = f"{COOKIE_NAME}=s1.e30.e30"
    elif kind == "expired":
        clock.advance(gateway.sessions.ttl_seconds)
    elif kind == "revoked":
        clock.advance(5)
        registry.revoke_user(BOB.id, at=clock.now)
    else:
        from efp_app_host.gateway.sessions import SessionCodec

        cookies = f"{COOKIE_NAME}={SessionCodec(b'x' * 32, 3600, clock=clock).issue(BOB)}"
    result = run(decide(gateway, request(cookies=cookies, headers=FETCH)))
    refused(result, code="sign_in_required", navigation=False, status=401, page=None)
    assert fake_portal.access_calls == 0


# ---- a session whose user is not allowed ----------------------------------------
@FORMS
def test_a_user_without_access_is_refused(gateway, registry, fake_runtime, fake_portal, headers, navigation):
    add_app(registry, fake_runtime)
    result = run(decide(gateway, request(cookies=session_cookie(gateway), headers=headers)))
    refused(result, code="no_access", navigation=navigation, status=403, page="no_access")
    assert fake_portal.access_calls == 1
    assert fake_runtime.started == [] and fake_runtime.touched == []


@pytest.mark.parametrize(
    "reason, code, status, page",
    [
        ("no_access", "no_access", 403, "no_access"),
        ("user_inactive", "no_access", 403, "no_access"),
        ("app_disabled", "app_disabled", 403, "disabled"),
        ("app_not_found", "app_not_found", 404, "not_found"),
        (None, "no_access", 403, "no_access"),
    ],
)
def test_the_portals_reason_chooses_the_refusal(gateway, registry, fake_runtime, fake_portal, reason, code, status, page):
    add_app(registry, fake_runtime)
    fake_portal.access[("app-1", BOB.id)] = (False, reason)
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    refused(result, code=code, navigation=True, status=status, page=page)


@FORMS
def test_portal_down_and_nothing_remembered_fails_closed(gateway, registry, fake_runtime, fake_portal, headers, navigation):
    add_app(registry, fake_runtime)
    fake_portal.fail = True
    result = run(decide(gateway, request(cookies=session_cookie(gateway), headers=headers)))
    refused(result, code="app_unavailable", navigation=navigation, status=503, page="unavailable")
    assert result.detail == "portal"
    assert fake_runtime.started == [] and fake_runtime.touched == []


def test_portal_down_with_a_recent_answer_still_proxies(gateway, registry, fake_runtime, fake_portal, clock):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    cookies = session_cookie(gateway)
    assert isinstance(run(decide(gateway, request(cookies=cookies))), Proxy)

    fake_portal.fail = True
    clock.advance(200)
    assert isinstance(run(decide(gateway, request(cookies=cookies))), Proxy)
    clock.advance(200)  # the answer is now older than five minutes
    result = run(decide(gateway, request(cookies=cookies)))
    refused(result, code="app_unavailable", navigation=True, status=503, page="unavailable")


# ---- never deployed -------------------------------------------------------------
@FORMS
def test_an_app_without_release_is_unavailable(gateway, registry, fake_runtime, fake_portal, headers, navigation):
    add_app(registry, fake_runtime, deployed=False)
    allow(fake_portal)
    result = run(decide(gateway, request(cookies=session_cookie(gateway), headers=headers)))
    refused(result, code="app_unavailable", navigation=navigation, status=503, page="not_deployed")
    assert result.detail == "not_deployed"
    assert fake_runtime.started == [] and fake_runtime.touched == []


def test_access_is_checked_before_the_state_of_the_app_is_told(gateway, registry, fake_runtime):
    add_app(registry, fake_runtime, deployed=False)
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    refused(result, code="no_access", navigation=True, status=403, page="no_access")


# ---- stopped: start it ------------------------------------------------------------
@FORMS
def test_a_stopped_app_is_started_and_answers_starting(gateway, registry, fake_runtime, fake_portal, headers, navigation):
    add_app(registry, fake_runtime, running=False)
    allow(fake_portal)
    assert fake_runtime.status("app-1").state == STATE_STOPPED
    result = run(decide(gateway, request(cookies=session_cookie(gateway), headers=headers)))
    refused(result, code="app_starting", navigation=navigation, status=503, page="starting")
    assert result.retry_after == 2
    assert fake_runtime.started == ["app-1"]
    assert fake_runtime.touched == []

    # While it comes up the answer stays the same and nothing is started twice.
    again = run(decide(gateway, request(cookies=session_cookie(gateway), headers=headers)))
    refused(again, code="app_starting", navigation=navigation, status=503, page="starting")
    assert fake_runtime.started == ["app-1"]

    fake_runtime.mark_running("app-1")
    assert isinstance(run(decide(gateway, request(cookies=session_cookie(gateway), headers=headers))), Proxy)


@pytest.mark.parametrize("state", [STATE_CRASHED, STATE_KILLED, STATE_FAILED])
@FORMS
def test_an_app_that_cannot_run_is_unavailable(gateway, registry, fake_runtime, fake_portal, state, headers, navigation):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    fake_runtime.set_state("app-1", state, "exit:1")
    result = run(decide(gateway, request(cookies=session_cookie(gateway), headers=headers)))
    refused(result, code="app_unavailable", navigation=navigation, status=503, page="crashed")
    assert result.detail == state
    assert fake_runtime.touched == []


def test_an_app_that_is_already_starting_is_not_started_again(gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    fake_runtime.set_state("app-1", STATE_STARTING)
    result = run(decide(gateway, request(cookies=session_cookie(gateway), headers=FETCH)))
    refused(result, code="app_starting", navigation=False, status=503, page=None)
    assert result.retry_after == 2
    assert fake_runtime.started == [] and fake_runtime.touched == []


@pytest.mark.parametrize("state, detail, page", [(STATE_UNDEPLOYED, "not_deployed", "not_deployed"), (STATE_STOPPED, "stopped", "unavailable")])
def test_whatever_else_the_runtime_answers_is_unavailable(gateway, registry, fake_runtime, fake_portal, state, detail, page):
    add_app(registry, fake_runtime)
    allow(fake_portal)

    async def cannot_start(app_id: str):
        return dataclasses.replace(fake_runtime.status(app_id), state=state)

    fake_runtime.ensure_running = cannot_start
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    refused(result, code="app_unavailable", navigation=True, status=503, page=page)
    assert result.detail == detail
    assert fake_runtime.touched == []


def test_an_app_the_runtime_calls_disabled_is_refused_as_disabled(gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    fake_runtime.set_state("app-1", STATE_DISABLED, "taken down")
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    refused(result, code="app_disabled", navigation=True, status=403, page="disabled")


# ---- proxy ------------------------------------------------------------------------
def test_a_running_app_gets_the_request(gateway, registry, fake_runtime, fake_portal, clock):
    record = add_app(registry, fake_runtime)
    allow(fake_portal)
    cookies = f"theme=dark; {session_cookie(gateway)}; sid=abc"
    result = run(decide(gateway, request("/mindmap/board", query="id=3", cookies=cookies, headers=FETCH)))

    assert isinstance(result, Proxy)
    assert (result.app_id, result.slug, result.kind) == ("app-1", "mindmap", "proxy")
    assert result.upstream == f"127.0.0.1:{record.port}"
    assert result.uri == "/board?id=3"
    assert result.cookie == "theme=dark; sid=abc"
    assert result.user_id == BOB.id
    assert result.static_root is None
    assert result.set_cookie is None
    body = visitor_header.verify(result.visitor_header, VISITOR_KEY, "app-1", now=clock.now)
    assert body is not None
    assert (body["sub"], body["username"], body["name"], body["group"], body["group_id"]) == (12, "bob", "Bob", "QA", 3)
    assert body["exp"] == int(clock.now) + 60
    assert fake_runtime.touched == ["app-1"]
    assert fake_runtime.started == []


def test_nothing_is_left_of_a_cookie_header_that_only_held_the_session(gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy) and result.cookie == ""


@pytest.mark.parametrize("visibility", ["private", "public", "platform", "anyone"])
def test_every_visibility_lets_an_allowed_member_in_with_identity(gateway, registry, fake_runtime, fake_portal, visibility, clock):
    add_app(registry, fake_runtime, visibility=visibility)
    allow(fake_portal)
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy)
    assert result.user_id == BOB.id
    assert visitor_header.verify(result.visitor_header, VISITOR_KEY, "app-1", now=clock.now)["username"] == "bob"


@pytest.mark.parametrize(
    "app_type, manifest, path, query, expected",
    [
        ("fastapi", {}, "/mindmap/api/items", "a=1", "/api/items?a=1"),
        ("fastapi", {}, "/mindmap/", None, "/"),
        ("gradio", {}, "/mindmap/queue/join", None, "/queue/join"),
        ("flask", {}, "/mindmap/api/items", "a=1", "/mindmap/api/items?a=1"),
        ("streamlit", {}, "/mindmap/_stcore/stream", None, "/mindmap/_stcore/stream"),
        ("command", {}, "/mindmap/", "", "/mindmap/?"),
        # The manifest overrides the default of the type, both ways.
        ("fastapi", {"strip_prefix": False}, "/mindmap/x", None, "/mindmap/x"),
        ("flask", {"strip_prefix": True}, "/mindmap/x", "q=%2F", "/x?q=%2F"),
        # The path goes on exactly as it came: no decoding, no tidying.
        ("fastapi", {}, "/mindmap/a%2Fb/../c//d", None, "/a%2Fb/../c//d"),
        ("fastapi", {}, "/mindmap/mindmap/x", None, "/mindmap/x"),
    ],
)
def test_the_prefix_is_kept_or_removed(gateway, registry, fake_runtime, fake_portal, app_type, manifest, path, query, expected):
    add_app(registry, fake_runtime, type=app_type, manifest=manifest)
    allow(fake_portal)
    result = run(decide(gateway, request(path, query=query, cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy)
    assert result.uri == expected


def test_upstream_uri():
    assert upstream_uri("mindmap", "/mindmap/a", None, strip_prefix=True) == "/a"
    assert upstream_uri("mindmap", "/mindmap/a", "x=1", strip_prefix=False) == "/mindmap/a?x=1"


def test_an_upstream_without_port_is_not_used(gateway, registry, fake_runtime, fake_portal):
    record = add_app(registry, fake_runtime)
    allow(fake_portal)
    record.port = 0
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    refused(result, code="app_unavailable", navigation=True, status=503, page="unavailable")
    assert fake_runtime.touched == []


# ---- static apps --------------------------------------------------------------------
def test_a_static_app_is_served_by_the_gateway_itself(gateway, settings, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, type="static")
    allow(fake_portal)
    fake_runtime.static_roots["app-1"] = "/var/lib/efp-host/store/apps/app-1/releases/s1/code/"
    result = run(decide(gateway, request("/mindmap/docs/index.html", query="v=2", cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy)
    assert result.kind == "static"
    # nginx's own file server, on the gateway port; it takes the public path.
    assert result.upstream == f"127.0.0.1:{settings.gateway_port}"
    assert result.uri == "/mindmap/docs/index.html?v=2"
    assert result.static_root == "/var/lib/efp-host/store/apps/app-1/releases/s1/code"
    assert result.user_id == BOB.id
    assert fake_runtime.touched == ["app-1"]


def test_a_static_app_needs_the_same_access_as_any_other(gateway, registry, fake_runtime):
    add_app(registry, fake_runtime, type="static")
    result = run(decide(gateway, request("/mindmap/index.html")))
    assert isinstance(result, Refusal) and result.code == "sign_in_required"
    result = run(decide(gateway, request("/mindmap/index.html", cookies=session_cookie(gateway))))
    assert isinstance(result, Refusal) and result.code == "no_access"


@pytest.mark.parametrize("root", [None, "", "/", "relative/dir", "/srv/../etc", "/srv/a\nb"])
def test_a_static_directory_that_is_not_plain_is_never_named(gateway, registry, fake_runtime, fake_portal, root):
    add_app(registry, fake_runtime, type="static")
    allow(fake_portal)
    fake_runtime.static_roots["app-1"] = root
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    refused(result, code="app_unavailable", navigation=True, status=503, page="unavailable")


# ---- apps anyone may open -----------------------------------------------------------
def test_anyone_needs_no_session(gateway, registry, fake_runtime, fake_portal):
    record = add_app(registry, fake_runtime, visibility="anyone")
    result = run(decide(gateway, request("/mindmap/x", cookies="theme=dark", headers=FETCH)))
    assert isinstance(result, Proxy)
    assert result.upstream == f"127.0.0.1:{record.port}"
    assert (result.visitor_header, result.user_id, result.set_cookie) == (None, None, None)
    assert result.cookie == "theme=dark"
    assert fake_portal.access_calls == 0
    assert fake_runtime.touched == ["app-1"]


def test_anyone_with_a_session_the_portal_does_not_confirm_stays_anonymous(gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, visibility="anyone")
    fake_portal.access[("app-1", BOB.id)] = (False, "user_inactive")
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy)
    assert (result.visitor_header, result.user_id, result.set_cookie) == (None, None, None)
    assert result.cookie == ""  # the session cookie still never reaches the app


def test_anyone_stays_open_while_the_portal_is_down(gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, visibility="anyone")
    fake_portal.fail = True
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy)
    assert (result.visitor_header, result.user_id) == (None, None)


def test_a_stopped_app_anyone_may_open_is_started(gateway, registry, fake_runtime):
    add_app(registry, fake_runtime, visibility="anyone", running=False)
    result = run(decide(gateway, request(headers=FETCH)))
    refused(result, code="app_starting", navigation=False, status=503, page=None)
    assert fake_runtime.started == ["app-1"]


@FORMS
def test_anyone_is_limited_per_source_address(settings, registry, fake_runtime, fake_portal, clock, headers, navigation):
    gateway = make_gateway(dataclasses.replace(settings, anon_rate_per_minute=3), registry, fake_runtime, fake_portal, clock=clock)
    add_app(registry, fake_runtime, visibility="anyone")
    for _ in range(3):
        assert isinstance(run(decide(gateway, request(headers=headers, source="203.0.113.7"))), Proxy)
    result = run(decide(gateway, request(headers=headers, source="203.0.113.7")))
    refused(result, code="rate_limited", navigation=navigation, status=429, page="rate_limited")
    assert result.retry_after == 20
    assert len(fake_runtime.touched) == 3

    # Another address has its own allowance; the first one gets more with time.
    assert isinstance(run(decide(gateway, request(headers=headers, source="203.0.113.8"))), Proxy)
    clock.advance(20)
    assert isinstance(run(decide(gateway, request(headers=headers, source="203.0.113.7"))), Proxy)
    assert isinstance(run(decide(gateway, request(headers=headers, source="203.0.113.7"))), Refusal)


def test_the_limit_does_not_wake_a_stopped_app(settings, registry, fake_runtime, fake_portal, clock):
    gateway = make_gateway(dataclasses.replace(settings, anon_rate_per_minute=1), registry, fake_runtime, fake_portal, clock=clock)
    add_app(registry, fake_runtime, visibility="anyone")
    assert isinstance(run(decide(gateway, request())), Proxy)
    fake_runtime.set_state("app-1", STATE_STOPPED)
    assert run(decide(gateway, request())).code == "rate_limited"
    assert fake_runtime.started == []


def test_apps_that_need_sign_in_are_not_limited(settings, registry, fake_runtime, fake_portal, clock):
    gateway = make_gateway(dataclasses.replace(settings, anon_rate_per_minute=1), registry, fake_runtime, fake_portal, clock=clock)
    add_app(registry, fake_runtime, visibility="platform")
    allow(fake_portal)
    for _ in range(5):
        assert isinstance(run(decide(gateway, request(cookies=session_cookie(gateway)))), Proxy)


# ---- the session on the way through ---------------------------------------------------
def test_the_session_cookie_is_renewed_after_a_tenth_of_its_lifetime(gateway, registry, fake_runtime, fake_portal, clock):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    cookies = session_cookie(gateway)
    clock.advance(gateway.sessions.ttl_seconds / 10)
    assert run(decide(gateway, request(cookies=cookies))).set_cookie is None

    clock.advance(1)
    result = run(decide(gateway, request(cookies=cookies)))
    assert isinstance(result, Proxy) and result.set_cookie is not None
    name_value = result.set_cookie.split(";")[0]
    assert name_value.startswith(f"{COOKIE_NAME}=")
    renewed = gateway.sessions.read(name_value.partition("=")[2])
    assert renewed.visitor == BOB
    assert renewed.issued_at == clock.now
    assert renewed.expires_at == clock.now + gateway.sessions.ttl_seconds
    for attribute in ("Path=/", "HttpOnly", "Secure", "SameSite=Lax"):
        assert attribute in result.set_cookie


def test_name_and_group_follow_the_portal(gateway, registry, fake_runtime, fake_portal, clock):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    cookies = session_cookie(gateway)
    moved = Visitor(id=BOB.id, username="bob", display_name="Robert", group_id=4, group_name="Ops")
    fake_portal.users[BOB.id] = moved

    result = run(decide(gateway, request(cookies=cookies)))
    assert isinstance(result, Proxy)
    body = visitor_header.verify(result.visitor_header, VISITOR_KEY, "app-1", now=clock.now)
    assert (body["name"], body["group"], body["group_id"]) == ("Robert", "Ops", 4)
    # The cookie is rewritten with what is true now.
    assert result.set_cookie is not None
    assert gateway.sessions.read(result.set_cookie.split(";")[0].partition("=")[2]).visitor == moved


def test_an_answer_about_somebody_else_never_changes_the_identity(gateway, registry, fake_runtime, fake_portal, clock):
    add_app(registry, fake_runtime)
    allow(fake_portal)
    fake_portal.users[BOB.id] = Visitor(id=1, username="admin", display_name="Admin")
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy)
    assert visitor_header.verify(result.visitor_header, VISITOR_KEY, "app-1", now=clock.now)["username"] == "bob"
    assert result.user_id == BOB.id and result.set_cookie is None


def test_an_app_without_visitor_key_gets_no_header(gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, visitor_key="")
    allow(fake_portal)
    result = run(decide(gateway, request(cookies=session_cookie(gateway))))
    assert isinstance(result, Proxy)
    assert result.visitor_header is None and result.user_id == BOB.id


# ---- the small parts ------------------------------------------------------------------
@pytest.mark.parametrize(
    "headers, expected",
    [
        ({"accept": "text/html,*/*", "sec-fetch-mode": "navigate"}, True),
        ({"accept": "text/html"}, True),  # an old browser sends no Sec-Fetch-Mode
        ({"accept": "TEXT/HTML", "sec-fetch-mode": "Navigate"}, True),
        ({"accept": "text/html", "sec-fetch-mode": "cors"}, False),
        ({"accept": "text/html", "sec-fetch-mode": "websocket"}, False),
        ({"accept": "*/*", "sec-fetch-mode": "navigate"}, False),
        ({"accept": "application/json"}, False),
        ({"accept": "", "sec-fetch-mode": ""}, False),
        ({}, False),
    ],
)
def test_what_counts_as_a_navigation(headers, expected):
    assert is_navigation(headers) is expected


@pytest.mark.parametrize(
    "uri, expected",
    [
        ("/mindmap/", ("mindmap", "/mindmap/", None)),
        ("/mindmap/a/b?x=1&y=2", ("mindmap", "/mindmap/a/b", "x=1&y=2")),
        ("/mindmap/?", ("mindmap", "/mindmap/", "")),
        ("/mindmap/a?b?c", ("mindmap", "/mindmap/a", "b?c")),
        ("/mindmap", ("", "/mindmap", None)),
        ("/", ("", "/", None)),
        ("", ("", "", None)),
        (None, ("", "", None)),
        ("/_efp/callback?code=1", ("", "/_efp/callback", "code=1")),
        ("/login/x", ("", "/login/x", None)),
        ("mindmap/x", ("", "mindmap/x", None)),
    ],
)
def test_the_request_target_is_taken_apart(uri, expected):
    assert parse_original_uri(uri) == expected


def test_refuse_falls_back_for_what_it_does_not_know():
    odd = refuse("something_new", portal_public_url="https://efp.example.com", headers=FETCH)
    assert (odd.code, odd.status, odd.detail) == ("app_unavailable", 503, "gateway")
    # A sign-in redirect needs an app to come back to.
    lost = refuse("sign_in_required", portal_public_url="https://efp.example.com", headers=NAVIGATION)
    assert (lost.code, lost.status, lost.page) == ("app_not_found", 404, "not_found")
    # The detail is a plain word, whatever was passed in.
    assert refuse("app_unavailable", portal_public_url="", detail="a b\r\nX: y", headers=FETCH).detail == "abX:y"


def test_page_for():
    assert page_for("app_unavailable", "crashed") == "crashed"
    assert page_for("app_unavailable", "not_deployed") == "not_deployed"
    assert page_for("app_unavailable", "stopped") == "unavailable"
    assert page_for("app_unavailable") == "unavailable"
    assert page_for("app_starting") == "starting"
    assert page_for("rate_limited") == "rate_limited"
