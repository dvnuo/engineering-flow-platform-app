"""Connector calls (section 9.3) and ``GET /app`` (section 9.1)."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from efp_app import app as sdk_app
from efp_app import connectors, errors
from conftest import FakePortal, envelope

VISITOR_VALUE = "v1.eyJhIjoxfQ.c2lnbmF0dXJl"
ISSUE = {
    "status": 200,
    "headers": {"Content-Type": "application/json"},
    "json": {"key": "ABC-1"},
    "body_base64": None,
    "truncated": False,
}
APP = {
    "id": "0b0f3a4e-0000-4000-8000-000000000001",
    "slug": "mindmap",
    "name": "Mind map",
    "url": "https://app.example.com/mindmap/",
    "owner": {"id": 5, "username": "alice", "display_name": "Alice", "group_id": 3, "group_name": "QA"},
    "llm": {"enabled": True, "default_model": "gpt-5.4", "models": ["gpt-5.4"]},
    "connectors": [{"type": "jira", "write": False}],
    "budget": {"daily_credits": 50.0, "used_credits": 1.2, "daily_tokens": 2000000, "used_tokens": 3400},
}


def run(coroutine):
    return asyncio.run(coroutine)


# --- connectors.request -------------------------------------------------------------


def test_request_sends_the_contract_body(portal):
    portal.on("POST", "/connectors/jira/request", ISSUE)
    answer = connectors.request(
        "jira",
        "get",
        "/rest/api/2/issue/ABC-1",
        query={"fields": "summary", "expand": None},
        headers={"Accept": "application/json"},
        visitor=VISITOR_VALUE,
    )
    assert portal.body() == {
        "method": "GET",
        "path": "/rest/api/2/issue/ABC-1",
        "query": {"fields": "summary"},
        "headers": {"Accept": "application/json"},
        "json": None,
        "body_base64": None,
    }
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE
    assert answer.status == 200 and answer.ok
    assert answer.headers == {"content-type": "application/json"}
    assert answer.json == {"key": "ABC-1"}
    assert answer.content == b'{"key": "ABC-1"}'
    assert answer.text == '{"key": "ABC-1"}'
    assert answer.truncated is False


def test_request_with_a_json_body(portal):
    portal.on("POST", "/connectors/jira/request", {"status": 201, "headers": {}, "json": {"id": "1"}, "body_base64": None, "truncated": False})
    answer = connectors.request("jira", "POST", "/rest/api/2/issue", json={"fields": {"summary": "x"}})
    assert portal.body()["method"] == "POST"
    assert portal.body()["json"] == {"fields": {"summary": "x"}}
    assert portal.body()["body_base64"] is None
    assert portal.body()["query"] is None and portal.body()["headers"] is None
    assert answer.status == 201


@pytest.mark.parametrize("body, raw", [(b"\x00\x01binary", b"\x00\x01binary"), ("text body", b"text body"), (bytearray(b"ab"), b"ab")])
def test_request_with_a_raw_body(portal, body, raw):
    portal.on("POST", "/connectors/nexus/request", {"status": 204, "headers": {}, "json": None, "body_base64": None, "truncated": False})
    answer = connectors.request("nexus", "PUT", "/repository/raw/a.bin", body=body, headers={"Content-Type": "application/octet-stream"})
    assert base64.b64decode(portal.body()["body_base64"]) == raw
    assert portal.body()["json"] is None
    assert answer.status == 204 and answer.content == b"" and answer.json is None


def test_a_body_that_is_not_json_comes_back_as_bytes(portal):
    page = b"<html>build log</html>"
    portal.on(
        "POST",
        "/connectors/jenkins/request",
        {"status": 200, "headers": {"content-type": "text/html"}, "json": None, "body_base64": base64.b64encode(page).decode("ascii"), "truncated": True},
    )
    answer = connectors.request("jenkins", "GET", "/job/build/1/consoleText")
    assert answer.content == page
    assert answer.text == "<html>build log</html>"
    assert answer.json is None
    assert answer.truncated is True


def test_an_answer_with_half_an_emoji_in_it_is_still_an_answer(portal):
    # JSON can carry half of a surrogate pair (a text the other system cut in the middle of an
    # emoji), UTF-8 cannot. Encoding the answer again for "content" used to fail with a
    # UnicodeEncodeError, also for an app that only wanted "json".
    half = chr(0xD83D)
    wire = json.dumps({"status": 200, "headers": {}, "json": {"summary": "cut " + half}, "body_base64": None, "truncated": False})
    assert "\\ud83d" in wire  # as an escape, which is what arrives
    portal.on("POST", "/connectors/jira/request", httpx.Response(200, content=wire.encode("ascii"), headers={"content-type": "application/json"}))
    answer = connectors.get("jira", "/rest/api/2/issue/ABC-1")
    assert answer.ok and answer.json == {"summary": "cut " + half}  # json keeps what was sent
    assert answer.content == b'{"summary": "cut ?"}'  # what UTF-8 cannot carry is a question mark there
    assert answer.text == '{"summary": "cut ?"}'
    assert run(connectors.aget("jira", "/rest/api/2/issue/ABC-1")).content == answer.content


@pytest.mark.parametrize("options", [{"json": {"summary": "cut " + chr(0xD83D)}}, {"body": "cut " + chr(0xD83D)}, {"query": {"jql": chr(0xD83D)}}])
def test_a_request_with_half_an_emoji_in_it_is_refused_before_the_call(portal, options):
    with pytest.raises(errors.InvalidRequestError) as caught:
        connectors.request("jira", "POST", "/rest/api/2/issue", **options)
    assert "U+D83D" in caught.value.message and caught.value.status == 400
    with pytest.raises(errors.InvalidRequestError):
        run(connectors.arequest("jira", "POST", "/rest/api/2/issue", **options))
    assert portal.requests == []


def test_the_upstream_status_is_not_an_exception(portal):
    portal.on("POST", "/connectors/jira/request", {"status": 404, "headers": {}, "json": {"errorMessages": ["no issue"]}, "body_base64": None, "truncated": False})
    answer = connectors.request("jira", "GET", "/rest/api/2/issue/NOPE-1")
    assert answer.status == 404
    assert answer.ok is False
    assert answer.json == {"errorMessages": ["no issue"]}


def test_get_puts_keyword_arguments_in_the_query(portal):
    portal.on("POST", "/connectors/jira/request", ISSUE).on("POST", "/connectors/github/request", ISSUE)
    connectors.get("jira", "/rest/api/2/search", jql="project = ABC", maxResults=20, visitor=VISITOR_VALUE)
    assert portal.body()["method"] == "GET"
    assert portal.body()["query"] == {"jql": "project = ABC", "maxResults": 20}
    assert portal.last.headers["x-efp-visitor"] == VISITOR_VALUE
    connectors.get("github", "/user/repos")
    assert portal.last.url.path.endswith("/connectors/github/request")
    assert portal.body()["query"] is None


@pytest.mark.parametrize(
    "path",
    ["rest/api/2/issue", "https://evil.test/x", "//evil.test/x", "/a/../b", "/a\\b", "", None],
)
def test_a_path_that_breaks_the_rules_is_refused_before_the_call(portal, path):
    with pytest.raises(errors.InvalidPathError):
        connectors.request("jira", "GET", path)
    assert portal.requests == []


def test_dots_in_the_query_string_are_fine(portal):
    portal.on("POST", "/connectors/jira/request", ISSUE)
    connectors.request("jira", "GET", "/rest/api/2/search?jql=a..b")
    assert portal.body()["path"] == "/rest/api/2/search?jql=a..b"


@pytest.mark.parametrize("bad_type", ["", "Jira/../x", "a b", None])
def test_a_type_that_cannot_be_a_name_is_refused(portal, bad_type):
    with pytest.raises(errors.InvalidRequestError):
        connectors.request(bad_type, "GET", "/x")
    assert portal.requests == []


def test_json_and_body_together_are_refused(portal):
    with pytest.raises(errors.InvalidRequestError):
        connectors.request("jira", "POST", "/x", json={}, body=b"x")
    with pytest.raises(errors.InvalidRequestError):
        connectors.request("jira", "", "/x")
    assert portal.requests == []


@pytest.mark.parametrize(
    "status, code, cls",
    [
        (400, "invalid_path", errors.InvalidPathError),
        (403, "connector_disabled", errors.ConnectorDisabledError),
        (403, "method_not_allowed", errors.MethodNotAllowedError),
        (424, "connector_not_configured", errors.ConnectorNotConfiguredError),
        (429, "rate_limited", errors.RateLimitedError),
        (502, "upstream_error", errors.UpstreamError),
        (504, "upstream_timeout", errors.UpstreamTimeoutError),
    ],
)
def test_connector_errors_are_typed(portal, status, code, cls):
    portal.on("POST", "/connectors/jira/request", envelope(status, code))
    with pytest.raises(cls) as caught:
        connectors.request("jira", "DELETE", "/rest/api/2/issue/ABC-1")
    assert caught.value.status == status
    assert portal.count("POST", "/connectors/jira/request") == 1


@pytest.mark.parametrize("answer", [{"headers": {}}, {"status": "200"}, [], {"status": 200, "body_base64": "!!not base64!!"}])
def test_an_answer_outside_the_contract_is_unexpected(portal, answer):
    portal.on("POST", "/connectors/jira/request", httpx.Response(200, json=answer))
    with pytest.raises(errors.UnexpectedResponseError):
        connectors.request("jira", "GET", "/x")


def test_available(portal):
    portal.on("GET", "/connectors", {"connectors": [{"type": "jira", "base_url": "https://jira.example.com", "write": False}, {"type": "github", "base_url": "https://api.github.com", "write": True}]})
    found = connectors.available()
    assert found == [
        connectors.Connector(type="jira", base_url="https://jira.example.com", write=False),
        connectors.Connector(type="github", base_url="https://api.github.com", write=True),
    ]
    portal.on("GET", "/connectors", {"connectors": []})
    assert connectors.available() == []


def test_the_async_variants(portal):
    portal.on("POST", "/connectors/confluence/request", ISSUE)
    portal.on("GET", "/connectors", {"connectors": [{"type": "confluence", "base_url": "https://wiki.example.com", "write": False}]})

    async def scenario():
        first = await connectors.arequest("confluence", "GET", "/rest/api/content/1", query={"expand": "body"})
        second = await connectors.aget("confluence", "/rest/api/content", title="Home", visitor=VISITOR_VALUE)
        return first, second, await connectors.aavailable()

    first, second, found = run(scenario())
    assert first.json == {"key": "ABC-1"} and second.ok
    assert portal.body(-2)["query"] == {"title": "Home"}
    assert portal.requests[-2].headers["x-efp-visitor"] == VISITOR_VALUE
    assert [item.type for item in found] == ["confluence"]


def test_a_client_can_be_passed(credentials):
    mine = FakePortal().on("POST", "/connectors/splunk/request", ISSUE).on("GET", "/connectors", {"connectors": []})
    assert connectors.request("splunk", "GET", "/services/search/jobs", client=mine.client()).ok
    assert connectors.available(client=mine.client()) == []


# --- app ------------------------------------------------------------------------------


def test_info(portal):
    portal.on("GET", "/app", APP)
    info = sdk_app.info()
    assert (info.id, info.slug, info.name, info.url) == (APP["id"], "mindmap", "Mind map", "https://app.example.com/mindmap/")
    assert info.owner == sdk_app.Owner(id=5, username="alice", display_name="Alice", group_id=3, group_name="QA")
    assert info.llm == sdk_app.LlmInfo(enabled=True, default_model="gpt-5.4", models=("gpt-5.4",))
    assert info.connectors == (sdk_app.ConnectorGrant(type="jira", write=False),)
    assert info.budget == sdk_app.Budget(daily_credits=50.0, used_credits=1.2, daily_tokens=2000000, used_tokens=3400)
    assert info.raw == APP


def test_info_asks_again_unless_cached(portal):
    portal.on("GET", "/app", APP)
    sdk_app.info()
    sdk_app.info()
    assert portal.count("GET", "/app") == 2
    assert sdk_app.info(cached=True).slug == "mindmap"
    assert portal.count("GET", "/app") == 2


def test_info_survives_a_sparse_answer(portal):
    portal.on("GET", "/app", {"id": "a1"})
    info = sdk_app.info()
    assert info.id == "a1"
    assert info.owner.username is None
    assert info.llm.enabled is False and info.llm.models == ()
    assert info.connectors == ()
    assert info.budget.daily_credits is None


def test_info_errors_are_typed(portal):
    portal.on("GET", "/app", envelope(401, "invalid_token"))
    with pytest.raises(errors.InvalidTokenError):
        sdk_app.info()
    portal.on("GET", "/app", envelope(403, "app_disabled"))
    with pytest.raises(errors.AppDisabledError):
        sdk_app.info()


def test_ainfo(portal):
    portal.on("GET", "/app", APP)
    assert run(sdk_app.ainfo()).owner.username == "alice"
    assert run(sdk_app.ainfo(cached=True)).owner.username == "alice"
    assert portal.count("GET", "/app") == 1


def test_base_path_and_public_url_on_the_host(monkeypatch):
    monkeypatch.setenv("EFP_APP_ID", "a1")
    monkeypatch.setenv("EFP_APP_SLUG", "mindmap")
    monkeypatch.setenv("EFP_APP_BASE_PATH", "/mindmap")
    monkeypatch.setenv("EFP_APP_PUBLIC_URL", "https://app.example.com/mindmap/")
    assert sdk_app.base_path() == "/mindmap"
    assert sdk_app.public_url() == "https://app.example.com/mindmap/"
    assert sdk_app.slug() == "mindmap"
    assert sdk_app.is_deployed() is True


def test_base_path_and_public_url_on_a_developers_machine(monkeypatch):
    assert sdk_app.base_path() == ""
    assert sdk_app.public_url() is None
    assert sdk_app.slug() is None
    assert sdk_app.is_deployed() is False
    monkeypatch.setenv("PORT", "8000")
    assert sdk_app.public_url() == "http://127.0.0.1:8000/"
