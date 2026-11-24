"""Image calls (section 9.4) and ``image_analysis`` of ``GET /app`` (section 9.1)."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

import efp_app
from efp_app import app as sdk_app
from efp_app import errors, images, llm
from conftest import FakePortal, envelope

VISITOR_VALUE = "v1.eyJhIjoxfQ.c2lnbmF0dXJl"
ANSWER = {
    "text": "The field Email is marked in red.",
    "model": "gpt-5.4",
    "finish_reason": "stop",
    "usage": {"input_tokens": 1830, "output_tokens": 210, "credits": None},
}
ROUTE = ("POST", "/images/inspect")

# How a file of each kind begins. The SDK and the Portal look at these first bytes and at nothing
# after them, so the rest of each "image" is padding.
PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + bytes(16)
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + bytes(16)
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + bytes(16)
GIF = b"GIF89a\x01\x00\x01\x00" + bytes(16)
#: Half of a surrogate pair: what is left of an emoji when a page cut the text in the middle of it.
HALF = chr(0xD83D)


def run(coroutine):
    return asyncio.run(coroutine)


def b64(data):
    return base64.b64encode(bytes(data)).decode("ascii")


# --- the request and the answer -------------------------------------------------------


def test_the_module_is_part_of_the_package():
    assert efp_app.images is images and "images" in efp_app.__all__
    assert (images.MAX_IMAGES, images.MAX_IMAGE_BYTES, images.MAX_TOTAL_BYTES) == (4, 5 * 1024 * 1024, 20 * 1024 * 1024)
    assert images.MEDIA_TYPES == ("image/jpeg", "image/png", "image/webp", "image/gif")
    assert images.INSPECT_TIMEOUT == 600.0


def test_inspect_sends_the_contract_body_for_one_image(portal):
    portal.on(*ROUTE, ANSWER)
    answer = images.inspect(PNG, "Which field is marked as wrong?")
    assert portal.last.method == "POST" and portal.last.url.path == "/api/app-sdk/v1/images/inspect"
    # What the call did not name is left out, as the contract words it: the Portal then uses
    # 4,096 tokens and the effort "low".
    assert portal.body() == {
        "images": [{"media_type": "image/png", "data_base64": b64(PNG)}],
        "prompt": "Which field is marked as wrong?",
        "system": None,
    }
    assert "x-efp-visitor" not in portal.last.headers
    assert answer == images.Inspection(
        text="The field Email is marked in red.",
        model="gpt-5.4",
        finish_reason="stop",
        usage=llm.Usage(input_tokens=1830, output_tokens=210, credits=None),
    )
    assert str(answer) == "The field Email is marked in red."
    assert answer.usage.total_tokens == 2040


def test_inspect_with_several_images_and_every_option(portal):
    portal.on(*ROUTE, ANSWER)
    images.inspect(
        [PNG, bytearray(JPEG), (WEBP, "image/webp"), GIF],
        "What differs between these four?",
        system="Answer in one sentence.",
        max_output_tokens=600,
        reasoning_effort="medium",
        visitor=VISITOR_VALUE,
    )
    assert portal.body() == {
        "images": [
            {"media_type": "image/png", "data_base64": b64(PNG)},
            {"media_type": "image/jpeg", "data_base64": b64(JPEG)},
            {"media_type": "image/webp", "data_base64": b64(WEBP)},
            {"media_type": "image/gif", "data_base64": b64(GIF)},
        ],
        "prompt": "What differs between these four?",
        "system": "Answer in one sentence.",
        "max_output_tokens": 600,
        "reasoning_effort": "medium",
    }
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE
    assert portal.count(*ROUTE) == 1


def portal_of_the_contract(request):
    """A Portal that takes section 9.4 by the letter and refuses everything else."""
    body = json.loads(request.content)
    assert set(body) <= {"images", "prompt", "system", "max_output_tokens", "reasoning_effort"}
    assert isinstance(body["images"], list) and 1 <= len(body["images"]) <= 4
    total = 0
    for item in body["images"]:
        assert sorted(item) == ["data_base64", "media_type"]
        data = base64.b64decode(item["data_base64"], validate=True)  # standard base64, nothing else in it
        assert 0 < len(data) <= 5242880
        total += len(data)
        assert item["media_type"] in ("image/jpeg", "image/png", "image/webp", "image/gif")
        assert item["media_type"] == images.media_type_of(data)
    assert total <= 20971520
    assert isinstance(body["prompt"], str) and body["prompt"].strip()
    assert body["system"] is None or isinstance(body["system"], str)
    assert len(body["prompt"]) + len(body["system"] or "") <= 200000
    if "max_output_tokens" in body:
        assert isinstance(body["max_output_tokens"], int) and body["max_output_tokens"] <= 16000
    if "reasoning_effort" in body:
        assert body["reasoning_effort"] in ("low", "medium", "high")
    assert request.headers["content-type"] == "application/json"
    return ANSWER


@pytest.mark.parametrize(
    "given, options",
    [
        (PNG, {}),
        (bytearray(JPEG), {"system": "Be brief."}),
        ((WEBP, "IMAGE/WEBP"), {"max_output_tokens": 16000}),
        ([PNG, JPEG, WEBP, GIF], {"system": "", "max_output_tokens": 1, "reasoning_effort": "high"}),
        ([GIF + bytes(1000), (PNG + bytes(3000), "image/png")], {"reasoning_effort": "low", "visitor": VISITOR_VALUE}),
    ],
)
def test_what_the_sdk_sends_is_what_the_contract_takes(portal, given, options):
    portal.on(*ROUTE, portal_of_the_contract)
    assert images.inspect(given, "What is shown?", **options).text == ANSWER["text"]
    assert run(images.ainspect(given, "What is shown?", **options)).text == ANSWER["text"]
    assert portal.count(*ROUTE) == 2


def test_a_visitor_object_is_passed_on_as_its_header_value(portal):
    class Who:
        raw = VISITOR_VALUE

    portal.on(*ROUTE, ANSWER)
    images.inspect(PNG, "What is shown?", visitor=Who())
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE
    images.inspect(PNG, "What is shown?", visitor="not a header value")
    assert "x-efp-visitor" not in portal.last.headers


@pytest.mark.parametrize(
    "given",
    [
        PNG,
        bytearray(PNG),
        (PNG, "image/png"),
        [PNG, "image/png"],
        (bytearray(PNG), "image/png"),
        [PNG],
        (PNG,),
        [(PNG, "image/png")],
        iter([PNG]),
    ],
)
def test_one_image_in_every_form_it_can_be_given(portal, given):
    portal.on(*ROUTE, ANSWER)
    images.inspect(given, "What is shown?")
    assert portal.body()["images"] == [{"media_type": "image/png", "data_base64": b64(PNG)}]


def test_two_images_in_a_tuple_are_two_images_not_a_pair(portal):
    portal.on(*ROUTE, ANSWER)
    images.inspect((PNG, JPEG), "What differs?")
    assert [item["media_type"] for item in portal.body()["images"]] == ["image/png", "image/jpeg"]
    images.inspect(((PNG, "image/png"), (JPEG, "image/jpeg")), "What differs?")
    assert [item["media_type"] for item in portal.body()["images"]] == ["image/png", "image/jpeg"]


@pytest.mark.parametrize("named", ["image/jpeg", "IMAGE/JPEG", " image/jpeg ", "Image/Jpeg\n"])
def test_letter_case_and_blanks_do_not_make_a_media_type_wrong(portal, named):
    portal.on(*ROUTE, ANSWER)
    images.inspect((JPEG, named), "What is shown?")
    assert portal.body()["images"][0]["media_type"] == "image/jpeg"  # what is sent is the type of the bytes


def test_an_image_of_exactly_the_largest_size_is_sent(portal):
    portal.on(*ROUTE, ANSWER)
    largest = PNG + bytes(images.MAX_IMAGE_BYTES - len(PNG))
    assert len(largest) == images.MAX_IMAGE_BYTES
    images.inspect(largest, "What is shown?")
    assert len(base64.b64decode(portal.body()["images"][0]["data_base64"])) == images.MAX_IMAGE_BYTES


def test_what_the_call_does_not_name_is_left_out(portal):
    portal.on(*ROUTE, ANSWER)
    images.inspect(PNG, "What is shown?", max_output_tokens=None, reasoning_effort=None)
    run(images.ainspect(PNG, "What is shown?"))
    for index in range(2):
        assert sorted(portal.body(index)) == ["images", "prompt", "system"]


@pytest.mark.parametrize("effort", ["low", "medium", "high"])
def test_reasoning_effort_is_sent_by_both_calls(portal, effort):
    portal.on(*ROUTE, ANSWER)
    images.inspect(PNG, "What is shown?", reasoning_effort=effort, max_output_tokens=4000)
    run(images.ainspect(PNG, "What is shown?", reasoning_effort=effort))
    assert [portal.body(index)["reasoning_effort"] for index in range(2)] == [effort] * 2
    assert portal.body(0)["max_output_tokens"] == 4000 and "max_output_tokens" not in portal.body(1)


def test_ainspect_sends_the_same_request_and_gives_the_same_answer(portal):
    portal.on(*ROUTE, ANSWER)
    options = {"system": "Be brief.", "max_output_tokens": 300, "reasoning_effort": "high", "visitor": VISITOR_VALUE}
    first = images.inspect([PNG, (GIF, "image/gif")], "What differs?", **options)
    second = run(images.ainspect([PNG, (GIF, "image/gif")], "What differs?", **options))
    assert first == second and isinstance(second, images.Inspection)
    assert portal.body(0) == portal.body(1)
    assert portal.requests[1].headers["x-efp-visitor"] == VISITOR_VALUE
    assert portal.requests[0].url == portal.requests[1].url


def test_inspect_uses_a_long_timeout_and_a_given_one(portal):
    portal.on(*ROUTE, ANSWER)
    images.inspect(PNG, "What is shown?")
    assert portal.last.extensions["timeout"]["read"] == images.INSPECT_TIMEOUT
    images.inspect(PNG, "What is shown?", timeout=5)
    assert portal.last.extensions["timeout"]["read"] == 5.0
    run(images.ainspect(PNG, "What is shown?"))
    assert portal.last.extensions["timeout"]["read"] == images.INSPECT_TIMEOUT
    run(images.ainspect(PNG, "What is shown?", timeout=httpx.Timeout(7.0)))
    assert portal.last.extensions["timeout"]["read"] == 7.0


def test_a_client_can_be_passed(credentials):
    mine = FakePortal().on(*ROUTE, ANSWER)
    assert images.inspect(PNG, "What is shown?", client=mine.client()).text == ANSWER["text"]
    assert run(images.ainspect(PNG, "What is shown?", client=mine.async_client())).text == ANSWER["text"]
    assert mine.count(*ROUTE) == 2


def test_an_answer_that_was_cut_and_one_without_usage(portal):
    portal.on(*ROUTE, {"text": "", "model": "gpt-5.4", "finish_reason": "length", "usage": {"input_tokens": 1530, "output_tokens": 64, "credits": 0.02}})
    answer = images.inspect(PNG, "Describe everything.", reasoning_effort="high", max_output_tokens=64)
    assert (answer.text, answer.finish_reason, answer.usage.credits) == ("", "length", 0.02)
    portal.on(*ROUTE, {"text": "A form."})
    assert images.inspect(PNG, "What is shown?") == images.Inspection(text="A form.", model=None, finish_reason=None, usage=llm.Usage())


# --- media_type_of --------------------------------------------------------------------


@pytest.mark.parametrize(
    "data, kind",
    [
        (JPEG, "image/jpeg"),
        (b"\xff\xd8\xff\xdb", "image/jpeg"),
        (b"\xff\xd8\xff\xe1\x00\x18Exif\x00\x00", "image/jpeg"),
        (PNG, "image/png"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (WEBP, "image/webp"),
        (b"RIFF\xff\xff\xff\xffWEBP", "image/webp"),
        (GIF, "image/gif"),
        (b"GIF87a", "image/gif"),
        (bytearray(GIF), "image/gif"),
    ],
)
def test_the_media_type_is_read_from_the_first_bytes(portal, data, kind):
    assert images.media_type_of(data) == kind and kind in images.MEDIA_TYPES
    portal.on(*ROUTE, ANSWER)
    images.inspect(data, "What is shown?")
    assert portal.body()["images"] == [{"media_type": kind, "data_base64": b64(data)}]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"BM\x36\x00\x0c\x00\x00\x00\x00\x00",  # a Windows bitmap
        b"II*\x00\x08\x00\x00\x00",  # TIFF
        b"%PDF-1.7\n",
        b"<svg xmlns='http://www.w3.org/2000/svg'/>",
        b"RIFF\x24\x00\x00\x00WAVEfmt ",  # the container of WebP, holding sound
        b"RIFF\x24\x00\x00\x00",
        b"\x89PNG\r\n",  # the signature of PNG, cut short
        b"\xff\xd8",
        b"GIF88a",
        b"  \xff\xd8\xff",  # the right bytes in the wrong place
        "\x89PNG\r\n\x1a\n",
        None,
        42,
        memoryview(PNG),
        (PNG, "image/png"),
    ],
)
def test_bytes_that_are_none_of_the_four_have_no_media_type(data):
    assert images.media_type_of(data) is None


# --- what the SDK refuses before any request ------------------------------------------

FIVE = [PNG, JPEG, WEBP, GIF, PNG]


@pytest.mark.parametrize(
    "given, said",
    [
        ([], "no image"),
        ((), "no image"),
        (FIVE, "at most 4 images, got 5"),
        (b"", "image 1 is empty"),
        ([PNG, bytearray()], "image 2 is empty"),
        ((b"", "image/png"), "image 1 is empty"),
        ("shot.png", "a path, a URL or base64 text is not an image"),
        ("data:image/png;base64,iVBORw0KGgo=", "is not an image"),
        ({"data": PNG}, "is not an image"),
        (None, "images must be"),
        (42, "images must be"),
        (["shot.png"], "image 1 must be bytes"),
        ([PNG, None], "image 2 must be bytes"),
        ((PNG, None), "image 2 must be bytes"),
        ([memoryview(PNG)], "image 1 must be bytes"),
        ([(PNG, "image/png", "extra")], "image 1 must be bytes"),
        ([(PNG, b"image/png")], "image 1 must be bytes"),
        (b"BM\x36\x00\x0c\x00\x00\x00\x00\x00", "image 1 is not a JPEG, PNG, WebP or GIF image"),
        ([PNG, b"%PDF-1.7\n"], "image 2 is not a JPEG, PNG, WebP or GIF image"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image 1 is not"),
        ((b"plain text", "image/png"), "image 1 is not"),
        ((PNG, "image/bmp"), "media type of image 1 must be one of image/jpeg, image/png, image/webp, image/gif"),
        ((PNG, "image/jpg"), "media type of image 1 must be one of"),
        ((PNG, "png"), "media type of image 1 must be one of"),
        ((PNG, ""), "media type of image 1 must be one of"),
        ([JPEG, (PNG, "image/svg+xml")], "media type of image 2 must be one of"),
        ((PNG, "image/jpeg"), "image 1 is given as image/jpeg, but its first bytes are those of image/png"),
        ([PNG, (JPEG, "image/gif")], "image 2 is given as image/gif, but its first bytes are those of image/jpeg"),
        (PNG + bytes(images.MAX_IMAGE_BYTES), "one image holds at most 5242880"),
        ([JPEG, (PNG + bytes(images.MAX_IMAGE_BYTES), "image/png")], "image 2 holds"),
    ],
)
def test_images_that_cannot_be_sent_are_refused_before_the_call(portal, given, said):
    with pytest.raises(errors.InvalidRequestError) as caught:
        images.inspect(given, "What is shown?")
    assert said in caught.value.message, caught.value.message
    assert caught.value.status == 400
    assert caught.value.user_message == "The app sent a request the EFP Portal could not accept."
    with pytest.raises(errors.InvalidRequestError):
        run(images.ainspect(given, "What is shown?"))
    assert portal.requests == []


def test_a_generator_that_yields_nothing_is_no_image(portal):
    with pytest.raises(errors.InvalidRequestError) as caught:
        images.inspect((shot for shot in ()), "What is shown?")
    assert "no image" in caught.value.message and portal.requests == []


def test_images_that_are_too_large_together_are_refused(portal, monkeypatch):
    # With the numbers of the contract this cannot happen: four images of 5 MiB are the 20 MiB
    # that are allowed. The rule is the contract's all the same, and it is the one that speaks
    # when the other two numbers change, so it is checked with smaller ones.
    monkeypatch.setattr(images, "MAX_IMAGE_BYTES", 100)
    monkeypatch.setattr(images, "MAX_TOTAL_BYTES", 250)
    portal.on(*ROUTE, ANSWER)
    hundred = PNG + bytes(100 - len(PNG))
    images.inspect([hundred, hundred], "What differs?")  # 200 bytes: sent
    assert portal.count(*ROUTE) == 1
    with pytest.raises(errors.InvalidRequestError) as caught:
        images.inspect([hundred, hundred, hundred], "What differs?")  # 300 bytes
    assert "more than 250 bytes together" in caught.value.message
    with pytest.raises(errors.InvalidRequestError):
        run(images.ainspect([hundred, hundred, hundred], "What differs?"))
    assert portal.count(*ROUTE) == 1


@pytest.mark.parametrize("prompt", ["", "   ", "\n\t", None, 42, b"What is shown?", ["What is shown?"]])
def test_a_prompt_that_is_blank_or_not_a_string_is_refused(portal, prompt):
    with pytest.raises(errors.InvalidRequestError) as caught:
        images.inspect(PNG, prompt)
    assert "prompt" in caught.value.message
    with pytest.raises(errors.InvalidRequestError):
        run(images.ainspect(PNG, prompt))
    assert portal.requests == []


@pytest.mark.parametrize("system", [42, b"Be brief.", ["Be brief."], False])
def test_a_system_prompt_that_is_not_a_string_is_refused(portal, system):
    with pytest.raises(errors.InvalidRequestError) as caught:
        images.inspect(PNG, "What is shown?", system=system)
    assert "system" in caught.value.message
    with pytest.raises(errors.InvalidRequestError):
        run(images.ainspect(PNG, "What is shown?", system=system))
    assert portal.requests == []


def test_an_empty_system_prompt_is_a_string_and_is_sent(portal):
    portal.on(*ROUTE, ANSWER)
    images.inspect(PNG, "What is shown?", system="")
    assert portal.body()["system"] == ""


@pytest.mark.parametrize(
    "call",
    [
        lambda: images.inspect(PNG, "half an emoji " + HALF),
        lambda: images.inspect(PNG, "What is shown?", system="be brief " + HALF),
        lambda: run(images.ainspect(PNG, "half an emoji " + HALF)),
    ],
)
def test_text_that_cannot_be_sent_is_an_invalid_request(portal, call):
    # Such a text comes from a visitor: it must end in the "except EfpError" of the app.
    portal.on(*ROUTE, ANSWER)
    with pytest.raises(errors.InvalidRequestError) as caught:
        call()
    assert caught.value.status == 400 and "half an emoji" not in caught.value.message
    assert portal.requests == []


@pytest.mark.parametrize("effort", ["max", "LOW", "minimal", "", " low", 3, True, ["low"]])
def test_another_reasoning_effort_is_a_value_error_before_any_request(portal, effort):
    with pytest.raises(ValueError) as caught:
        images.inspect(PNG, "What is shown?", reasoning_effort=effort)
    assert "reasoning_effort" in str(caught.value) and "'low', 'medium', 'high'" in str(caught.value)
    assert not isinstance(caught.value, errors.EfpError)
    with pytest.raises(ValueError):
        run(images.ainspect(PNG, "What is shown?", reasoning_effort=effort))
    assert portal.requests == []


def test_a_refused_argument_needs_no_token_and_a_good_call_does():
    # The arguments are checked first: a mistake in them shows on a machine without a token too.
    with pytest.raises(errors.InvalidRequestError):
        images.inspect(b"not an image", "What is shown?")
    with pytest.raises(errors.ConfigurationError) as caught:
        images.inspect(PNG, "What is shown?")
    assert "EFP_APP_TOKEN" in caught.value.message
    with pytest.raises(errors.ConfigurationError):
        run(images.ainspect(PNG, "What is shown?"))


# --- what the Portal refuses ----------------------------------------------------------


@pytest.mark.parametrize(
    "status, code, cls, sentence",
    [
        (400, "invalid_request", errors.InvalidRequestError, "The app sent a request the EFP Portal could not accept."),
        (401, "invalid_token", errors.InvalidTokenError, "This app is not connected to the EFP Portal: its token is missing or no longer valid."),
        (403, "app_disabled", errors.AppDisabledError, "This app has been disabled."),
        (403, "connector_disabled", errors.ConnectorDisabledError, "The owner has not turned on this connector for the app."),
        (424, "connector_not_configured", errors.ConnectorNotConfiguredError, "The owner has not set up this connector in the Portal."),
        (424, "provider_auth_failed", errors.ProviderAuthFailedError, "The owner's sign-in to the AI provider has expired. The owner needs to reconnect it in the Portal."),
        (429, "rate_limited", errors.RateLimitedError, "Too many requests right now. Please wait a moment and try again."),
        (429, "budget_exceeded", errors.BudgetExceededError, "This app has used up its AI budget for today. Try again in about a minute."),
        (502, "provider_error", errors.ProviderError, "The AI provider returned an error. Please try again."),
    ],
)
def test_image_errors_are_typed(portal, status, code, cls, sentence):
    portal.on(*ROUTE, envelope(status, code, "said by the Portal", headers={"Retry-After": "60"}))
    with pytest.raises(cls) as caught:
        images.inspect(PNG, "What is shown?")
    assert type(caught.value) is cls
    assert (caught.value.code, caught.value.status, caught.value.message) == (code, status, "said by the Portal")
    assert caught.value.user_message == sentence
    # The two refusals that are worth another try say when; no other class has the attribute.
    assert getattr(caught.value, "retry_after", None) == (60.0 if status == 429 else None)
    with pytest.raises(cls) as again:
        run(images.ainspect(PNG, "What is shown?"))
    assert (again.value.status, getattr(again.value, "retry_after", None)) == (status, 60.0 if status == 429 else None)
    assert portal.count(*ROUTE) == 2  # one request per call: a refused call is not sent again


def test_a_refusal_without_retry_after_has_none(portal):
    portal.on(*ROUTE, envelope(429, "budget_exceeded"))
    with pytest.raises(errors.BudgetExceededError) as caught:
        images.inspect(PNG, "What is shown?")
    assert caught.value.retry_after is None
    assert caught.value.user_message == "This app has used up its AI budget for today."


def test_a_code_without_a_class_is_a_plain_efp_error(portal):
    portal.on(*ROUTE, envelope(413, "too_large", "the request is too large"))
    with pytest.raises(errors.EfpError) as caught:
        images.inspect(PNG, "What is shown?")
    assert type(caught.value) is errors.EfpError and (caught.value.code, caught.value.status) == ("too_large", 413)


@pytest.mark.parametrize(
    "broken, cls",
    [
        (httpx.ConnectError("no route to the Portal"), errors.PortalUnreachableError),
        (httpx.ReadError("the connection was reset"), errors.PortalUnreachableError),
        (httpx.ReadTimeout("the Portal is silent"), errors.PortalTimeoutError),
        (httpx.ConnectTimeout("the Portal does not answer"), errors.PortalTimeoutError),
    ],
)
def test_an_image_call_is_never_sent_twice(portal, no_sleep, broken, cls):
    # The Portal may have asked the model already when the connection broke, and the app's budget
    # paid for it: only the app can know whether asking again is worth it.
    portal.on(*ROUTE, broken)
    with pytest.raises(cls) as caught:
        images.inspect(PNG, "What is shown?")
    assert type(caught.value) is cls
    assert portal.count(*ROUTE) == 1
    with pytest.raises(cls):
        run(images.ainspect(PNG, "What is shown?"))
    assert portal.count(*ROUTE) == 2


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"model": "gpt-5.4"}),
        httpx.Response(200, json={"text": None}),
        httpx.Response(200, json={"text": ["not", "text"]}),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, content=b"<html>Sign in</html>", headers={"content-type": "text/html"}),
        httpx.Response(302, headers={"location": "https://sso.example.com/login"}),
        httpx.Response(502, content=b"<html>Bad Gateway</html>", headers={"content-type": "text/html"}),
        # A Portal from before image analysis has no such route, and its 404 is the framework's own.
        httpx.Response(404, json={"detail": "Not Found"}),
        # A proxy that finds the body too large answers before the Portal does.
        httpx.Response(413, content=b"<html>Request Entity Too Large</html>", headers={"content-type": "text/html"}),
    ],
)
def test_an_answer_outside_the_contract_is_unexpected(portal, answer):
    portal.on(*ROUTE, answer)
    with pytest.raises(errors.UnexpectedResponseError) as caught:
        images.inspect(PNG, "What is shown?")
    if answer.status_code >= 300:
        assert caught.value.status == answer.status_code  # what tells an old Portal from a proxy's refusal
    with pytest.raises(errors.UnexpectedResponseError):
        run(images.ainspect(PNG, "What is shown?"))


# --- image_analysis of GET /app -------------------------------------------------------

APP = {
    "id": "0b0f3a4e-0000-4000-8000-000000000001",
    "slug": "mindmap",
    "name": "Mind map",
    "url": "https://app.example.com/mindmap/",
    "owner": {"id": 5, "username": "alice", "display_name": "Alice", "group_id": 3, "group_name": "QA"},
    "llm": {"enabled": True, "default_model": "gpt-5.4", "models": ["gpt-5.4"]},
    "connectors": [{"type": "jira", "write": False}],
    "image_analysis": {"enabled": True, "model": "gpt-5.4", "max_images": 4, "max_image_bytes": 5242880, "max_total_bytes": 20971520},
    "budget": {"daily_credits": 50.0, "used_credits": 1.2, "daily_tokens": 2000000, "used_tokens": 3400},
}


def test_info_has_image_analysis(portal):
    portal.on("GET", "/app", APP)
    info = sdk_app.info()
    assert info.image_analysis == sdk_app.ImageAnalysis(
        enabled=True, model="gpt-5.4", max_images=4, max_image_bytes=5242880, max_total_bytes=20971520
    )
    assert info.raw["image_analysis"] == APP["image_analysis"]
    # Not one of the connectors: it has no address an app could call.
    assert [grant.type for grant in info.connectors] == ["jira"]
    assert run(sdk_app.ainfo()).image_analysis == info.image_analysis


def test_image_analysis_that_is_off_keeps_the_limits(portal):
    off = {"enabled": False, "model": None, "max_images": 4, "max_image_bytes": 5242880, "max_total_bytes": 20971520}
    portal.on("GET", "/app", dict(APP, image_analysis=off))
    assert sdk_app.info().image_analysis == sdk_app.ImageAnalysis()
    assert sdk_app.ImageAnalysis() == sdk_app.ImageAnalysis(
        enabled=False,
        model=None,
        max_images=images.MAX_IMAGES,
        max_image_bytes=images.MAX_IMAGE_BYTES,
        max_total_bytes=images.MAX_TOTAL_BYTES,
    )


def test_a_portal_that_sends_no_image_analysis_means_it_is_off(portal):
    # A Portal from before this release: the object is not in its answer at all.
    before = {name: value for name, value in APP.items() if name != "image_analysis"}
    portal.on("GET", "/app", before)
    info = sdk_app.info()
    assert info.image_analysis == sdk_app.ImageAnalysis() and info.image_analysis.enabled is False
    assert info.llm.enabled is True and info.budget.daily_credits == 50.0  # the rest is read as before
    portal.on("GET", "/app", {"id": "a1"})
    assert sdk_app.info().image_analysis.enabled is False


def test_the_portals_limits_are_passed_on(portal):
    portal.on("GET", "/app", dict(APP, image_analysis={"enabled": True, "model": "gpt-5.4-mini", "max_images": 2, "max_image_bytes": 1048576, "max_total_bytes": 2097152}))
    found = sdk_app.info().image_analysis
    assert (found.model, found.max_images, found.max_image_bytes, found.max_total_bytes) == ("gpt-5.4-mini", 2, 1048576, 2097152)


@pytest.mark.parametrize(
    "sent",
    [
        None,
        "on",
        ["enabled"],
        True,
        {"enabled": True},
        {"enabled": True, "max_images": None, "max_image_bytes": "5 MiB", "max_total_bytes": 0},
        {"enabled": 1, "max_images": True, "max_image_bytes": -1, "max_total_bytes": 2.5},
    ],
)
def test_image_analysis_survives_a_sparse_or_odd_answer(portal, sent):
    portal.on("GET", "/app", dict(APP, image_analysis=sent))
    found = sdk_app.info().image_analysis
    assert found.enabled is (isinstance(sent, dict))
    assert found.model is None
    # A limit that is not a count is not passed on: the page would show nonsense.
    assert (found.max_images, found.max_image_bytes, found.max_total_bytes) == (4, 5242880, 20971520)
