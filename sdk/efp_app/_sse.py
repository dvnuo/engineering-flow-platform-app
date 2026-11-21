"""A small parser for the server-sent events of ``POST /llm/complete``.

Every event of that stream is one JSON object in ``data:`` lines
(docs/CONTRACTS.md, section 9.2). The parser follows the SSE rules (comment
lines, ``data:`` with or without a space, several ``data:`` lines joined by a
newline, a blank line ends an event) and is tolerant in one way: an event is
also complete as soon as its data is a whole JSON document, so a stream that
leaves out the blank lines between events still arrives piece by piece.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class SSEParser:
    """Feed it lines (without the line ending); it returns a decoded event or ``None``."""

    def __init__(self) -> None:
        self._data: List[str] = []

    def feed(self, line: str) -> Optional[Dict[str, Any]]:
        line = line.rstrip("\r\n")
        if line == "":
            return self._dispatch(final=True)
        if line.startswith(":"):
            return None
        name, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if name != "data":
            return None
        self._data.append(value)
        return self._dispatch(final=False)

    def flush(self) -> Optional[Dict[str, Any]]:
        """Call at the end of the stream: an event without its closing blank line."""
        return self._dispatch(final=True)

    def _dispatch(self, *, final: bool) -> Optional[Dict[str, Any]]:
        if not self._data:
            return None
        text = "\n".join(self._data)
        try:
            event = json.loads(text)
        except ValueError:
            if final:
                self._data = []
            return None
        self._data = []
        return event if isinstance(event, dict) else None
