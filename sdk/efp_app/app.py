"""Facts about the app itself (docs/CONTRACTS.md, sections 8 and 9.1).

    from efp_app import app

    app.base_path()      # "/mindmap" when deployed, "" on a developer's machine
    app.public_url()     # "https://app.example.com/mindmap/"
    app.info().owner     # who owns the app, what the Portal lets it use
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from . import client as _client
from . import config


@dataclass(frozen=True)
class Owner:
    id: Optional[int] = None
    username: Optional[str] = None
    display_name: Optional[str] = None
    group_id: Optional[int] = None
    group_name: Optional[str] = None


@dataclass(frozen=True)
class LlmInfo:
    enabled: bool = False
    default_model: Optional[str] = None
    models: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorGrant:
    type: str
    write: bool = False


@dataclass(frozen=True)
class Budget:
    daily_credits: Optional[float] = None
    used_credits: Optional[float] = None
    daily_tokens: Optional[int] = None
    used_tokens: Optional[int] = None


@dataclass(frozen=True)
class AppInfo:
    """The answer of ``GET /app``; ``raw`` is the JSON as the Portal sent it."""

    id: Optional[str] = None
    slug: Optional[str] = None
    name: Optional[str] = None
    url: Optional[str] = None
    owner: Owner = field(default_factory=Owner)
    llm: LlmInfo = field(default_factory=LlmInfo)
    connectors: Tuple[ConnectorGrant, ...] = ()
    budget: Budget = field(default_factory=Budget)
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _parse(payload: Any) -> AppInfo:
    data = _dict(payload)
    owner = _dict(data.get("owner"))
    llm = _dict(data.get("llm"))
    budget = _dict(data.get("budget"))
    models = llm.get("models")
    connectors = data.get("connectors")
    return AppInfo(
        id=data.get("id"),
        slug=data.get("slug"),
        name=data.get("name"),
        url=data.get("url"),
        owner=Owner(
            id=owner.get("id"),
            username=owner.get("username"),
            display_name=owner.get("display_name"),
            group_id=owner.get("group_id"),
            group_name=owner.get("group_name"),
        ),
        llm=LlmInfo(
            enabled=bool(llm.get("enabled")),
            default_model=llm.get("default_model"),
            models=tuple(str(item) for item in models) if isinstance(models, (list, tuple)) else (),
        ),
        connectors=tuple(
            ConnectorGrant(type=str(item.get("type")), write=bool(item.get("write")))
            for item in (connectors if isinstance(connectors, (list, tuple)) else ())
            if isinstance(item, dict) and item.get("type")
        ),
        budget=Budget(
            daily_credits=budget.get("daily_credits"),
            used_credits=budget.get("used_credits"),
            daily_tokens=budget.get("daily_tokens"),
            used_tokens=budget.get("used_tokens"),
        ),
        raw=data,
    )


def info(*, cached: bool = False, client: Optional[_client.Client] = None) -> AppInfo:
    """Ask the Portal about this app: owner, model, connectors, today's budget.

    ``cached=True`` returns the previous answer when there is one (the budget
    in it may be out of date).
    """
    http = client or _client.get_client()
    if cached and isinstance(http.cached_app_info, AppInfo):
        return http.cached_app_info
    found = _parse(http.get("/app"))
    http.cached_app_info = found
    return found


def _info_once(timeout: float) -> AppInfo:
    """What ``info(cached=True)`` returns, from one short request when the Portal has to be asked.

    For ``visitor.current`` on a developer's machine: a request of the app
    waits for this answer, so it is not tried again and does not wait long.
    """
    http = _client.get_client()
    if isinstance(http.cached_app_info, AppInfo):
        return http.cached_app_info
    # A client that somebody put in the place of the shared one may be a stand-in without this method.
    once = getattr(http, "_get_once", None)
    found = _parse(once("/app", timeout=timeout) if once is not None else http.get("/app"))
    http.cached_app_info = found
    return found


async def ainfo(*, cached: bool = False, client: Optional[_client.AsyncClient] = None) -> AppInfo:
    """:func:`info` for asynchronous code."""
    http = client or _client.get_async_client()
    if cached and isinstance(http.cached_app_info, AppInfo):
        return http.cached_app_info
    found = _parse(await http.get("/app"))
    http.cached_app_info = found
    return found


def base_path() -> str:
    """The path prefix the app is served under: ``/{slug}``, or ``""`` on a developer's machine."""
    return config.load().base_path


def public_url() -> Optional[str]:
    """The address visitors use, ending in ``/``.

    On a developer's machine this is ``http://127.0.0.1:{PORT}/`` when ``PORT``
    is set (``efp-app dev`` sets it), otherwise ``None``.
    """
    settings = config.load()
    if settings.public_url:
        return settings.public_url if settings.public_url.endswith("/") else settings.public_url + "/"
    if not settings.deployed and settings.port:
        return "http://127.0.0.1:%d/" % settings.port
    return None


def slug() -> Optional[str]:
    """The app's slug (``EFP_APP_SLUG``), or ``None`` on a developer's machine."""
    return config.load().slug


def is_deployed() -> bool:
    """True inside the EFP host, false on a developer's machine."""
    return config.load().deployed
