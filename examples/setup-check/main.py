"""Example EFP app: see what the app may use, and try things before you build on them.

It shows:

- what the Portal says about the app: model, budget      efp_app.app.info
- the connectors that are on for it                       efp_app.connectors.available
- a page only the owner may use                           the visitor compared with the owner
- one place that turns every SDK error into an answer     a FastAPI exception handler

Deploy this first when you start with EFP apps. It tells you whether the
model is on and what a call costs, what is left of today's budget, and what
Jira, GitHub and the others answer to the path you plan to use.

Trying a path reads the owner's systems with the owner's connector. The API
below therefore answers the owner alone, whatever the audience of the app is.

Path prefix: the gateway removes /{slug} before a request arrives here, so
routes are written without it; the page uses relative URLs ("api/overview").
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from efp_app import Visitor, connectors, llm, storage
from efp_app import app as about  # "app" below is the FastAPI application
from efp_app.errors import EfpError
from efp_app.integrations.fastapi import get_visitor

HERE = Path(__file__).parent
MAX_SHOWN = 20000  # characters of a connector's answer that go to the page

log = logging.getLogger("app")
app = FastAPI(title="Setup check")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


class Refused(Exception):
    """A request this app turns away, with the sentence the page shows."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@app.exception_handler(Refused)
def refused(request: Request, exc: Refused) -> JSONResponse:
    return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@app.exception_handler(EfpError)
def sdk_error(request: Request, exc: EfpError) -> JSONResponse:
    """Every SDK error of every route ends here: a sentence for the page, the detail for the log."""
    log.warning("%s", exc)
    if exc.code in ("invalid_path", "invalid_request"):
        status = 400  # what was typed cannot be sent
    else:
        status = 429 if exc.status == 429 else 502
    return JSONResponse({"error": {"code": exc.code, "message": exc.user_message}}, status_code=status)


def owner_only(visitor: Optional[Visitor] = Depends(get_visitor)) -> Visitor:
    """The visitor, when it is the owner of the app. Everybody else is turned away.

    The owner's name comes from the Portal (asked once, then remembered); the
    visitor's from the header the gateway signs. On your own machine the
    visitor is the owner, unless EFP_DEV_VISITOR names somebody else.
    """
    owner = about.info(cached=True).owner  # raises while the app has no token: the page then says so
    if visitor is None:
        raise Refused(401, "sign_in_required", "Sign in to use this app.")
    if not owner.username or visitor.username != owner.username:
        raise Refused(403, "owner_only", "Only the owner of this app can use this page.")
    return visitor


class Trial(BaseModel):
    type: str = Field(max_length=40)
    path: str = Field(max_length=2000)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.get("/api/overview")
def overview(visitor: Visitor = Depends(owner_only)):
    info = about.info()  # asked again, for today's budget
    return {
        "app": {"name": info.name, "slug": info.slug, "url": info.url, "deployed": about.is_deployed()},
        "owner": {"username": info.owner.username, "name": info.owner.display_name, "group": info.owner.group_name},
        "model": {"enabled": info.llm.enabled, "default": info.llm.default_model, "models": list(info.llm.models)},
        "budget": {
            "daily_credits": info.budget.daily_credits,
            "used_credits": info.budget.used_credits,
            "daily_tokens": info.budget.daily_tokens,
            "used_tokens": info.budget.used_tokens,
        },
        "connectors": [
            {"type": item.type, "address": item.base_url, "write": item.write} for item in connectors.available()
        ],
        "storage": {"mode": storage.mode()},
    }


@app.post("/api/try")
def try_path(trial: Trial, visitor: Visitor = Depends(owner_only)):
    """One GET to a connector, and what came back.

    The path may carry a query ("/rest/api/2/search?jql=project=ABC"). The
    other system's own status is passed on as it is: a 404 from Jira is an
    answer, not an error of this app.
    """
    answer = connectors.get(trial.type.strip(), trial.path.strip(), visitor=visitor)
    if answer.json is not None:
        body = json.dumps(answer.json, indent=2, ensure_ascii=False)
    else:
        body = answer.text
    return {
        "status": answer.status,
        "content_type": answer.headers.get("content-type", ""),
        "body": body[:MAX_SHOWN],
        "cut": len(body) > MAX_SHOWN or answer.truncated,
    }


@app.post("/api/ping-model")
def ping_model(visitor: Visitor = Depends(owner_only)):
    """The smallest model call: proves the model works and shows what a call costs."""
    answer = llm.complete("Reply with the single word: pong", max_output_tokens=64, visitor=visitor)
    return {
        "text": answer.text,
        "model": answer.model,
        "input_tokens": answer.usage.input_tokens,
        "output_tokens": answer.usage.output_tokens,
        "credits": answer.usage.credits,
    }
