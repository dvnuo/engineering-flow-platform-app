"""Example EFP app: a typing sprint on what engineers type, with a history and a leaderboard.

It shows:

- a result the server works out itself from what it can check      measure
- refusing what cannot be true                                     too fast, too short, lengths that do not add up
- a member's history in one object of their own                    players/<visitor id>.json
- data that ships with the app                                     snippets.json

The page reports which lines were typed, how many keys were pressed and how
many of them were wrong. It does not report a speed. The server knows the
lines (snippets.json is its own file), so it adds up their lengths itself and
derives words per minute from that; a browser that claims "git-03" claims
exactly the 34 characters of that line. What the server cannot check (the
keys really were pressed, by a person, in that minute) it does not pretend
to: see the README.

Storage:

    runs/<visitor id>/<run id>.json    an open sprint: its topic and when it began
    players/<visitor id>.json          the member's last 30 sprints and their best one

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/runs").
"""

import json
import logging
import math
import mimetypes
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from efp_app import Visitor, storage
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor, require_visitor

HERE = Path(__file__).parent
SNIPPETS = json.loads((HERE / "snippets.json").read_text(encoding="utf-8"))
SPRINT_SECONDS = SNIPPETS["sprint_seconds"]
BUCKET_SECONDS = 5  # the speed chart has one bar for every five seconds
BUCKETS = SPRINT_SECONDS // BUCKET_SECONDS
EVERYTHING = "all"  # the topic that draws from every other topic
MAX_WPM = 250  # nobody types code faster; a result above it is refused
MAX_KEYS_A_SECOND = 25
MIN_ACCURACY = 90  # percent: a faster but sloppier sprint is kept in the history and not ranked
KEPT_RUNS = 30
CLOCK_SLACK = 2  # seconds a sprint may seem shorter than it was: two clocks never agree exactly
MAX_WAIT = 600  # seconds between opening a sprint and its end: the clock starts with the first key
TOP = 20
RUN_ID = re.compile(r"[0-9a-f]{16}")

#: Every line by its id ("git-03"), and the topic it belongs to.
TEXTS: Dict[str, str] = {}
TOPIC_OF: Dict[str, str] = {}
for _topic in SNIPPETS["topics"]:
    if not re.fullmatch(r"[a-z0-9-]{1,20}", _topic["id"]) or _topic["id"] == EVERYTHING or not _topic["snippets"]:
        raise ValueError('snippets.json: a topic needs an "id" of lower-case letters, digits and "-", and lines')
    for _number, _text in enumerate(_topic["snippets"], 1):
        if not re.fullmatch(r"[ -~]{5,80}", _text) or _text != _text.strip():
            # What cannot be typed on every keyboard has no place in a typing game.
            raise ValueError("snippets.json: a line is 5 to 80 printable ASCII characters: %r" % _text)
        TEXTS["%s-%02d" % (_topic["id"], _number)] = _text
        TOPIC_OF["%s-%02d" % (_topic["id"], _number)] = _topic["id"]

# Browsers refuse a module script that is not served as JavaScript, and some
# Windows machines have ".js" registered as plain text.
mimetypes.add_type("text/javascript", ".js")

log = logging.getLogger("app")
app = FastAPI(title="Type sprint")
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


class Chosen(BaseModel):
    topic: str = Field(max_length=20)


class Partial(BaseModel):
    """The line that was being typed when the time ran out, and how far it was right."""

    id: str = Field(max_length=40)
    chars: int = Field(ge=0, le=80)


class Typed(BaseModel):
    """What the page reports of a sprint. There is no speed in it: the server works that out."""

    completed: List[str] = Field(max_length=400)  # the ids of the lines typed to the end, in order
    partial: Optional[Partial] = None
    keystrokes: int = Field(ge=0, le=100000)  # every character typed, right or wrong
    mistakes: int = Field(ge=0, le=100000)  # the wrong ones
    buckets: List[int] = Field(max_length=120)  # characters typed right in every five seconds


def now() -> float:
    """The server's clock, in seconds. The page's clock is never asked."""
    return time.time()


def tenth(value: float) -> float:
    """Rounded to one decimal the way the page rounds (halves go up)."""
    return math.floor(value * 10 + 0.5) / 10


def line_length(topic: str, snippet_id: str) -> int:
    """The length of a line of this topic; anything else was not on the screen."""
    if snippet_id not in TEXTS or topic not in (EVERYTHING, TOPIC_OF[snippet_id]):
        raise Refused(400, "implausible", "The sprint names a line that is not part of its topic.")
    return len(TEXTS[snippet_id])


def measure(topic: str, typed: Typed) -> dict:
    """The result of a sprint, worked out from the lines the server knows. Refuses what cannot be true."""
    chars = sum(line_length(topic, snippet_id) for snippet_id in typed.completed)
    if typed.partial is not None:
        if typed.partial.chars >= line_length(topic, typed.partial.id):
            raise Refused(400, "implausible", "The typed lengths do not add up.")  # a whole line is a completed one
        chars += typed.partial.chars
    if len(typed.buckets) != BUCKETS or min(typed.buckets) < 0 or sum(typed.buckets) != chars:
        raise Refused(400, "implausible", "The typed lengths do not add up.")
    if typed.keystrokes == 0:
        raise Refused(400, "nothing_typed", "Nothing was typed in this sprint.")
    # Every character that stands right was typed right at least once.
    if typed.mistakes > typed.keystrokes or typed.keystrokes - typed.mistakes < chars:
        raise Refused(400, "implausible", "The sprint has more right characters than right keys.")
    wpm = chars / 5 * 60 / SPRINT_SECONDS  # a "word" is five characters, as everywhere
    if wpm > MAX_WPM or typed.keystrokes > MAX_KEYS_A_SECOND * SPRINT_SECONDS:
        raise Refused(400, "implausible", "This is faster than anybody types.")
    right = typed.keystrokes - typed.mistakes
    return {
        "topic": topic,
        "wpm": tenth(wpm),
        "accuracy": tenth(right * 100 / typed.keystrokes),
        "chars": chars,
        "mistakes": typed.mistakes,
        "ranked": right * 100 >= MIN_ACCURACY * typed.keystrokes,
    }


def player_key(visitor: Visitor) -> str:
    """The one object a member writes: nobody else ever saves this key."""
    return "players/%d.json" % visitor.id


def player_of(visitor: Visitor) -> dict:
    """The member's sprints as they are kept; the shape is checked, because a stored object can be old."""
    kept = storage.load_json(player_key(visitor), default=None)
    kept = kept if isinstance(kept, dict) else {}
    runs = kept.get("runs")
    best = kept.get("best")
    return {
        "runs": [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else [],
        "best": best if isinstance(best, dict) and isinstance(best.get("wpm"), (int, float)) else None,
    }


def leaderboard(visitor: Optional[Visitor]) -> dict:
    """Every member's best ranked sprint, the fastest first, and where this visitor stands.

    Reading every member's object is fine for a team. For thousands of
    players keep the top of the board in an object of its own.
    """
    entries = []
    for item in storage.list("players/"):
        kept = storage.load_json(item.key, default=None)  # gone since the listing: not an error
        best = kept.get("best") if isinstance(kept, dict) else None
        if isinstance(best, dict) and isinstance(best.get("wpm"), (int, float)):
            entries.append((item.key, kept, best))
    # The faster first; of two equal speeds the more accurate, then the earlier.
    entries.sort(key=lambda entry: (-entry[2]["wpm"], -float(entry[2].get("accuracy") or 0), str(entry[2].get("at", ""))))
    own_key = player_key(visitor) if visitor else None
    top, you = [], None
    for rank, (key, kept, best) in enumerate(entries, 1):
        if key == own_key:
            you = {"rank": rank, "wpm": best["wpm"]}
        if rank <= TOP:
            top.append(
                {
                    "rank": rank,
                    "name": str(kept.get("name") or "")[:80],
                    "wpm": best["wpm"],
                    "accuracy": best.get("accuracy"),
                    "topic": best.get("topic"),
                    "you": key == own_key,
                }
            )
    return {"top": top, "players": len(entries), "you": you, "signed_in": visitor is not None}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/snippets")
def read_snippets(visitor: Optional[Visitor] = Depends(get_visitor)):
    """The lines to type, by topic, and the numbers of a sprint. A guest gets them too and plays unranked."""
    return {
        "sprint_seconds": SPRINT_SECONDS,
        "bucket_seconds": BUCKET_SECONDS,
        "min_accuracy": MIN_ACCURACY,
        "everything": EVERYTHING,
        "topics": [
            {
                "id": topic["id"],
                "label": topic["label"],
                "snippets": [
                    {"id": "%s-%02d" % (topic["id"], number), "text": text}
                    for number, text in enumerate(topic["snippets"], 1)
                ],
            }
            for topic in SNIPPETS["topics"]
        ],
        "signed_in": visitor is not None,
        "name": visitor.name if visitor else None,
    }


@app.get("/api/leaderboard")
def read_leaderboard(visitor: Optional[Visitor] = Depends(get_visitor)):
    return leaderboard(visitor)


@app.get("/api/history")
def read_history(visitor: Visitor = Depends(require_visitor)):
    """The member's own sprints. Nobody can ask for somebody else's: the key is made from the visitor."""
    return player_of(visitor)


@app.post("/api/runs")
def start_run(chosen: Chosen, visitor: Visitor = Depends(require_visitor)):
    """Open a sprint for the signed-in member and note when. A guest gets 401 and plays unranked."""
    if chosen.topic != EVERYTHING and chosen.topic not in TOPIC_OF.values():
        raise Refused(400, "unknown_topic", "Choose one of the topics.")
    prefix = "runs/%d/" % visitor.id
    for item in storage.list(prefix):
        storage.delete(item.key)  # one open sprint per member: starting again gives up the one before
    run_id = secrets.token_hex(8)
    storage.save_json(prefix + run_id + ".json", {"topic": chosen.topic, "started": now()})
    return {"id": run_id}


@app.post("/api/runs/{run_id}/finish")
def finish_run(run_id: str, typed: Typed, visitor: Visitor = Depends(require_visitor)):
    """Take the result of a sprint, when the sprint is this member's, still open, long enough and possible."""
    if not RUN_ID.fullmatch(run_id):  # it becomes part of a storage key
        raise Refused(400, "bad_run", "This is not a sprint of this app.")
    key = "runs/%d/%s.json" % (visitor.id, run_id)  # looked for among this member's sprints only
    run = storage.load_json(key, default=None)
    started = run.get("started") if isinstance(run, dict) else None
    # delete() says whether there was something to delete: of two requests
    # that finish the same sprint at the same moment, only one is told "yes".
    if not isinstance(started, (int, float)) or not storage.delete(key):
        raise Refused(404, "no_such_run", "This sprint is not open: it was finished already, or you started a newer one.")
    lasted = now() - started
    if lasted < SPRINT_SECONDS - CLOCK_SLACK:
        raise Refused(400, "too_soon", "This sprint ended sooner than a sprint lasts.")
    if lasted > SPRINT_SECONDS + MAX_WAIT:
        raise Refused(400, "expired", "This sprint was started too long ago to count.")
    result = dict(measure(str(run.get("topic")), typed), at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

    # One object per member, and a member has one open sprint, so nobody
    # else is writing this object now: read, change, save is safe here.
    player = player_of(visitor)
    improved = result["ranked"] and (player["best"] is None or result["wpm"] > player["best"]["wpm"])
    player = {
        "username": visitor.username,
        "name": visitor.name,
        "best": result if improved else player["best"],
        "runs": (player["runs"] + [result])[-KEPT_RUNS:],
    }
    storage.save_json(player_key(visitor), player)
    return {
        "run": result,
        "improved": improved,
        "best": player["best"],
        "runs": player["runs"],
        "board": leaderboard(visitor),
    }
