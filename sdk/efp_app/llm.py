"""The owner's language model, through the Portal (docs/CONTRACTS.md, section 9.2).

    from efp_app import llm

    answer = llm.complete("Summarise this: ...", system="You are concise.")
    print(answer.text, answer.usage.output_tokens)

    stream = llm.stream([{"role": "user", "content": "Tell me a story"}])
    for piece in stream:                 # pieces of text as they arrive
        print(piece, end="")
    print(stream.usage)                  # known once the stream has ended

``acomplete`` and ``astream`` are the same for asynchronous code. Every call
uses the model provider of the app's owner and counts against the app's
daily budget; pass ``visitor=`` (a ``Visitor`` or the ``X-EFP-Visitor``
value) so the Portal records who caused the call.

The request starts when ``stream()`` is called (when ``astream()`` is awaited
or first iterated), so an error such as ``BudgetExceededError`` is raised
before any text arrives and a web handler can still answer with an error.
"""

from __future__ import annotations

import json as _json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import httpx

from . import client as _client
from . import errors
from ._sse import SSEParser

#: Seconds an LLM call may take (for a stream: the longest silence).
LLM_TIMEOUT = 600.0
#: The values of ``reasoning_effort``; ``None`` leaves the choice to the Portal (``low``).
REASONING_EFFORTS = ("low", "medium", "high")
_PATH = "/llm/complete"

Messages = Union[str, Mapping[str, Any], Sequence[Any]]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    #: What the call cost, or ``None`` when the provider reports no cost.
    credits: Optional[float] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Completion:
    text: str
    model: Optional[str] = None
    #: ``stop`` or ``length`` (the answer hit ``max_output_tokens``).
    finish_reason: Optional[str] = None
    usage: Usage = field(default_factory=Usage)

    def __str__(self) -> str:
        return self.text


# --- Request and answer ------------------------------------------------------


def _prepare(messages: Messages, system: Optional[str]) -> Tuple[List[Dict[str, str]], Optional[str]]:
    if isinstance(messages, str):
        items: List[Any] = [{"role": "user", "content": messages}]
    elif isinstance(messages, Mapping):
        items = [messages]
    else:
        try:
            items = list(messages)
        except TypeError:
            raise errors.InvalidRequestError(
                "messages must be a prompt string or a list of {'role': ..., 'content': ...} messages"
            ) from None
    prepared: List[Dict[str, str]] = []
    lifted: List[str] = []
    for item in items:
        if isinstance(item, Mapping):
            role, content = item.get("role"), item.get("content")
        elif isinstance(item, (tuple, list)) and len(item) == 2:
            role, content = item
        else:
            role, content = getattr(item, "role", None), getattr(item, "content", None)
        if not isinstance(content, str):
            raise errors.InvalidRequestError("the content of a message must be a string")
        if role == "system":
            lifted.append(content)
            continue
        if role not in ("user", "assistant"):
            raise errors.InvalidRequestError("the role of a message must be 'user' or 'assistant', got %r" % (role,))
        prepared.append({"role": role, "content": content})
    if lifted:
        if system is not None:
            raise errors.InvalidRequestError("give the system prompt as system= or as a system message, not both")
        system = "\n\n".join(lifted)
    if not prepared:
        raise errors.InvalidRequestError("there is no message to answer")
    return prepared, system


def _body(
    messages: Messages,
    system: Optional[str],
    model: Optional[str],
    max_output_tokens: Optional[int],
    temperature: Optional[float],
    reasoning_effort: Optional[str],
    stream: bool,
) -> Dict[str, Any]:
    if reasoning_effort is not None and (
        not isinstance(reasoning_effort, str) or reasoning_effort not in REASONING_EFFORTS
    ):
        raise ValueError(
            "reasoning_effort must be one of %s, or None; got %r"
            % (", ".join(repr(level) for level in REASONING_EFFORTS), reasoning_effort)
        )
    prepared, system = _prepare(messages, system)
    body: Dict[str, Any] = {
        "messages": prepared,
        "system": system,
        "model": model,
        "max_output_tokens": max_output_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if reasoning_effort is not None:
        # Left out, the Portal uses "low" for an app call.
        body["reasoning_effort"] = reasoning_effort
    return body


def _number(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _usage(data: Any) -> Usage:
    data = data if isinstance(data, dict) else {}
    credits = data.get("credits")
    return Usage(
        input_tokens=_number(data.get("input_tokens")),
        output_tokens=_number(data.get("output_tokens")),
        credits=float(credits) if isinstance(credits, (int, float)) and not isinstance(credits, bool) else None,
    )


def _completion(payload: Any) -> Completion:
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise errors.UnexpectedResponseError("The Portal's answer to an LLM call has no text.")
    return Completion(
        text=payload["text"],
        model=payload.get("model"),
        finish_reason=payload.get("finish_reason"),
        usage=_usage(payload.get("usage")),
    )


def _is_json(response: httpx.Response) -> bool:
    return (response.headers.get("content-type") or "").lower().startswith("application/json")


def _not_an_answer(response: httpx.Response) -> Optional[errors.EfpError]:
    """The error for a success that is neither events nor JSON, or ``None`` when it can be read.

    A sign-in page or the page of a proxy answers 200 as well. Read as events
    it holds none, and the app would be told that the answer was cut off and
    to try again, when the address of the Portal is what is wrong. An answer
    that names no content type is read as events, as before.
    """
    kind = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if not kind or kind == "text/event-stream" or _is_json(response):
        return None
    return errors.UnexpectedResponseError(
        "The Portal answered an LLM call that asked for a stream with HTTP %d and the content type %s, "
        "which is neither events nor JSON. Check EFP_PORTAL_URL." % (response.status_code, kind),
        status=response.status_code,
    )


def _events_of(payload: Any) -> List[Dict[str, Any]]:
    """A complete (not streamed) answer as the two events of a stream."""
    done = _completion(payload)
    return [
        {"type": "delta", "text": done.text},
        {
            "type": "done",
            "model": done.model,
            "finish_reason": done.finish_reason,
            "usage": payload.get("usage"),
        },
    ]


# --- Streams -----------------------------------------------------------------


class _StreamState:
    """What a stream knows so far; shared by the two stream classes."""

    def __init__(self) -> None:
        self._pieces: List[str] = []
        self._parser = SSEParser()
        self._queued: List[Dict[str, Any]] = []
        self._closed = False
        #: True once the final event has arrived.
        self.done = False
        self.model: Optional[str] = None
        self.finish_reason: Optional[str] = None
        #: The usage of the call; ``None`` until the stream has ended.
        self.usage: Optional[Usage] = None

    @property
    def text(self) -> str:
        """Everything received so far."""
        return "".join(self._pieces)

    def _apply(self, event: Dict[str, Any]) -> Optional[str]:
        kind = event.get("type")
        if kind == "delta":
            piece = event.get("text")
            if isinstance(piece, str) and piece:
                self._pieces.append(piece)
                return piece
            return None
        if kind == "done":
            self.model = event.get("model")
            self.finish_reason = event.get("finish_reason")
            self.usage = _usage(event.get("usage"))
            self.done = True
            return None
        if kind == "error":
            error = event.get("error")
            error = error if isinstance(error, dict) else {}
            message = error.get("message")
            raise errors.for_code(
                str(error.get("code") or errors.ProviderError.code),
                message if isinstance(message, str) and message else None,
            )
        return None

    def _result(self) -> Completion:
        return Completion(
            text=self.text,
            model=self.model,
            finish_reason=self.finish_reason,
            usage=self.usage or Usage(),
        )

    @staticmethod
    def _interrupted() -> errors.EfpError:
        return errors.StreamInterruptedError("The answer stream ended before its final event.")


class CompletionStream(_StreamState):
    """A streamed answer: iterate it for the text pieces.

    After the last piece ``text``, ``model``, ``finish_reason`` and ``usage``
    are complete. ``completion()`` reads whatever is left and returns the
    whole answer. Reading to the end closes the connection; call ``close()``
    (or use ``with``) when you stop early.
    """

    def __init__(self, response: httpx.Response, transport_error: Callable[[Exception], errors.EfpError]) -> None:
        super().__init__()
        self._response = response
        self._transport_error = transport_error
        self._lines = None
        refused = _not_an_answer(response)
        if refused is not None:
            self.close()
            raise refused
        try:
            if _is_json(response):
                self._queued = _events_of(_json.loads(response.read().decode("utf-8")))
            else:
                self._lines = response.iter_lines()
        except httpx.HTTPError as exc:
            self.close()
            raise transport_error(exc) from exc
        except (ValueError, errors.EfpError):
            self.close()
            raise errors.UnexpectedResponseError("The Portal's answer to an LLM call could not be read.") from None

    def __iter__(self) -> "CompletionStream":
        return self

    def __next__(self) -> str:
        while True:
            if self.done or self._closed:
                raise StopIteration
            event = self._read_event()
            if event is None:
                self.close()
                raise self._interrupted()
            try:
                piece = self._apply(event)
            except errors.EfpError:
                self.close()
                raise
            if self.done:
                self.close()
            if piece:
                return piece

    def _read_event(self) -> Optional[Dict[str, Any]]:
        if self._queued:
            return self._queued.pop(0)
        if self._lines is None:
            return None
        try:
            for line in self._lines:
                event = self._parser.feed(line)
                if event is not None:
                    return event
        except httpx.HTTPError as exc:
            self.close()
            raise self._transport_error(exc) from exc
        return self._parser.flush()

    def completion(self) -> Completion:
        """Read the rest of the stream and return the whole answer."""
        for _ in self:
            pass
        return self._result()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._response.close()
            except Exception:
                pass

    def __enter__(self) -> "CompletionStream":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class AsyncCompletionStream(_StreamState):
    """The asynchronous stream: ``async for piece in stream``.

    Awaiting it (``stream = await llm.astream(...)``) sends the request and
    raises its errors right away; without that the request is sent by the
    first iteration.
    """

    def __init__(
        self,
        opener: Callable[[], Awaitable[httpx.Response]],
        transport_error: Callable[[Exception], errors.EfpError],
    ) -> None:
        super().__init__()
        self._opener = opener
        self._transport_error = transport_error
        self._response: Optional[httpx.Response] = None
        self._lines = None

    def __await__(self):
        return self._start().__await__()

    async def _start(self) -> "AsyncCompletionStream":
        if self._response is not None or self._closed:
            return self
        try:
            self._response = await self._opener()
        except BaseException:
            # A request that was refused or could not be made is the end of this stream. Left
            # open, a second "async for" over the same object would send the POST a second
            # time, and a model call is never repeated for the app.
            self._closed = True
            raise
        refused = _not_an_answer(self._response)
        if refused is not None:
            await self.aclose()
            raise refused
        try:
            if _is_json(self._response):
                self._queued = _events_of(_json.loads((await self._response.aread()).decode("utf-8")))
            else:
                self._lines = self._response.aiter_lines()
        except httpx.HTTPError as exc:
            await self.aclose()
            raise self._transport_error(exc) from exc
        except (ValueError, errors.EfpError):
            await self.aclose()
            raise errors.UnexpectedResponseError("The Portal's answer to an LLM call could not be read.") from None
        return self

    def __aiter__(self) -> "AsyncCompletionStream":
        return self

    async def __anext__(self) -> str:
        await self._start()
        while True:
            if self.done or self._closed:
                raise StopAsyncIteration
            event = await self._read_event()
            if event is None:
                await self.aclose()
                raise self._interrupted()
            try:
                piece = self._apply(event)
            except errors.EfpError:
                await self.aclose()
                raise
            if self.done:
                await self.aclose()
            if piece:
                return piece

    async def _read_event(self) -> Optional[Dict[str, Any]]:
        if self._queued:
            return self._queued.pop(0)
        if self._lines is None:
            return None
        try:
            async for line in self._lines:
                event = self._parser.feed(line)
                if event is not None:
                    return event
        except httpx.HTTPError as exc:
            await self.aclose()
            raise self._transport_error(exc) from exc
        return self._parser.flush()

    async def completion(self) -> Completion:
        """Read the rest of the stream and return the whole answer."""
        async for _ in self:
            pass
        return self._result()

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            if self._response is not None:
                try:
                    await self._response.aclose()
                except Exception:
                    pass

    async def __aenter__(self) -> "AsyncCompletionStream":
        return await self._start()

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()


# --- The calls ---------------------------------------------------------------


def complete(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.Client] = None,
) -> Completion:
    """Ask the model and wait for the whole answer.

    ``messages`` is a prompt string or a list of ``{"role": "user" |
    "assistant", "content": "..."}``. ``model`` defaults to the app's default
    model; see ``efp_app.app.info().llm.models`` for the ones allowed.

    ``reasoning_effort`` is ``"low"``, ``"medium"`` or ``"high"``; ``None``
    (the default) means ``low`` for an app call. Any other value raises
    ``ValueError`` before a request is made. Reasoning tokens count as output
    tokens, so a high effort with a small ``max_output_tokens`` can use the
    whole allowance on reasoning and come back with an empty text.
    """
    body = _body(messages, system, model, max_output_tokens, temperature, reasoning_effort, False)
    http = client or _client.get_client()
    return _completion(http.post(_PATH, body, visitor=visitor, timeout=LLM_TIMEOUT if timeout is None else timeout))


def stream(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.Client] = None,
) -> CompletionStream:
    """Ask the model and receive the answer piece by piece. The arguments are those of :func:`complete`."""
    body = _body(messages, system, model, max_output_tokens, temperature, reasoning_effort, True)
    http = client or _client.get_client()
    response = http.stream(_PATH, body, visitor=visitor, timeout=LLM_TIMEOUT if timeout is None else timeout)
    return CompletionStream(response, http.transport_error)


async def acomplete(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.AsyncClient] = None,
) -> Completion:
    """:func:`complete` for asynchronous code."""
    body = _body(messages, system, model, max_output_tokens, temperature, reasoning_effort, False)
    http = client or _client.get_async_client()
    return _completion(
        await http.post(_PATH, body, visitor=visitor, timeout=LLM_TIMEOUT if timeout is None else timeout)
    )


def astream(
    messages: Messages,
    *,
    system: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    reasoning_effort: Optional[str] = None,
    visitor: Any = None,
    timeout: _client.TimeoutLike = None,
    client: Optional[_client.AsyncClient] = None,
) -> AsyncCompletionStream:
    """:func:`stream` for asynchronous code.

    ``stream = await llm.astream(...)`` sends the request now;
    ``async for piece in llm.astream(...)`` sends it on the first iteration.
    """
    body = _body(messages, system, model, max_output_tokens, temperature, reasoning_effort, True)
    http = client or _client.get_async_client()

    def opener() -> Awaitable[httpx.Response]:
        return http.stream(_PATH, body, visitor=visitor, timeout=LLM_TIMEOUT if timeout is None else timeout)

    return AsyncCompletionStream(opener, http.transport_error)
