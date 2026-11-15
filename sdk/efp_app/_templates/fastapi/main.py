"""A starter EFP app: a FastAPI backend behind a small static page.

It shows the three things most apps need:

- who is visiting                         efp_app.integrations.fastapi.get_visitor
- the owner's language model, streamed    efp_app.llm
- data that survives restarts             efp_app.storage

Path prefix: once deployed the app is served under /{slug}/. The gateway
removes that prefix before a request arrives here, so the routes below are
written without it. The page, however, runs in the browser: it must use
relative URLs ("api/me", "static/app.js") and never "/api/me".
"""

import logging
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from efp_app import Visitor, llm, storage
from efp_app.errors import BudgetExceededError, EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
SYSTEM_PROMPT = "You are a helpful assistant. Keep your answers short."

log = logging.getLogger("app")
app = FastAPI(title="My EFP app")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Question(BaseModel):
    text: str = Field(max_length=8000)


class Note(BaseModel):
    text: str = Field(max_length=20000)


def note_key(visitor: Optional[Visitor]) -> str:
    """One note per signed-in visitor; anonymous visitors share one."""
    return "notes/%s.txt" % (visitor.id if visitor else "guest")


def problem(exc: EfpError) -> JSONResponse:
    """An SDK error as an answer the page can show.

    ``user_message`` is a sentence written for visitors; the code and the
    detail go to the log, which the owner reads in the Portal.
    """
    log.warning("%s", exc)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.user_message}},
        status_code=429 if exc.status == 429 else 502,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/me")
def me(visitor: Optional[Visitor] = Depends(get_visitor)):
    """Who is visiting: the gateway signs the visitor into every request."""
    if visitor is None:
        return {"signed_in": False}
    return {"signed_in": True, "username": visitor.username, "name": visitor.name, "group": visitor.group}


@app.post("/api/ask")
async def ask(question: Question, visitor: Optional[Visitor] = Depends(get_visitor)):
    """Send the question to the owner's model and stream the answer as plain text."""
    if not question.text.strip():
        return JSONResponse({"error": {"code": "empty", "message": "Type a question first."}}, status_code=400)
    try:
        # Awaiting sends the request, so a refusal is raised here, before any
        # text has gone to the browser. visitor= lets the Portal record who asked.
        stream = await llm.astream(question.text, system=SYSTEM_PROMPT, visitor=visitor)
    except BudgetExceededError as exc:
        # The app's daily budget is used up: say so instead of failing.
        return problem(exc)
    except EfpError as exc:
        return problem(exc)

    async def pieces():
        try:
            async for piece in stream:
                yield piece
        except EfpError as exc:
            log.warning("%s", exc)
            yield "\n\n[%s]" % exc.user_message
        finally:
            await stream.aclose()

    return StreamingResponse(pieces(), media_type="text/plain; charset=utf-8")


@app.get("/api/note")
def read_note(visitor: Optional[Visitor] = Depends(get_visitor)):
    try:
        text = storage.load(note_key(visitor), default=b"").decode("utf-8")
    except EfpError as exc:
        return problem(exc)
    return {"text": text}


@app.put("/api/note")
def write_note(note: Note, visitor: Optional[Visitor] = Depends(get_visitor)):
    try:
        storage.save(note_key(visitor), note.text)
    except EfpError as exc:
        return problem(exc)
    return {"saved": True}
