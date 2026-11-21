"""Example EFP app: a poll in which every member has one vote.

It shows:

- who is visiting, and turning away visitors who are not signed in    require_visitor
- data that many visitors write at the same moment                    efp_app.storage

Storage has no transactions: when two saves of the same object meet, one of
them is lost. So nothing here is shared between voters. Each visitor owns one
object, votes/<poll id>/<visitor id>.json; voting again replaces it, and
counting reads them all.

The poll itself is poll.json next to this file. To ask something else, change
it, give it a new "id" (the count starts from zero), push and deploy.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/poll").
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from efp_app import Visitor, storage
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor, require_visitor

HERE = Path(__file__).parent
POLL = json.loads((HERE / "poll.json").read_text(encoding="utf-8"))
OPTION_IDS = [option["id"] for option in POLL["options"]]
if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", POLL["id"]):
    # The id becomes part of a storage key.
    raise ValueError('poll.json: "id" may hold letters, digits, "-" and "_"')

log = logging.getLogger("app")
app = FastAPI(title="Team poll")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Choice(BaseModel):
    option: str = Field(max_length=64)


def vote_key(visitor: Visitor) -> str:
    """The one object a visitor writes: nobody else ever saves this key."""
    return "votes/%s/%d.json" % (POLL["id"], visitor.id)


def problem(exc: EfpError) -> JSONResponse:
    """An SDK error as an answer the page can show; the detail goes to the log."""
    log.warning("%s", exc)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.user_message}},
        status_code=429 if exc.status == 429 else 502,
    )


def result(visitor: Optional[Visitor]) -> dict:
    """The poll with its counts, and the vote of this visitor.

    Reading every vote is fine for a team. For thousands of voters, keep a
    tally object as well and rebuild it from the votes now and then.
    """
    counts = {option_id: 0 for option_id in OPTION_IDS}
    own_key = vote_key(visitor) if visitor else None
    own_vote = None
    for item in storage.list("votes/%s/" % POLL["id"]):
        vote = storage.load_json(item.key, default=None)  # gone since the listing: not an error
        option = vote.get("option") if isinstance(vote, dict) else None
        if option not in counts:
            continue  # a vote for an option that poll.json no longer has
        counts[option] += 1
        if item.key == own_key:
            own_vote = option
    return {
        "question": POLL["question"],
        "options": [
            {"id": option["id"], "label": option["label"], "votes": counts[option["id"]]}
            for option in POLL["options"]
        ],
        "total": sum(counts.values()),
        "signed_in": visitor is not None,
        "your_vote": own_vote,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/poll")
def read_poll(visitor: Optional[Visitor] = Depends(get_visitor)):
    """Everybody who may open the app sees the result, signed in or not."""
    try:
        return result(visitor)
    except EfpError as exc:
        return problem(exc)


@app.post("/api/vote")
def vote(choice: Choice, visitor: Visitor = Depends(require_visitor)):
    """One vote per member. require_visitor answers 401 to a visitor who is not signed in."""
    if choice.option not in OPTION_IDS:
        return JSONResponse(
            {"error": {"code": "unknown_option", "message": "Choose one of the options."}}, status_code=400
        )
    try:
        storage.save_json(
            vote_key(visitor),
            {
                "option": choice.option,
                "username": visitor.username,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
        )
        return result(visitor)
    except EfpError as exc:
        return problem(exc)


@app.delete("/api/vote")
def withdraw(visitor: Visitor = Depends(require_visitor)):
    try:
        storage.delete(vote_key(visitor))
        return result(visitor)
    except EfpError as exc:
        return problem(exc)
