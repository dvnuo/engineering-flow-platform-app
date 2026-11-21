"""The app center: the page at the root of the apps address (docs/CONTRACTS.md, section 6.5).

It lists the apps a signed-in member may open, as the Portal names them
(section 5.6), each as a card that leads to ``/{slug}/``. The page is plain
HTML from ``host/pages/center.html`` with the words of
``host/pages/<locale>.json`` (the ``center`` section); the one script on it
filters the cards as the member types and runs under a nonce.

Every value that comes from the Portal is checked and escaped here: a slug
that is not a slug is left out, text is cut to a sane length, and an icon is
only ever an address of this host.
"""

from __future__ import annotations

import html
import json
import re
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..models import Visitor, is_valid_slug
from .pages import DEFAULT_LOCALE, LOCALES, PagesError, _href
from .resources import pages_dir

MAX_APPS = 2000
MAX_NAME = 80
MAX_DESCRIPTION = 500
MAX_OWNER = 120
VISIBILITIES = ("private", "public", "platform", "anyone")
ICON_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
ICON_MAX_BYTES = 256 * 1024
ICON_CACHE_BYTES = 32 * 1024 * 1024
# How long an icon is served without asking the Portal whether it changed,
# when the page did not name the version it wants.
ICON_FRESH_SECONDS = 300.0

WORDS = (
    "title",
    "heading",
    "search",
    "count_one",
    "count_other",
    "empty_heading",
    "empty_message",
    "no_match",
    "mine",
    "portal",
    "sign_out",
    "audience_private",
    "audience_public",
    "audience_platform",
    "audience_anyone",
)

_LAYOUT_SLOT = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")
_VERSION = re.compile(r"[0-9A-Za-z]{1,64}")
_SPACE = re.compile(r"\s+")


class CatalogError(ValueError):
    """The Portal's answer to the catalog call cannot be used."""


@dataclass(frozen=True)
class CenterApp:
    id: str
    slug: str
    name: str
    description: str
    owner: str
    visibility: str
    mine: bool
    icon: str | None  # the icon's version, when the app has one


@dataclass(frozen=True)
class Catalog:
    allowed: bool
    reason: str | None
    visitor: Visitor | None
    apps: tuple[CenterApp, ...]


def _text(value: Any, limit: int) -> str:
    """One line of plain text, cut to ``limit`` characters."""

    if not isinstance(value, str):
        return ""
    return _SPACE.sub(" ", value).strip()[:limit]


def parse_catalog(data: Any) -> Catalog:
    """The Portal's catalog (section 5.6) as the page needs it; a ``CatalogError`` when it is not one."""

    if not isinstance(data, dict):
        raise CatalogError("the catalog is not a JSON object")
    if not data.get("allowed"):
        reason = data.get("reason")
        return Catalog(allowed=False, reason=reason if isinstance(reason, str) else None, visitor=None, apps=())
    user = data.get("user")
    try:
        visitor = Visitor.from_json(user)
    except (KeyError, TypeError, ValueError) as exc:
        raise CatalogError("the catalog does not say whose it is") from exc
    items = data.get("apps")
    if not isinstance(items, list):
        raise CatalogError("the catalog has no list of apps")

    apps: list[CenterApp] = []
    seen: set[str] = set()
    for item in items[:MAX_APPS]:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        app_id = item.get("id")
        if not isinstance(slug, str) or not is_valid_slug(slug) or slug in seen:
            continue
        if not isinstance(app_id, str) or not app_id:
            continue
        seen.add(slug)
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        visibility = item.get("visibility")
        icon = item.get("icon")
        apps.append(
            CenterApp(
                id=app_id,
                slug=slug,
                name=_text(item.get("name"), MAX_NAME) or slug,
                description=_text(item.get("description"), MAX_DESCRIPTION),
                owner=_text(owner.get("display_name"), MAX_OWNER) or _text(owner.get("username"), MAX_OWNER),
                visibility=visibility if visibility in VISIBILITIES else "",
                mine=item.get("mine") is True,
                icon=icon if isinstance(icon, str) and _VERSION.fullmatch(icon) else None,
            )
        )
    return Catalog(allowed=True, reason=None, visitor=visitor, apps=tuple(apps))


def initial_of(name: str) -> str:
    """The letter shown for an app without an icon."""

    for character in name:
        if character.isalnum():
            return character.upper()
    return "?"


def hue_of(slug: str) -> int:
    """A colour for the app's tile that is the same on every visit."""

    return zlib.crc32(slug.encode("utf-8")) % 360


class CenterRenderer:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or pages_dir()
        try:
            with open(self.directory / "center.html", "r", encoding="utf-8") as handle:
                self._layout = handle.read()
            self._words: dict[str, dict[str, str]] = {}
            self._product: dict[str, str] = {}
            for locale in LOCALES:
                with open(self.directory / f"{locale}.json", "r", encoding="utf-8") as handle:
                    catalogue = json.load(handle)
                self._words[locale] = dict(catalogue.get("center") or {})
                self._product[locale] = str((catalogue.get("labels") or {}).get("product") or "")
        except (OSError, ValueError) as exc:
            raise PagesError(f"cannot load the app center from {self.directory}: {exc}") from exc
        missing = [name for name in WORDS if not self._words[DEFAULT_LOCALE].get(name)]
        if missing:
            raise PagesError(f"{self.directory / (DEFAULT_LOCALE + '.json')} lacks center words: {', '.join(missing)}")

    def word(self, locale: str, name: str) -> str:
        return str(self._words.get(locale, {}).get(name) or self._words[DEFAULT_LOCALE][name])

    def _card(self, locale: str, app: CenterApp) -> str:
        escape = html.escape
        address = f"/{app.slug}/"
        if app.icon:
            icon = (
                f'<img class="icon" src="/_efp/icon/{app.slug}?v={escape(app.icon, quote=True)}" alt="" '
                'width="48" height="48" loading="lazy" decoding="async">'
            )
        else:
            icon = f'<span class="icon letter" style="--hue: {hue_of(app.slug)}" aria-hidden="true">{escape(initial_of(app.name))}</span>'
        description = f'<span class="description">{escape(app.description)}</span>' if app.description else ""
        if app.mine:
            meta = f'<span class="badge">{escape(self.word(locale, "mine"))}</span>'
            if app.visibility:
                meta += f'<span class="audience">{escape(self.word(locale, "audience_" + app.visibility))}</span>'
        else:
            meta = f'<span class="owner">{escape(app.owner)}</span>' if app.owner else ""
        searchable = " ".join(part for part in (app.name, app.slug, app.description, app.owner) if part).lower()
        return (
            f'<li><a class="card" href="{escape(address, quote=True)}" data-search="{escape(searchable, quote=True)}">'
            f"{icon}"
            f'<span class="text"><span class="name">{escape(app.name)}</span>{description}'
            f'<span class="meta">{meta}</span></span>'
            "</a></li>"
        )

    def render(self, catalog: Catalog, locale: str = DEFAULT_LOCALE, *, portal_url: str = "", nonce: str = "") -> str:
        if locale not in LOCALES:
            locale = DEFAULT_LOCALE
        escape = html.escape
        apps = catalog.apps
        visitor = catalog.visitor
        who = (visitor.display_name or visitor.username) if visitor is not None else ""
        count = self.word(locale, "count_one" if len(apps) == 1 else "count_other").replace("{n}", str(len(apps)))

        portal = _href(portal_url)
        portal_link = f'<a href="{escape(portal, quote=True)}">{escape(self.word(locale, "portal"))}</a>' if portal else ""
        empty_html = ""
        if not apps:
            action = f'<p class="actions"><a class="button" href="{escape(portal, quote=True)}">{escape(self.word(locale, "portal"))}</a></p>' if portal else ""
            empty_html = (
                '<section class="empty">'
                f'<h2>{escape(self.word(locale, "empty_heading"))}</h2>'
                f'<p>{escape(self.word(locale, "empty_message"))}</p>{action}'
                "</section>"
            )
        slots = {
            "lang": escape(locale, quote=True),
            "title": escape(self.word(locale, "title")),
            "product": escape(self._product.get(locale) or self._product[DEFAULT_LOCALE]),
            "heading": escape(self.word(locale, "heading")),
            "who": escape(who),
            "portal_link": portal_link,
            "sign_out": escape(self.word(locale, "sign_out")),
            "search": escape(self.word(locale, "search"), quote=True),
            "count": escape(count),
            "cards_html": "".join(self._card(locale, app) for app in apps),
            "empty_html": empty_html,
            "no_match": escape(self.word(locale, "no_match")),
            "tools_hidden": "" if apps else " hidden",
            "nonce": escape(nonce, quote=True),
        }
        return _LAYOUT_SLOT.sub(lambda match: slots.get(match.group(1), ""), self._layout)


@dataclass
class _Icon:
    data: bytes
    content_type: str
    version: str
    fetched_at: float


def version_of(data: bytes) -> str:
    """The same name the Portal gives an icon (section 5.6): the start of its SHA-1."""

    import hashlib

    return hashlib.sha1(data).hexdigest()[:12]


class IconCache:
    """The icons the page shows, kept in memory: one per app, the newest the Portal gave.

    ``get`` asks the Portal when it holds nothing for the app, when the page
    names another version than the one held, or when no version is named and
    the one held is older than ``ICON_FRESH_SECONDS``. "No icon" is
    remembered as well, for the same time.
    """

    def __init__(self, *, max_bytes: int = ICON_CACHE_BYTES, clock: Callable[[], float] = time.monotonic):
        self.max_bytes = int(max_bytes)
        self._clock = clock
        self._icons: dict[str, _Icon | None] = {}
        self._missing_at: dict[str, float] = {}
        self._size = 0

    def _drop(self, app_id: str) -> None:
        held = self._icons.pop(app_id, None)
        if held is not None:
            self._size -= len(held.data)
        self._missing_at.pop(app_id, None)

    def forget_app(self, app_id: str) -> None:
        self._drop(app_id)

    def _keep(self, app_id: str, icon: _Icon | None) -> None:
        self._drop(app_id)
        if icon is None:
            self._missing_at[app_id] = self._clock()
            if len(self._missing_at) > 10_000:
                self._missing_at.clear()
            return
        while self._icons and self._size + len(icon.data) > self.max_bytes:
            self._drop(next(iter(self._icons)))  # the one kept longest
        self._icons[app_id] = icon
        self._size += len(icon.data)

    async def get(self, app_id: str, wanted_version: str, fetch: Callable[[str], Any]) -> tuple[bytes, str] | None:
        """``(bytes, content type)`` of the app's icon, or ``None`` when it has none.

        ``fetch`` is the Portal call (section 5.7); what it raises passes
        through, so the caller can tell "no icon" from "could not ask".
        """

        now = self._clock()
        held = self._icons.get(app_id)
        if held is not None:
            if wanted_version == held.version or (not wanted_version and now - held.fetched_at < ICON_FRESH_SECONDS):
                return held.data, held.content_type
            if wanted_version and wanted_version != held.version and now - held.fetched_at < 5.0:
                # Just fetched, and the Portal's icon is not the one asked for: do not ask again at once.
                return held.data, held.content_type
        missing_at = self._missing_at.get(app_id)
        # A page that names a version was told by the Portal that the app has
        # an icon: "no icon" from a moment ago is then only held briefly, so
        # that an icon added since shows without the long wait.
        if missing_at is not None and now - missing_at < (5.0 if wanted_version else ICON_FRESH_SECONDS):
            return None

        found = await fetch(app_id)
        if found is None:
            self._keep(app_id, None)
            return None
        data, content_type = found
        if content_type not in ICON_TYPES or not data or len(data) > ICON_MAX_BYTES:
            self._keep(app_id, None)
            return None
        icon = _Icon(data=bytes(data), content_type=content_type, version=version_of(data), fetched_at=now)
        self._keep(app_id, icon)
        return icon.data, icon.content_type
