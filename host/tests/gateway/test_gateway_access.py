from __future__ import annotations

import asyncio

import pytest

from efp_app_host.gateway import access as access_module
from efp_app_host.gateway.access import BACKOFF_SECONDS, STALE_SECONDS, AccessCache, AccessUnavailable
from efp_app_host.models import Visitor

from gateway_support import BOB, Clock, allow, run


def make(fake_portal, *, ttl: float = 30) -> tuple[AccessCache, Clock]:
    clock = Clock(1000.0)
    return AccessCache(fake_portal, ttl, clock=clock), clock


def test_an_answer_is_remembered_for_the_cache_lifetime(fake_portal):
    allow(fake_portal)
    cache, clock = make(fake_portal)

    first = run(cache.check("app-1", BOB.id))
    assert (first.allowed, first.reason, first.visitor, first.stale) == (True, None, BOB, False)
    clock.advance(29)
    again = run(cache.check("app-1", BOB.id))
    assert again.allowed is True
    assert fake_portal.access_calls == 1


def test_the_portal_is_asked_again_after_the_lifetime(fake_portal):
    allow(fake_portal)
    cache, clock = make(fake_portal)
    run(cache.check("app-1", BOB.id))

    # Access was taken away in the meantime.
    fake_portal.access[("app-1", BOB.id)] = (False, "no_access")
    clock.advance(30)
    decision = run(cache.check("app-1", BOB.id))
    assert (decision.allowed, decision.reason) == (False, "no_access")
    assert fake_portal.access_calls == 2


def test_a_refusal_is_remembered_too(fake_portal):
    cache, clock = make(fake_portal)
    assert run(cache.check("app-1", BOB.id)).allowed is False
    clock.advance(10)
    assert run(cache.check("app-1", BOB.id)).reason == "no_access"
    assert fake_portal.access_calls == 1


def test_each_app_and_user_has_its_own_answer(fake_portal):
    allow(fake_portal, "app-1")
    cache, _clock = make(fake_portal)
    assert run(cache.check("app-1", BOB.id)).allowed is True
    assert run(cache.check("app-2", BOB.id)).allowed is False
    assert run(cache.check("app-1", 99)).allowed is False
    assert fake_portal.access_calls == 3


def test_without_a_cache_lifetime_every_request_asks(fake_portal):
    allow(fake_portal)
    cache, _clock = make(fake_portal, ttl=0)
    run(cache.check("app-1", BOB.id))
    run(cache.check("app-1", BOB.id))
    assert fake_portal.access_calls == 2


def test_portal_down_reuses_an_answer_younger_than_five_minutes(fake_portal):
    allow(fake_portal)
    cache, clock = make(fake_portal)
    run(cache.check("app-1", BOB.id))

    fake_portal.fail = True
    clock.advance(STALE_SECONDS - 1)
    decision = run(cache.check("app-1", BOB.id))
    assert decision.allowed is True and decision.stale is True

    clock.advance(BACKOFF_SECONDS + 1)
    with pytest.raises(AccessUnavailable):
        run(cache.check("app-1", BOB.id))


def test_portal_down_reuses_a_refusal_as_well(fake_portal):
    cache, clock = make(fake_portal)
    run(cache.check("app-1", BOB.id))
    fake_portal.fail = True
    clock.advance(60)
    decision = run(cache.check("app-1", BOB.id))
    assert decision.allowed is False and decision.stale is True


def test_portal_down_and_nothing_remembered_fails_closed(fake_portal):
    fake_portal.fail = True
    cache, _clock = make(fake_portal)
    with pytest.raises(AccessUnavailable):
        run(cache.check("app-1", BOB.id))


def test_a_portal_that_is_down_is_left_alone_for_a_moment(fake_portal):
    fake_portal.fail = True
    cache, clock = make(fake_portal)
    for _ in range(5):
        with pytest.raises(AccessUnavailable):
            run(cache.check("app-1", BOB.id))
    assert fake_portal.access_calls == 1

    # Back up: the next attempt after the pause succeeds.
    fake_portal.fail = False
    allow(fake_portal)
    clock.advance(BACKOFF_SECONDS)
    assert run(cache.check("app-1", BOB.id)).allowed is True
    assert fake_portal.access_calls == 2


def test_requests_that_arrive_together_share_one_call(fake_portal):
    allow(fake_portal)
    cache, _clock = make(fake_portal)

    async def burst():
        return await asyncio.gather(*(cache.check("app-1", BOB.id) for _ in range(20)))

    decisions = run(burst())
    assert all(decision.allowed for decision in decisions)
    assert fake_portal.access_calls == 1


def test_an_answer_can_be_stored_without_asking(fake_portal):
    cache, clock = make(fake_portal)
    renamed = Visitor(id=BOB.id, username="bob", display_name="Robert")
    cache.remember("app-1", BOB.id, True, None, renamed)
    decision = run(cache.check("app-1", BOB.id))
    assert decision.allowed is True and decision.visitor == renamed
    assert fake_portal.access_calls == 0

    # Like any other answer it is checked again once the lifetime is over.
    clock.advance(30)
    assert run(cache.check("app-1", BOB.id)).allowed is False
    assert fake_portal.access_calls == 1


class SlowPortal:
    """Takes the call and answers only when told to."""

    def __init__(self):
        self.access_calls = 0
        self.release = None

    async def check_access(self, app_id, user_id):
        self.access_calls += 1
        self.release = asyncio.Event()
        await self.release.wait()
        return True, None, BOB


def test_a_remembered_answer_does_not_wait_out_a_portal_that_says_nothing(monkeypatch):
    monkeypatch.setattr(access_module, "STALE_WAIT_SECONDS", 0.05)
    portal, clock = SlowPortal(), Clock(1000.0)
    cache = AccessCache(portal, 30, clock=clock)
    cache.remember("app-1", BOB.id, True, None, BOB)
    clock.advance(60)  # past the lifetime, well inside the five minutes

    async def scenario():
        first = await cache.check("app-1", BOB.id)  # waits the short time, then falls back
        clock.advance(1)
        second = await asyncio.wait_for(cache.check("app-1", BOB.id), 0.01)  # the same call is overdue: no wait at all
        portal.release.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        third = await cache.check("app-1", BOB.id)  # the answer the call brought in the end
        return first, second, third

    first, second, third = run(scenario())
    assert (first.allowed, first.stale) == (True, True)
    assert (second.allowed, second.stale) == (True, True)
    assert (third.allowed, third.stale) == (True, False)
    assert portal.access_calls == 1


def test_without_a_remembered_answer_the_call_is_waited_for(monkeypatch):
    monkeypatch.setattr(access_module, "STALE_WAIT_SECONDS", 0.01)
    portal = SlowPortal()
    cache = AccessCache(portal, 30, clock=Clock(1000.0))

    async def scenario():
        asking = asyncio.ensure_future(cache.check("app-1", BOB.id))
        await asyncio.sleep(0.05)
        assert not asking.done()  # nothing to fall back on: refusing now would turn a slow Portal into an outage
        portal.release.set()
        return await asking

    assert run(scenario()).allowed is True
