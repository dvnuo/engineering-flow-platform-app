"""``efp_app.testing``: what an app's own tests use in place of the host, the Portal and the gateway."""

from __future__ import annotations

import os
import textwrap

import httpx
import pytest

from efp_app import app as sdk_app
from efp_app import client as sdk_client
from efp_app import config, connectors, errors, images, llm, storage, testing, visitor
from example_support import load_module

#: How a PNG file begins, which is all the SDK looks at.
PNG = b"\x89PNG\r\n\x1a\n" + bytes(24)


# --- The environment -----------------------------------------------------------------------------


def test_deployed_gives_the_environment_of_the_host_and_takes_it_back(tmp_path, monkeypatch):
    monkeypatch.setenv("EFP_APP_SLUG", "mine")
    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    with testing.deployed(tmp_path / "data", slug="triage") as portal:
        assert isinstance(portal, testing.FakePortal)
        settings = config.load()
        assert settings.deployed and settings.app_id == testing.APP_ID
        assert settings.slug == "triage" and settings.base_path == "/triage"
        assert settings.token == testing.TOKEN and settings.portal_url == testing.PORTAL_URL
        assert settings.dev_visitor is None
        assert sdk_app.public_url() == "https://apps.test/triage/"
        assert storage.mode() == "sandbox"
    # What was there before is there again; what was not is gone.
    assert os.environ["EFP_APP_SLUG"] == "mine" and os.environ["EFP_DEV_VISITOR"] == "carol"
    for name in ("EFP_APP_ID", "EFP_APP_TOKEN", "EFP_PORTAL_URL", "EFP_VISITOR_KEY", "EFP_APP_DATA_DIR", "EFP_APP_BASE_PATH"):
        assert name not in os.environ, name


def test_deployed_puts_everything_back_when_the_test_fails(tmp_path):
    with pytest.raises(RuntimeError):
        with testing.deployed(tmp_path):
            assert config.load().deployed
            raise RuntimeError("the test failed")
    assert not config.load().deployed and "EFP_APP_ID" not in os.environ
    # The shared client is no longer the fake one: without settings there is nothing to call.
    with pytest.raises(errors.ConfigurationError):
        sdk_app.info()


def test_environment_is_what_deployed_sets(tmp_path):
    found = testing.environment(tmp_path, slug="triage")
    assert found == {
        "EFP_APP_ID": testing.APP_ID,
        "EFP_APP_SLUG": "triage",
        "EFP_APP_TOKEN": testing.TOKEN,
        "EFP_APP_BASE_PATH": "/triage",
        "EFP_APP_PUBLIC_URL": "https://apps.test/triage/",
        "EFP_PORTAL_URL": testing.PORTAL_URL,
        "EFP_VISITOR_KEY": testing.VISITOR_KEY,
        "EFP_APP_DATA_DIR": str(tmp_path),
    }
    assert config.load(found).deployed


def test_storage_goes_to_the_directory_of_the_test(tmp_path):
    with testing.deployed(tmp_path / "data"):
        storage.save_json("notes/1.json", {"text": "hello"})
        assert storage.load_json("notes/1.json") == {"text": "hello"}
        assert storage.data_dir() == tmp_path / "data"
    assert [path.name for path in (tmp_path / "data").rglob("1.json")] == ["1.json"]
    with testing.deployed(tmp_path / "other"):
        assert storage.load_json("notes/1.json", None) is None


# --- The visitor ---------------------------------------------------------------------------------


def test_visitor_headers_name_a_member_the_app_can_trust(tmp_path):
    with testing.deployed(tmp_path):
        who = visitor.current(testing.visitor_headers("bob"))
        assert who is not None
        assert (who.username, who.name, who.group, who.group_id) == ("bob", "Bob", "QA", 3)
        assert who.id == visitor.dev_visitor_id("bob")
        other = visitor.current(testing.visitor_headers("dana", id=77, name="Dana K", group=None, group_id=None))
        assert (other.id, other.username, other.name, other.group, other.group_id) == (77, "dana", "Dana K", None, None)
        # No header is a guest, as on an app open to anyone.
        assert visitor.current({}) is None


def test_visitor_headers_expire_and_belong_to_one_app(tmp_path, monkeypatch):
    with testing.deployed(tmp_path):
        assert visitor.current(testing.visitor_headers("bob", expires_in=-5)) is None
        made_here = testing.visitor_headers("bob")
        monkeypatch.setenv("EFP_APP_ID", "0b0f3a4e-0000-4000-8000-00000000ffff")
        assert visitor.current(made_here) is None


def test_the_owner_of_app_info_is_the_visitor_of_the_same_name(tmp_path):
    with testing.deployed(tmp_path):
        info = sdk_app.info()
        assert info.owner.username == "alice" and info.slug == "example"
        assert visitor.current(testing.visitor_headers("alice")).id == info.owner.id
        assert visitor.current(testing.visitor_headers("bob")).id != info.owner.id
    with testing.deployed(tmp_path, info=testing.app_info(owner="dana", connectors=[{"type": "jira", "write": False}])):
        info = sdk_app.info()
        assert info.owner.username == "dana" and [grant.type for grant in info.connectors] == ["jira"]


# --- The Portal's answers ------------------------------------------------------------------------


def test_a_completion_and_what_the_app_sent(tmp_path):
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/llm/complete", testing.completion("Three bugs are open.", model="gpt-5.4-mini"))
        bob = testing.visitor_headers("bob")
        answer = llm.complete("Summarise the bugs.", system="Be short.", visitor=visitor.current(bob))
        assert answer.text == "Three bugs are open." and answer.model == "gpt-5.4-mini"
        assert answer.usage.input_tokens == 12
        sent = portal.sent_to("/llm/complete")
        assert len(sent) == 1 and sent[0]["messages"][-1] == {"role": "user", "content": "Summarise the bugs."}
        assert portal.count("post", "/llm/complete") == 1 and portal.body() == sent[0]
        # The Portal's usage page learns who caused the call.
        assert portal.last.headers["X-EFP-Visitor"] == bob["X-EFP-Visitor"]


def test_a_streamed_completion(tmp_path):
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/llm/complete", testing.stream("Three ", "bugs."))
        with llm.stream("Summarise the bugs.") as reply:
            assert list(reply) == ["Three ", "bugs."]
            assert reply.text == "Three bugs." and reply.finish_reason == "stop"
        portal.on("POST", "/llm/complete", testing.stream("Cut", finish_reason="length"))
        assert llm.stream("Again.").completion().finish_reason == "length"


def test_an_inspection_and_what_the_app_sent(tmp_path):
    assert testing.inspection() == {
        "text": "A red square.",
        "model": "gpt-5.4",
        "finish_reason": "stop",
        "usage": {"input_tokens": 1530, "output_tokens": 6, "credits": None},
    }
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/images/inspect", testing.inspection("A sign-in form with two fields."))
        bob = testing.visitor_headers("bob")
        answer = images.inspect(PNG, "What is shown?", max_output_tokens=300, visitor=visitor.current(bob))
        assert (answer.text, answer.model, answer.finish_reason) == ("A sign-in form with two fields.", "gpt-5.4", "stop")
        assert (answer.usage.input_tokens, answer.usage.output_tokens, answer.usage.credits) == (1530, 6, None)
        sent = portal.sent_to("/images/inspect")
        assert len(sent) == 1 and sent[0]["prompt"] == "What is shown?" and sent[0]["max_output_tokens"] == 300
        assert [item["media_type"] for item in sent[0]["images"]] == ["image/png"]
        assert portal.count("POST", "/images/inspect") == 1
        assert portal.last.headers["X-EFP-Visitor"] == bob["X-EFP-Visitor"]

        cut = testing.inspection("A sign-in", finish_reason="length", usage={"input_tokens": 1530, "output_tokens": 300, "credits": 0.02})
        portal.on("POST", "/images/inspect", cut)
        answer = images.inspect(PNG, "What is shown?")
        assert (answer.finish_reason, answer.usage.output_tokens, answer.usage.credits) == ("length", 300, 0.02)


def test_image_analysis_is_on_unless_the_test_turns_it_off(tmp_path):
    assert testing.app_info()["image_analysis"] == {
        "enabled": True,
        "model": "gpt-5.4",
        "max_images": 4,
        "max_image_bytes": 5242880,
        "max_total_bytes": 20971520,
    }
    with testing.deployed(tmp_path):
        assert sdk_app.info().image_analysis == sdk_app.ImageAnalysis(enabled=True, model="gpt-5.4")
    with testing.deployed(tmp_path, info=testing.app_info(image_analysis={"enabled": False})) as portal:
        assert sdk_app.info().image_analysis == sdk_app.ImageAnalysis(enabled=False, model=None)
        # The fake Portal refuses nothing by itself: the refusal is the test's to give.
        portal.on("POST", "/images/inspect", testing.error(403, "connector_disabled", "image analysis is not turned on"))
        with pytest.raises(errors.ConnectorDisabledError) as caught:
            images.inspect(PNG, "What is shown?")
        assert str(caught.value) == "connector_disabled: image analysis is not turned on"


def test_a_connector_answer(tmp_path):
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/connectors/jira/request", testing.connector_answer({"issues": [{"key": "SHOP-1"}]}))
        found = connectors.get("jira", "/rest/api/2/search", jql="project = SHOP")
        assert found.ok and found.json == {"issues": [{"key": "SHOP-1"}]}
        assert portal.sent_to("/connectors/jira/request")[0]["path"] == "/rest/api/2/search"

        portal.on("POST", "/connectors/jenkins/request", testing.connector_answer(text="BUILD FAILED", status=404, truncated=True))
        log = connectors.get("jenkins", "/job/shop/1/consoleText")
        assert (log.status, log.ok, log.json, log.text, log.truncated) == (404, False, None, "BUILD FAILED", True)


def test_a_refusal_is_raised_as_the_error_of_its_code(tmp_path):
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/llm/complete", testing.error(429, "budget_exceeded", headers={"Retry-After": "3600"}))
        with pytest.raises(errors.BudgetExceededError) as caught:
            llm.complete("Hello")
        assert caught.value.retry_after == 3600

        portal.on("POST", "/connectors/jira/request", testing.error(403, "connector_disabled"))
        with pytest.raises(errors.ConnectorDisabledError):
            connectors.get("jira", "/rest/api/2/myself")


def test_one_response_answers_any_number_of_calls_of_either_kind(tmp_path):
    # An app with one route that calls llm.complete and another that awaits llm.acomplete, tested
    # with one canned answer: httpx binds a response to the kind of client that sent the request,
    # so the second call used to end in a bare AssertionError.
    import asyncio

    async def pieces():
        return [piece async for piece in llm.astream("Hello")]

    with testing.deployed(tmp_path) as portal:
        refusal = testing.error(429, "budget_exceeded", headers={"Retry-After": "60"})
        portal.on("POST", "/llm/complete", refusal)
        for call in (lambda: llm.complete("Hello"), lambda: asyncio.run(llm.acomplete("Hello")), lambda: llm.complete("Hello")):
            with pytest.raises(errors.BudgetExceededError) as caught:
                call()
            assert caught.value.retry_after == 60

        portal.on("POST", "/llm/complete", testing.stream("Three ", "bugs."))
        assert asyncio.run(pieces()) == ["Three ", "bugs."]
        assert list(llm.stream("Hello")) == ["Three ", "bugs."]
        assert asyncio.run(pieces()) == ["Three ", "bugs."]
        # Each call got a response of its own: the canned ones never met a request.
        for canned in (refusal, portal.routes[("POST", "/llm/complete")]):
            with pytest.raises(RuntimeError):
                canned.request


def test_a_response_built_around_a_stream_is_handed_on_as_it_is(tmp_path):
    class Once(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"type": "done"}\n\n'

    with testing.deployed(tmp_path) as portal:
        answer = httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Once())
        portal.on("POST", "/llm/complete", answer)
        assert list(llm.stream("Hello")) == []
        assert answer.is_closed is True  # the very object: a stream of the test's own cannot be copied


def test_answers_in_turn_a_callable_and_a_broken_connection(tmp_path, no_sleep):
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/llm/complete", [testing.error(502, "provider_error"), testing.completion("Second try.")])
        with pytest.raises(errors.ProviderError):
            llm.complete("Hello")
        assert llm.complete("Hello").text == "Second try."
        assert llm.complete("Hello").text == "Second try."  # the last answer stays

        portal.on("POST", "/llm/complete", lambda request: testing.completion("You sent %d bytes." % len(request.content)))
        assert llm.complete("Hello").text.startswith("You sent ")

        portal.on("POST", "/llm/complete", httpx.ConnectError("no route to the Portal"))
        with pytest.raises(errors.PortalUnreachableError):
            llm.complete("Hello")


def test_a_list_of_answers_is_the_tests_own_and_cannot_be_empty(tmp_path, no_sleep):
    answers = [testing.completion("First."), testing.completion("Second.")]
    with testing.deployed(tmp_path) as portal:
        portal.on("POST", "/llm/complete", answers)
        assert [llm.complete("Hello").text for _ in range(3)] == ["First.", "Second.", "Second."]
        assert len(answers) == 2  # the list the test wrote is not used up
        with pytest.raises(ValueError):
            portal.on("POST", "/llm/complete", [])


def test_a_request_without_a_body_is_none(tmp_path):
    with testing.deployed(tmp_path) as portal:
        sdk_app.info()
        assert portal.sent_to("/app") == [None] and portal.body() is None
        assert portal.count("GET", "/app") == 1


def test_a_route_without_an_answer_says_which_one(tmp_path):
    with testing.deployed(tmp_path) as portal:
        with pytest.raises(errors.EfpError) as caught:
            connectors.get("github", "/user/repos")
        assert caught.value.code == "not_found"
        assert "the test gave no answer for POST /connectors/github/request" in str(caught.value)
        assert portal.count("POST", "/connectors/github/request") == 1


def test_a_fake_portal_without_the_environment(credentials):
    # For code that takes a client: no environment of a deployed app, no shared state.
    portal = testing.FakePortal().on("GET", "/app", testing.app_info(name="Mine"))
    with portal.client() as http:
        assert sdk_app.info(client=http).name == "Mine"
    # install() is what deployed() does for the shared clients.
    portal.install()
    assert sdk_app.info().name == "Mine"
    sdk_client.reset()


# --- A whole app under test ----------------------------------------------------------------------

FLASK_APP = '''
from flask import Flask, jsonify

from efp_app import app as efp, errors, llm, storage
from efp_app.integrations.flask import get_visitor, require_visitor

app = Flask(__name__)


@app.post("/api/summary")
def summary():
    who = require_visitor()
    try:
        answer = llm.complete("Summarise the bugs.", visitor=who)
    except errors.EfpError as exc:
        return jsonify({"error": {"code": exc.code, "message": exc.user_message}}), 502
    storage.save_json("summaries/%d.json" % who.id, {"text": answer.text})
    return jsonify({"text": answer.text, "by": who.username})


@app.post("/api/hide")
def hide():
    who = require_visitor()
    if who.id != efp.info(cached=True).owner.id:
        return jsonify({"error": {"code": "owner_only", "message": "Only the owner may hide a bug."}}), 403
    return jsonify({"ok": True})
'''

FASTAPI_APP = '''
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from efp_app import connectors
from efp_app.integrations.fastapi import get_visitor, require_visitor
from efp_app.visitor import Visitor

app = FastAPI()
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/api/me")
def me(who: Optional[Visitor] = Depends(get_visitor)):
    return {"username": who.username if who else None}


@app.get("/api/bugs")
async def bugs(who: Visitor = Depends(require_visitor)):
    found = await connectors.aget("jira", "/rest/api/2/search", visitor=who, jql="project = SHOP")
    return {"keys": [issue["key"] for issue in found.json["issues"]]}
'''


def test_a_flask_app_is_tested_from_its_own_repository(tmp_path):
    pytest.importorskip("flask")
    source = tmp_path / "app.py"
    source.write_text(textwrap.dedent(FLASK_APP), encoding="utf-8")
    with testing.deployed(tmp_path / "data") as portal:
        portal.on("POST", "/llm/complete", testing.completion("Three bugs are open."))
        client = load_module("testing_flask_app", source).app.test_client()

        answer = client.post("/api/summary", headers=testing.visitor_headers("bob"))
        assert answer.status_code == 200 and answer.get_json() == {"text": "Three bugs are open.", "by": "bob"}
        assert storage.load_json("summaries/%d.json" % visitor.dev_visitor_id("bob")) == {"text": "Three bugs are open."}

        # A guest of an app open to anyone: no header.
        assert client.post("/api/summary").status_code == 401

        assert client.post("/api/hide", headers=testing.visitor_headers("bob")).status_code == 403
        assert client.post("/api/hide", headers=testing.visitor_headers("alice")).get_json() == {"ok": True}

        portal.on("POST", "/llm/complete", testing.error(429, "budget_exceeded", headers={"Retry-After": "60"}))
        refused = client.post("/api/summary", headers=testing.visitor_headers("bob"))
        assert refused.status_code == 502 and refused.get_json()["error"]["code"] == "budget_exceeded"


def test_a_fastapi_app_is_tested_from_its_own_repository(tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    source = tmp_path / "main.py"
    source.write_text(textwrap.dedent(FASTAPI_APP), encoding="utf-8")
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "app.js").write_text("// the page's script", encoding="utf-8")
    with testing.deployed(tmp_path / "data") as portal:
        portal.on("POST", "/connectors/jira/request", testing.connector_answer({"issues": [{"key": "SHOP-1"}, {"key": "SHOP-2"}]}))
        module = load_module("testing_fastapi_app", source)
        # The prefix is given as on the host: uvicorn --root-path /example, which hands
        # the app paths that begin with the prefix. So do the requests of a test.
        with TestClient(module.app, root_path=config.load().base_path) as client:
            assert client.get("/example/api/me").json() == {"username": None}
            assert client.get("/example/api/me", headers=testing.visitor_headers("bob")).json() == {"username": "bob"}
            assert client.get("/example/api/bugs").status_code == 401
            assert client.get("/example/api/bugs", headers=testing.visitor_headers("bob")).json() == {"keys": ["SHOP-1", "SHOP-2"]}
            assert client.get("/example/static/app.js").text == "// the page's script"
            # Written without the prefix, the request for the mounted file was answered
            # with 404 by Starlette 1.10: a test would report a missing file the host serves.
        sent = portal.sent_to("/connectors/jira/request")
        assert sent[0]["path"] == "/rest/api/2/search" and sent[0]["query"] == {"jql": "project = SHOP"}
