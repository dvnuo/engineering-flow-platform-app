"""The app center: the page at the root of the apps address (docs/CONTRACTS.md, section 6.5)."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from efp_app_host.app import create_app
from efp_app_host.gateway.center import (
    ICON_FRESH_SECONDS,
    ICON_MAX_BYTES,
    WORDS,
    CatalogError,
    CenterRenderer,
    IconCache,
    hue_of,
    initial_of,
    parse_catalog,
    version_of,
)
from efp_app_host.gateway.pages import LOCALES
from efp_app_host.gateway.resources import pages_dir
from efp_app_host.gateway.sessions import COOKIE_NAME

from gateway_support import BOB, Clock, add_app, run, session_cookie

NAVIGATE = {"Accept": "text/html,application/xhtml+xml", "Sec-Fetch-Mode": "navigate"}
FETCH = {"Accept": "application/json", "Sec-Fetch-Mode": "cors"}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def entry(slug: str = "mindmap", **changes):
    item = {
        "id": f"id-{slug}",
        "slug": slug,
        "name": "Mind map",
        "description": "Draw and share mind maps.",
        "type": "fastapi",
        "visibility": "public",
        "mine": False,
        "owner": {"username": "alice", "display_name": "Alice"},
        "icon": None,
        "last_visited_at": None,
    }
    item.update(changes)
    return item


def catalog_of(*apps, user=None):
    return {"allowed": True, "reason": None, "user": (user or BOB).to_json(), "apps": list(apps)}


# ---- reading the Portal's answer -----------------------------------------------------
def test_a_catalog_is_read_into_what_the_page_needs():
    catalog = parse_catalog(catalog_of(entry(icon="5f2c9a81d3e0"), entry("board", name="Board", mine=True, visibility="private")))
    assert catalog.allowed and catalog.visitor == BOB
    first, second = catalog.apps
    assert (first.id, first.slug, first.name, first.owner, first.icon) == ("id-mindmap", "mindmap", "Mind map", "Alice", "5f2c9a81d3e0")
    assert first.description == "Draw and share mind maps." and first.mine is False and first.visibility == "public"
    assert second.mine is True and second.visibility == "private" and second.icon is None


def test_a_member_who_is_no_longer_one_gets_no_list():
    catalog = parse_catalog({"allowed": False, "reason": "user_inactive", "apps": []})
    assert (catalog.allowed, catalog.reason, catalog.visitor, catalog.apps) == (False, "user_inactive", None, ())


@pytest.mark.parametrize(
    "data",
    [None, [], "x", {"allowed": True}, {"allowed": True, "user": {"username": "bob"}, "apps": []}, {"allowed": True, "user": BOB.to_json(), "apps": "none"}],
)
def test_an_answer_that_is_no_catalog_is_refused(data):
    with pytest.raises(CatalogError):
        parse_catalog(data)


def test_entries_the_page_cannot_use_are_left_out_and_text_is_tidied():
    catalog = parse_catalog(
        catalog_of(
            entry("../evil"),
            entry("login"),  # a reserved word is no slug
            entry("UPPER"),
            entry("ok", id=""),
            entry("ok", id=5),
            "not an object",
            entry("good", name="  Two\n lines  ", description="x" * 900, owner={"username": "eve"}, icon="../../etc", visibility="everyone", mine="yes"),
            entry("good", name="A second app with the same address"),
            entry("bare", name="", description=None, owner=None),
        )
    )
    assert [app.slug for app in catalog.apps] == ["good", "bare"]
    good, bare = catalog.apps
    assert good.name == "Two lines" and len(good.description) == 500 and good.owner == "eve"
    assert good.icon is None and good.visibility == "" and good.mine is False
    assert (bare.name, bare.description, bare.owner) == ("bare", "", "")


def test_the_letter_and_the_colour_of_an_app_without_an_icon():
    assert initial_of("mind map") == "M" and initial_of("  42 things") == "4" and initial_of("\u601d\u7ef4\u5bfc\u56fe") == "\u601d"
    assert initial_of("---") == "?" and initial_of("") == "?"
    assert hue_of("mindmap") == hue_of("mindmap") and 0 <= hue_of("mindmap") < 360
    assert hue_of("mindmap") != hue_of("board")


# ---- the page ----------------------------------------------------------------------------
@pytest.fixture(scope="module")
def center() -> CenterRenderer:
    return CenterRenderer()


def test_the_page_lists_a_card_for_every_app(center):
    catalog = parse_catalog(
        catalog_of(
            entry(icon="5f2c9a81d3e0"),
            entry("board", name="Board", description="", mine=True, visibility="platform"),
        )
    )
    page = center.render(catalog, "en", portal_url="https://efp.example.com", nonce="n0nce")
    assert page.startswith("<!doctype html>") and '<html lang="en">' in page
    assert "{{" not in page and "}}" not in page
    assert "<h1>Apps</h1>" in page and '<span class="count">2 apps</span>' in page
    assert '<span class="who">Bob</span>' in page
    assert '<a href="https://efp.example.com">Portal</a>' in page and '<a href="/_efp/logout">Sign out</a>' in page

    cards = re.findall(r'<li><a class="card" href="([^"]+)" data-search="([^"]*)">(.*?)</a></li>', page, re.S)
    assert [card[0] for card in cards] == ["/mindmap/", "/board/"]  # the Portal's order is kept
    assert cards[0][1] == "mind map mindmap draw and share mind maps. alice"
    assert '<img class="icon" src="/_efp/icon/mindmap?v=5f2c9a81d3e0" alt=""' in cards[0][2]
    assert '<span class="owner">Alice</span>' in cards[0][2] and "badge" not in cards[0][2]
    # No icon: the first letter on a tile. One's own app says so, and who may open it.
    assert re.search(r'<span class="icon letter" style="--hue: \d+" aria-hidden="true">B</span>', cards[1][2])
    assert '<span class="badge">Yours</span><span class="audience">Every member</span>' in cards[1][2]
    assert '<span class="description">' not in cards[1][2]

    # One script, under the nonce; nothing is loaded from anywhere but this host.
    assert page.count("<script") == 1 and '<script nonce="n0nce">' in page
    assert re.findall(r'\bsrc="([^"]+)"', page) == ["/_efp/icon/mindmap?v=5f2c9a81d3e0"]
    assert not re.search(r"url\(|@import|<link\b|https?://(?!efp\.example\.com)", page)
    assert 'id="none" hidden' in page and "No app matches your search." in page


def test_the_page_without_apps_says_where_they_come_from(center):
    page = center.render(parse_catalog(catalog_of()), "en", portal_url="https://efp.example.com", nonce="n")
    assert "No apps yet" in page and "Apps that are shared with you appear here." in page
    assert '<a class="button" href="https://efp.example.com">Portal</a>' in page
    assert '<div class="tools" hidden>' in page and '<ul class="cards"></ul>' in page
    assert '<span class="count">0 apps</span>' in page
    assert '<span class="count">1 app</span>' in center.render(parse_catalog(catalog_of(entry())), "en")


def test_what_the_portal_sends_is_escaped(center):
    hostile = entry(
        name='<img src=x onerror="alert(1)">',
        description="</a><script>alert(2)</script> & more",
        owner={"username": "eve", "display_name": '"><b>Eve</b>'},
    )
    user = BOB.to_json()
    user["display_name"] = "<i>Bob</i>"
    page = center.render(parse_catalog({"allowed": True, "user": user, "apps": [hostile]}), "en", portal_url="javascript:alert(3)", nonce='"><script>')
    assert page.count("<script") == 1 and "<img src=x" not in page and "<b>Eve" not in page and "<i>Bob" not in page
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page
    assert "&lt;/a&gt;&lt;script&gt;alert(2)&lt;/script&gt; &amp; more" in page
    assert "javascript:" not in page  # an address that is no web address gives no link
    assert 'nonce="&quot;&gt;&lt;script&gt;"' in page


@pytest.mark.parametrize("locale", LOCALES)
def test_the_page_renders_in_every_locale(center, locale):
    page = center.render(parse_catalog(catalog_of(entry(mine=True, visibility="anyone"))), locale, portal_url="https://efp.example.com", nonce="n")
    assert f'<html lang="{locale}">' in page and "{{" not in page
    for name in ("heading", "search", "mine", "audience_anyone", "sign_out", "no_match"):
        assert center.word(locale, name) in page.replace("&quot;", '"'), name


@pytest.mark.parametrize("locale", ["zh-Hans", "zh-Hant-HK"])
def test_the_chinese_words_of_the_center_are_complete(locale):
    with open(pages_dir() / "en.json", "r", encoding="utf-8") as handle:
        english = json.load(handle)["center"]
    with open(pages_dir() / f"{locale}.json", "r", encoding="utf-8") as handle:
        translated = json.load(handle)["center"]
    assert set(english) == set(translated) == set(WORDS)
    for name, text in english.items():
        if name in ("portal",):
            continue  # a name, the same everywhere
        assert re.search(r"[\u4e00-\u9fff]", translated[name]), name
        assert set(re.findall(r"\{[a-z_]+\}", translated[name])) == set(re.findall(r"\{[a-z_]+\}", text)), name


# ---- the icons ---------------------------------------------------------------------------
class Icons:
    def __init__(self, found=(PNG, "image/png")):
        self.found = found
        self.calls = 0

    async def __call__(self, app_id: str):
        self.calls += 1
        if isinstance(self.found, Exception):
            raise self.found
        return self.found


def test_an_icon_is_asked_for_once_and_then_kept():
    clock = Clock(1000.0)
    cache, portal = IconCache(clock=clock), Icons()
    version = version_of(PNG)
    assert run(cache.get("app-1", version, portal)) == (PNG, "image/png")
    assert run(cache.get("app-1", version, portal)) == (PNG, "image/png")
    clock.advance(10 * ICON_FRESH_SECONDS)
    assert run(cache.get("app-1", version, portal)) == (PNG, "image/png")  # the named version is the one held
    assert portal.calls == 1

    # Without a version the icon is good for a while, then the Portal is asked again.
    assert run(cache.get("app-1", "", portal)) == (PNG, "image/png") and portal.calls == 2
    assert run(cache.get("app-1", "", portal)) == (PNG, "image/png") and portal.calls == 2


def test_a_new_version_is_fetched_but_not_over_and_over():
    clock = Clock(1000.0)
    cache, portal = IconCache(clock=clock), Icons()
    run(cache.get("app-1", version_of(PNG), portal))
    newer = PNG + b"\x01"
    portal.found = (newer, "image/png")
    clock.advance(60)
    assert run(cache.get("app-1", version_of(newer), portal)) == (newer, "image/png") and portal.calls == 2
    # A version the Portal does not have (made up, or already replaced) costs one call, not one per request.
    assert run(cache.get("app-1", "madeup", portal)) == (newer, "image/png") and portal.calls == 2
    clock.advance(6)
    assert run(cache.get("app-1", "madeup", portal)) == (newer, "image/png") and portal.calls == 3


def test_no_icon_and_a_bad_icon_are_remembered_as_none():
    clock = Clock(1000.0)
    cache, portal = IconCache(clock=clock), Icons(found=None)
    assert run(cache.get("app-1", "", portal)) is None
    assert run(cache.get("app-1", "abc", portal)) is None and portal.calls == 1
    clock.advance(ICON_FRESH_SECONDS + 1)
    for bad in ((b"<svg/>", "image/svg+xml"), (b"", "image/png"), (b"x" * (ICON_MAX_BYTES + 1), "image/png"), (PNG, "text/html")):
        portal.found = bad
        assert run(cache.get("app-2", "", portal)) is None
        cache.forget_app("app-2")


def test_an_icon_added_after_no_icon_shows_when_the_page_names_its_version():
    clock = Clock(1000.0)
    cache, portal = IconCache(clock=clock), Icons(found=None)
    assert run(cache.get("app-1", "", portal)) is None and portal.calls == 1
    portal.found = (PNG, "image/png")  # the owner publishes a release with an icon
    version = version_of(PNG)
    assert run(cache.get("app-1", version, portal)) is None and portal.calls == 1  # not asked again at once
    clock.advance(6)
    assert run(cache.get("app-1", version, portal)) == (PNG, "image/png") and portal.calls == 2


def test_a_failed_call_is_not_remembered_and_the_cache_has_a_ceiling():
    clock = Clock(1000.0)
    cache, portal = IconCache(max_bytes=100, clock=clock), Icons(found=RuntimeError("portal down"))
    with pytest.raises(RuntimeError):
        run(cache.get("app-1", "", portal))
    portal.found = (PNG, "image/png")
    assert run(cache.get("app-1", "", portal)) == (PNG, "image/png")  # asked again at once

    for number in range(2, 6):  # 40 bytes each: only two fit under 100
        run(cache.get(f"app-{number}", "", portal))
    calls = portal.calls
    assert run(cache.get("app-5", "", portal)) == (PNG, "image/png") and portal.calls == calls
    run(cache.get("app-1", "", portal))
    assert portal.calls == calls + 1  # the oldest had been dropped


# ---- the routes ---------------------------------------------------------------------------
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


def home(client, gateway, *, cookie: str = "", uri: str = "/", headers: dict[str, str] | None = None):
    """The root of the apps address, as nginx passes it on."""

    sent = {"X-EFP-Gateway-Secret": gateway.secret, "X-EFP-Original-URI": uri, **(NAVIGATE if headers is None else headers)}
    if cookie:
        sent["Cookie"] = cookie
    return client.get("/_efp/home", headers=sent)


def icon(client, gateway, slug: str, *, cookie: str = "", version: str = ""):
    sent = {"X-EFP-Gateway-Secret": gateway.secret}
    if cookie:
        sent["Cookie"] = cookie
    return client.get(f"/_efp/icon/{slug}", params={"v": version} if version else None, headers=sent)


@pytest.mark.parametrize("path", ["/_efp/home", "/_efp/icon/mindmap"])
@pytest.mark.parametrize("presented", [None, "", "wrong"])
def test_the_center_is_reached_through_nginx_only(client, gateway, registry, fake_runtime, fake_portal, path, presented):
    add_app(registry, fake_runtime)
    fake_portal.catalogs[BOB.id] = catalog_of(entry())
    headers = {**NAVIGATE, "Cookie": session_cookie(gateway)}
    if presented is not None:
        headers["X-EFP-Gateway-Secret"] = presented
    response = client.get(path, headers=headers)
    assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden"
    assert fake_portal.catalog_calls == 0 and fake_portal.icon_calls == 0


def test_a_visitor_without_a_session_is_sent_to_sign_in(client, gateway, fake_portal):
    response = home(client, gateway)
    assert response.status_code == 302
    assert response.headers["location"] == "https://efp.example.com/apps/launch"
    assert response.headers["cache-control"] == "no-store" and "set-cookie" not in response.headers
    assert fake_portal.catalog_calls == 0

    # Something that is not a page navigation gets the error as JSON, never a redirect.
    error = home(client, gateway, headers=FETCH)
    assert (error.status_code, error.json()["error"]["code"]) == (401, "sign_in_required")


def test_the_sign_in_of_the_center_comes_back_with_a_session(client, gateway, fake_portal):
    fake_portal.codes["c1"] = {"app_id": None, "slug": None, "user": BOB.to_json()}
    fake_portal.catalogs[BOB.id] = catalog_of(entry())
    back = client.get("/_efp/callback", params={"code": "c1", "next": "/"})
    assert (back.status_code, back.headers["location"]) == (302, "/?signed_in=1")
    cookie = back.headers["set-cookie"].split(";")[0]
    assert cookie.startswith(f"{COOKIE_NAME}=") and gateway.sessions.read(cookie.partition("=")[2]).visitor == BOB

    page = home(client, gateway, cookie=cookie, uri=back.headers["location"])
    assert page.status_code == 200 and 'href="/mindmap/"' in page.text

    # "next" plays no part for the center: whatever it says, the way back is the list.
    fake_portal.codes["c2"] = {"app_id": None, "slug": None, "user": BOB.to_json()}
    elsewhere = client.get("/_efp/callback", params={"code": "c2", "next": "https://evil.example.com/"})
    assert elsewhere.headers["location"] == "/?signed_in=1"


def test_a_browser_that_kept_no_cookie_is_not_sent_round_again(client, gateway):
    response = home(client, gateway, uri="/?signed_in=1")
    assert response.status_code == 400 and "Sign-in did not finish" in response.text
    assert 'href="/"' in response.text  # "Try again"
    assert "location" not in response.headers


def test_the_center_lists_the_members_apps(client, gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, "id-mindmap", "mindmap")
    fake_portal.catalogs[BOB.id] = catalog_of(entry(icon="5f2c9a81d3e0"), entry("board", name="Board", mine=True))
    response = home(client, gateway, cookie=session_cookie(gateway))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store" and response.headers["x-robots-tag"] == "noindex"
    assert re.findall(r'<a class="card" href="([^"]+)"', response.text) == ["/mindmap/", "/board/"]
    assert fake_portal.catalog_calls == 1

    policy = response.headers["content-security-policy"]
    nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", policy).group(1)
    assert f'<script nonce="{nonce}">' in response.text
    assert "default-src 'none'" in policy and "img-src 'self'" in policy and "frame-ancestors 'none'" in policy
    assert home(client, gateway, cookie=session_cookie(gateway)).headers["content-security-policy"] != policy  # a nonce per page

    # The Portal just said what this member may open: the first click costs no further call.
    sent = {"X-EFP-Gateway-Secret": gateway.secret, "X-EFP-Original-URI": "/mindmap/", "X-EFP-Method": "GET", "X-EFP-Source": "203.0.113.7", **NAVIGATE, "Cookie": session_cookie(gateway)}
    assert client.get("/_efp/auth", headers=sent).status_code == 200
    assert fake_portal.access_calls == 0


def test_the_center_follows_accept_language(client, gateway, fake_portal):
    fake_portal.catalogs[BOB.id] = catalog_of()
    page = home(client, gateway, cookie=session_cookie(gateway), headers={**NAVIGATE, "Accept-Language": "zh-CN,zh;q=0.9"})
    assert '<html lang="zh-Hans">' in page.text and "\u8fd8\u6ca1\u6709\u5e94\u7528" in page.text


def test_the_list_is_a_page_not_an_api(client, gateway, fake_portal):
    fake_portal.catalogs[BOB.id] = catalog_of(entry())
    response = home(client, gateway, cookie=session_cookie(gateway), headers=FETCH)
    assert (response.status_code, response.json()["error"]["code"]) == (404, "app_not_found")
    assert fake_portal.catalog_calls == 0


def test_the_center_renews_the_session_and_the_name(client, gateway, fake_portal):
    renamed = type(BOB)(id=BOB.id, username="bob", display_name="Robert", group_id=3, group_name="QA")
    fake_portal.catalogs[BOB.id] = catalog_of(user=renamed)
    response = home(client, gateway, cookie=session_cookie(gateway))
    assert '<span class="who">Robert</span>' in response.text
    cookie = response.headers["set-cookie"].split(";")[0]
    assert gateway.sessions.read(cookie.partition("=")[2]).visitor == renamed

    fake_portal.catalogs[BOB.id] = catalog_of()
    assert "set-cookie" not in home(client, gateway, cookie=session_cookie(gateway)).headers  # nothing changed, nothing to renew


def test_a_member_who_lost_access_is_signed_out_and_sent_to_the_portal(client, gateway, fake_portal):
    fake_portal.catalogs[BOB.id] = {"allowed": False, "reason": "user_inactive", "apps": []}
    response = home(client, gateway, cookie=session_cookie(gateway))
    assert (response.status_code, response.headers["location"]) == (302, "https://efp.example.com/apps/launch")
    assert response.headers["set-cookie"].startswith(f"{COOKIE_NAME}=;") and "Max-Age=0" in response.headers["set-cookie"]

    # A list that is somebody else's is not shown either.
    other = type(BOB)(id=99, username="eve", display_name="Eve", group_id=None, group_name="")
    fake_portal.catalogs[BOB.id] = catalog_of(entry(), user=other)
    assert home(client, gateway, cookie=session_cookie(gateway)).status_code == 302


def test_the_center_says_so_when_the_portal_cannot_be_asked(client, gateway, fake_portal):
    fake_portal.fail = True
    down = home(client, gateway, cookie=session_cookie(gateway))
    assert down.status_code == 503 and "The list of apps is not available right now" in down.text
    assert 'href="/"' in down.text and "set-cookie" not in down.headers
    fake_portal.fail = False
    fake_portal.catalogs[BOB.id] = {"allowed": True, "apps": []}  # an answer the page cannot use
    assert home(client, gateway, cookie=session_cookie(gateway)).status_code == 503


def test_an_icon_goes_to_a_visitor_who_may_open_the_app(client, gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, "id-mindmap", "mindmap")
    fake_portal.icons["id-mindmap"] = (PNG, "image/png")
    cookie = session_cookie(gateway)
    assert icon(client, gateway, "mindmap").status_code == 404  # no session
    assert icon(client, gateway, "mindmap", cookie=cookie).status_code == 404  # the Portal says no
    assert fake_portal.icon_calls == 0

    fake_portal.catalogs[BOB.id] = catalog_of(entry())
    home(client, gateway, cookie=cookie)  # the page was shown: its apps are the visitor's
    response = icon(client, gateway, "mindmap", cookie=cookie, version=version_of(PNG))
    assert response.status_code == 200 and response.content == PNG
    assert response.headers["content-type"] == "image/png" and response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, max-age=86400"
    assert icon(client, gateway, "mindmap", cookie=cookie, version=version_of(PNG)).status_code == 200
    assert fake_portal.icon_calls == 1

    for nowhere in ("nope", "login", "..", "MINDMAP"):
        assert icon(client, gateway, nowhere, cookie=cookie).status_code == 404
    assert icon(client, gateway, "mindmap", cookie=cookie).headers.get("cache-control") != "no-store"


def test_an_icon_that_is_missing_or_cannot_be_fetched_is_404(client, gateway, registry, fake_runtime, fake_portal):
    add_app(registry, fake_runtime, "id-mindmap", "mindmap", visibility="anyone")
    cookie = session_cookie(gateway)
    assert icon(client, gateway, "mindmap", cookie=cookie).status_code == 404  # the app has none
    add_app(registry, fake_runtime, "id-board", "board", visibility="anyone")
    fake_portal.fail = True
    assert icon(client, gateway, "board", cookie=cookie).status_code == 404
    fake_portal.fail = False
    fake_portal.icons["id-board"] = (PNG, "image/png")
    assert icon(client, gateway, "board", cookie=cookie).status_code == 200  # a failure was not remembered

    registry.upsert("id-board", slug="board", enabled=False, disabled_reason="Under review")
    assert icon(client, gateway, "board", cookie=cookie).status_code == 404


def test_the_whole_way_from_the_address_to_the_list(client, gateway, registry, fake_runtime, fake_portal):
    first = home(client, gateway)
    launch = urlsplit(first.headers["location"])
    assert (launch.netloc, launch.path, parse_qs(launch.query)) == ("efp.example.com", "/apps/launch", {})

    # The Portal signs the member in and sends the browser back with a code.
    fake_portal.codes["one-time"] = {"app_id": None, "slug": None, "user": BOB.to_json()}
    back = client.get("/_efp/callback", params={"code": "one-time", "next": "/"})
    session = back.headers["set-cookie"].split(";")[0]
    fake_portal.catalogs[BOB.id] = catalog_of(entry())
    page = home(client, gateway, cookie=session, uri=back.headers["location"])
    assert page.status_code == 200 and "Mind map" in page.text
