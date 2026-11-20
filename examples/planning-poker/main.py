"""Example EFP app: planning poker, a live estimation room over a WebSocket.

It shows:

- a WebSocket behind the gateway                      the visitor comes from the headers of the handshake
- live state in memory and its copy in storage         one object per room, saved under the room's own lock
- a secret the server keeps until it is time           a pick leaves the server as "has picked" until the reveal
- housekeeping without a scheduler                     old rooms are removed when a new one is created
- settings next to the code                            deck.json

A member creates a room for a story and passes on its link. Everybody in the
room picks a card; the others see that somebody has picked, not what. When
somebody presses Reveal the cards turn over, with the spread, the average,
the median and whether everybody agrees. "New round" files the round in the
room's history and starts over.

Where the state lives. An idle app is stopped, and a deployment restarts it,
so memory is never the only place for something that matters. Every change
of a room is saved to storage first (rooms/<room id>.json) and only then put
into memory and sent to the members; a page that reconnects after a restart
gets the room back as it was. Memory holds what storage cannot: who is
connected right now. One process serves the whole app, so one registry of
rooms in this process is correct. With several processes it would not be.

An open WebSocket keeps the app awake. That is the honest exception to "an
idle app is stopped": while somebody is in a room, the app is in use.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/rooms") and
builds the WebSocket address from its own location.
"""

import asyncio
import copy
import json
import logging
import math
import mimetypes
import re
import secrets
import statistics
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from efp_app import Visitor, storage
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
MAX_TITLE = 120
MAX_LINK = 500
MAX_MESSAGE = 2000  # characters of one WebSocket message
MAX_HISTORY = 50  # finished rounds a room keeps
LISTED_ROOMS = 20
ROOM_DAYS = 30  # a room nobody changed for this long is removed
ROOM_ID = re.compile(r"[0-9a-f]{10}")  # becomes part of a storage key
# Close codes of this app (4000-4999 are free for applications). The page
# reads them to tell "do not try again" from "the connection dropped".
SIGN_IN_REQUIRED = 4401
NO_SUCH_ROOM = 4404


def read_deck(path: Path) -> List[str]:
    """deck.json, checked once when the app starts: a mistake in it should stop the deployment, not a round."""
    cards = json.loads(path.read_text(encoding="utf-8")).get("cards")
    if not isinstance(cards, list) or not 2 <= len(cards) <= 24:
        raise ValueError('deck.json: "cards" lists 2 to 24 cards')
    if not all(isinstance(card, str) and 1 <= len(card) <= 8 and card == card.strip() for card in cards):
        raise ValueError("deck.json: a card is a text of 1 to 8 characters")
    if len(set(cards)) != len(cards):
        raise ValueError("deck.json: two cards are the same")
    return cards


DECK = read_deck(HERE / "deck.json")

# Windows may map .js to text/plain (the registry decides), and a browser
# refuses a module script that is not served as JavaScript.
mimetypes.add_type("text/javascript", ".js")

log = logging.getLogger("app")
app = FastAPI(title="Planning poker")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Refused(Exception):
    """Something this app turns away, with the sentence the page shows."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@app.exception_handler(Refused)
def refused(request: Request, exc: Refused) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
def malformed(request: Request, exc: RequestValidationError) -> JSONResponse:
    """A body or path that does not fit a route: the same envelope as every other refusal."""
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


class NewRoom(BaseModel):
    title: str = Field(max_length=1000)
    link: Optional[str] = Field(default=None, max_length=2000)


# --- What a member sends, checked ------------------------------------------------


def clean_title(value: Any) -> str:
    title = " ".join(value.split()) if isinstance(value, str) else ""
    if not title or len(title) > MAX_TITLE:
        raise Refused(400, "bad_title", "A story needs a title of 1 to %d characters." % MAX_TITLE)
    return title


def clean_link(value: Any) -> Optional[str]:
    """An optional link to the story. Only http and https: the page makes it something to click on."""
    if value is None or value == "":
        return None
    link = value.strip() if isinstance(value, str) else ""
    try:
        parts = urlsplit(link)
    except ValueError:
        parts = None
    plain = link.isascii() and link.isprintable() and " " not in link
    if not plain or len(link) > MAX_LINK or parts is None or parts.scheme not in ("http", "https") or not parts.netloc:
        raise Refused(400, "bad_link", "A link starts with http:// or https:// and has at most %d characters." % MAX_LINK)
    return link


# --- A room: plain data, and the three things that happen to it -------------------


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def room_key(room_id: str) -> str:
    return "rooms/%s.json" % room_id


def new_room(title: str, link: Optional[str], visitor: Visitor) -> Dict[str, Any]:
    created = now()
    return {
        "id": secrets.token_hex(5),
        "title": title,
        "link": link,
        "deck": list(DECK),  # a copy: a room keeps its cards when deck.json changes
        "round": 1,
        "revealed": False,
        "picks": {},  # member id -> {"name", "card"}: what the server keeps to itself until the reveal
        "history": [],
        "created_by": {"id": visitor.id, "name": visitor.name},
        "created_at": created,
        "updated_at": created,
    }


def summary(deck: List[str], cards: List[str]) -> Dict[str, Any]:
    """What a revealed round says: who chose what, in numbers. Cards that are not numbers ("?") count as votes only."""
    numbers = []
    for card in cards:
        try:
            value = float(card)
        except ValueError:
            continue
        if math.isfinite(value):
            numbers.append(value)
    return {
        "votes": len(cards),
        "spread": [{"card": card, "count": cards.count(card)} for card in deck if card in cards],
        "average": round(sum(numbers) / len(numbers), 1) if numbers else None,
        "median": round(statistics.median(numbers), 1) if numbers else None,
        "consensus": len(cards) > 1 and len(set(cards)) == 1,
    }


def pick(room: Dict[str, Any], visitor: Visitor, card: Any) -> None:
    """A member chooses a card, another one, or (None) takes the choice back."""
    if room["revealed"]:
        raise Refused(409, "round_revealed", "This round is revealed. Start a new round to pick again.")
    if card is None:
        room["picks"].pop(str(visitor.id), None)
    elif card in room["deck"]:
        room["picks"][str(visitor.id)] = {"name": visitor.name, "card": card}
    else:
        raise Refused(400, "unknown_card", "Choose one of the cards of this room.")


def reveal(room: Dict[str, Any]) -> None:
    """Turn the cards over. Two members pressing at the same moment is fine: the second finds it done."""
    if not room["picks"]:
        raise Refused(409, "nothing_to_reveal", "Nobody has picked a card yet.")
    room["revealed"] = True


def new_round(room: Dict[str, Any], message: Dict[str, Any]) -> None:
    """File the round in the history when it was revealed, then start over, with a new story when one is named."""
    title = clean_title(message["title"]) if "title" in message else room["title"]
    link = clean_link(message["link"]) if "link" in message else room["link"]
    if room["revealed"]:
        picks = sorted(room["picks"].values(), key=lambda item: item["name"].lower())
        room["history"].append(
            {
                "round": room["round"],
                "title": room["title"],
                "link": room["link"],
                "picks": picks,
                "result": summary(room["deck"], [item["card"] for item in picks]),
                "at": now(),
            }
        )
        del room["history"][:-MAX_HISTORY]
    room.update(title=title, link=link, round=room["round"] + 1, revealed=False, picks={})


def load_room(room_id: str) -> Optional[Dict[str, Any]]:
    found = storage.load_json(room_key(room_id), default=None)
    usable = isinstance(found, dict) and isinstance(found.get("picks"), dict) and isinstance(found.get("deck"), list)
    return found if usable else None


# --- The rooms somebody is in ------------------------------------------------------


class Room:
    """A room with members in it: its state, their connections, and the lock that orders its changes."""

    def __init__(self, data: Dict[str, Any]) -> None:
        self.data = data
        self.sockets: Dict[WebSocket, Visitor] = {}
        self.lock = asyncio.Lock()


# One process serves the app, so this one registry is the truth about who is
# connected. It holds only rooms with somebody in them; an empty room lives
# in storage alone.
ROOMS: Dict[str, Room] = {}


async def enter(room_id: str) -> Optional[Room]:
    """The room with this id, read from storage when nobody is in it yet; None when there is none."""
    room = ROOMS.get(room_id)
    if room is None:
        # Storage calls block, and this is the event loop: a thread does the waiting.
        data = await asyncio.to_thread(load_room, room_id)
        if data is None:
            return None
        # Two members may arrive together and both have read the room. The
        # first one registers it; the second finds it and joins the same one.
        room = ROOMS.setdefault(room_id, Room(data))
    return room


def view(room: Room, visitor: Visitor) -> Dict[str, Any]:
    """The room as one member may see it.

    Until the round is revealed, a pick leaves the server as "picked": true
    and nothing more. Only its own member gets the card back ("you"). What
    the page does not receive, it cannot show by mistake or to the curious.
    """
    data = room.data
    people: Dict[int, Dict[str, Any]] = {}
    for key, chosen in data["picks"].items():
        people[int(key)] = {"id": int(key), "name": chosen["name"], "online": False}
    for present in room.sockets.values():  # two tabs of one member are one member
        people[present.id] = {"id": present.id, "name": present.name, "online": True}
    members = []
    for person in sorted(people.values(), key=lambda item: (item["name"].lower(), item["id"])):
        chosen = data["picks"].get(str(person["id"]))
        members.append(dict(person, picked=chosen is not None, card=chosen["card"] if chosen and data["revealed"] else None))
    own = data["picks"].get(str(visitor.id))
    cards = [chosen["card"] for chosen in data["picks"].values()]
    return {
        "id": data["id"],
        "title": data["title"],
        "link": data["link"],
        "deck": data["deck"],
        "round": data["round"],
        "revealed": data["revealed"],
        "members": members,
        "you": {"id": visitor.id, "name": visitor.name, "card": own["card"] if own else None},
        "result": summary(data["deck"], cards) if data["revealed"] else None,
        "history": data["history"][::-1],  # the latest round first
    }


async def tell_everybody(room: Room) -> None:
    """Send each member the room as that member may see it. Called with the room's lock held."""
    for socket, visitor in list(room.sockets.items()):
        try:
            await socket.send_json({"type": "state", "room": view(room, visitor)})
        except Exception:  # that connection is gone; one member's network must not stop the others' update
            room.sockets.pop(socket, None)


async def act(room: Room, visitor: Visitor, text: Any) -> None:
    """One message of a member: change the room, save it, tell everybody. A refusal is raised as Refused."""
    if not isinstance(text, str) or len(text) > MAX_MESSAGE:
        raise Refused(400, "bad_message", "That message cannot be read.")
    try:
        message = json.loads(text)
    except (ValueError, RecursionError):
        message = None
    if not isinstance(message, dict):
        raise Refused(400, "bad_message", "That message cannot be read.")
    async with room.lock:
        # Work on a copy. Storage first, memory second: what the members are
        # told is what a restart would bring back, and a save that fails
        # leaves the room exactly as it was.
        data = copy.deepcopy(room.data)
        if message.get("type") == "pick":
            pick(data, visitor, message.get("card"))
        elif message.get("type") == "reveal":
            reveal(data)
        elif message.get("type") == "new_round":
            new_round(data, message)
        else:
            raise Refused(400, "bad_message", "That message cannot be read.")
        data["updated_at"] = now()
        await asyncio.to_thread(storage.save_json, room_key(data["id"]), data)
        room.data = data
        await tell_everybody(room)


# --- Routes ---------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/rooms")
def list_rooms(visitor: Optional[Visitor] = Depends(get_visitor)):
    """The rooms that were used last, for the lobby. A guest gets none: a story's title is the team's business."""
    rooms = []
    if visitor is not None:
        newest = sorted(storage.list("rooms/"), key=lambda item: (str(item.modified_at), item.key), reverse=True)
        for item in newest[:LISTED_ROOMS]:  # only these are read; the listing itself costs one call
            found = storage.load_json(item.key, default=None)
            if isinstance(found, dict) and isinstance(found.get("title"), str):
                rooms.append(
                    {
                        "id": found.get("id"),
                        "title": found["title"],
                        "round": found.get("round"),
                        "rounds_done": len(found.get("history") or []),
                        "updated_at": found.get("updated_at"),
                    }
                )
    return {
        "signed_in": visitor is not None,
        "me": {"id": visitor.id, "name": visitor.name} if visitor else None,
        "rooms": rooms,
        "deck": DECK,
        "limits": {"title": MAX_TITLE, "link": MAX_LINK},
    }


@app.post("/api/rooms", status_code=201)
def create_room(sent: NewRoom, visitor: Visitor = Depends(member)):
    room = new_room(clean_title(sent.title), clean_link(sent.link), visitor)
    # There is no scheduler in an app, and no need for one: the rooms nobody
    # touched for a month are cleared away here, when somebody makes a new one.
    too_old = datetime.now(timezone.utc) - timedelta(days=ROOM_DAYS)
    for item in storage.list("rooms/"):
        if item.modified_at is not None and item.modified_at < too_old:
            storage.delete(item.key)
    storage.save_json(room_key(room["id"]), room)
    return {"id": room["id"], "title": room["title"], "link": room["link"]}


@app.websocket("/ws/{room_id}")
async def room_socket(websocket: WebSocket, room_id: str) -> None:
    """A member in a room, for as long as the page is open.

    The visitor is read once, from the headers of the handshake: the gateway
    signs them like those of any other request. That is who this connection
    is from then on; nothing a later message says can change it.
    """
    visitor = get_visitor(websocket)
    # Accepted first and closed with a code of our own: a browser cannot see
    # the status of a refused handshake, but it does see a close code, and
    # the page uses it to say why and to stop trying.
    await websocket.accept()
    if visitor is None:
        await websocket.close(code=SIGN_IN_REQUIRED, reason="Sign in to join a room.")
        return
    try:
        room = await enter(room_id) if ROOM_ID.fullmatch(room_id) else None
    except EfpError as exc:
        log.warning("%s", exc)
        await websocket.close(code=1011, reason=exc.user_message[:120])
        return
    if room is None:
        await websocket.close(code=NO_SUCH_ROOM, reason="This room does not exist any more.")
        return

    room.sockets[websocket] = visitor
    try:
        async with room.lock:
            await tell_everybody(room)  # the newcomer gets the room, the others see who came
        while True:
            received = await websocket.receive()
            if received["type"] == "websocket.disconnect":
                break
            try:
                await act(room, visitor, received.get("text"))
            except Refused as exc:  # only the member who asked hears about it
                await websocket.send_json({"type": "error", "code": exc.code, "message": exc.message})
            except EfpError as exc:
                log.warning("%s", exc)
                await websocket.send_json({"type": "error", "code": exc.code, "message": exc.user_message})
    except (WebSocketDisconnect, RuntimeError):
        pass  # the page was closed, or the connection broke while something was being sent
    finally:
        room.sockets.pop(websocket, None)
        if room.sockets:
            async with room.lock:
                await tell_everybody(room)
        elif ROOMS.get(room_id) is room:
            del ROOMS[room_id]  # nobody is left: the room is in storage, and that is enough
