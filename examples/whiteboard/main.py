"""Example EFP app: a drawing board whose pictures are kept as binary objects.

It shows:

- binary objects in storage                                storage.save(key, bytes), storage.load(key)
- an upload that is read with an upper bound and checked    upload, png_size
- stored bytes sent back with a content type and a cache header
- "the author, or the owner of the app"                     the visitor against app.info().owner
- two objects that belong together, without a transaction   the bytes first, the record last

The board itself is the page's business (static/): pointer events, the tools
and undo all happen in the browser. "Save" sends the finished picture as PNG
bytes. The server keeps them under drawings/<author id>/<drawing id>.png and
a small record next to them (.json): title, author, size, time. The gallery
reads the records; the pictures are fetched by the browser one by one.

What arrives is not trusted to be a picture just because the page made it:
anybody can send this API any bytes. They are checked before they are kept
(a PNG, of sensible dimensions, at most 2 MB) and counted per member.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/drawings").
"""

import logging
import mimetypes
import re
import secrets
import threading
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from efp_app import Visitor, storage
from efp_app import app as about  # "app" below is the FastAPI application
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
MAX_BYTES = 2 * 1024 * 1024  # one drawing; the board's pictures are a tenth of that
MAX_SIDE = 4096  # pixels, each way
MAX_PER_MEMBER = 24  # drawings one member may keep
MAX_TITLE = 80
DRAWING_ID = re.compile(r"[0-9a-f]{16}")  # becomes part of a storage key
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# A drawing never changes: every save gets a new id. So a browser may keep
# the bytes for good. "private": they were served to a signed-in visitor and
# belong in no shared cache.
CACHE_FOR_GOOD = "private, max-age=31536000, immutable"

# The host runs one process per app, so a lock in this process makes "count
# the member's drawings, then add one" a single step.
SAVING = threading.Lock()
_owner_unknown_until = 0.0

# Windows may map .js to text/plain (the registry decides), and a browser
# refuses a module script that is not served as JavaScript.
mimetypes.add_type("text/javascript", ".js")

log = logging.getLogger("app")
app = FastAPI(title="Whiteboard")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Refused(Exception):
    """A request this app turns away, with the sentence the page shows."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


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


def is_owner(visitor: Optional[Visitor]) -> bool:
    """Whether this visitor owns the app.

    The Portal names the owner; it is asked once and the answer remembered.
    When it cannot be asked (no token on a developer's machine, or the Portal
    is away) nobody is the owner. That is the safe answer, and it is kept for
    a minute so that a Portal that is away does not slow down every request.
    """
    global _owner_unknown_until
    if visitor is None or time.monotonic() < _owner_unknown_until:
        return False
    try:
        owner = about.info(cached=True).owner
    except EfpError as exc:
        log.warning("who owns the app is not known: %s", exc)
        _owner_unknown_until = time.monotonic() + 60
        return False
    return owner.id is not None and owner.id == visitor.id


# --- What arrives, checked -------------------------------------------------------


async def upload(request: Request) -> bytes:
    """The request body: the picture. Read with an upper bound.

    Nothing limits a body by default (the gateway lets 100 MB through, and one
    stored object may have 64 MiB). The bytes are counted while they arrive
    and the reading stops at this app's own limit.
    """
    too_large = Refused(413, "too_large", "A drawing can be at most %d MB." % (MAX_BYTES // (1024 * 1024)))
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BYTES:
        raise too_large
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_BYTES:
            raise too_large
    return bytes(received)


def png_size(data: bytes) -> Tuple[int, int]:
    """Width and height of a PNG picture, or a refusal.

    Everything is checked that can be checked without decoding the picture:
    the signature; that the file is a row of well-formed chunks (length, type,
    checksum) from IHDR to IEND with picture data in between and nothing after
    it; and the dimensions IHDR states. The server will hand these bytes to
    every visitor as image/png, so it makes sure that is what they are.
    """
    broken = Refused(400, "not_a_png", "That is not a complete PNG picture.")
    if not data.startswith(PNG_SIGNATURE):
        raise Refused(400, "not_a_png", "That is not a PNG picture.")
    position, width, height, has_pixels = len(PNG_SIGNATURE), 0, 0, False
    while True:
        # A chunk: 4 bytes length, 4 bytes type, the data, 4 bytes CRC of type and data.
        length = int.from_bytes(data[position : position + 4], "big")
        kind = data[position + 4 : position + 8]
        end = position + 12 + length
        if len(kind) < 4 or end > len(data):
            raise broken
        if zlib.crc32(data[position + 4 : end - 4]) != int.from_bytes(data[end - 4 : end], "big"):
            raise broken
        if position == len(PNG_SIGNATURE):
            if kind != b"IHDR" or length != 13:
                raise broken
            width = int.from_bytes(data[position + 8 : position + 12], "big")
            height = int.from_bytes(data[position + 12 : position + 16], "big")
            if not (0 < width <= MAX_SIDE and 0 < height <= MAX_SIDE):
                raise Refused(400, "bad_size", "A drawing is at most %d by %d pixels." % (MAX_SIDE, MAX_SIDE))
        elif kind == b"IDAT":
            has_pixels = True
        position = end
        if kind == b"IEND":
            break
    if position != len(data) or not has_pixels:
        raise broken
    return width, height


def clean_title(value: str) -> str:
    title = " ".join(value.split())
    if len(title) > MAX_TITLE:
        raise Refused(400, "bad_title", "A title has at most %d characters." % MAX_TITLE)
    return title or "Untitled"


# --- Drawings in storage ---------------------------------------------------------


def image_key(author_id: int, drawing_id: str) -> str:
    return "drawings/%d/%s.png" % (author_id, drawing_id)


def record_key(author_id: int, drawing_id: str) -> str:
    return "drawings/%d/%s.json" % (author_id, drawing_id)


def is_record(record: Any) -> bool:
    return isinstance(record, dict) and isinstance(record.get("author"), dict) and isinstance(record.get("id"), str)


def shown(record: Dict[str, Any], visitor: Optional[Visitor], owner: bool) -> Dict[str, Any]:
    """A record as the page gets it. Of the author only the id and the display name leave the server."""
    mine = visitor is not None and record["author"].get("id") == visitor.id
    return {
        "id": record["id"],
        "author": {"id": record["author"].get("id"), "name": record["author"].get("name")},
        "title": record.get("title"),
        "width": record.get("width"),
        "height": record.get("height"),
        "bytes": record.get("bytes"),
        "created_at": record.get("created_at"),
        "mine": mine,
        "can_delete": mine or owner,
    }


def kept_by(author_id: int) -> int:
    """How many drawings a member has: their records are counted, not their pictures."""
    return sum(1 for item in storage.list("drawings/%d/" % author_id) if item.key.endswith(".json"))


# --- Routes ----------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/drawings")
def gallery(visitor: Optional[Visitor] = Depends(get_visitor)):
    """Everybody's drawings, newest first. Only the records are read here, never the pictures."""
    owner = is_owner(visitor)
    drawings = []
    for item in storage.list("drawings/"):
        if not item.key.endswith(".json"):
            continue
        record = storage.load_json(item.key, default=None)  # gone since the listing: not an error
        if is_record(record):
            drawings.append(shown(record, visitor, owner))
    drawings.sort(key=lambda drawing: str(drawing["created_at"]), reverse=True)
    return {
        "signed_in": visitor is not None,
        "me": {"id": visitor.id, "name": visitor.name} if visitor else None,
        "drawings": drawings,
        "limits": {"bytes": MAX_BYTES, "side": MAX_SIDE, "per_member": MAX_PER_MEMBER, "title": MAX_TITLE},
    }


@app.post("/api/drawings", status_code=201)
def save_drawing(title: str = "", visitor: Visitor = Depends(member), data: bytes = Depends(upload)):
    """Keep a picture: the request body is the PNG, the title comes in the query.

    The order of the two saves matters, because they are not one transaction.
    The bytes go first and the record last: a drawing exists for the gallery
    once its record does, so a failure in between never shows a broken one.
    """
    title = clean_title(title)
    width, height = png_size(data)
    drawing_id = secrets.token_hex(8)
    record = {
        "id": drawing_id,
        "author": {"id": visitor.id, "username": visitor.username, "name": visitor.name},
        "title": title,
        "width": width,
        "height": height,
        "bytes": len(data),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with SAVING:
        if kept_by(visitor.id) >= MAX_PER_MEMBER:
            raise Refused(
                409, "limit_reached", "You have %d drawings in the gallery, which is the limit. Delete one first." % MAX_PER_MEMBER
            )
        storage.save(image_key(visitor.id, drawing_id), data)
        try:
            storage.save_json(record_key(visitor.id, drawing_id), record)
        except EfpError:
            # No record: take the bytes back, or they would use up the app's space unseen.
            try:
                storage.delete(image_key(visitor.id, drawing_id))
            except EfpError as exc:
                log.warning("a picture without a record stays behind: %s", exc)
            raise
    return shown(record, visitor, False)


@app.get("/api/drawings/{author_id}/{drawing_id}/image")
def image(author_id: int, drawing_id: str) -> Response:
    """The picture itself, for an <img> of the page: the bytes, what they are, and how long they stay true."""
    data = storage.load(image_key(author_id, drawing_id), default=None) if DRAWING_ID.fullmatch(drawing_id) else None
    if data is None:
        raise Refused(404, "not_found", "That drawing does not exist.")
    return Response(content=data, media_type="image/png", headers={"Cache-Control": CACHE_FOR_GOOD})


@app.delete("/api/drawings/{author_id}/{drawing_id}")
def delete_drawing(author_id: int, drawing_id: str, visitor: Visitor = Depends(member)):
    """Remove a drawing: its author may, and the owner of the app may remove any."""
    key = record_key(author_id, drawing_id)
    if not DRAWING_ID.fullmatch(drawing_id) or not is_record(storage.load_json(key, default=None)):
        raise Refused(404, "not_found", "That drawing does not exist.")
    if visitor.id != author_id and not is_owner(visitor):
        raise Refused(403, "not_yours", "Only its author, or the owner of this app, can delete a drawing.")
    storage.delete(key)  # the record first: the gallery never shows a drawing whose picture is gone
    storage.delete(image_key(author_id, drawing_id))
    return {"deleted": drawing_id}
