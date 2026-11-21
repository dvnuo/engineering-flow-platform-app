"""Streamlit: the visitor of the current session.

    import streamlit as st

    from efp_app.integrations.streamlit import get_visitor

    who = get_visitor()                      # a Visitor, or None for an anonymous visitor
    st.write("Hello, " + (who.name if who else "guest"))

Streamlit hands a script the headers of the request that opened the session
(``st.context.headers``, Streamlit 1.37 or newer). The visitor value in them
is valid for 60 seconds, while a session lasts much longer, so the visitor is
verified on the first run and then remembered in ``st.session_state``.
"""

from typing import Any, Optional

from .. import visitor as _visitor
from ..visitor import Visitor

SESSION_KEY = "_efp_visitor"


def _headers(st: Any) -> Any:
    context = getattr(st, "context", None)
    if context is not None:
        try:
            return context.headers
        except Exception:
            pass
    try:  # Streamlit before 1.37
        from streamlit.web.server.websocket_headers import _get_websocket_headers

        return _get_websocket_headers() or {}
    except Exception:
        return {}


def get_visitor() -> Optional[Visitor]:
    """The signed-in visitor of this session, or ``None`` for an anonymous one."""
    import streamlit as st

    state = st.session_state
    try:
        remembered = state[SESSION_KEY] if SESSION_KEY in state else None
    except Exception:
        remembered = None
    if isinstance(remembered, Visitor):
        return remembered
    who = _visitor.current(_headers(st))
    if who is not None:
        try:
            state[SESSION_KEY] = who
        except Exception:
            pass
    return who
