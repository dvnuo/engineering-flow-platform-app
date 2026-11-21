"""The owner's connectors (Jira, Confluence, GitHub ...), through the Portal.

docs/CONTRACTS.md, section 9.3. The Portal adds the owner's credentials and
forwards the request; the app never sees them.

    from efp_app import connectors

    issue = connectors.get("jira", "/rest/api/2/issue/ABC-1", fields="summary")
    if issue.ok:
        print(issue.json["fields"]["summary"])

    connectors.available()       # which connectors the owner turned on

The answer carries the upstream's own status: a 404 from Jira is a
``ConnectorResponse`` with ``status == 404``, not an exception. Exceptions
are for calls the Portal refused or could not complete (``ConnectorDisabledError``,
``MethodNotAllowedError``, ``ConnectorNotConfiguredError``, ``UpstreamError``,
``UpstreamTimeoutError`` ...). Only ``GET`` and ``HEAD`` are allowed unless
the owner allowed writes for the connector.
"""

from __future__ import annotations

import base64
import binascii
import json as _json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Union

from . import client as _client
from . import errors

#: The connector types of this release of the Portal.
TYPES = ("jira", "confluence", "github", "jenkins", "nexus", "splunk")

_TYPE = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass(frozen=True)
class Connector:
    """A connector the owner turned on for the app."""

    type: str
    base_url: Optional[str] = None
    write: bool = False


@dataclass(frozen=True)
class ConnectorResponse:
    """What the other system answered.

    ``status``     its HTTP status
    ``headers``    its response headers, names in lower case
    ``json``       the decoded body when it was JSON, else ``None``
    ``content``    the body as bytes
    ``truncated``  true when the body was longer than 5 MiB and was cut
    """

    status: int
    headers: Dict[str, str] = field(default_factory=dict)
    json: Any = None
    content: bytes = b""
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def _check_type(type: str) -> str:
    if not isinstance(type, str) or not _TYPE.fullmatch(type):
        raise errors.InvalidRequestError("the connector type must be a name such as %s" % ", ".join(TYPES))
    return type


def _check_path(path: str) -> str:
    """The rules of section 9.3, checked before the call is made."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise errors.InvalidPathError("the path must start with '/': it is relative to the connector's address")
    if path.startswith("//") or "\\" in path:
        raise errors.InvalidPathError("the path may not name a host or contain a backslash")
    if ".." in path.split("?", 1)[0].split("/"):
        raise errors.InvalidPathError("the path may not contain a '..' segment")
    return path


def _payload(
    method: str,
    path: str,
    query: Optional[Mapping[str, Any]],
    json: Any,
    body: Union[bytes, bytearray, str, None],
    headers: Optional[Mapping[str, str]],
) -> Dict[str, Any]:
    if not isinstance(method, str) or not method.strip():
        raise errors.InvalidRequestError("the method must be an HTTP method such as GET")
    if json is not None and body is not None:
        raise errors.InvalidRequestError("give json= or body=, not both")
    encoded = None
    if body is not None:
        try:
            raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        except UnicodeEncodeError as exc:
            # The same refusal as for such a text in json= (client._encode_body); not chained,
            # the original error holds the whole body.
            raise errors.InvalidRequestError(
                "the body holds text that cannot be sent: half of a surrogate pair (U+%04X), "
                "which is what cutting a string in the middle of an emoji leaves" % ord(exc.object[exc.start])
            ) from None
        encoded = base64.b64encode(raw).decode("ascii")
    return {
        "method": method.strip().upper(),
        "path": _check_path(path),
        "query": {name: value for name, value in query.items() if value is not None} if query else None,
        "headers": dict(headers) if headers else None,
        "json": json,
        "body_base64": encoded,
    }


def _response(payload: Any) -> ConnectorResponse:
    if not isinstance(payload, dict) or isinstance(payload.get("status"), bool) or not isinstance(payload.get("status"), int):
        raise errors.UnexpectedResponseError("The Portal's answer to a connector call has no status.")
    headers = payload.get("headers")
    decoded = payload.get("json")
    content = b""
    encoded = payload.get("body_base64")
    if isinstance(encoded, str) and encoded:
        try:
            content = base64.b64decode(encoded)
        except (binascii.Error, ValueError):
            raise errors.UnexpectedResponseError("The Portal's answer to a connector call has a damaged body.") from None
    elif decoded is not None:
        # "replace": JSON may carry half of a surrogate pair (a text the other system cut in the
        # middle of an emoji), which UTF-8 cannot. Such a character becomes "?" in content; json
        # keeps it. An answer of the other system must never fail here.
        content = _json.dumps(decoded, ensure_ascii=False).encode("utf-8", errors="replace")
    return ConnectorResponse(
        status=payload["status"],
        headers={str(name).lower(): str(value) for name, value in headers.items()} if isinstance(headers, dict) else {},
        json=decoded,
        content=content,
        truncated=bool(payload.get("truncated")),
    )


def _connectors(payload: Any) -> List[Connector]:
    items = payload.get("connectors") if isinstance(payload, dict) else None
    return [
        Connector(type=str(item["type"]), base_url=item.get("base_url"), write=bool(item.get("write")))
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and item.get("type")
    ]


def request(
    type: str,
    method: str,
    path: str,
    *,
    query: Optional[Mapping[str, Any]] = None,
    json: Any = None,
    body: Union[bytes, bytearray, str, None] = None,
    headers: Optional[Mapping[str, str]] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.Client] = None,
) -> ConnectorResponse:
    """Send one request to a connector of the owner.

    ``path`` is relative to the connector's address and starts with ``/``.
    ``json`` is sent as a JSON body, ``body`` (bytes or text) as it is. Of
    ``headers`` only ``Accept``, ``Content-Type`` and ``X-*`` are forwarded.
    """
    payload = _payload(method, path, query, json, body, headers)
    http = client or _client.get_client()
    return _response(
        http.post("/connectors/%s/request" % _check_type(type), payload, visitor=visitor, timeout=timeout)
    )


def get(type: str, path: str, *, visitor: Any = None, **query: Any) -> ConnectorResponse:
    """``GET`` with the keyword arguments as the query string.

    ``connectors.get("jira", "/rest/api/2/search", jql="project = ABC", maxResults=20)``.
    A query parameter that is itself called ``visitor`` needs ``request(..., query={...})``.
    """
    return request(type, "GET", path, query=query or None, visitor=visitor)


def available(*, client: Optional[_client.Client] = None) -> List[Connector]:
    """The connectors the owner turned on for this app (no credentials)."""
    http = client or _client.get_client()
    return _connectors(http.get("/connectors"))


async def arequest(
    type: str,
    method: str,
    path: str,
    *,
    query: Optional[Mapping[str, Any]] = None,
    json: Any = None,
    body: Union[bytes, bytearray, str, None] = None,
    headers: Optional[Mapping[str, str]] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.AsyncClient] = None,
) -> ConnectorResponse:
    """:func:`request` for asynchronous code."""
    payload = _payload(method, path, query, json, body, headers)
    http = client or _client.get_async_client()
    return _response(
        await http.post("/connectors/%s/request" % _check_type(type), payload, visitor=visitor, timeout=timeout)
    )


async def aget(type: str, path: str, *, visitor: Any = None, **query: Any) -> ConnectorResponse:
    """:func:`get` for asynchronous code."""
    return await arequest(type, "GET", path, query=query or None, visitor=visitor)


async def aavailable(*, client: Optional[_client.AsyncClient] = None) -> List[Connector]:
    """:func:`available` for asynchronous code."""
    http = client or _client.get_async_client()
    return _connectors(await http.get("/connectors"))
