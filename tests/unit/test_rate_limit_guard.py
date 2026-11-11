import pytest

from app.application.services.rate_limit_guard import RateLimitGuard
from app.domain.enums import RateLimitAction
from app.domain.exceptions import RateLimitExceededError
from tests.fixtures.fake_rate_limiter import FakeRateLimiter


async def test_an_allowed_request_passes_and_the_limiter_is_asked_once():
    limiter = FakeRateLimiter()

    await RateLimitGuard(limiter).check(RateLimitAction.CONTACT_OPEN, 42)

    assert limiter.hits == [(RateLimitAction.CONTACT_OPEN, 42)]


async def test_a_refused_request_raises_with_the_action_and_the_time_to_wait():
    limiter = FakeRateLimiter(denied={RateLimitAction.TAG_CREATE}, retry_after_seconds=13)

    with pytest.raises(RateLimitExceededError) as raised:
        await RateLimitGuard(limiter).check(RateLimitAction.TAG_CREATE, 42)

    assert raised.value.action is RateLimitAction.TAG_CREATE
    assert raised.value.retry_after_seconds == 13


async def test_only_the_refused_action_is_refused():
    limiter = FakeRateLimiter(denied={RateLimitAction.TAG_CREATE})

    await RateLimitGuard(limiter).check(RateLimitAction.TAG_SUGGEST, 42)
