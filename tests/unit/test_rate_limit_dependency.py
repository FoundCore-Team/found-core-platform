import pytest
from fastapi import HTTPException

from app.api.v1.dependencies.rate_limit import rate_limited, too_many_requests
from app.application.services.rate_limit_guard import RateLimitGuard
from app.domain.enums import RateLimitAction
from app.domain.exceptions import RateLimitExceededError
from tests.fixtures.factories import make_user_entity
from tests.fixtures.fake_rate_limiter import FakeRateLimiter


class _FakeContainer:
    def __init__(self, limiter: FakeRateLimiter) -> None:
        self._limiter = limiter

    def rate_limit_guard(self) -> RateLimitGuard:
        return RateLimitGuard(self._limiter)


async def test_an_allowed_request_passes_and_the_users_id_is_counted_under_the_action():
    limiter = FakeRateLimiter()
    user = make_user_entity(id=42)

    await rate_limited(RateLimitAction.TAG_SUGGEST)(current_user=user, container=_FakeContainer(limiter))

    assert limiter.hits == [(RateLimitAction.TAG_SUGGEST, 42)]


async def test_a_refused_request_answers_429_with_the_body_and_the_retry_after_header():
    limiter = FakeRateLimiter(denied={RateLimitAction.TAG_CREATE}, retry_after_seconds=13)

    with pytest.raises(HTTPException) as raised:
        await rate_limited(RateLimitAction.TAG_CREATE)(
            current_user=make_user_entity(id=42), container=_FakeContainer(limiter)
        )

    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "13"}
    assert raised.value.detail == {
        "code": "rate_limited",
        "message": "Too many requests.",
        "action": "tag_create",
        "retry_after_seconds": 13,
    }


async def test_two_rules_on_one_route_are_two_separate_counters():
    limiter = FakeRateLimiter(denied={RateLimitAction.TAG_CREATE_DAILY})
    container = _FakeContainer(limiter)
    user = make_user_entity(id=42)

    await rate_limited(RateLimitAction.TAG_CREATE)(current_user=user, container=container)
    with pytest.raises(HTTPException):
        await rate_limited(RateLimitAction.TAG_CREATE_DAILY)(current_user=user, container=container)

    assert limiter.hits == [(RateLimitAction.TAG_CREATE, 42), (RateLimitAction.TAG_CREATE_DAILY, 42)]


def test_too_many_requests_is_the_same_answer_for_the_dependency_and_for_a_use_case():
    error = too_many_requests(RateLimitExceededError(RateLimitAction.FEED_SESSION_BUILD, 9))

    assert error.status_code == 429
    assert error.headers == {"Retry-After": "9"}
    assert error.detail["action"] == "feed_session_build"
