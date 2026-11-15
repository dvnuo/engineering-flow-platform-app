"""A starter EFP app: Flask with a Jinja template.

It shows the three things most apps need:

- who is visiting                         efp_app.integrations.flask.get_visitor
- the owner's language model, streamed    efp_app.llm
- data that survives restarts             efp_app.storage

Path prefix: once deployed the app is served under /{slug}/, and a Flask app
receives the full path. The host sets SCRIPT_NAME to the prefix, so the
routes below work unchanged and url_for() returns addresses that include it.
Build every URL with url_for(), in templates and in Python; never write
"/static/app.js" or "/ask" by hand.
"""

import logging

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from efp_app import llm, storage
from efp_app.errors import BudgetExceededError, EfpError
from efp_app.integrations.flask import get_visitor

SYSTEM_PROMPT = "You are a helpful assistant. Keep your answers short."

log = logging.getLogger("app")
app = Flask(__name__)


def note_key(visitor):
    """One note per signed-in visitor; anonymous visitors share one."""
    return "notes/%s.txt" % (visitor.id if visitor else "guest")


def problem(exc):
    """An SDK error as an answer the page can show.

    ``user_message`` is a sentence written for visitors; the code and the
    detail go to the log, which the owner reads in the Portal.
    """
    log.warning("%s", exc)
    return jsonify(error={"code": exc.code, "message": exc.user_message}), 429 if exc.status == 429 else 502


def posted_text():
    body = request.get_json(silent=True) or {}
    text = body.get("text") if isinstance(body, dict) else None
    return text if isinstance(text, str) else ""


@app.get("/")
def index():
    visitor = get_visitor()  # the gateway signs the visitor into every request
    note, note_problem = "", None
    try:
        note = storage.load(note_key(visitor), default=b"").decode("utf-8")
    except EfpError as exc:
        log.warning("%s", exc)
        note_problem = exc.user_message
    return render_template("index.html", visitor=visitor, note=note, note_problem=note_problem)


@app.post("/ask")
def ask():
    """Send the question to the owner's model and stream the answer as plain text."""
    question = posted_text()
    if not question.strip():
        return jsonify(error={"code": "empty", "message": "Type a question first."}), 400
    try:
        # stream() sends the request, so a refusal is raised here, before any
        # text has gone to the browser. visitor= lets the Portal record who asked.
        stream = llm.stream(question[:8000], system=SYSTEM_PROMPT, visitor=get_visitor())
    except BudgetExceededError as exc:
        # The app's daily budget is used up: say so instead of failing.
        return problem(exc)
    except EfpError as exc:
        return problem(exc)

    def pieces():
        try:
            for piece in stream:
                yield piece
        except EfpError as exc:
            log.warning("%s", exc)
            yield "\n\n[%s]" % exc.user_message
        finally:
            stream.close()

    return Response(stream_with_context(pieces()), mimetype="text/plain")


@app.post("/note")
def save_note():
    try:
        storage.save(note_key(get_visitor()), posted_text()[:20000])
    except EfpError as exc:
        return problem(exc)
    return jsonify(saved=True)
