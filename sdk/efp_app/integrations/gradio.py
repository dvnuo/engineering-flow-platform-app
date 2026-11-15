"""Gradio: the visitor behind an event.

Add a ``gr.Request`` parameter to the event function; Gradio fills it in::

    import gradio as gr

    from efp_app.integrations.gradio import get_visitor

    def greet(request: gr.Request):
        who = get_visitor(request)           # a Visitor, or None for an anonymous visitor
        return "Hello, " + (who.name if who else "guest")

Gradio is not imported here: any object with a ``headers`` mapping works.
"""

from typing import Any, Optional

from .. import visitor as _visitor
from ..visitor import Visitor


def get_visitor(request: Any = None) -> Optional[Visitor]:
    """The signed-in visitor behind ``request`` (a ``gr.Request``), or ``None``."""
    headers = None
    if request is not None:
        try:
            headers = request.headers
        except Exception:
            headers = None
    return _visitor.current(headers)
