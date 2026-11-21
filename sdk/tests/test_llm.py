"""The SSE parser and the LLM calls (section 9.2)."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from efp_app import errors, llm
from efp_app._sse import SSEParser
from conftest import FakePortal, envelope, sse

VISITOR_VALUE = "v1.eyJhIjoxfQ.c2lnbmF0dXJl"
ANSWER = {
    "text": "Hello there",
    "model": "gpt-5.4",
    "finish_reason": "stop",
    "usage": {"input_tokens": 10, "output_tokens": 20, "credits": 0.004},
}
DONE = {"type": "done", "model": "gpt-5.4", "finish_reason": "stop", "usage": {"input_tokens": 3, "output_tokens": 2, "credits": None}}


def run(coroutine):
    return asyncio.run(coroutine)


# --- The SSE parser -----------------------------------------------------------------


def feed(parser, text):
    events = [parser.feed(line) for line in text.split("\n")]
    events.append(parser.flush())
    return [event for event in events if event is not None]


def test_events_separated_by_blank_lines():
    text = 'data: {"type": "delta", "text": "Hel"}\n\ndata: {"type": "delta", "text": "lo"}\n\n'
    assert feed(SSEParser(), text) == [{"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}]


def test_events_without_blank_lines_as_the_contract_prints_them():
    text = 'data: {"type": "delta", "text": "Hel"}\ndata: {"type": "delta", "text": "lo"}\ndata: {"type": "done"}'
    assert [event["type"] for event in feed(SSEParser(), text)] == ["delta", "delta", "done"]


def test_an_event_is_returned_by_the_line_that_completes_it():
    parser = SSEParser()
    assert parser.feed('data: {"type": "delta", "text": "a"}') == {"type": "delta", "text": "a"}
    assert parser.feed("") is None


def test_comments_other_fields_and_carriage_returns_are_handled():
    parser = SSEParser()
    assert parser.feed(": keepalive") is None
    assert parser.feed("event: message") is None
    assert parser.feed("id: 7") is None
    assert parser.feed("retry: 1000") is None
    assert parser.feed('data:{"type":"delta","text":"x"}\r') == {"type": "delta", "text": "x"}


def test_data_over_several_lines_is_joined():
    parser = SSEParser()
    assert parser.feed('data: {"type": "delta",') is None
    assert parser.feed('data:  "text": "multi"}') == {"type": "delta", "text": "multi"}


def test_data_that_is_not_json_is_dropped_at_the_end_of_the_event():
    parser = SSEParser()
    assert parser.feed("data: [DONE") is None
    assert parser.feed("") is None
    assert parser.feed('data: {"type": "done"}') == {"type": "done"}
    assert parser.feed("data: 42") is None
    assert parser.flush() is None


def test_a_text_with_newlines_survives():
    parser = SSEParser()
    line = "data: " + json.dumps({"type": "delta", "text": "line one\nline two: still text"})
    assert parser.feed(line)["text"] == "line one\nline two: still text"


# --- complete -----------------------------------------------------------------------


def test_complete_with_a_prompt(portal):
    portal.on("POST", "/llm/complete", ANSWER)
    answer = llm.complete("Hello")
    assert answer == llm.Completion(
        text="Hello there",
        model="gpt-5.4",
        finish_reason="stop",
        usage=llm.Usage(input_tokens=10, output_tokens=20, credits=0.004),
    )
    assert str(answer) == "Hello there"
    assert answer.usage.total_tokens == 30
    assert portal.body() == {
        "messages": [{"role": "user", "content": "Hello"}],
        "system": None,
        "model": None,
        "max_output_tokens": None,
        "temperature": None,
        "stream": False,
    }
    assert "x-efp-visitor" not in portal.last.headers


def test_complete_with_messages_and_every_option(portal):
    portal.on("POST", "/llm/complete", ANSWER)
    messages = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
        ("user", "And now?"),
    ]
    llm.complete(
        messages,
        system="You are concise.",
        model="gpt-5.4",
        max_output_tokens=1024,
        temperature=0.2,
        visitor=VISITOR_VALUE,
    )
    assert portal.body() == {
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "And now?"},
        ],
        "system": "You are concise.",
        "model": "gpt-5.4",
        "max_output_tokens": 1024,
        "temperature": 0.2,
        "stream": False,
    }
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE


def test_a_system_message_becomes_the_system_prompt(portal):
    portal.on("POST", "/llm/complete", ANSWER)
    llm.complete([{"role": "system", "content": "Be brief."}, {"role": "user", "content": "Hi"}])
    assert portal.body()["system"] == "Be brief."
    assert portal.body()["messages"] == [{"role": "user", "content": "Hi"}]


@pytest.mark.parametrize(
    "messages, system",
    [
        ([], None),
        ([{"role": "tool", "content": "x"}], None),
        ([{"role": "user", "content": ["not", "text"]}], None),
        ([{"role": "system", "content": "a"}, {"role": "user", "content": "b"}], "also a system prompt"),
        ([{"role": "system", "content": "only a system message"}], None),
        (42, None),
    ],
)
def test_messages_that_cannot_be_sent_are_refused_before_the_call(portal, messages, system):
    with pytest.raises(errors.InvalidRequestError):
        llm.complete(messages, system=system)
    assert portal.requests == []


#: Half of a surrogate pair: what is left of an emoji when a page cut the text in the middle of it.
HALF = chr(0xD83D)


async def first_piece(stream):
    return await stream.__anext__()


@pytest.mark.parametrize(
    "call",
    [
        lambda: llm.complete("half an emoji " + HALF),
        lambda: llm.complete("Hello", system="be brief " + HALF),
        lambda: llm.complete([{"role": "user", "content": "one"}, {"role": "assistant", "content": HALF}]),
        lambda: llm.stream("half an emoji " + HALF),
        lambda: run(llm.acomplete("half an emoji " + HALF)),
        lambda: run(first_piece(llm.astream("half an emoji " + HALF))),
        lambda: llm.complete("Hello", temperature=float("nan")),
        lambda: llm.stream("Hello", temperature=float("inf")),
    ],
)
def test_text_and_numbers_that_cannot_be_sent_are_an_invalid_request(portal, call):
    # Such a text comes from a visitor. Before, it left the SDK as a UnicodeEncodeError (a NaN as a
    # ValueError), past the "except EfpError" every app is told to write.
    portal.on("POST", "/llm/complete", ANSWER)
    with pytest.raises(errors.InvalidRequestError) as caught:
        call()
    assert caught.value.status == 400 and "half an emoji" not in caught.value.message
    assert caught.value.user_message == "The app sent a request the EFP Portal could not accept."
    assert portal.requests == []


def test_credits_may_be_missing(portal):
    portal.on("POST", "/llm/complete", {"text": "x", "model": "m", "finish_reason": "length", "usage": {"input_tokens": 1, "output_tokens": 2, "credits": None}})
    answer = llm.complete("Hello")
    assert answer.usage.credits is None
    assert answer.finish_reason == "length"


def test_an_answer_without_text_is_unexpected(portal):
    portal.on("POST", "/llm/complete", {"model": "m"})
    with pytest.raises(errors.UnexpectedResponseError):
        llm.complete("Hello")


@pytest.mark.parametrize(
    "status, code, cls",
    [
        (400, "model_not_allowed", errors.ModelNotAllowedError),
        (401, "invalid_token", errors.InvalidTokenError),
        (403, "llm_disabled", errors.LlmDisabledError),
        (424, "provider_auth_failed", errors.ProviderAuthFailedError),
        (429, "budget_exceeded", errors.BudgetExceededError),
        (429, "rate_limited", errors.RateLimitedError),
        (502, "provider_error", errors.ProviderError),
    ],
)
def test_llm_errors_are_typed_for_complete_and_stream(portal, status, code, cls):
    portal.on("POST", "/llm/complete", envelope(status, code, headers={"Retry-After": "60"}))
    with pytest.raises(cls):
        llm.complete("Hello")
    with pytest.raises(cls) as caught:
        llm.stream("Hello")  # raised by the call itself, before any iteration
    assert caught.value.status == status
    assert portal.count("POST", "/llm/complete") == 2


def test_complete_uses_a_long_timeout_and_a_given_one(portal):
    portal.on("POST", "/llm/complete", ANSWER)
    llm.complete("Hello")
    assert portal.last.extensions["timeout"]["read"] == llm.LLM_TIMEOUT
    llm.complete("Hello", timeout=5)
    assert portal.last.extensions["timeout"]["read"] == 5.0


def test_a_client_can_be_passed(credentials):
    mine = FakePortal().on("POST", "/llm/complete", ANSWER)
    assert llm.complete("Hello", client=mine.client()).text == "Hello there"


# --- reasoning_effort ---------------------------------------------------------------


def test_reasoning_effort_is_left_out_unless_given(portal):
    portal.on("POST", "/llm/complete", [ANSWER, sse(DONE), ANSWER, sse(DONE)])

    async def asynchronous():
        await llm.acomplete("Hello")
        return [piece async for piece in llm.astream("Hello")]

    llm.complete("Hello")
    list(llm.stream("Hello", reasoning_effort=None))
    run(asynchronous())
    assert len(portal.requests) == 4
    for index in range(4):
        assert "reasoning_effort" not in portal.body(index)  # the Portal then uses "low"


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_reasoning_effort_is_sent_by_every_call(portal, effort):
    assert llm.REASONING_EFFORTS == ("low", "medium", "high")
    portal.on("POST", "/llm/complete", [ANSWER, sse(DONE), ANSWER, sse(DONE)])

    async def asynchronous():
        await llm.acomplete("Hello", reasoning_effort=effort)
        return [piece async for piece in llm.astream("Hello", reasoning_effort=effort)]

    llm.complete("Hello", reasoning_effort=effort, max_output_tokens=4000)
    list(llm.stream("Hello", reasoning_effort=effort))
    run(asynchronous())
    assert [portal.body(index)["reasoning_effort"] for index in range(4)] == [effort] * 4
    assert [portal.body(index)["stream"] for index in range(4)] == [False, True, False, True]
    assert portal.body(0)["max_output_tokens"] == 4000


@pytest.mark.parametrize("effort", ["max", "LOW", "minimal", "", " low", 3, True, ["low"]])
def test_another_reasoning_effort_is_a_value_error_before_any_request(portal, effort):
    with pytest.raises(ValueError) as caught:
        llm.complete("Hello", reasoning_effort=effort)
    assert "reasoning_effort" in str(caught.value)
    assert "'low', 'medium', 'high'" in str(caught.value)
    assert not isinstance(caught.value, errors.EfpError)
    with pytest.raises(ValueError):
        llm.stream("Hello", reasoning_effort=effort)
    with pytest.raises(ValueError):
        llm.astream("Hello", reasoning_effort=effort)  # raised by the call, not by the first iteration
    with pytest.raises(ValueError):
        run(llm.acomplete("Hello", reasoning_effort=effort))
    assert portal.requests == []


def test_a_high_effort_can_use_up_the_output_allowance(portal):
    # Reasoning tokens count as output tokens: the answer may then be empty.
    spent = {"input_tokens": 12, "output_tokens": 64, "credits": 0.01}
    portal.on("POST", "/llm/complete", {"text": "", "model": "gpt-5.4", "finish_reason": "length", "usage": spent})
    answer = llm.complete("Prove it", reasoning_effort="high", max_output_tokens=64)
    assert answer.text == "" and answer.finish_reason == "length"
    assert answer.usage.output_tokens == 64

    done = {"type": "done", "model": "gpt-5.4", "finish_reason": "length", "usage": spent}
    portal.on("POST", "/llm/complete", sse(done))
    stream = llm.stream("Prove it", reasoning_effort="high", max_output_tokens=64)
    assert list(stream) == []
    assert stream.text == "" and stream.finish_reason == "length" and stream.usage.output_tokens == 64


# --- stream -------------------------------------------------------------------------


def test_stream_yields_the_pieces_and_keeps_the_usage(portal):
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}, DONE))
    stream = llm.stream("Hello", visitor=VISITOR_VALUE)
    assert stream.usage is None and stream.done is False
    assert list(stream) == ["Hel", "lo"]
    assert stream.done is True
    assert stream.text == "Hello"
    assert stream.model == "gpt-5.4"
    assert stream.finish_reason == "stop"
    assert stream.usage == llm.Usage(input_tokens=3, output_tokens=2, credits=None)
    assert portal.body()["stream"] is True
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE
    assert list(stream) == []


def test_stream_without_blank_lines_and_with_keepalives(portal):
    events = [": keepalive", {"type": "delta", "text": "a"}, {"type": "delta", "text": ""}, {"type": "delta", "text": "b"}, DONE]
    portal.on("POST", "/llm/complete", sse(*events, blank_lines=False))
    assert list(llm.stream("Hello")) == ["a", "b"]


def test_completion_reads_the_rest(portal):
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}, DONE))
    stream = llm.stream("Hello")
    assert next(stream) == "Hel"
    answer = stream.completion()
    assert answer == llm.Completion(text="Hello", model="gpt-5.4", finish_reason="stop", usage=llm.Usage(3, 2, None))


def test_an_error_event_after_the_stream_began(portal):
    failure = {"type": "error", "error": {"code": "provider_error", "message": "the provider hung up"}}
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}, failure))
    stream = llm.stream("Hello")
    assert next(stream) == "Hel"
    with pytest.raises(errors.ProviderError) as caught:
        next(stream)
    assert caught.value.message == "the provider hung up"
    assert caught.value.status == 502
    assert stream.text == "Hel"
    assert list(stream) == []


def test_a_budget_error_event_keeps_its_type(portal):
    failure = {"type": "error", "error": {"code": "budget_exceeded", "message": "used up"}}
    portal.on("POST", "/llm/complete", sse(failure))
    with pytest.raises(errors.BudgetExceededError):
        list(llm.stream("Hello"))


def test_a_stream_that_ends_without_done_is_interrupted(portal):
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}))
    stream = llm.stream("Hello")
    assert next(stream) == "Hel"
    with pytest.raises(errors.StreamInterruptedError):
        next(stream)


def test_a_connection_lost_mid_stream_is_portal_unreachable(portal):
    class Broken(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"type": "delta", "text": "Hel"}\n\n'
            raise httpx.ReadError("connection reset")

    portal.on("POST", "/llm/complete", httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Broken()))
    stream = llm.stream("Hello")
    assert next(stream) == "Hel"
    with pytest.raises(errors.PortalUnreachableError):
        next(stream)


def test_a_json_answer_to_a_stream_request_still_works(portal):
    portal.on("POST", "/llm/complete", ANSWER)
    stream = llm.stream("Hello")
    assert list(stream) == ["Hello there"]
    assert stream.usage.output_tokens == 20
    assert stream.model == "gpt-5.4"


def test_stream_as_a_context_manager_closes_early(portal):
    closed = []

    class Tracked(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"type": "delta", "text": "a"}\n\n'
            yield b'data: {"type": "delta", "text": "b"}\n\n'

        def close(self):
            closed.append(True)

    portal.on("POST", "/llm/complete", httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Tracked()))
    with llm.stream("Hello") as stream:
        assert next(stream) == "a"
    assert closed == [True]
    assert list(stream) == []


# --- async --------------------------------------------------------------------------


def test_acomplete(portal):
    portal.on("POST", "/llm/complete", ANSWER)
    answer = run(llm.acomplete("Hello", system="Be brief.", visitor=VISITOR_VALUE))
    assert answer.text == "Hello there"
    assert portal.body()["system"] == "Be brief."
    assert portal.body()["stream"] is False
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE


def test_astream_iterated_directly(portal):
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}, DONE))

    async def scenario():
        stream = llm.astream("Hello")
        pieces = [piece async for piece in stream]
        return pieces, stream

    pieces, stream = run(scenario())
    assert pieces == ["Hel", "lo"]
    assert stream.text == "Hello"
    assert stream.usage == llm.Usage(3, 2, None)
    assert portal.body()["stream"] is True


def test_awaiting_astream_sends_the_request_and_raises_its_errors(portal):
    portal.on("POST", "/llm/complete", envelope(429, "budget_exceeded", headers={"Retry-After": "30"}))

    async def scenario():
        with pytest.raises(errors.BudgetExceededError) as caught:
            await llm.astream("Hello")
        return caught.value

    assert run(scenario()).retry_after == 30.0


def test_awaited_astream_then_iterated_and_completed(portal):
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "Hel"}, {"type": "delta", "text": "lo"}, DONE))

    async def scenario():
        stream = await llm.astream("Hello")
        assert len(portal.requests) == 1
        first = await stream.__anext__()
        return first, await stream.completion()

    first, answer = run(scenario())
    assert first == "Hel"
    assert answer.text == "Hello"
    assert answer.usage.input_tokens == 3
    assert len(portal.requests) == 1


def test_astream_error_event_and_interruption(portal):
    failure = {"type": "error", "error": {"code": "upstream_timeout", "message": "too slow"}}
    portal.on("POST", "/llm/complete", [sse({"type": "delta", "text": "a"}, failure), sse({"type": "delta", "text": "a"})])

    async def collect():
        return [piece async for piece in llm.astream("Hello")]

    with pytest.raises(errors.UpstreamTimeoutError):
        run(collect())
    with pytest.raises(errors.StreamInterruptedError):
        run(collect())


def test_astream_as_a_context_manager(portal):
    portal.on("POST", "/llm/complete", sse({"type": "delta", "text": "a"}, DONE))

    async def scenario():
        async with llm.astream("Hello") as stream:
            return [piece async for piece in stream]

    assert run(scenario()) == ["a"]


# --- a stream that never was one ------------------------------------------------------


def page():
    return httpx.Response(200, content=b"<html>Sign in</html>", headers={"content-type": "text/html; charset=utf-8"})


def test_a_page_instead_of_events_is_an_unexpected_answer(portal):
    # EFP_PORTAL_URL at a sign-in page or behind a proxy: 200 and a page. Read as events it holds
    # none, and the app was told that the answer was cut off and to try again.
    portal.on("POST", "/llm/complete", page())
    with pytest.raises(errors.UnexpectedResponseError) as caught:
        llm.stream("Hello")  # raised by the call itself, like a refusal
    assert caught.value.status == 200
    assert "text/html" in caught.value.message and "EFP_PORTAL_URL" in caught.value.message
    assert "Sign in" not in caught.value.message

    async def awaited():
        await llm.astream("Hello")

    async def iterated():
        return [piece async for piece in llm.astream("Hello")]

    for scenario in (awaited, iterated):
        with pytest.raises(errors.UnexpectedResponseError) as caught:
            run(scenario())
        assert "text/html" in caught.value.message


def test_a_page_closes_the_connection_it_came_on(portal):
    closed = []

    class Tracked(httpx.SyncByteStream):
        def __iter__(self):
            yield b"<html>Sign in</html>"

        def close(self):
            closed.append(True)

    portal.on("POST", "/llm/complete", httpx.Response(200, headers={"content-type": "text/html"}, stream=Tracked()))
    with pytest.raises(errors.UnexpectedResponseError):
        llm.stream("Hello")
    assert closed == [True]


def test_events_that_name_no_content_type_are_still_read(portal):
    body = "".join("data: %s\n\n" % json.dumps(event) for event in ({"type": "delta", "text": "a"}, DONE))
    portal.on("POST", "/llm/complete", httpx.Response(200, content=body.encode("utf-8")))
    assert "content-type" not in portal.routes[("POST", "/llm/complete")].headers
    assert list(llm.stream("Hello")) == ["a"]

    async def collect():
        return [piece async for piece in llm.astream("Hello")]

    assert run(collect()) == ["a"]


# --- a model call is never sent twice ---------------------------------------------------


def test_a_refused_astream_is_over_and_is_not_sent_again(portal):
    portal.on("POST", "/llm/complete", envelope(429, "budget_exceeded", headers={"Retry-After": "30"}))

    async def scenario():
        stream = llm.astream("Hello")
        with pytest.raises(errors.BudgetExceededError):
            async for _piece in stream:
                pass
        # The request was refused: that is the end of this stream. A second loop over the same
        # object used to send the POST once more.
        again = [piece async for piece in stream]
        await stream  # awaiting it again sends nothing either
        return again, await stream.completion()

    again, answer = run(scenario())
    assert again == [] and answer.text == "" and answer.finish_reason is None
    assert portal.count("POST", "/llm/complete") == 1


def test_an_astream_whose_connection_failed_is_over_as_well(portal):
    portal.on("POST", "/llm/complete", httpx.ConnectError("no route"))

    async def scenario():
        stream = llm.astream("Hello")
        with pytest.raises(errors.PortalUnreachableError):
            await stream
        return [piece async for piece in stream]

    assert run(scenario()) == []
    assert portal.count("POST", "/llm/complete") == 1
