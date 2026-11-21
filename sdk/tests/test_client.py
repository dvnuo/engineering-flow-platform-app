"""The HTTP clients: headers, error mapping, retries, proxies and CA bundles."""

from __future__ import annotations

import asyncio
import ssl

import httpx
import pytest

from efp_app import client as sdk_client
from efp_app import config, errors
from conftest import PORTAL, PREFIX, TOKEN, FakePortal, envelope
from test_config_errors import SECTION_9

VISITOR_VALUE = "v1.eyJhIjoxfQ.c2lnbmF0dXJl"


def run(coroutine):
    return asyncio.run(coroutine)


# --- What goes out ----------------------------------------------------------------


def test_a_get_sends_the_bearer_token_and_no_cookies(credentials):
    portal = FakePortal().on("GET", "/app", {"id": "a1"})
    with portal.client() as client:
        assert client.get("/app") == {"id": "a1"}
    request = portal.last
    assert str(request.url) == PORTAL + PREFIX + "/app"
    assert request.headers["authorization"] == "Bearer " + TOKEN
    assert request.headers["user-agent"].startswith("efp-app-sdk/")
    assert "cookie" not in request.headers
    assert "x-efp-visitor" not in request.headers


def test_a_post_sends_json(credentials):
    portal = FakePortal().on("POST", "/llm/complete", {"text": "hi"})
    with portal.client() as client:
        assert client.post("/llm/complete", {"messages": []}) == {"text": "hi"}
    assert portal.last.headers["content-type"] == "application/json"
    assert portal.body() == {"messages": []}


def test_a_body_goes_out_as_compact_utf8_json(credentials):
    # The SDK encodes the body itself, so that it is the same whatever release of httpx is installed.
    portal = FakePortal().on("POST", "/llm/complete", {"text": "hi"})
    name = "Zo" + chr(0xEB)  # a letter outside ASCII
    with portal.client() as client:
        client.post("/llm/complete", {"system": name, "n": 1})
    assert portal.last.content == b'{"system":"Zo\xc3\xab","n":1}'
    assert portal.last.headers["content-length"] == str(len(portal.last.content))


@pytest.mark.parametrize(
    "body, said",
    [
        ({"text": "half an emoji " + chr(0xD83D)}, "U+D83D"),
        ({"temperature": float("nan")}, "JSON"),
        ({"temperature": float("inf")}, "JSON"),
    ],
)
def test_a_body_that_json_or_utf8_cannot_carry_is_an_invalid_request(credentials, body, said):
    # Half of a surrogate pair is what a visitor's text holds when a page cut it in the middle of an
    # emoji. It used to leave the SDK as a UnicodeEncodeError, which no "except EfpError" catches.
    portal = FakePortal().on("POST", "/llm/complete", {"text": "hi"})
    with portal.client() as client:
        with pytest.raises(errors.InvalidRequestError) as caught:
            client.post("/llm/complete", body)
    assert caught.value.code == "invalid_request" and caught.value.status == 400
    assert said in caught.value.message and "half an emoji" not in caught.value.message
    assert caught.value.__cause__ is None  # the original error would carry the whole text
    assert portal.requests == []

    async def scenario():
        async with portal.async_client() as client:
            await client.post("/llm/complete", body)

    with pytest.raises(errors.InvalidRequestError):
        run(scenario())
    assert portal.requests == []


def test_a_body_of_a_type_json_does_not_know_stays_a_type_error(credentials):
    portal = FakePortal().on("POST", "/llm/complete", {"text": "hi"})
    with portal.client() as client:
        with pytest.raises(TypeError):
            client.post("/llm/complete", {"when": object()})
    assert portal.requests == []


def test_the_portal_url_may_have_a_path_and_a_trailing_slash(monkeypatch):
    monkeypatch.setenv("EFP_APP_TOKEN", TOKEN)
    monkeypatch.setenv("EFP_PORTAL_URL", "https://corp.test/efp/")
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    sdk_client.Client(transport=httpx.MockTransport(handler)).get("/app")
    assert seen == ["https://corp.test/efp/api/app-sdk/v1/app"]


def test_arguments_win_over_the_environment(credentials):
    seen = []

    def handler(request):
        seen.append((str(request.url), request.headers["authorization"]))
        return httpx.Response(200, json={})

    client = sdk_client.Client(token="efpa_other", portal_url="http://other.test/", transport=httpx.MockTransport(handler))
    client.get("/app")
    assert seen == [("http://other.test/api/app-sdk/v1/app", "Bearer efpa_other")]


@pytest.mark.parametrize(
    "visitor, expected",
    [
        (VISITOR_VALUE, VISITOR_VALUE),
        ("  " + VISITOR_VALUE + " ", VISITOR_VALUE),
        (None, None),
        ("not a header\r\nX-Injected: 1", None),
        ("v1.only-two-parts", None),
        (12, None),
    ],
)
def test_the_visitor_header_is_forwarded_only_when_well_formed(credentials, visitor, expected):
    portal = FakePortal().on("POST", "/llm/complete", {})
    portal.client().post("/llm/complete", {}, visitor=visitor)
    assert portal.last.headers.get("x-efp-visitor") == expected


def test_a_visitor_object_forwards_its_raw_value(credentials):
    from efp_app.visitor import Visitor

    portal = FakePortal().on("GET", "/app", {})
    portal.client().get("/app", visitor=Visitor(id=1, username="bob", name="Bob", raw=VISITOR_VALUE))
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE
    portal.client().get("/app", visitor=Visitor(id=1, username="bob", name="Bob"))
    assert "x-efp-visitor" not in portal.last.headers


# --- Settings ---------------------------------------------------------------------


def test_no_token_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("EFP_PORTAL_URL", PORTAL)
    with pytest.raises(errors.ConfigurationError) as caught:
        sdk_client.Client()
    assert "EFP_APP_TOKEN" in str(caught.value)


def test_no_portal_url_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("EFP_APP_TOKEN", TOKEN)
    with pytest.raises(errors.ConfigurationError) as caught:
        sdk_client.get_client()
    assert "EFP_PORTAL_URL" in str(caught.value)
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize(
    "strange, said",
    [
        (chr(0x200B), "U+200B"),  # a zero-width space, which a copy and paste brings along unseen
        (chr(0xA0), "U+00A0"),  # a no-break space
        (" ", "U+0020"),
        ("\t", "U+0009"),
        (chr(1), "U+0001"),
    ],
)
def test_a_token_with_a_character_no_token_has_is_a_clear_error(monkeypatch, strange, said):
    # httpx refuses such a header with a UnicodeEncodeError whose repr() holds the whole value.
    broken = TOKEN[:20] + strange + TOKEN[20:]
    monkeypatch.setenv("EFP_PORTAL_URL", PORTAL)
    for make in (
        lambda: sdk_client.Client(token=broken),
        lambda: sdk_client.AsyncClient(token=broken),
        lambda: sdk_client.Client(settings=config.load({"EFP_APP_TOKEN": broken, "EFP_PORTAL_URL": PORTAL})),
    ):
        with pytest.raises(errors.ConfigurationError) as caught:
            make()
        message = caught.value.message
        assert "EFP_APP_TOKEN" in message and said in message and "position 21" in message
        for shown in (message, str(caught.value), repr(caught.value)):
            assert TOKEN[:20] not in shown and TOKEN[20:] not in shown  # the name, never the value


def test_the_shared_client_refuses_such_a_token_too(monkeypatch):
    monkeypatch.setenv("EFP_APP_TOKEN", TOKEN[:10] + " " + TOKEN[10:])  # two halves pasted with a blank between
    monkeypatch.setenv("EFP_PORTAL_URL", PORTAL)
    with pytest.raises(errors.ConfigurationError) as caught:
        sdk_client.get_client()
    assert "EFP_APP_TOKEN" in caught.value.message and "position 11" in caught.value.message
    assert TOKEN[10:] not in repr(caught.value)


# --- Errors -----------------------------------------------------------------------


@pytest.mark.parametrize("status, code, cls", SECTION_9)
def test_every_error_code_is_mapped_for_get_and_post(credentials, status, code, cls):
    portal = FakePortal()
    portal.on("GET", "/app", envelope(status, code, "detail"))
    portal.on("POST", "/llm/complete", envelope(status, code, "detail"))
    client = portal.client()
    for call in (lambda: client.get("/app"), lambda: client.post("/llm/complete", {})):
        with pytest.raises(cls) as caught:
            call()
        assert (caught.value.code, caught.value.status, caught.value.message) == (code, status, "detail")


@pytest.mark.parametrize("status, code, cls", SECTION_9)
def test_every_error_code_is_mapped_by_the_async_client(credentials, status, code, cls):
    portal = FakePortal()
    portal.on("GET", "/app", envelope(status, code, "detail"))
    portal.on("POST", "/llm/complete", envelope(status, code, "detail"))

    async def scenario():
        async with portal.async_client() as client:
            with pytest.raises(cls):
                await client.get("/app")
            with pytest.raises(cls) as caught:
                await client.post("/llm/complete", {})
            return caught.value

    error = run(scenario())
    assert (error.code, error.status) == (code, status)


def test_retry_after_reaches_the_exception(credentials):
    portal = FakePortal().on("POST", "/llm/complete", envelope(429, "budget_exceeded", headers={"Retry-After": "120"}))
    with pytest.raises(errors.BudgetExceededError) as caught:
        portal.client().post("/llm/complete", {})
    assert caught.value.retry_after == 120.0


def test_an_error_page_instead_of_the_envelope(credentials):
    portal = FakePortal().on("GET", "/app", httpx.Response(503, text="<html>upstream down</html>"))
    with pytest.raises(errors.UnexpectedResponseError) as caught:
        portal.client().get("/app")
    assert caught.value.status == 503


def test_a_redirect_is_not_followed(credentials):
    portal = FakePortal().on("GET", "/app", httpx.Response(302, headers={"location": "https://portal.test/login"}))
    with pytest.raises(errors.UnexpectedResponseError) as caught:
        portal.client().get("/app")
    assert caught.value.status == 302
    assert "EFP_PORTAL_URL" in caught.value.message
    assert len(portal.requests) == 1


def test_a_success_that_is_not_json(credentials):
    portal = FakePortal().on("GET", "/app", httpx.Response(200, text="<html>hello</html>"))
    with pytest.raises(errors.UnexpectedResponseError):
        portal.client().get("/app")


# --- Retries ----------------------------------------------------------------------


def test_a_post_is_never_repeated(credentials, no_sleep):
    portal = FakePortal().on("POST", "/llm/complete", httpx.ConnectError("refused"))
    with pytest.raises(errors.PortalUnreachableError) as caught:
        portal.client().post("/llm/complete", {})
    assert portal.count("POST", "/llm/complete") == 1
    assert "portal.test" in caught.value.message
    assert TOKEN not in str(caught.value)


def test_a_get_is_tried_again_when_the_connection_fails(credentials, no_sleep):
    portal = FakePortal().on("GET", "/app", [httpx.ConnectError("refused"), httpx.ConnectError("refused"), {"id": "a1"}])
    assert portal.client().get("/app") == {"id": "a1"}
    assert portal.count("GET", "/app") == 3


def test_a_get_gives_up_after_three_attempts(credentials, no_sleep):
    portal = FakePortal().on("GET", "/app", httpx.ConnectError("refused"))
    with pytest.raises(errors.PortalUnreachableError):
        portal.client().get("/app")
    assert portal.count("GET", "/app") == sdk_client.GET_ATTEMPTS == 3


def test_a_get_is_not_repeated_after_an_error_answer_or_a_read_timeout(credentials, no_sleep):
    portal = FakePortal().on("GET", "/app", envelope(502, "upstream_error"))
    with pytest.raises(errors.UpstreamError):
        portal.client().get("/app")
    assert portal.count("GET", "/app") == 1

    slow = FakePortal().on("GET", "/app", httpx.ReadTimeout("too slow"))
    with pytest.raises(errors.PortalTimeoutError) as caught:
        slow.client().get("/app")
    assert slow.count("GET", "/app") == 1
    assert isinstance(caught.value, errors.PortalUnreachableError)
    assert caught.value.code == "portal_timeout"


def test_the_async_client_follows_the_same_rules(credentials, no_sleep):
    portal = FakePortal()
    portal.on("GET", "/app", [httpx.ConnectError("refused"), {"id": "a1"}])
    portal.on("POST", "/llm/complete", httpx.ConnectError("refused"))

    async def scenario():
        async with portal.async_client() as client:
            assert await client.get("/app") == {"id": "a1"}
            with pytest.raises(errors.PortalUnreachableError):
                await client.post("/llm/complete", {})

    run(scenario())
    assert portal.count("GET", "/app") == 2
    assert portal.count("POST", "/llm/complete") == 1


# --- Timeouts ---------------------------------------------------------------------


def test_timeouts(credentials):
    assert sdk_client.as_timeout(None).read == sdk_client.DEFAULT_TIMEOUT
    assert sdk_client.as_timeout(None).connect == sdk_client.CONNECT_TIMEOUT
    assert sdk_client.as_timeout(3).connect == 3.0
    custom = httpx.Timeout(7.0)
    assert sdk_client.as_timeout(custom) is custom

    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={})

    client = sdk_client.Client(transport=httpx.MockTransport(handler), timeout=30)
    client.get("/app")
    client.post("/llm/complete", {}, timeout=600)
    assert seen[0]["read"] == 30.0
    assert seen[1]["read"] == 600.0
    assert seen[1]["connect"] == sdk_client.CONNECT_TIMEOUT


# --- Proxies and CA bundles ---------------------------------------------------------


def _options(env_settings):
    core = sdk_client._Core(None, None, None, env_settings)
    return core.http_options(None)


def test_a_developers_machine_uses_the_proxy_variables(monkeypatch, credentials):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.test:3128")
    assert _options(config.load())["trust_env"] is True
    client = sdk_client.Client()
    proxied = [pattern.pattern for pattern, transport in client._http._mounts.items() if transport is not None]
    assert any(pattern.startswith("https://") for pattern in proxied)
    client.close()


def test_a_deployed_app_never_uses_a_proxy(monkeypatch, credentials):
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    monkeypatch.setenv("EFP_PORTAL_URL", "http://portal.efp.svc.cluster.local:8000")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.corp.test:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.test:3128")
    monkeypatch.setenv("ALL_PROXY", "http://proxy.corp.test:3128")
    assert _options(config.load())["trust_env"] is False
    client = sdk_client.Client()
    assert client._http._mounts == {}
    client.close()
    asynchronous = sdk_client.AsyncClient()
    assert asynchronous._http._mounts == {}
    run(asynchronous.aclose())


def test_a_supplied_transport_is_used_even_with_a_proxy_in_the_environment(monkeypatch, credentials):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.test:3128")
    portal = FakePortal().on("GET", "/app", {"ok": True})
    assert portal.client().get("/app") == {"ok": True}


def test_the_default_trust_is_used_without_ca_variables():
    default = sdk_client.ca_bundle({})
    assert default is True or isinstance(default, ssl.SSLContext)
    assert sdk_client.ca_bundle({"SSL_CERT_FILE": "/does/not/exist.pem"}) is default
    assert sdk_client.ca_bundle({}) is default


@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_a_ca_bundle_from_the_environment_is_loaded(variable):
    certifi = pytest.importorskip("certifi")
    context = sdk_client.ca_bundle({variable: certifi.where()})
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_a_ca_bundle_applies_on_the_host_as_well(monkeypatch, credentials):
    certifi = pytest.importorskip("certifi")
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())
    options = _options(config.load())
    assert isinstance(options["verify"], ssl.SSLContext)
    assert options["trust_env"] is False


def test_a_broken_ca_bundle_names_the_variables(tmp_path):
    broken = tmp_path / "ca.pem"
    broken.write_text("this is not a certificate", encoding="utf-8")
    with pytest.raises(errors.ConfigurationError) as caught:
        sdk_client.ca_bundle({"SSL_CERT_FILE": str(broken)})
    assert "SSL_CERT_FILE" in caught.value.message


# --- The shared clients ---------------------------------------------------------------


def test_the_shared_client_follows_the_environment(monkeypatch, credentials):
    first = sdk_client.get_client()
    assert sdk_client.get_client() is first
    monkeypatch.setenv("EFP_APP_TOKEN", "efpa_" + "n" * 43)
    second = sdk_client.get_client()
    assert second is not first
    assert second._core.token == "efpa_" + "n" * 43


def test_a_configured_client_stays_until_reset(credentials):
    portal = FakePortal().on("GET", "/app", {"id": "a1"})
    mine = portal.client()
    sdk_client.configure(client=mine)
    assert sdk_client.get_client() is mine
    sdk_client.reset()
    assert sdk_client.get_client() is not mine


def test_the_shared_async_client_belongs_to_its_loop(credentials):
    async def current():
        client = sdk_client.get_async_client()
        assert sdk_client.get_async_client() is client
        return client

    assert run(current()) is not run(current())


def test_the_token_is_not_in_the_text_of_the_client(credentials):
    client = sdk_client.Client()
    assert TOKEN not in repr(client)
    client.close()
