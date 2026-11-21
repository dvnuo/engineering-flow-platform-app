"""Example EFP app: a small canvas game with a leaderboard that checks what it can.

It shows:

- a game that runs in the page, with only the facts on the server     static/game.js
- a score that is accepted only when it is plausible                  finish_run, doubt
- a run that can be finished once                                     storage.delete as the claim
- what many members write at once, one object per member              best/<visitor id>.json

A score that is computed in a browser can always be forged: the page is in
the visitor's hands. So the server does not pretend to referee. It checks
what it can know (who is signed in, when the run began, that the numbers of a
result can belong together and do not exceed what the rules allow) and the
board is for fun. The README says where that line is.

The numbers of the game are rules.json. The page plays by them and the limits
below are derived from the same file, so the two cannot drift apart.

Storage, all of it small:

    runs/<visitor id>/<run id>.json    an open run: its seed and when it began
    best/<visitor id>.json             the member's best score

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/runs").
"""

import json
import logging
import mimetypes
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from efp_app import Visitor, storage
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor, require_visitor

HERE = Path(__file__).parent
RULES = json.loads((HERE / "rules.json").read_text(encoding="utf-8"))
RUN_SECONDS = RULES["run_seconds"]
BUG_KINDS = [name for name, kind in RULES["kinds"].items() if not kind.get("friend")]
SHIP_BONUS = max(kind.get("ship_bonus", 0) for kind in RULES["kinds"].values())
CLOCK_SLACK = 2  # seconds a run may seem shorter than it was: two clocks never agree exactly
MAX_PAUSE = 600  # seconds a run may take longer than it lasts: the game pauses while its tab is hidden
TOP = 20
RUN_ID = re.compile(r"[0-9a-f]{16}")

# Browsers refuse a module script that is not served as JavaScript, and some
# Windows machines have ".js" registered as plain text.
mimetypes.add_type("text/javascript", ".js")

log = logging.getLogger("app")
app = FastAPI(title="Bug squash")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# --- What the rules allow ----------------------------------------------------------------------


def multiplier(combo: int) -> int:
    """What a squash is worth after ``combo`` squashes in a row; multiplier() in static/game.js."""
    return min(RULES["combo"]["max_multiplier"], 1 + combo // RULES["combo"]["step"])


def spawn_count() -> int:
    """How many bugs and features enter the board in one run.

    The same loop as spawnTimes() in static/game.js: the gap between two
    arrivals shrinks as the run goes on, and no chance is involved.
    """
    run_ms = RUN_SECONDS * 1000
    gap = RULES["spawn_gap_ms"]
    at, count = 0.0, 0
    while at < run_ms:
        count += 1
        at += gap["start"] + (gap["end"] - gap["start"]) * at / run_ms
    return count


def max_score() -> int:
    """The score of a run in which every arrival is the most valuable bug, squashed in one unbroken combo."""
    best = max(kind["points"] for kind in RULES["kinds"].values())
    return sum(best * multiplier(squashed) for squashed in range(MAX_SPAWNS))


MAX_SPAWNS = spawn_count()
MAX_SCORE = max_score()


class Summary(BaseModel):
    """What happened in a run, as the page counted it."""

    squashed: Dict[str, int]
    features_squashed: int = Field(ge=0, le=MAX_SPAWNS)
    shipped: int = Field(ge=0, le=MAX_SPAWNS)
    escaped: int = Field(ge=0, le=MAX_SPAWNS)
    misses: int = Field(ge=0, le=100000)
    best_combo: int = Field(ge=0, le=MAX_SPAWNS)


class Result(BaseModel):
    score: int = Field(ge=0, le=10**9)
    summary: Summary


def doubt(score: int, summary: Summary) -> Optional[str]:
    """Why these numbers cannot come from a run played by the rules, or ``None``.

    This is arithmetic, not a replay: it proves that a result is impossible,
    never that it was played.
    """
    if set(summary.squashed) != set(BUG_KINDS) or min(summary.squashed.values()) < 0:
        return "The result does not name the bugs of this game."
    squashed = sum(summary.squashed.values())
    if squashed + summary.features_squashed + summary.shipped + summary.escaped > MAX_SPAWNS:
        return "The result counts more bugs than a run has."
    if summary.best_combo > squashed:
        return "The result has a longer combo than it has squashes."
    # No squash of the run was worth more than the best combo made it.
    earned = sum(count * RULES["kinds"][name]["points"] for name, count in summary.squashed.items())
    ceiling = earned * multiplier(max(summary.best_combo - 1, 0)) + summary.shipped * SHIP_BONUS
    if score > min(ceiling, MAX_SCORE):
        return "The score is higher than these squashes can give."
    return None


# --- Answers -----------------------------------------------------------------------------------


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


def now() -> float:
    """The server's clock, in seconds. The page's clock is never asked."""
    return time.time()


def best_key(visitor: Visitor) -> str:
    """The one object a member writes: nobody else ever saves this key."""
    return "best/%d.json" % visitor.id


def leaderboard(visitor: Optional[Visitor]) -> dict:
    """The top of the board, and where this visitor stands.

    Reading every member's best is fine for a team. For thousands of players
    keep the top of the board in an object of its own and rebuild it now and then.
    """
    entries = []
    for item in storage.list("best/"):
        best = storage.load_json(item.key, default=None)  # gone since the listing: not an error
        if isinstance(best, dict) and isinstance(best.get("score"), int):
            entries.append((item.key, best))
    # The higher score first; of two equal scores, the one that was reached first.
    entries.sort(key=lambda entry: (-entry[1]["score"], str(entry[1].get("at", "")), entry[0]))
    own_key = best_key(visitor) if visitor else None
    top, you = [], None
    for rank, (key, best) in enumerate(entries, 1):
        if key == own_key:
            you = {"rank": rank, "score": best["score"]}
        if rank <= TOP:
            top.append({"rank": rank, "name": str(best.get("name") or "")[:80], "score": best["score"], "you": key == own_key})
    return {"top": top, "players": len(entries), "you": you, "signed_in": visitor is not None}


# --- Routes ------------------------------------------------------------------------------------


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/rules")
def read_rules(visitor: Optional[Visitor] = Depends(get_visitor)):
    """The numbers the page plays by, and whether this visitor's runs can be ranked."""
    return {"rules": RULES, "signed_in": visitor is not None, "name": visitor.name if visitor else None}


@app.get("/api/leaderboard")
def read_leaderboard(visitor: Optional[Visitor] = Depends(get_visitor)):
    return leaderboard(visitor)


@app.post("/api/runs")
def start_run(visitor: Visitor = Depends(require_visitor)):
    """Open a run for the signed-in member and note when it began. A guest gets 401 and plays unranked."""
    prefix = "runs/%d/" % visitor.id
    for item in storage.list(prefix):
        storage.delete(item.key)  # one open run per member: starting again gives up the run before
    run_id = secrets.token_hex(8)
    seed = secrets.randbits(32)
    storage.save_json(prefix + run_id + ".json", {"seed": seed, "started": now()})
    return {"id": run_id, "seed": seed}


@app.post("/api/runs/{run_id}/finish")
def finish_run(run_id: str, result: Result, visitor: Visitor = Depends(require_visitor)):
    """Take the score of a run, when the run is this member's, still open, long enough and plausible."""
    if not RUN_ID.fullmatch(run_id):  # it becomes part of a storage key
        raise Refused(400, "bad_run", "This is not a run of this game.")
    key = "runs/%d/%s.json" % (visitor.id, run_id)  # looked for among this member's runs only
    run = storage.load_json(key, default=None)
    started = run.get("started") if isinstance(run, dict) else None
    # delete() says whether there was something to delete: of two requests
    # that finish the same run at the same moment, only one is told "yes".
    if not isinstance(started, (int, float)) or not storage.delete(key):
        raise Refused(404, "no_such_run", "This run is not open: it was finished already, or you started a newer one.")
    played = now() - started
    if played < RUN_SECONDS - CLOCK_SLACK:
        raise Refused(400, "too_soon", "This run ended sooner than a run lasts.")
    if played > RUN_SECONDS + MAX_PAUSE:
        raise Refused(400, "expired", "This run was started too long ago to be ranked.")
    reason = doubt(result.score, result.summary)
    if reason:
        raise Refused(400, "implausible", reason)

    before = storage.load_json(best_key(visitor), default=None)
    improved = not isinstance(before, dict) or not isinstance(before.get("score"), int) or result.score > before["score"]
    if improved:
        storage.save_json(
            best_key(visitor),
            {
                "score": result.score,
                "summary": result.summary.model_dump(),
                "username": visitor.username,
                "name": visitor.name,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
    return dict(leaderboard(visitor), accepted=True, score=result.score, improved=improved)
