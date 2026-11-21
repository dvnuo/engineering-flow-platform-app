"""Example EFP app: a wheel that decides who speaks next in the stand-up.

It shows:

- a choice made on the server, so every page shows the same one        spin
- "create only if absent" on a storage that has no such call            one process, one lock, one object per pick
- an object only the owner writes, with a version against stale saves   team.json in storage, save_team
- owner-only actions, checked on the server                             owner_only

The page turns the wheel, but it does not choose. "Spin" asks the server,
which picks at random among the people who have not spoken in this round, and
the page animates the wheel to that name. Nobody can spin until they like the
result, and two people who press at the same moment get one pick, not two.

Storage has no transactions and no "create only if absent". What an app does
have is the host's promise of exactly one process. So a lock in that process
turns "look, then save" into one step; the lock guards the decision, and
everything that has to survive (the picks, the list, the history) is in
storage, because the process can be stopped at any moment.

    team.json                            the list; only the owner's requests write it
    rounds/<round>/picks/<number>.json   one object per pick of the round in progress
    history/<round>.json                 one object per round that was closed

The list starts as team.json next to this file and lives in storage once the
owner has saved it from the page.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/state").
"""

import json
import logging
import mimetypes
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from efp_app import Visitor, storage
from efp_app import app as about  # "app" below is the FastAPI application
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
MAX_MEMBERS = 40  # more names than that cannot be read on a wheel
MAX_NAME = 40
HISTORY_SHOWN = 10
TEAM_KEY = "team.json"
USERNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,63}")

# Browsers refuse a module script that is not served as JavaScript, and some
# Windows machines have ".js" registered as plain text.
mimetypes.add_type("text/javascript", ".js")

log = logging.getLogger("app")
app = FastAPI(title="Stand-up wheel")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")

# One process per app: this lock makes "read the round, then write" one step
# for every request. It holds no data; what it protects is in storage.
_step = threading.Lock()


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
    """A body that is not the shape the route expects: 400, in the envelope every other error has."""
    return JSONResponse(
        {"error": {"code": "bad_input", "message": "The app did not understand what the page sent."}}, status_code=400
    )


@app.exception_handler(EfpError)
def sdk_error(request: Request, exc: EfpError) -> JSONResponse:
    """Every SDK error of every route ends here: a sentence for the page, the detail for the log."""
    log.warning("%s", exc)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.user_message}},
        status_code=429 if exc.status == 429 else 502,
    )


class Person(BaseModel):
    username: str = Field(max_length=200)
    name: str = Field(default="", max_length=200)
    away: bool = False


class TeamEdit(BaseModel):
    version: int = Field(ge=0)  # the version of the list the owner was looking at
    members: List[Person] = Field(max_length=500)


class Spin(BaseModel):
    round: int = Field(ge=1)  # the round the page shows
    pick: int = Field(ge=1, le=MAX_MEMBERS + 1)  # the pick it asks for: one more than it has seen


class Close(BaseModel):
    round: int = Field(ge=1)


# --- The list ----------------------------------------------------------------------------------


def checked(people: List[Person]) -> List[dict]:
    """The list as it may be saved, or the sentence that says what is wrong with it."""
    if len(people) > MAX_MEMBERS:
        raise Refused(400, "too_many", "A wheel holds at most %d names." % MAX_MEMBERS)
    members, seen = [], set()
    for person in people:
        username = person.username.strip()
        if not USERNAME.fullmatch(username):
            raise Refused(
                400,
                "bad_username",
                'A username is made of letters, digits and . _ - @ (as in the Portal): "%s" is not.' % username[:40],
            )
        if username.casefold() in seen:
            raise Refused(400, "twice", '"%s" is on the list twice.' % username)
        seen.add(username.casefold())
        name = " ".join(person.name.split()) or username
        if len(name) > MAX_NAME or not name.isprintable():
            raise Refused(400, "bad_name", "A name on the wheel is at most %d characters." % MAX_NAME)
        members.append({"username": username, "name": name, "away": person.away})
    return members


try:
    STARTER = checked([Person(**entry) for entry in json.loads((HERE / "team.json").read_text(encoding="utf-8"))["members"]])
except Refused as exc:
    raise ValueError("team.json: " + exc.message) from None


def same(one: Optional[str], other: Optional[str]) -> bool:
    """Usernames typed by hand are compared without regard to case."""
    return bool(one) and bool(other) and one.casefold() == other.casefold()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_team() -> dict:
    """The list from storage, or the starter list while the owner has never saved one."""
    kept = storage.load_json(TEAM_KEY, default=None)
    members = kept.get("members") if isinstance(kept, dict) else None
    if not isinstance(members, list):
        return {"version": 0, "starter": True, "members": [dict(member, away_round=None) for member in STARTER]}
    return {
        "version": kept.get("version") if isinstance(kept.get("version"), int) else 0,
        "starter": False,
        "members": [m for m in members if isinstance(m, dict) and isinstance(m.get("username"), str)],
    }


# --- The round ---------------------------------------------------------------------------------


def history_key(number: int) -> str:
    return "history/%06d.json" % number


def pick_key(number: int, pick: int) -> str:
    return "rounds/%06d/picks/%03d.json" % (number, pick)


def current_round() -> int:
    """Rounds are numbered; the one in progress follows the last one that was closed."""
    closed = [re.fullmatch(r"history/(\d{6})\.json", item.key) for item in storage.list("history/")]
    return max([int(match.group(1)) for match in closed if match], default=0) + 1


def read_picks(number: int) -> List[dict]:
    """The picks of a round, in the order they were made."""
    picks = []
    for item in storage.list("rounds/%06d/picks/" % number):
        match = re.fullmatch(r".*/(\d{3})\.json", item.key)
        pick = storage.load_json(item.key, default=None)
        if match and isinstance(pick, dict) and isinstance(pick.get("username"), str):
            picks.append(dict(pick, number=int(match.group(1))))
    return picks


def snapshot() -> Tuple[dict, int, List[dict]]:
    """The list, the number of the round in progress and its picks, as storage has them now."""
    number = current_round()
    return read_team(), number, read_picks(number)


def waiting(team: dict, number: int, picks: List[dict]) -> List[dict]:
    """Who has not spoken in this round and is not away for it."""
    spoken = {pick["username"].casefold() for pick in picks}
    return [m for m in team["members"] if m.get("away_round") != number and m["username"].casefold() not in spoken]


def read_history() -> List[dict]:
    """The last rounds that were closed, the newest first."""
    rounds = []
    for item in reversed(storage.list("history/")[-HISTORY_SHOWN:]):
        closed = storage.load_json(item.key, default=None)
        if isinstance(closed, dict) and isinstance(closed.get("order"), list):
            rounds.append(
                {
                    "round": closed.get("round"),
                    "closed_at": closed.get("closed_at"),
                    "order": [str(name) for name in closed["order"]],
                    "left": [str(name) for name in closed.get("left") or []],
                }
            )
    return rounds


# --- Who is asking -----------------------------------------------------------------------------


def is_owner(visitor: Optional[Visitor]) -> bool:
    """Whether the visitor owns the app, for what the page offers. The routes check again."""
    if visitor is None:
        return False
    try:
        return visitor.username == about.info(cached=True).owner.username  # asked once, then remembered
    except EfpError as exc:
        log.info("nobody is the owner while the Portal cannot be asked: %s", exc)
        return False


def owner_only(visitor: Optional[Visitor] = Depends(get_visitor)) -> Visitor:
    """The visitor, when it is the owner of the app. Everybody else is turned away.

    The owner's name comes from the Portal, the visitor's from the header the
    gateway signs. Hiding the form in the page would not be a check.
    """
    owner = about.info(cached=True).owner  # raises while the app has no token: the page then says so
    if visitor is None:
        raise Refused(401, "sign_in_required", "Sign in to use this app.")
    if not owner.username or visitor.username != owner.username:
        raise Refused(403, "owner_only", "Only the owner of this app can change the list.")
    return visitor


def on_the_wheel(visitor: Optional[Visitor] = Depends(get_visitor)) -> Visitor:
    """The visitor, when their name is on the list (or they own the app). Everybody else may watch."""
    if visitor is None:
        raise Refused(401, "sign_in_required", "Sign in to spin the wheel.")
    if not any(same(member["username"], visitor.username) for member in read_team()["members"]) and not is_owner(visitor):
        raise Refused(403, "not_on_the_list", "Only the people on the wheel can spin it. You can watch.")
    return visitor


def describe(team: dict, number: int, picks: List[dict], visitor: Optional[Visitor]) -> dict:
    """Everything the page shows, for this visitor."""
    on_list = visitor is not None and any(same(member["username"], visitor.username) for member in team["members"])
    owner = is_owner(visitor)
    remaining = [member["username"] for member in waiting(team, number, picks)]
    return {
        "you": {
            "signed_in": visitor is not None,
            "name": visitor.name if visitor else None,
            "on_list": on_list,
            "owner": owner,
            "may_spin": on_list or owner,
        },
        "team": {
            "version": team["version"],
            "starter": team["starter"],
            "members": [
                {
                    "username": member["username"],
                    "name": str(member.get("name") or member["username"]),
                    "away": member.get("away_round") == number,
                }
                for member in team["members"]
            ],
        },
        "round": {"number": number, "picks": picks, "remaining": remaining, "complete": bool(picks) and not remaining},
        "history": read_history(),
    }


# --- Routes ------------------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/state")
def read_state(visitor: Optional[Visitor] = Depends(get_visitor)):
    """Everybody who may open the app can watch, on the list or not."""
    return describe(*snapshot(), visitor)


@app.post("/api/spin")
def spin(wanted: Spin, visitor: Visitor = Depends(on_the_wheel)):
    """Choose who speaks next. The page says which pick it asks for, so the same pick is never made twice."""
    with _step:
        team, number, picks = snapshot()
        made = picks[-1]["number"] if picks else 0
        earlier = [pick for pick in picks if pick["number"] == wanted.pick]
        if wanted.round != number or (not earlier and wanted.pick != made + 1):
            raise Refused(409, "out_of_step", "The round has moved on since this page was loaded.")
        if earlier:
            # Somebody pressed a moment sooner. This is the same spin, not a
            # second one: both pages turn to the same name.
            pick, created = earlier[0], False
        else:
            candidates = waiting(team, number, picks)
            if not candidates and not picks:
                raise Refused(409, "nobody", "Nobody is on the wheel.")
            if not candidates:
                raise Refused(409, "round_complete", "Everybody has spoken in this round.")
            chosen = secrets.choice(candidates)
            pick = {"number": wanted.pick, "username": chosen["username"], "name": chosen["name"], "by": visitor.name, "at": now()}
            key = pick_key(number, wanted.pick)
            if storage.exists(key):  # a pick is written once and never replaced, whatever the listing said
                raise Refused(409, "out_of_step", "The round has moved on since this page was loaded.")
            storage.save_json(key, {name: value for name, value in pick.items() if name != "number"})
            picks.append(pick)
            created = True
    return {"pick": pick, "created": created, "state": describe(team, number, picks, visitor)}


@app.post("/api/round/next")
def next_round(wanted: Close, visitor: Visitor = Depends(on_the_wheel)):
    """Close the round in progress and start the next one. Asking twice closes one round, not two."""
    with _step:
        team, number, picks = snapshot()
        closed = wanted.round == number and bool(picks)
        if closed:
            # The history first, the picks second: should the app stop in
            # between, the round is closed and its leftover picks are never read again.
            storage.save_json(
                history_key(number),
                {
                    "round": number,
                    "closed_at": now(),
                    "closed_by": visitor.name,
                    "order": [pick["name"] for pick in picks],
                    "left": [member["name"] for member in waiting(team, number, picks)],
                },
            )
            for pick in picks:
                storage.delete(pick_key(number, pick["number"]))
            number, picks = number + 1, []
    return {"closed": closed, "state": describe(team, number, picks, visitor)}


@app.put("/api/team")
def save_team(edit: TeamEdit, visitor: Visitor = Depends(owner_only)):
    """Replace the list. "Away" holds for the round in progress and is over when the next one starts."""
    members = checked(edit.members)
    with _step:
        team, number, picks = snapshot()
        if edit.version != team["version"]:
            # The owner has the page open twice: the second save would undo the first without knowing of it.
            raise Refused(409, "stale", "The list was changed in another window while you were editing.")
        team = {
            "version": team["version"] + 1,
            "members": [
                {"username": member["username"], "name": member["name"], "away_round": number if member["away"] else None}
                for member in members
            ],
            "updated_by": visitor.username,
            "updated_at": now(),
        }
        storage.save_json(TEAM_KEY, team)
    return describe(dict(team, starter=False), number, picks, visitor)
