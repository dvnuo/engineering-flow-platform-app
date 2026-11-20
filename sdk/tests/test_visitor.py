"""The visitor header (section 6.4).

``sign`` below is written from the text of the contract, not from the SDK::

    X-EFP-Visitor: v1.<payload>.<signature>
    payload   = base64url(no padding) of the JSON, keys sorted, no spaces
    signature = base64url(no padding) of HMAC-SHA256(key, "v1." + payload)
    key       = base64url-decoded visitor_key of the app
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time

import pytest

from efp_app import visitor
from efp_app.visitor import Visitor
from conftest import envelope

APP_ID = "0b0f3a4e-0000-4000-8000-000000000001"
RAW_KEY = bytes(range(32))
VISITOR_KEY = base64.urlsafe_b64encode(RAW_KEY).decode("ascii").rstrip("=")
OTHER_KEY = base64.urlsafe_b64encode(bytes(range(1, 33))).decode("ascii").rstrip("=")
OWNER = {"id": 5, "username": "alice", "display_name": "Alice", "group_id": 3, "group_name": "QA"}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def payload_of(**changes):
    payload = {
        "app": APP_ID,
        "exp": int(time.time()) + 60,
        "group": "QA",
        "group_id": 3,
        "name": "Bob",
        "sub": 12,
        "username": "bob",
    }
    payload.update(changes)
    return payload


def sign(payload, key: str = VISITOR_KEY) -> str:
    body = b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    raw_key = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
    signature = b64url(hmac.new(raw_key, ("v1." + body).encode("ascii"), hashlib.sha256).digest())
    return "v1." + body + "." + signature


@pytest.fixture
def deployed(monkeypatch):
    monkeypatch.setenv("EFP_APP_ID", APP_ID)
    monkeypatch.setenv("EFP_VISITOR_KEY", VISITOR_KEY)


# --- parse ----------------------------------------------------------------------------


def test_round_trip(deployed):
    value = sign(payload_of())
    found = visitor.parse(value)
    assert found == Visitor(id=12, username="bob", name="Bob", group="QA", group_id=3)
    assert found.raw == value
    assert "raw" not in repr(found) and value not in repr(found)


def test_the_key_can_be_given_as_text_or_bytes():
    value = sign(payload_of())
    assert visitor.parse(value, VISITOR_KEY).username == "bob"
    assert visitor.parse(value, VISITOR_KEY + "=").username == "bob"
    assert visitor.parse(value, RAW_KEY).username == "bob"
    assert visitor.parse(value, key=RAW_KEY).id == 12


def test_names_outside_ascii_survive(deployed):
    found = visitor.parse(sign(payload_of(name="Zo\u00eb \u5f20", group="\u6d4b\u8bd5")))
    assert found.name == "Zo\u00eb \u5f20"
    assert found.group == "\u6d4b\u8bd5"


def test_a_visitor_without_a_group(deployed):
    found = visitor.parse(sign(payload_of(group=None, group_id=None)))
    assert found.group is None and found.group_id is None


def test_a_missing_name_falls_back_to_the_username(deployed):
    assert visitor.parse(sign(payload_of(name=""))).name == "bob"


def test_tampered_payload(deployed):
    version, body, signature = sign(payload_of()).split(".")
    forged = b64url(json.dumps(payload_of(sub=1, username="admin"), sort_keys=True, separators=(",", ":")).encode("utf-8"))
    assert visitor.parse(".".join([version, forged, signature])) is None


def test_tampered_signature(deployed):
    version, body, signature = sign(payload_of()).split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]
    assert visitor.parse(".".join([version, body, flipped])) is None
    assert visitor.parse(".".join([version, body, signature[:-2]])) is None
    assert visitor.parse(".".join([version, body, ""])) is None


def test_wrong_key(deployed):
    assert visitor.parse(sign(payload_of(), OTHER_KEY)) is None
    assert visitor.parse(sign(payload_of()), OTHER_KEY) is None


def test_expired(deployed):
    assert visitor.parse(sign(payload_of(exp=int(time.time()) - 1))) is None
    value = sign(payload_of(exp=1763200000))
    assert visitor.parse(value, now=1763199999.0).username == "bob"
    assert visitor.parse(value, now=1763200000.0).username == "bob"
    assert visitor.parse(value, now=1763200000.5) is None


def test_a_value_made_for_another_app(deployed):
    assert visitor.parse(sign(payload_of(app="another-app"))) is None


def test_the_signature_is_compared_in_constant_time(deployed, monkeypatch):
    calls = []
    real = hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(visitor.hmac, "compare_digest", spy)
    assert visitor.parse(sign(payload_of())) is not None
    assert len(calls) == 1


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "garbage",
        "v1.only-two",
        "v2.e30.c2ln",
        "v1.e30.c2ln.extra",
        "v1.not base64!.c2ln",
        "v1." + "e30" + ".!!!",
        12345,
    ],
)
def test_malformed_values_are_anonymous(deployed, value):
    assert visitor.parse(value) is None


@pytest.mark.parametrize(
    "changes",
    [
        {"exp": "soon"},
        {"exp": True},
        {"sub": "12"},
        {"sub": True},
        {"sub": None},
        {"username": ""},
        {"username": 7},
    ],
)
def test_a_signed_payload_with_wrong_types_is_anonymous(deployed, changes):
    assert visitor.parse(sign(payload_of(**changes))) is None


def test_a_signed_payload_that_is_not_an_object_is_anonymous(deployed):
    assert visitor.parse(sign([1, 2, 3])) is None


def test_without_a_key_nothing_can_be_trusted(monkeypatch):
    assert visitor.parse(sign(payload_of())) is None
    monkeypatch.setenv("EFP_VISITOR_KEY", "***not base64***")
    assert visitor.parse(sign(payload_of())) is None


# --- current --------------------------------------------------------------------------


class Headers:
    """A case-insensitive mapping, like the header objects of web frameworks."""

    def __init__(self, items):
        self._items = {name.lower(): value for name, value in items.items()}

    def get(self, name, default=None):
        return self._items.get(name.lower(), default)

    def items(self):
        return self._items.items()


@pytest.mark.parametrize(
    "make",
    [
        lambda value: {"X-EFP-Visitor": value},
        lambda value: {"x-efp-visitor": value},
        lambda value: {"X-Efp-Visitor": value, "Accept": "text/html"},
        lambda value: Headers({"X-EFP-VISITOR": value}),
        lambda value: {"HTTP_X_EFP_VISITOR": value, "REQUEST_METHOD": "GET"},
        lambda value: {b"x-efp-visitor": value.encode("ascii")},
        lambda value: {"x-efp-visitor": [value]},
    ],
)
def test_current_finds_the_header_in_any_mapping(deployed, make):
    found = visitor.current(make(sign(payload_of())))
    assert found is not None and found.username == "bob"


def test_no_header_on_the_host_is_an_anonymous_visitor(deployed, monkeypatch):
    monkeypatch.setenv("EFP_DEV_VISITOR", "mallory")
    assert visitor.current({}) is None
    assert visitor.current({"accept": "text/html"}) is None
    assert visitor.current(None) is None
    assert visitor.current() is None


def test_a_bad_header_is_an_anonymous_visitor_even_locally(monkeypatch):
    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    monkeypatch.setenv("EFP_VISITOR_KEY", VISITOR_KEY)
    assert visitor.current({"X-EFP-Visitor": "v1.forged.forged"}) is None
    assert visitor.current({"X-EFP-Visitor": sign(payload_of())}).username == "bob"


# --- a developer's machine ---------------------------------------------------------------


def test_dev_visitor_from_the_environment(monkeypatch):
    monkeypatch.setenv("EFP_DEV_VISITOR", "carol")
    found = visitor.current({})
    assert found == Visitor(id=visitor.dev_visitor_id("carol"), username="carol", name="carol")
    assert found.raw is None


def test_two_names_are_two_visitors_on_a_developers_machine(monkeypatch):
    # An app that keeps data per visitor.id can be tried as two members on one machine.
    ids = {}
    for name in ("alice", "bob", "carol", "dave", "Alice ", "ALICE"):
        monkeypatch.setenv("EFP_DEV_VISITOR", name)
        ids[name] = visitor.current({}).id
    assert ids["alice"] == ids["Alice "] == ids["ALICE"] == visitor.dev_visitor_id("alice")
    assert len({ids["alice"], ids["bob"], ids["carol"], ids["dave"]}) == 4
    # Far above the ids the Portal gives its members, and the same on every run.
    assert all(900_000_000 <= value < 1_000_000_000 for value in ids.values())
    assert visitor.dev_visitor_id("alice") == 900_000_000 + __import__("zlib").crc32(b"alice") % 100_000_000


@pytest.mark.parametrize("name", ["anonymous", "Anonymous", "none"])
def test_dev_visitor_can_be_nobody(monkeypatch, portal, name):
    monkeypatch.setenv("EFP_DEV_VISITOR", name)
    assert visitor.current({}) is None
    assert portal.requests == []


def test_dev_visitor_defaults_to_the_owner_of_the_app(portal):
    portal.on("GET", "/app", {"id": "a1", "owner": OWNER})
    found = visitor.current({})
    assert found == Visitor(id=5, username="alice", name="Alice", group="QA", group_id=3)
    assert found.raw is None
    assert visitor.current({}) == found
    assert visitor.current({"accept": "*/*"}) == found
    assert portal.count("GET", "/app") == 1


def test_dev_visitor_without_credentials_is_nobody_and_says_so_once(caplog):
    with caplog.at_level(logging.WARNING, logger="efp_app"):
        assert visitor.current({}) is None
        assert visitor.current({}) is None
    warnings = [record for record in caplog.records if "EFP_DEV_VISITOR" in record.getMessage()]
    assert len(warnings) == 1
    assert "EFP_APP_TOKEN" in warnings[0].getMessage()


def test_dev_visitor_when_the_portal_refuses(portal, caplog):
    portal.on("GET", "/app", envelope(401, "invalid_token"))
    with caplog.at_level(logging.WARNING, logger="efp_app"):
        assert visitor.current({}) is None
        assert visitor.current({}) is None
    assert portal.count("GET", "/app") == 1  # not asked again right away
    assert any("invalid_token" in record.getMessage() for record in caplog.records)


def test_dev_visitor_is_asked_again_after_the_pause(portal, monkeypatch):
    portal.on("GET", "/app", [envelope(502, "upstream_error"), {"id": "a1", "owner": OWNER}])
    assert visitor.current({}) is None
    monkeypatch.setattr(visitor, "_OWNER_RETRY_SECONDS", 0.0)
    visitor._owner = (visitor._owner[0], 0.0, None)
    assert visitor.current({}).username == "alice"
