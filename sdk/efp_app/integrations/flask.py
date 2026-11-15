"""Flask: the visitor of the current request.

    from efp_app.integrations.flask import get_visitor, require_visitor

    @app.get("/")
    def index():
        who = get_visitor()                  # a Visitor, or None for an anonymous visitor
        return render_template("index.html", visitor=who)

    @app.post("/notes")
    def add_note():
        who = require_visitor()              # aborts with 401 for an anonymous visitor
        ...
"""

from typing import Optional

from .. import visitor as _visitor
from ..visitor import Visitor


def get_visitor() -> Optional[Visitor]:
    """The signed-in visitor of the request being handled, or ``None``."""
    from flask import request

    return _visitor.current(request.headers)


def require_visitor() -> Visitor:
    """The signed-in visitor; an anonymous request is aborted with 401."""
    who = get_visitor()
    if who is None:
        from flask import abort

        abort(401, description="Sign in to use this app.")
    return who  # type: ignore[return-value]
