from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from efp_app_host.gateway import visitor as visitor_header
from efp_app_host.models import Visitor

from gateway_support import BOB, NOW, OTHER_VISITOR_KEY, VISITOR_KEY


def unpadded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def test_round_trip():
    value = visitor_header.sign("app-1", VISITOR_KEY, BOB, now=NOW)
    assert visitor_header.verify(value, VISITOR_KEY, "app-1", now=NOW) == {
        "app": "app-1",
        "exp": int(NOW) + 60,
        "group": "QA",
        "group_id": 3,
        "name": "Bob",
        "sub": 12,
        "username": "bob",
    }


def test_the_value_has_the_format_of_the_contract():
    value = visitor_header.sign("app-1", VISITOR_KEY, BOB, now=NOW)
    version, payload, signature = value.split(".")
    assert version == "v1"
    assert "=" not in value

    body = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    # Keys sorted, no spaces.
    assert payload == unpadded(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    key = bytes(range(32))
    assert signature == unpadded(hmac.new(key, f"v1.{payload}".encode("ascii"), hashlib.sha256).digest())


def test_it_is_valid_for_sixty_seconds():
    value = visitor_header.sign("app-1", VISITOR_KEY, BOB, now=NOW)
    assert visitor_header.verify(value, VISITOR_KEY, "app-1", now=NOW + 60) is not None
    assert visitor_header.verify(value, VISITOR_KEY, "app-1", now=NOW + 61) is None


def test_a_header_signed_for_another_app_does_not_verify():
    # Another app has another key ...
    foreign = visitor_header.sign("app-2", OTHER_VISITOR_KEY, BOB, now=NOW)
    assert visitor_header.verify(foreign, VISITOR_KEY, "app-1", now=NOW) is None
    # ... and even with the same key the app named inside must match.
    same_key = visitor_header.sign("app-2", VISITOR_KEY, BOB, now=NOW)
    assert visitor_header.verify(same_key, VISITOR_KEY, "app-1", now=NOW) is None
    assert visitor_header.verify(same_key, VISITOR_KEY, "app-2", now=NOW) is not None


def test_a_changed_value_does_not_verify():
    value = visitor_header.sign("app-1", VISITOR_KEY, BOB, now=NOW)
    version, payload, signature = value.split(".")
    admin = dict(visitor_header.payload_for("app-1", BOB, expires_at=int(NOW) + 60), username="admin")
    forged = unpadded(json.dumps(admin, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    assert visitor_header.verify(f"{version}.{forged}.{signature}", VISITOR_KEY, "app-1", now=NOW) is None
    assert visitor_header.verify(f"v2.{payload}.{signature}", VISITOR_KEY, "app-1", now=NOW) is None
    assert visitor_header.verify(f"{version}.{payload}", VISITOR_KEY, "app-1", now=NOW) is None
    assert visitor_header.verify(f"{version}.{payload}.{signature}x", VISITOR_KEY, "app-1", now=NOW) is None
    assert visitor_header.verify("", VISITOR_KEY, "app-1", now=NOW) is None
    assert visitor_header.verify(None, VISITOR_KEY, "app-1", now=NOW) is None


def test_no_usable_key_means_no_header():
    assert visitor_header.sign("app-1", "", BOB, now=NOW) is None
    assert visitor_header.sign("app-1", None, BOB, now=NOW) is None
    assert visitor_header.sign("app-1", "not base64 !", BOB, now=NOW) is None
    value = visitor_header.sign("app-1", VISITOR_KEY, BOB, now=NOW)
    assert visitor_header.verify(value, "", "app-1", now=NOW) is None


def test_a_padded_key_is_the_same_key():
    padded = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
    assert padded.endswith("=")
    value = visitor_header.sign("app-1", padded, BOB, now=NOW)
    assert visitor_header.verify(value, VISITOR_KEY, "app-1", now=NOW) is not None


def test_names_outside_ascii_and_a_visitor_without_group():
    visitor = Visitor(id=7, username="lee", display_name="\u674e\u96f7")
    value = visitor_header.sign("app-1", VISITOR_KEY, visitor, now=NOW)
    assert value.isascii()
    body = visitor_header.verify(value, VISITOR_KEY, "app-1", now=NOW)
    assert body["name"] == "\u674e\u96f7"
    assert body["group"] is None and body["group_id"] is None

    nameless = visitor_header.sign("app-1", VISITOR_KEY, Visitor(id=8, username="kim"), now=NOW)
    assert visitor_header.verify(nameless, VISITOR_KEY, "app-1", now=NOW)["name"] == "kim"


def test_the_sdk_reads_what_the_gateway_signs(monkeypatch):
    """The SDK's check (sdk/efp_app/visitor.py) mirrors ``verify``."""

    sdk = Path(__file__).resolve().parents[3] / "sdk"
    if not (sdk / "efp_app" / "visitor.py").is_file():
        pytest.skip("the SDK is not in this checkout")
    monkeypatch.syspath_prepend(str(sdk))
    sdk_visitor = pytest.importorskip("efp_app.visitor")
    monkeypatch.setenv("EFP_APP_ID", "app-1")
    monkeypatch.setenv("EFP_VISITOR_KEY", VISITOR_KEY)

    value = visitor_header.sign("app-1", VISITOR_KEY, BOB, now=NOW)
    seen = sdk_visitor.parse(value, now=NOW + 30)
    assert seen is not None
    assert (seen.id, seen.username, seen.name, seen.group, seen.group_id) == (12, "bob", "Bob", "QA", 3)
    assert sdk_visitor.parse(value, now=NOW + 61) is None
    assert sdk_visitor.parse(visitor_header.sign("app-2", VISITOR_KEY, BOB, now=NOW), now=NOW) is None
