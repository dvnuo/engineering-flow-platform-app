"""The SDK of EFP Apps.

An app is a small web app that runs inside the EFP app host and uses, with
one token, what its owner has in the EFP Portal:

    from efp_app import llm, connectors, storage, visitor, app

    who = visitor.current(request.headers)            # the signed-in visitor, or None
    answer = llm.complete("Hello", visitor=who)       # the owner's language model
    issue = connectors.get("jira", "/rest/api/2/issue/ABC-1")
    storage.save_json("state.json", {"count": 1})     # survives restarts and deployments
    app.base_path()                                   # the path prefix the app is served under

Errors are typed (``efp_app.errors``); helpers for FastAPI, Flask, Streamlit
and Gradio live in ``efp_app.integrations``; the command-line tool is
``efp-app`` (``efp_app.cli``). See docs/DEVELOPING_APPS.md.
"""

from . import app, client, config, connectors, errors, llm, storage, visitor
from ._version import __version__
from .errors import EfpError
from .visitor import Visitor

__all__ = [
    "EfpError",
    "Visitor",
    "__version__",
    "app",
    "client",
    "config",
    "connectors",
    "errors",
    "llm",
    "storage",
    "visitor",
]
