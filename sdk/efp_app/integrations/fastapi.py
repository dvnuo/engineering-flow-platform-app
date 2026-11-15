"""FastAPI and Starlette: the visitor as a dependency.

    from typing import Optional

    from fastapi import Depends, FastAPI

    from efp_app import Visitor
    from efp_app.integrations.fastapi import get_visitor, require_visitor

    app = FastAPI()

    @app.get("/api/me")
    def me(who: Optional[Visitor] = Depends(get_visitor)):
        return {"name": who.name if who else None}

    @app.post("/api/notes")
    def add_note(who: Visitor = Depends(require_visitor)):   # 401 for an anonymous visitor
        ...

The dependencies work for WebSocket routes as well. In a plain Starlette app
call ``get_visitor(request)`` yourself.
"""

from typing import Optional

from .. import visitor as _visitor
from ..visitor import Visitor

try:  # Starlette is part of FastAPI. Without it this module still imports.
    from starlette.requests import HTTPConnection
except ImportError:  # pragma: no cover - exercised only where Starlette is absent

    class HTTPConnection:  # type: ignore[no-redef]
        """Stand-in for ``starlette.requests.HTTPConnection``."""


def get_visitor(connection: HTTPConnection) -> Optional[Visitor]:
    """The signed-in visitor of this request, or ``None`` for an anonymous one."""
    return _visitor.current(connection.headers)


def require_visitor(connection: HTTPConnection) -> Visitor:
    """The signed-in visitor; an anonymous request is answered with 401."""
    who = _visitor.current(connection.headers)
    if who is None:
        from starlette.exceptions import HTTPException

        raise HTTPException(status_code=401, detail="Sign in to use this app.")
    return who
