from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from efp_app_host.gateway.pages import LOCALES, PAGES, PageRenderer, PagesError, pick_locale
from efp_app_host.gateway.resources import pages_dir

GATEWAY_SOURCES = Path(__file__).resolve().parents[2] / "efp_app_host" / "gateway"


@pytest.fixture(scope="module")
def pages() -> PageRenderer:
    return PageRenderer()


@pytest.mark.parametrize(
    "header, expected",
    [
        (None, "en"),
        ("", "en"),
        ("*", "en"),
        ("en-US,en;q=0.9", "en"),
        ("fr-FR,fr;q=0.9", "en"),
        ("zh-CN,zh;q=0.9,en;q=0.8", "zh-Hans"),
        ("zh", "zh-Hans"),
        ("zh-Hans-CN", "zh-Hans"),
        ("zh-SG", "zh-Hans"),
        ("zh-HK,zh;q=0.9,en;q=0.8", "zh-Hant-HK"),
        ("zh-TW", "zh-Hant-HK"),
        ("zh-Hant", "zh-Hant-HK"),
        ("zh-Hant-HK", "zh-Hant-HK"),
        ("ZH-hk", "zh-Hant-HK"),
        # The highest quality wins, not the first in the list.
        ("en;q=0.5, zh-HK;q=0.9", "zh-Hant-HK"),
        ("zh-CN;q=0.1, en;q=0.8", "en"),
        # A language that is not offered is passed over.
        ("fr, de;q=0.9, zh-TW;q=0.5", "zh-Hant-HK"),
        ("zh-CN;q=0, en", "en"),
        ("zh-CN;q=abc, zh-TW;q=0.3", "zh-Hant-HK"),
    ],
)
def test_locale_from_accept_language(header, expected):
    assert pick_locale(header) == expected


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_in_every_locale(pages, locale, page):
    html = pages.render(
        page,
        locale,
        app="Mind map",
        owner="alice",
        reason="Policy review",
        portal_url="https://efp.example.com",
        app_url="/mindmap/",
        retry_url="/mindmap/board?id=3",
    )
    assert html.startswith("<!doctype html>")
    assert f'<html lang="{locale}">' in html
    assert f'class="page-{page}"' in html
    # Nothing is left unfilled, in the layout or in the words.
    assert "{{" not in html and "}}" not in html
    assert not re.search(r"\{(app|owner|reason)\}", html)
    title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
    heading = re.search(r"<h1>(.*?)</h1>", html, re.S).group(1)
    message = re.search(r'<p class="message">(.*?)</p>', html, re.S).group(1)
    assert title.strip() and heading.strip() and message.strip()
    # Self-contained: no script, nothing loaded from elsewhere.
    assert "<script" not in html.lower()
    assert not re.search(r'\b(src|action)\s*=', html)
    assert not re.search(r"url\(|@import", html)
    assert re.findall(r'<link\b', html) == []


@pytest.mark.parametrize("locale", ["zh-Hans", "zh-Hant-HK"])
def test_the_chinese_catalogues_are_complete_and_translated(pages, locale):
    with open(pages_dir() / "en.json", "r", encoding="utf-8") as handle:
        english = json.load(handle)
    with open(pages_dir() / f"{locale}.json", "r", encoding="utf-8") as handle:
        translated = json.load(handle)
    assert set(translated["labels"]) == set(english["labels"])
    assert set(translated["pages"]) == set(english["pages"]) == set(PAGES)
    for name, entry in english["pages"].items():
        other = translated["pages"][name]
        assert set(other) == set(entry), name
        assert other["actions"] == entry["actions"], name
        for field in ("title", "heading", "message", "message_no_owner", "detail"):
            if field in entry:
                assert re.search(r"[\u4e00-\u9fff]", other[field]), (name, field)
                # The same values are filled in, whatever the language.
                assert set(re.findall(r"\{[a-z_]+\}", other[field])) == set(re.findall(r"\{[a-z_]+\}", entry[field])), (name, field)


def test_the_two_chinese_catalogues_differ(pages):
    simplified = pages.render("no_access", "zh-Hans", app="Mind map", owner="alice")
    traditional = pages.render("no_access", "zh-Hant-HK", app="Mind map", owner="alice")
    assert "\u65e0\u6743\u8bbf\u95ee" in simplified
    assert "\u7121\u6b0a\u5b58\u53d6" in traditional
    assert simplified != traditional


def test_values_are_escaped(pages):
    html = pages.render(
        "disabled",
        "en",
        app='<img src=x onerror="alert(1)">',
        owner="<b>alice</b>",
        reason="</p><script>alert(2)</script> & more",
        portal_url='https://efp.example.com/?a=1&b="2"',
    )
    assert "<img" not in html and "<script" not in html and "<b>" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt; has been taken down" in html
    assert "Reason: &lt;/p&gt;&lt;script&gt;alert(2)&lt;/script&gt; &amp; more" in html
    assert 'href="https://efp.example.com/?a=1&amp;b=&quot;2&quot;"' in html


def test_a_value_cannot_smuggle_in_a_placeholder(pages):
    html = pages.render("no_access", "en", app="{owner}", owner="{{title}} {reason}")
    assert "You do not have access to {owner}" in html
    assert "Ask its owner, {{title}} {reason}, to share it with you." in html


@pytest.mark.parametrize("url", ["javascript:alert(1)", "//evil.example.com/", "data:text/html,x", "evil.example.com", " "])
def test_only_plain_addresses_become_links(pages, url):
    html = pages.render("unavailable", "en", app="Mind map", portal_url=url, retry_url=url, app_url=url)
    assert "<a " not in html


def test_owner_and_reason_are_optional(pages):
    with_owner = pages.render("no_access", "en", app="Mind map", owner="alice")
    assert "Ask its owner, alice, to share it with you." in with_owner
    without_owner = pages.render("no_access", "en", app="Mind map")
    assert "Ask its owner to share it with you." in without_owner

    with_reason = pages.render("disabled", "en", app="Mind map", reason="Policy review")
    assert '<p class="detail">Reason: Policy review</p>' in with_reason
    without_reason = pages.render("disabled", "en", app="Mind map")
    assert 'class="detail"' not in without_reason and "Reason" not in without_reason

    nameless = pages.render("starting", "en")
    assert "<h1>This app is starting</h1>" in nameless


def test_the_starting_page_reloads_and_the_others_do_not(pages):
    starting = pages.render("starting", "en", app="Mind map", refresh_seconds=2)
    assert '<meta http-equiv="refresh" content="2">' in starting
    assert "http-equiv" not in pages.render("crashed", "en", app="Mind map")


def test_actions(pages):
    html = pages.render(
        "unavailable", "en", app="Mind map", portal_url="https://efp.example.com", retry_url="/mindmap/x?y=1&z=2"
    )
    assert '<a class="button" href="/mindmap/x?y=1&amp;z=2">Try again</a>' in html
    assert '<a class="button quiet" href="https://efp.example.com">Open the Portal</a>' in html
    # The first link that exists is the main one.
    only_portal = pages.render("unavailable", "en", app="Mind map", portal_url="https://efp.example.com")
    assert '<a class="button" href="https://efp.example.com">Open the Portal</a>' in only_portal


def test_an_unknown_locale_falls_back_to_english_and_an_unknown_page_is_an_error(pages):
    assert "Too many requests" in pages.render("rate_limited", "de")
    with pytest.raises(PagesError):
        pages.render("nope", "en")


def test_missing_or_broken_files_are_reported(tmp_path):
    with pytest.raises(PagesError):
        PageRenderer(tmp_path)

    broken = tmp_path / "broken"
    shutil.copytree(pages_dir(), broken)
    (broken / "zh-Hans.json").write_text("{", encoding="utf-8")
    with pytest.raises(PagesError):
        PageRenderer(broken)

    incomplete = tmp_path / "incomplete"
    shutil.copytree(pages_dir(), incomplete)
    with open(incomplete / "en.json", "r", encoding="utf-8") as handle:
        catalogue = json.load(handle)
    del catalogue["pages"]["crashed"]
    (incomplete / "en.json").write_text(json.dumps(catalogue), encoding="utf-8")
    with pytest.raises(PagesError, match="crashed"):
        PageRenderer(incomplete)


def test_a_locale_may_leave_a_text_to_english(tmp_path):
    partial = tmp_path / "partial"
    shutil.copytree(pages_dir(), partial)
    with open(partial / "zh-Hans.json", "r", encoding="utf-8") as handle:
        catalogue = json.load(handle)
    del catalogue["pages"]["rate_limited"]
    del catalogue["labels"]["retry"]
    (partial / "zh-Hans.json").write_text(json.dumps(catalogue), encoding="utf-8")
    html = PageRenderer(partial).render("rate_limited", "zh-Hans", retry_url="/mindmap/")
    assert "Too many requests" in html and ">Try again</a>" in html


def test_the_resource_directory_can_be_moved(tmp_path, monkeypatch):
    moved = tmp_path / "share"
    shutil.copytree(pages_dir(), moved / "pages")
    monkeypatch.setenv("EFP_HOST_RESOURCE_DIR", str(moved))
    assert pages_dir() == moved / "pages"
    assert "Too many requests" in PageRenderer().render("rate_limited")


def test_python_sources_of_the_gateway_are_ascii():
    """CJK text lives in the catalogues only."""

    for source in sorted(GATEWAY_SOURCES.glob("*.py")) + sorted(Path(__file__).parent.glob("*.py")):
        with open(source, "rb") as handle:
            data = handle.read()
        assert data.isascii(), source.name
    with open(pages_dir() / "page.html", "rb") as handle:
        assert handle.read().isascii()
    with open(pages_dir() / "en.json", "rb") as handle:
        assert handle.read().isascii()
