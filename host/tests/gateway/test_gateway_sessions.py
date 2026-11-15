from __future__ import annotations

import os
import sys

import pytest

from efp_app_host.gateway.secret import access_log_key, load_or_create, matches
from efp_app_host.gateway.sessions import COOKIE_NAME, SessionCodec, b64decode, b64encode, split_cookie_header
from efp_app_host.models import Visitor
from efp_app_host.paths import HostPaths

from gateway_support import BOB, NOW, Clock, make_gateway

TTL = 168 * 3600


def codec(clock: Clock | None = None, *, key: bytes = b"k" * 32, revoked=None) -> SessionCodec:
    return SessionCodec(key, TTL, clock=clock or Clock(), revoked_before=revoked)


def test_a_token_gives_back_the_visitor_and_its_times():
    sessions = codec()
    session = sessions.read(sessions.issue(BOB))
    assert session is not None
    assert session.visitor == BOB
    assert session.issued_at == NOW
    assert session.expires_at == NOW + TTL


def test_names_outside_ascii_survive():
    visitor = Visitor(id=7, username="lee", display_name="\u674e\u96f7", group_id=None, group_name="\u6e2c\u8a66\u7d44")
    sessions = codec()
    token = sessions.issue(visitor)
    assert token.isascii()
    assert sessions.read(token).visitor == visitor


def test_the_cookie_has_the_attributes_of_the_contract():
    sessions = codec()
    cookie = sessions.cookie("token")
    name_value, *attributes = [part.strip() for part in cookie.split(";")]
    assert name_value == "__Host-efp_apps=token"
    assert sorted(attributes) == sorted(["Path=/", f"Max-Age={TTL}", "HttpOnly", "Secure", "SameSite=Lax"])
    cleared = sessions.clear_cookie()
    assert cleared.startswith("__Host-efp_apps=;") and "Max-Age=0" in cleared and "Path=/" in cleared


@pytest.mark.parametrize("position", ["payload", "signature"])
def test_a_changed_token_is_refused(position):
    sessions = codec()
    version, payload, signature = sessions.issue(BOB).split(".")

    def flip(text: str) -> str:
        return ("B" if text[0] == "A" else "A") + text[1:]

    if position == "payload":
        payload = flip(payload)
    else:
        signature = flip(signature)
    assert sessions.read(f"{version}.{payload}.{signature}") is None


def test_a_payload_cannot_be_swapped_under_another_signature():
    sessions = codec()
    mallory = Visitor(id=99, username="mallory")
    _, bob_payload, _ = sessions.issue(BOB).split(".")
    version, _, mallory_signature = sessions.issue(mallory).split(".")
    assert sessions.read(f"{version}.{bob_payload}.{mallory_signature}") is None


def test_a_token_signed_with_another_key_is_refused():
    token = codec(key=b"a" * 32).issue(BOB)
    assert codec(key=b"b" * 32).read(token) is None
    assert codec(key=b"a" * 32).read(token) is not None


@pytest.mark.parametrize(
    "token",
    ["", "s1", "s1.x", "s1..", "a.b.c", "s2.e30.e30", "s1.e30.!!!", "s1.e30.e30.e30", "x" * 5000, "s1.\u00e9.e30"],
)
def test_garbage_is_not_a_session(token):
    assert codec().read(token) is None


def test_a_signed_token_with_a_broken_body_is_refused():
    sessions = codec()
    payload = b64encode(b'{"exp": true, "iat": 1, "u": {"id": 1}}')
    assert sessions.read(f"s1.{payload}.{sessions._sign(payload)}") is None
    payload = b64encode(b'{"exp": 99999999999, "iat": 1}')
    assert sessions.read(f"s1.{payload}.{sessions._sign(payload)}") is None
    payload = b64encode(b"[1, 2]")
    assert sessions.read(f"s1.{payload}.{sessions._sign(payload)}") is None


def test_a_session_ends_when_its_lifetime_is_over():
    clock = Clock()
    sessions = codec(clock)
    token = sessions.issue(BOB)
    clock.advance(TTL - 1)
    assert sessions.read(token) is not None
    clock.advance(1)
    assert sessions.read(token) is None


def test_sliding_lifetime_renews_after_a_tenth():
    clock = Clock()
    sessions = codec(clock)
    session = sessions.read(sessions.issue(BOB))
    clock.advance(TTL / 10)
    assert sessions.needs_refresh(session) is False
    clock.advance(1)
    assert sessions.needs_refresh(session) is True

    renewed = sessions.read(sessions.issue(session.visitor))
    assert renewed.issued_at == clock.now
    assert renewed.expires_at == clock.now + TTL
    assert sessions.needs_refresh(renewed) is False


def test_revocation_ends_sessions_issued_before_it(settings, registry, fake_runtime, fake_portal):
    clock = Clock()
    gateway = make_gateway(settings, registry, fake_runtime, fake_portal, clock=clock)
    before = gateway.sessions.issue(BOB)
    other = gateway.sessions.issue(Visitor(id=13, username="carol"))
    assert gateway.sessions.read(before) is not None

    clock.advance(60)
    registry.revoke_user(BOB.id, at=clock.now)
    assert gateway.sessions.read(before) is None
    assert gateway.sessions.read(other) is not None

    clock.advance(1)
    after = gateway.sessions.issue(BOB)
    assert gateway.sessions.read(after) is not None


def test_the_cookie_header_is_split_into_session_and_the_rest():
    tokens, rest = split_cookie_header("a=1; __Host-efp_apps=tok; theme=dark; __Secure-efp_x=2; __HOST-EFP_other=3;  b=2 ")
    assert tokens == ["tok"]
    assert rest == "a=1; theme=dark; b=2"
    assert split_cookie_header(None) == ([], "")
    assert split_cookie_header("__Host-efp_apps=only") == (["only"], "")
    # A cookie that merely contains the name is left alone.
    assert split_cookie_header("x__Host-efp_apps=1") == ([], "x__Host-efp_apps=1")


def test_the_first_valid_session_of_a_cookie_header_wins():
    sessions = codec()
    good = sessions.issue(BOB)
    session, rest = sessions.read_cookie_header(f"{COOKIE_NAME}=junk; a=1; {COOKIE_NAME}={good}")
    assert session is not None and session.visitor == BOB
    assert rest == "a=1"
    session, rest = sessions.read_cookie_header("a=1")
    assert session is None and rest == "a=1"


def test_the_session_key_is_made_once_and_kept(settings, registry, fake_runtime, fake_portal):
    paths = HostPaths.of(settings)
    first = make_gateway(settings, registry, fake_runtime, fake_portal)
    assert not paths.session_key_file.exists()  # nothing is written before it is needed
    token = first.sessions.issue(BOB)
    assert paths.session_key_file.exists()
    if sys.platform != "win32":
        assert (os.stat(paths.session_key_file).st_mode & 0o777) == 0o600

    second = make_gateway(settings, registry, fake_runtime, fake_portal)
    assert second.sessions.read(token).visitor == BOB


def test_secret_files(tmp_path):
    path = tmp_path / "run" / "gateway.secret"
    first = load_or_create(path)
    assert len(first) >= 43
    assert load_or_create(path) == first
    if sys.platform != "win32":
        assert (os.stat(path).st_mode & 0o777) == 0o600

    # An empty left-over is replaced instead of being handed out.
    empty = tmp_path / "run" / "empty.secret"
    empty.write_text("", encoding="utf-8")
    assert load_or_create(empty)

    assert matches(first, first) is True
    assert matches(first + "x", first) is False
    assert matches("", first) is False
    assert matches(None, first) is False
    key = access_log_key(first)
    assert key == access_log_key(first) and key != first and first not in key


def test_strict_base64():
    assert b64decode(b64encode(b"\x00\xff")) == b"\x00\xff"
    with pytest.raises(ValueError):
        b64decode("ab cd")
    with pytest.raises(ValueError):
        b64decode("ab=d")
