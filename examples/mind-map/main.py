"""Example EFP app: mind maps that members draw, keep and show to each other.

It shows:

- one stored object per item, written by one member only       maps/<owner id>/<map id>.json
- a version number that refuses a stale save                    the same member in two tabs
- checking the shape of what a page sends                       clean_nodes: one root, no loops
- a list that is kept next to the data and can be wrong         shared/<owner id>/<map id>.json

Storage has no transactions: when two saves of one object meet, one is lost.
Two members never meet here, because a map is saved under the id of the
visitor the gateway signed in; no route lets anybody name another owner. The
same member in two tabs can still meet themselves. So a map carries a version
number: a save says which version it started from and is refused (409) when
the stored map has moved on. The page then offers to reload.

The editor is the page's business (static/): the tree, its layout, the
drawing and the exports all happen in the browser. The server keeps maps and
trusts nothing about them: a page is somebody's browser, and anybody can send
this API whatever they like.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/maps").
"""

import json
import logging
import mimetypes
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from efp_app import Visitor, storage
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
MAX_MAP_BYTES = 256 * 1024  # one request body, and so one stored map
MAX_NODES = 500
MAX_TITLE = 80
MAX_TEXT = 200  # characters in one node
MAX_MAPS = 30  # per member
COLORS = ("blue", "green", "amber", "red", "purple", "teal", "pink", "gray")
MAP_ID = re.compile(r"[0-9a-f]{12}")  # becomes part of a storage key
NODE_ID = re.compile(r"[A-Za-z0-9_-]{1,24}")

# The host runs one process per app, so a lock in this process is enough to
# make "read the stored version, compare, save" one step. Without it two
# requests of one member could both pass the comparison and one save be lost.
WRITING = threading.Lock()

# Windows may map .js to text/plain (the registry decides), and a browser
# refuses a module script that is not served as JavaScript.
mimetypes.add_type("text/javascript", ".js")

log = logging.getLogger("app")
app = FastAPI(title="Mind map")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Refused(Exception):
    """A request this app turns away, with the sentence the page shows."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def bad(message: str) -> Refused:
    return Refused(400, "bad_input", message)


@app.exception_handler(Refused)
def refused(request: Request, exc: Refused) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
def malformed(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A path or query that does not fit a route: the same envelope as every other refusal."""
    return JSONResponse({"error": {"code": "bad_input", "message": "That request is not valid."}}, status_code=400)


@app.exception_handler(EfpError)
def problem(request: Request, exc: EfpError) -> JSONResponse:
    """An SDK error (here: storage) as an answer the page can show; the detail goes to the log."""
    log.warning("%s", exc)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.user_message}},
        status_code=429 if exc.status == 429 else 502,
    )


def member(visitor: Optional[Visitor] = Depends(get_visitor)) -> Visitor:
    """The signed-in visitor; a guest (possible with the audience "anyone") is turned away."""
    if visitor is None:
        raise Refused(401, "sign_in_required", "Sign in to use this app.")
    return visitor


async def document(request: Request) -> Dict[str, Any]:
    """The JSON object a page sent, read with an upper bound.

    Nothing limits a request body by default (the gateway lets 100 MB
    through), so the bytes are counted while they arrive and the reading
    stops at the limit, before anything is parsed.
    """
    too_large = Refused(413, "too_large", "That map is too large to save. Split it into two maps.")
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_MAP_BYTES:
        raise too_large
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_MAP_BYTES:
            raise too_large
    try:
        sent = json.loads(bytes(received).decode("utf-8"))
    except (ValueError, RecursionError):  # not JSON, not UTF-8, or brackets nested a thousand deep
        raise bad("The request is not JSON.") from None
    if not isinstance(sent, dict):
        raise bad("The request is not a JSON object.")
    return sent


# --- What a page sends, checked -------------------------------------------------


def clean_title(value: Any) -> str:
    title = " ".join(value.split()) if isinstance(value, str) else ""
    if not title or len(title) > MAX_TITLE:
        raise Refused(400, "bad_title", "A title has 1 to %d characters." % MAX_TITLE)
    return title


def clean_version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise bad("A save names the version it started from.")
    return value


def clean_nodes(value: Any) -> List[Dict[str, Any]]:
    """The nodes of a map as they are stored, or a refusal that says what is wrong.

    A node is {"id", "parent", "text", "color", "collapsed"}; the order of the
    list is the order of siblings. The tree must be one tree: ids unique, one
    node without a parent, every parent present, and no node its own ancestor.
    Unknown fields are dropped, so nothing but these five is ever stored.
    """
    if not isinstance(value, list) or not value:
        raise bad("A map needs at least its central node.")
    if len(value) > MAX_NODES:
        raise Refused(400, "too_many_nodes", "A map has at most %d nodes." % MAX_NODES)
    nodes: List[Dict[str, Any]] = []
    parents: Dict[str, Optional[str]] = {}
    for item in value:
        if not isinstance(item, dict):
            raise bad("Every node is an object.")
        node_id, parent, text, color = item.get("id"), item.get("parent"), item.get("text"), item.get("color")
        if not isinstance(node_id, str) or not NODE_ID.fullmatch(node_id):
            raise bad("A node id has 1 to 24 letters, digits, '-' or '_'.")
        if node_id in parents:
            raise bad("Two nodes have the same id.")
        if parent is not None and not isinstance(parent, str):
            raise bad("A parent is the id of another node.")
        if not isinstance(text, str):
            raise bad("Every node has a text.")
        text = " ".join(text.split())
        if len(text) > MAX_TEXT:
            raise bad("The text of a node has at most %d characters." % MAX_TEXT)
        if color is not None and color not in COLORS:
            raise bad("A colour is one of: %s." % ", ".join(COLORS))
        parents[node_id] = parent
        nodes.append(
            {"id": node_id, "parent": parent, "text": text, "color": color, "collapsed": item.get("collapsed") is True}
        )
    roots = [node_id for node_id, parent in parents.items() if parent is None]
    if len(roots) != 1:
        raise bad("A map has exactly one central node.")
    if any(parent is not None and parent not in parents for parent in parents.values()):
        raise bad("A node names a parent that is not in the map.")
    # Walk up from every node. A walk that meets one of its own steps again
    # is a loop that never reaches the root.
    rooted = {roots[0]}
    for node_id in parents:
        trail: List[str] = []
        current: Any = node_id
        while current not in rooted:
            if current in trail:
                raise bad("The nodes form a loop.")
            trail.append(current)
            current = parents[current]
        rooted.update(trail)
    return nodes


# --- Maps in storage -------------------------------------------------------------


def map_key(owner_id: int, map_id: str) -> str:
    """The one object of a map. The owner id is always the signed-in visitor's when it is written."""
    return "maps/%d/%s.json" % (owner_id, map_id)


def marker_key(owner_id: int, map_id: str) -> str:
    return "shared/%d/%s.json" % (owner_id, map_id)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def summary(stored: Dict[str, Any]) -> Dict[str, Any]:
    """What a list shows of a map. Of the owner only the id and the display name leave the server."""
    return {
        "id": stored["id"],
        "owner": {"id": stored["owner"]["id"], "name": stored["owner"]["name"]},
        "title": stored["title"],
        "nodes": len(stored["nodes"]),
        "shared": stored["shared"],
        "version": stored["version"],
        "updated_at": stored["updated_at"],
    }


def load_map(owner_id: int, map_id: str) -> Dict[str, Any]:
    """The stored map, or a 404. The id is matched before it becomes part of a key."""
    stored = storage.load_json(map_key(owner_id, map_id), default=None) if MAP_ID.fullmatch(map_id) else None
    if not isinstance(stored, dict) or not isinstance(stored.get("nodes"), list):
        raise Refused(404, "not_found", "That map does not exist, or it is not shared.")
    return stored


def mark_shared(stored: Dict[str, Any]) -> None:
    """Keep the "Shared maps" list in step with the map.

    The list is a second object, and two saves are not one transaction: after
    a failure between them the marker can be wrong. So the marker decides only
    what the list shows; whether a map may be read is asked of the map itself
    (read_map), and every later save writes the marker again.
    """
    key = marker_key(stored["owner"]["id"], stored["id"])
    if stored["shared"]:
        storage.save_json(key, summary(stored))
    else:
        storage.delete(key)


def change_map(visitor: Visitor, map_id: str, sent: Dict[str, Any], whole: bool) -> Dict[str, Any]:
    """Save a new version of the visitor's own map; a save that started from an older version is refused."""
    version = clean_version(sent.get("version"))
    title = clean_title(sent.get("title")) if whole or "title" in sent else None
    nodes = clean_nodes(sent.get("nodes")) if whole else None
    if "shared" in sent and not isinstance(sent["shared"], bool):
        raise bad('"shared" is true or false.')
    with WRITING:
        stored = load_map(visitor.id, map_id)
        if stored.get("version") != version:
            raise Refused(
                409, "stale", "This map was changed in another tab or window since you opened it. Reload it to go on."
            )
        if title is not None:
            stored["title"] = title
        if nodes is not None:
            stored["nodes"] = nodes
        if "shared" in sent:
            stored["shared"] = sent["shared"]
        stored["version"] = version + 1
        stored["updated_at"] = now()
        stored["owner"] = {"id": visitor.id, "username": visitor.username, "name": visitor.name}
        storage.save_json(map_key(visitor.id, map_id), stored)
        mark_shared(stored)
    return stored


# --- Routes ----------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/maps")
def list_maps(visitor: Optional[Visitor] = Depends(get_visitor)):
    """The visitor's own maps and the maps other members share. A guest sees the shared ones."""
    mine = []
    if visitor is not None:
        # Reading each map for its title is fine for MAX_MAPS small maps.
        for item in storage.list("maps/%d/" % visitor.id):
            stored = storage.load_json(item.key, default=None)  # gone since the listing: not an error
            if isinstance(stored, dict) and isinstance(stored.get("nodes"), list):
                mine.append(summary(stored))
    shared = []
    for item in storage.list("shared/"):
        marker = storage.load_json(item.key, default=None)
        owner = marker.get("owner") if isinstance(marker, dict) else None
        if isinstance(owner, dict) and (visitor is None or owner.get("id") != visitor.id):
            shared.append(marker)
    newest_first = {"key": lambda found: str(found.get("updated_at")), "reverse": True}
    return {
        "signed_in": visitor is not None,
        "me": {"id": visitor.id, "name": visitor.name} if visitor else None,
        "mine": sorted(mine, **newest_first),
        "shared": sorted(shared, **newest_first),
        "limits": {"maps": MAX_MAPS, "nodes": MAX_NODES, "title": MAX_TITLE, "text": MAX_TEXT},
    }


@app.post("/api/maps", status_code=201)
def create_map(visitor: Visitor = Depends(member), sent: Dict[str, Any] = Depends(document)):
    """A new map of the visitor: empty, or with the nodes sent along (a copy of a shared map)."""
    title = clean_title(sent.get("title"))
    if "nodes" in sent:
        nodes = clean_nodes(sent["nodes"])
    else:
        nodes = [{"id": "root", "parent": None, "text": title, "color": None, "collapsed": False}]
    with WRITING:
        if len(storage.list("maps/%d/" % visitor.id)) >= MAX_MAPS:
            raise Refused(409, "limit_reached", "You have %d maps, which is the limit. Delete one first." % MAX_MAPS)
        stored = {
            "id": secrets.token_hex(6),
            "owner": {"id": visitor.id, "username": visitor.username, "name": visitor.name},
            "title": title,
            "shared": False,
            "version": 1,
            "created_at": now(),
            "updated_at": now(),
            "nodes": nodes,
        }
        storage.save_json(map_key(visitor.id, stored["id"]), stored)
    return dict(summary(stored), nodes=nodes, editable=True)


@app.get("/api/maps/{owner_id}/{map_id}")
def read_map(owner_id: int, map_id: str, visitor: Optional[Visitor] = Depends(get_visitor)):
    """A map with its nodes: for its owner, and for everybody else while it is shared."""
    stored = load_map(owner_id, map_id)
    own = visitor is not None and visitor.id == owner_id
    if not own and stored.get("shared") is not True:
        # The same answer as for a map that does not exist: a guess learns nothing.
        raise Refused(404, "not_found", "That map does not exist, or it is not shared.")
    return dict(summary(stored), nodes=stored["nodes"], editable=own)


@app.put("/api/maps/{map_id}")
def save_map(map_id: str, visitor: Visitor = Depends(member), sent: Dict[str, Any] = Depends(document)):
    """The editor's save: title, nodes and "shared" at once, from the version the page loaded."""
    return summary(change_map(visitor, map_id, sent, whole=True))


@app.patch("/api/maps/{map_id}")
def retitle_map(map_id: str, visitor: Visitor = Depends(member), sent: Dict[str, Any] = Depends(document)):
    """Rename a map, or share it, from the list: the nodes stay as they are."""
    return summary(change_map(visitor, map_id, sent, whole=False))


@app.delete("/api/maps/{map_id}")
def delete_map(map_id: str, visitor: Visitor = Depends(member)):
    with WRITING:
        load_map(visitor.id, map_id)  # 404 for a map that is not the visitor's
        storage.delete(marker_key(visitor.id, map_id))  # first, so the list never names a map that is gone
        storage.delete(map_key(visitor.id, map_id))
    return {"deleted": map_id}
