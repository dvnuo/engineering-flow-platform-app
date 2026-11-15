from __future__ import annotations

import asyncio

import pytest

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
