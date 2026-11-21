"""Example EFP app: a sprint retrospective board with anonymous cards and dot votes.

It shows:

- what the server does not send, the page cannot leak      a card's author leaves the server for its author only
- one stored object per card and one per vote               nothing two members could overwrite
- a limit per member that is counted from the keys          votes/<board>/<card>/<voter>.json
- actions for the owner of the app alone                    reveal the authors, close the board
- settings next to the code                                 board.json; a new "id" starts a new board

Anonymous means anonymous on the wire. The page of a member never receives
who wrote the other cards, so no look into the browser's tools can tell.
Hiding names with a style sheet, or leaving them out when the list is drawn,
would not be the same thing. The same holds for votes: the page gets a number
per card and whether its own visitor voted, never who else did.

Storage has no transactions, so nothing here is shared between writers. A
card is an object its author alone writes; a vote is an object named after
the card and the voter, and voting twice writes the same object twice.
Counting needs no reading at all: the keys say who voted for what.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/board").
"""

import json
import logging
import mimetypes
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictBool

from efp_app import Visitor, storage
from efp_app import app as about  # "app" below is the FastAPI application
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
MAX_TEXT = 500  # characters on one card
MAX_CARDS = 40  # cards one member may put on one board
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")  # ids of boards and columns: they become parts of storage keys
CARD_ID = re.compile(r"[0-9a-f]{16}")


def read_settings(path: Path) -> Dict[str, Any]:
    """board.json, checked once when the app starts: a mistake in it should stop the deployment, not a visitor."""
    found = json.loads(path.read_text(encoding="utf-8"))
    columns = found.get("columns")
    votes = found.get("votes_per_member", 5)
    if not isinstance(found.get("id"), str) or not NAME.fullmatch(found["id"]):
        raise ValueError('board.json: "id" may hold letters, digits, "-" and "_"')
    if not isinstance(columns, list) or not 1 <= len(columns) <= 8:
        raise ValueError('board.json: "columns" lists one to eight columns')
    for column in columns:
        if not isinstance(column, dict) or not NAME.fullmatch(str(column.get("id"))) or not column.get("title"):
            raise ValueError('board.json: every column needs an "id" (letters, digits, "-", "_") and a "title"')
    if len({column["id"] for column in columns}) != len(columns):
        raise ValueError("board.json: two columns have the same id")
    if isinstance(votes, bool) or not isinstance(votes, int) or not 0 <= votes <= 50:
        raise ValueError('board.json: "votes_per_member" is a number from 0 to 50')
    return {
        "id": found["id"],
        "title": str(found.get("title") or found["id"])[:120],
        "votes_per_member": votes,
        "columns": [{"id": column["id"], "title": str(column["title"])[:60]} for column in columns],
    }


BOARD = read_settings(HERE / "board.json")

# The host runs one process per app, so a lock in this process makes "is the
# board open, is the member below the limit, then write" a single step.
WRITING = threading.Lock()
_owner_unknown_until = 0.0

# Windows may map .js to text/plain (the registry decides), and a browser
# refuses a module script that is not served as JavaScript.
mimetypes.add_type("text/javascript", ".js")

log = logging.getLogger("app")
app = FastAPI(title="Retro board")
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
    """A body, path or query that does not fit a route: the same envelope as every other refusal."""
    return JSONResponse({"error": {"code": "bad_input", "message": "That request is not valid."}}, status_code=400)


@app.exception_handler(EfpError)
def problem(request: Request, exc: EfpError) -> JSONResponse:
    """An SDK error as an answer the page can show; the detail goes to the log."""
    log.warning("%s", exc)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.user_message}},
        status_code=429 if exc.status == 429 else 502,
    )


class NewCard(BaseModel):
    column: str = Field(max_length=64)
    text: str = Field(max_length=4000)


class CardChange(BaseModel):
    text: str = Field(max_length=4000)
    column: Optional[str] = Field(default=None, max_length=64)


class StateChange(BaseModel):
    # Strict: true or false, not "yes" or 1. What the owner switches should not depend on a guess.
    revealed: Optional[StrictBool] = None
    closed: Optional[StrictBool] = None


# --- Who is asking ---------------------------------------------------------------


def member(visitor: Optional[Visitor] = Depends(get_visitor)) -> Visitor:
    """The signed-in visitor; a guest (possible with the audience "anyone") is turned away."""
    if visitor is None:
        raise Refused(401, "sign_in_required", "Sign in to use this app.")
    return visitor


def owner_only(visitor: Visitor = Depends(member)) -> Visitor:
    """The visitor, when it is the owner of the app; everybody else is turned away.

    The owner comes from the Portal (asked once, then remembered), the
    visitor from the header the gateway signs. While the Portal cannot be
    asked the SDK's error goes out as it is: "not set up yet" is more useful
    to the owner than "you are not the owner".
    """
    owner = about.info(cached=True).owner
    if owner.id is None or owner.id != visitor.id:
        raise Refused(403, "owner_only", "Only the owner of this app can do that.")
    return visitor


def is_owner(visitor: Optional[Visitor]) -> bool:
    """Whether to offer the owner's buttons. Never an error: without an answer nobody is the owner.

    A failure is remembered for a minute, so that a Portal that is away does
    not slow down every look at the board.
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


# --- The board in storage -----------------------------------------------------------


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def card_key(board_id: str, card_id: str) -> str:
    return "cards/%s/%s.json" % (board_id, card_id)


def vote_key(board_id: str, card_id: str, voter_id: int) -> str:
    """The one object of a vote: this card, this voter. Nobody else ever saves this key."""
    return "votes/%s/%s/%d.json" % (board_id, card_id, voter_id)


def name_of(key: str) -> str:
    """The last part of a key without ".json": the id of a card, a voter or a board."""
    return key.rsplit("/", 1)[-1][: -len(".json")]


def remember_settings() -> None:
    """Keep what board.json says about this board, for the day board.json describes the next one.

    Several members may get here at the same moment with their first cards.
    That is the one place where two saves of one object are harmless: all of
    them write the same content.
    """
    key = "index/%s.json" % BOARD["id"]
    if storage.load_json(key, default=None) != BOARD:
        storage.save_json(key, BOARD)


def settings_of(board_id: Optional[str]) -> Dict[str, Any]:
    """The current board, or a past one as it was remembered; 404 for anything else."""
    if board_id is None or board_id == BOARD["id"]:
        return BOARD
    found = storage.load_json("index/%s.json" % board_id, default=None) if NAME.fullmatch(board_id) else None
    if not isinstance(found, dict) or not isinstance(found.get("columns"), list):
        raise Refused(404, "not_found", "There is no board with that name.")
    return found


def state_of(board_id: str) -> Dict[str, bool]:
    kept = storage.load_json("state/%s.json" % board_id, default=None)
    kept = kept if isinstance(kept, dict) else {}
    return {"revealed": kept.get("revealed") is True, "closed": kept.get("closed") is True}


def cards_of(board_id: str) -> List[Dict[str, Any]]:
    """Every card of a board. Reading them all is fine for a team's board."""
    cards = []
    for item in storage.list("cards/%s/" % board_id):
        card = storage.load_json(item.key, default=None)  # gone since the listing: not an error
        if isinstance(card, dict) and isinstance(card.get("author"), dict) and isinstance(card.get("text"), str):
            cards.append(dict(card, id=name_of(item.key)))  # the key is the id, whatever the object says
    return cards


def voters_of(board_id: str) -> Dict[str, Set[str]]:
    """Card id -> the ids of its voters, read from the keys alone: no vote is opened for it."""
    voters: Dict[str, Set[str]] = {}
    for item in storage.list("votes/%s/" % board_id):
        parts = item.key.split("/")  # votes / board / card / <voter>.json
        if len(parts) == 4:
            voters.setdefault(parts[2], set()).add(name_of(item.key))
    return voters


def view(settings: Dict[str, Any], visitor: Optional[Visitor]) -> Dict[str, Any]:
    """The board as this visitor may see it.

    This function is the anonymity of the app. A card's author is put into
    the answer for the author, and for everybody once the owner has revealed
    the board; in every other case the field is None before the answer leaves
    the server. Of the votes only a count goes out, and "voted" for the
    visitor's own.
    """
    current = settings["id"] == BOARD["id"]
    state = state_of(settings["id"])
    voters = voters_of(settings["id"])
    me = str(visitor.id) if visitor else None
    columns = [column["id"] for column in settings["columns"]]
    cards = []
    for card in cards_of(settings["id"]):
        mine = visitor is not None and card["author"].get("id") == visitor.id
        voted_by = voters.get(card["id"], set())
        cards.append(
            {
                "id": card["id"],
                # A card of a column that board.json no longer has moves to the first one.
                "column": card.get("column") if card.get("column") in columns else columns[0],
                "text": card["text"],
                "created_at": card.get("created_at"),
                "edited": bool(card.get("edited_at")),
                "votes": len(voted_by),
                "voted": me in voted_by,
                "mine": mine,
                "author": card["author"].get("name") if mine or state["revealed"] else None,
            }
        )
    cards.sort(key=lambda card: (str(card["created_at"]), card["id"]))
    used = sum(1 for card in cards if card["voted"])  # votes for cards that were deleted count for nobody
    past = []
    for item in storage.list("index/"):
        kept = storage.load_json(item.key, default=None)
        if isinstance(kept, dict) and kept.get("id") != BOARD["id"] and isinstance(kept.get("id"), str):
            past.append({"id": kept["id"], "title": kept.get("title") or kept["id"]})
    return {
        "board": {key: settings[key] for key in ("id", "title", "columns", "votes_per_member")},
        "current": current,
        "revealed": state["revealed"],
        "closed": state["closed"],
        "writable": current and not state["closed"] and visitor is not None,
        "signed_in": visitor is not None,
        "me": {"id": visitor.id, "name": visitor.name} if visitor else None,
        "is_owner": is_owner(visitor),
        "votes_left": max(0, settings["votes_per_member"] - used) if visitor else 0,
        "cards": cards,
        "past": past,
        "limits": {"text": MAX_TEXT, "cards": MAX_CARDS},
    }


# --- What a member sends, checked -----------------------------------------------------


def clean_text(value: str) -> str:
    text = value.replace("\r\n", "\n").strip()
    if not text or len(text) > MAX_TEXT:
        raise Refused(400, "bad_text", "A card has 1 to %d characters." % MAX_TEXT)
    return text


def known_column(value: str) -> str:
    if value not in [column["id"] for column in BOARD["columns"]]:
        raise Refused(400, "unknown_column", "Choose one of the columns of the board.")
    return value


def must_be_open() -> None:
    if state_of(BOARD["id"])["closed"]:
        raise Refused(409, "closed", "This board is closed: it can be read, not changed.")


def card_of(card_id: str) -> Dict[str, Any]:
    """A card of the current board, or a 404. The id is matched before it becomes part of a key."""
    card = storage.load_json(card_key(BOARD["id"], card_id), default=None) if CARD_ID.fullmatch(card_id) else None
    if not isinstance(card, dict) or not isinstance(card.get("author"), dict):
        raise Refused(404, "not_found", "That card is no longer on the board.")
    return card


def own_card(card_id: str, visitor: Visitor) -> Dict[str, Any]:
    card = card_of(card_id)
    if card["author"].get("id") != visitor.id:
        raise Refused(403, "not_yours", "You can change only your own cards.")
    return card


# --- Routes ---------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/board")
def read_board(board: Optional[str] = None, visitor: Optional[Visitor] = Depends(get_visitor)):
    """The current board, or with ?board=<id> a past one (read-only)."""
    return view(settings_of(board), visitor)


@app.post("/api/cards", status_code=201)
def add_card(sent: NewCard, visitor: Visitor = Depends(member)):
    column, text = known_column(sent.column), clean_text(sent.text)
    with WRITING:
        must_be_open()
        if sum(1 for card in cards_of(BOARD["id"]) if card["author"].get("id") == visitor.id) >= MAX_CARDS:
            raise Refused(409, "limit_reached", "You have %d cards on this board, which is the limit." % MAX_CARDS)
        remember_settings()
        card = {
            "id": secrets.token_hex(8),  # random, so an id says nothing about who wrote the card or when
            "column": column,
            "text": text,
            "author": {"id": visitor.id, "username": visitor.username, "name": visitor.name},
            "created_at": now(),
        }
        storage.save_json(card_key(BOARD["id"], card["id"]), card)
    return view(BOARD, visitor)


@app.put("/api/cards/{card_id}")
def change_card(card_id: str, sent: CardChange, visitor: Visitor = Depends(member)):
    text = clean_text(sent.text)
    column = known_column(sent.column) if sent.column is not None else None
    with WRITING:
        must_be_open()
        card = own_card(card_id, visitor)
        card.update(text=text, column=column or card.get("column"), edited_at=now())
        storage.save_json(card_key(BOARD["id"], card_id), card)
    return view(BOARD, visitor)


@app.delete("/api/cards/{card_id}")
def delete_card(card_id: str, visitor: Visitor = Depends(member)):
    with WRITING:
        must_be_open()
        own_card(card_id, visitor)
        storage.delete(card_key(BOARD["id"], card_id))  # the card first: it is gone for everybody at once
        for item in storage.list("votes/%s/%s/" % (BOARD["id"], card_id)):
            storage.delete(item.key)  # its votes go back to their voters
    return view(BOARD, visitor)


@app.put("/api/cards/{card_id}/vote")
def give_vote(card_id: str, visitor: Visitor = Depends(member)):
    """One dot on a card. A member has a few per board; giving the same dot twice changes nothing."""
    with WRITING:
        must_be_open()
        card_of(card_id)
        key = vote_key(BOARD["id"], card_id, visitor.id)
        if not storage.exists(key):
            existing = {name_of(item.key) for item in storage.list("cards/%s/" % BOARD["id"])}
            used = sum(1 for card, voters in voters_of(BOARD["id"]).items() if card in existing and str(visitor.id) in voters)
            if used >= BOARD["votes_per_member"]:
                raise Refused(
                    409,
                    "no_votes_left",
                    "You have used all your %d votes. Take one back to give it to another card." % BOARD["votes_per_member"],
                )
            storage.save_json(key, {"at": now()})
    return view(BOARD, visitor)


@app.delete("/api/cards/{card_id}/vote")
def take_vote_back(card_id: str, visitor: Visitor = Depends(member)):
    with WRITING:
        must_be_open()
        card_of(card_id)
        storage.delete(vote_key(BOARD["id"], card_id, visitor.id))
    return view(BOARD, visitor)


@app.post("/api/board/state")
def change_state(sent: StateChange, visitor: Visitor = Depends(owner_only)):
    """The owner reveals who wrote the cards, or closes (and reopens) the board."""
    with WRITING:
        state = state_of(BOARD["id"])
        if sent.revealed is not None:
            state["revealed"] = sent.revealed
        if sent.closed is not None:
            state["closed"] = sent.closed
        remember_settings()
        storage.save_json("state/%s.json" % BOARD["id"], dict(state, changed_by=visitor.username, changed_at=now()))
    return view(BOARD, visitor)
