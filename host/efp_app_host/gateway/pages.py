"""The gateway's own pages: starting, unavailable, no access and so on.

``host/pages/page.html`` is the one layout; ``host/pages/<locale>.json`` holds
the words. The pages are plain HTML without any outside asset, and every
value that is put into them is escaped.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from .resources import pages_dir

DEFAULT_LOCALE = "en"
LOCALES = ("en", "zh-Hans", "zh-Hant-HK")
PAGES = (
    "home",
    "starting",
    "unavailable",
    "not_deployed",
    "crashed",
    "no_access",
    "disabled",
    "not_found",
    "signin_failed",
    "rate_limited",
)
ACTIONS = ("retry", "app", "portal")

_LAYOUT_SLOT = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_TEXT_SLOT = re.compile(r"\{([a-z_]+)\}")
_TRADITIONAL_SUBTAGS = frozenset({"hant", "hk", "tw", "mo"})


class PagesError(RuntimeError):
    """The page layout or a catalogue is missing or incomplete."""


def pick_locale(accept_language: str | None) -> str:
    """The best of ``LOCALES`` for an ``Accept-Language`` header."""

    wanted: list[tuple[float, int, str]] = []
    for position, part in enumerate((accept_language or "").split(",")):
        tag, _, parameters = part.strip().partition(";")
        tag = tag.strip().lower()
        if not tag:
            continue
        quality = 1.0
        for parameter in parameters.split(";"):
            name, _, value = parameter.strip().partition("=")
            if name.strip().lower() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if quality > 0:
            wanted.append((-quality, position, tag))
    for _quality, _position, tag in sorted(wanted):
        subtags = tag.split("-")
        if subtags[0] == "en":
            return "en"
        if subtags[0] == "zh":
            return "zh-Hant-HK" if _TRADITIONAL_SUBTAGS.intersection(subtags[1:]) else "zh-Hans"
    return DEFAULT_LOCALE


def _fill(text: str, values: dict[str, str]) -> str:
    return _TEXT_SLOT.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def _href(url: str | None) -> str:
    """Only addresses a page of ours may link to; anything else gives no link."""

    value = (url or "").strip()
    if value.startswith("/") and not value.startswith("//"):
        return value
    if value.startswith(("https://", "http://")):
        return value
    return ""


class PageRenderer:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or pages_dir()
        try:
            with open(self.directory / "page.html", "r", encoding="utf-8") as handle:
                self._layout = handle.read()
            self._catalogues: dict[str, dict[str, Any]] = {}
            for locale in LOCALES:
                with open(self.directory / f"{locale}.json", "r", encoding="utf-8") as handle:
                    self._catalogues[locale] = json.load(handle)
        except (OSError, ValueError) as exc:
            raise PagesError(f"cannot load the gateway pages from {self.directory}: {exc}") from exc
        base = self._catalogues[DEFAULT_LOCALE]
        missing = [page for page in PAGES if page not in (base.get("pages") or {})]
        missing += [f"labels.{name}" for name in (*ACTIONS, "product", "this_app") if name not in (base.get("labels") or {})]
        if missing:
            raise PagesError(f"{self.directory / (DEFAULT_LOCALE + '.json')} lacks: {', '.join(missing)}")

    def _entry(self, locale: str, page: str) -> dict[str, Any]:
        base = self._catalogues[DEFAULT_LOCALE]["pages"]
        if page not in base:
            raise PagesError(f"unknown page {page!r}")
        own = (self._catalogues.get(locale, {}).get("pages") or {}).get(page) or {}
        return {**base[page], **own}

    def _label(self, locale: str, name: str) -> str:
        own = (self._catalogues.get(locale, {}).get("labels") or {}).get(name)
        return str(own or self._catalogues[DEFAULT_LOCALE]["labels"][name])

    def render(
        self,
        page: str,
        locale: str = DEFAULT_LOCALE,
        *,
        app: str = "",
        owner: str = "",
        reason: str = "",
        portal_url: str = "",
        app_url: str = "",
        retry_url: str = "",
        refresh_seconds: int | None = None,
    ) -> str:
        if locale not in LOCALES:
            locale = DEFAULT_LOCALE
        entry = self._entry(locale, page)
        values = {"app": app.strip() or self._label(locale, "this_app"), "owner": owner.strip(), "reason": reason.strip()}

        message = str(entry.get("message") or "")
        if not values["owner"] and "{owner}" in message:
            message = str(entry.get("message_no_owner") or "")

        detail_html = ""
        if values["reason"] and entry.get("detail"):
            detail_html = f'<p class="detail">{html.escape(_fill(str(entry["detail"]), values))}</p>'

        links = {"retry": _href(retry_url), "app": _href(app_url), "portal": _href(portal_url)}
        buttons = []
        for name in entry.get("actions") or []:
            target = links.get(name)
            if not target:
                continue
            style = "button" if not buttons else "button quiet"
            buttons.append(f'<a class="{style}" href="{html.escape(target, quote=True)}">{html.escape(self._label(locale, name))}</a>')
        actions_html = f'<p class="actions">{"".join(buttons)}</p>' if buttons else ""

        refresh_html = ""
        if refresh_seconds:
            refresh_html = f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'

        slots = {
            "lang": html.escape(locale, quote=True),
            "page": html.escape(page, quote=True),
            "product": html.escape(self._label(locale, "product")),
            "title": html.escape(_fill(str(entry.get("title") or ""), values)),
            "heading": html.escape(_fill(str(entry.get("heading") or ""), values)),
            "message": html.escape(_fill(message, values)),
            "detail_html": detail_html,
            "actions_html": actions_html,
            "refresh_html": refresh_html,
        }
        return _LAYOUT_SLOT.sub(lambda match: slots.get(match.group(1), ""), self._layout)
