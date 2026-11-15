from __future__ import annotations

import pytest

from efp_app_host.gateway import ratelimit
from efp_app_host.gateway.ratelimit import RateLimiter, normalize_source

from gateway_support import Clock


def test_a_burst_up_to_the_limit_then_a_steady_rate():
    clock = Clock(0.0)
    limiter = RateLimiter(120, clock=clock)
    assert all(limiter.allow("203.0.113.7") for _ in range(120))
    assert limiter.allow("203.0.113.7") is False
    assert limiter.retry_after == 1

    clock.advance(0.5)  # one request every half second at 120 a minute
    assert limiter.allow("203.0.113.7") is True
    assert limiter.allow("203.0.113.7") is False

    clock.advance(60)  # a minute of silence fills the allowance, not more
    assert sum(limiter.allow("203.0.113.7") for _ in range(200)) == 120


def test_each_address_has_its_own_allowance():
    limiter = RateLimiter(2, clock=Clock(0.0))
    assert [limiter.allow("198.51.100.1") for _ in range(3)] == [True, True, False]
    assert [limiter.allow("198.51.100.2") for _ in range(3)] == [True, True, False]
    assert limiter.retry_after == 30


def test_requests_without_an_address_share_one_allowance():
    limiter = RateLimiter(1, clock=Clock(0.0))
    assert limiter.allow("") is True
    assert limiter.allow(None) is False


@pytest.mark.parametrize(
    "source, key",
    [
        ("203.0.113.7", "203.0.113.7"),
        (" 203.0.113.7 ", "203.0.113.7"),
        ("203.0.113.7:51234", "203.0.113.7"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("[2001:db8:1:2::1]:443", "2001:db8:1:2::/64"),
        ("2001:db8:1:3::1", "2001:db8:1:3::/64"),
        ("Unknown", "unknown"),
        ("", ""),
        (None, ""),
    ],
)
def test_one_key_per_client(source, key):
    assert normalize_source(source) == key


def test_an_ipv6_client_cannot_dodge_the_limit_inside_its_network():
    limiter = RateLimiter(2, clock=Clock(0.0))
    assert limiter.allow("2001:db8:1:2::1") is True
    assert limiter.allow("2001:db8:1:2::2") is True
    assert limiter.allow("2001:db8:1:2::3") is False


def test_the_table_of_addresses_does_not_grow_without_end(monkeypatch):
    monkeypatch.setattr(ratelimit, "MAX_BUCKETS", 100)
    clock = Clock(0.0)
    limiter = RateLimiter(5, clock=clock)
    for index in range(100):
        limiter.allow(f"198.51.100.{index}")
    clock.advance(61)
    limiter.allow("203.0.113.1")
    assert len(limiter._buckets) == 1

    # Even a flood of fresh addresses within a minute stays bounded.
    for index in range(500):
        limiter.allow(f"10.0.{index // 250}.{index % 250}")
    assert len(limiter._buckets) <= 100
