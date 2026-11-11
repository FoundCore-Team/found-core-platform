"""The fixed-window counter against a real Redis. Time is injected through the limiter's `clock`, so the window
boundary is tested without sleeping."""

import asyncio
import logging
from datetime import datetime, timezone

from redis.asyncio import Redis

from app.domain.enums import RateLimitAction
from app.infrastructure.repositories import RedisRateLimiter

# Fifteen seconds into a minute, so the first window is 15 s old and ends in 45 s.
AT = datetime(2026, 11, 11, 12, 0, 15, tzinfo=timezone.utc).timestamp()
ACTION = RateLimitAction.CONTACT_OPEN


def build(redis_client, now: list[float], limit: int = 3, window_seconds: int = 60) -> RedisRateLimiter:
    rules = {action: (limit, window_seconds) for action in RateLimitAction}
    return RedisRateLimiter(redis_client, rules, clock=lambda: now[0])


async def test_requests_up_to_the_limit_are_allowed_and_the_next_one_is_not(redis_client):
    limiter = build(redis_client, [AT], limit=3)

    decisions = [await limiter.hit(ACTION, 42) for _ in range(5)]

    assert [decision.allowed for decision in decisions] == [True, True, True, False, False]


async def test_the_counter_lives_under_a_key_of_its_action_user_and_window_and_expires(redis_client):
    limiter = build(redis_client, [AT], window_seconds=60)

    await limiter.hit(ACTION, 42)

    key = f"rate:contact_open:42:{int(AT // 60)}"
    assert await redis_client.get(key) == "1"
    assert 0 < await redis_client.ttl(key) <= 60


async def test_rejected_requests_keep_counting(redis_client):
    limiter = build(redis_client, [AT], limit=3)

    for _ in range(5):
        await limiter.hit(ACTION, 42)

    assert await redis_client.get(f"rate:contact_open:42:{int(AT // 60)}") == "5"


async def test_a_new_window_starts_a_new_count(redis_client):
    now = [AT]
    limiter = build(redis_client, now, limit=1)
    assert (await limiter.hit(ACTION, 42)).allowed is True
    assert (await limiter.hit(ACTION, 42)).allowed is False

    now[0] = AT + 45  # the first second of the next minute
    assert (await limiter.hit(ACTION, 42)).allowed is True
    assert (await limiter.hit(ACTION, 42)).allowed is False


async def test_retry_after_is_the_time_left_in_the_window(redis_client):
    now = [AT]
    limiter = build(redis_client, now, window_seconds=60)

    assert (await limiter.hit(ACTION, 42)).retry_after_seconds == 45

    now[0] = AT + 40  # 55 s into the minute
    assert (await limiter.hit(ACTION, 42)).retry_after_seconds == 5

    now[0] = AT + 45  # the first second of the next window: a whole window ahead, never 0
    assert (await limiter.hit(ACTION, 42)).retry_after_seconds == 60


async def test_users_and_actions_are_counted_separately(redis_client):
    limiter = build(redis_client, [AT], limit=1)
    assert (await limiter.hit(ACTION, 1)).allowed is True
    assert (await limiter.hit(ACTION, 1)).allowed is False

    assert (await limiter.hit(ACTION, 2)).allowed is True
    assert (await limiter.hit(RateLimitAction.TAG_SUGGEST, 1)).allowed is True


async def test_thirty_parallel_hits_with_a_limit_of_ten_allow_exactly_ten(redis_client):
    now = [AT]
    # Each request builds its own limiter, as the container does, so nothing but Redis is shared.
    decisions = await asyncio.gather(*(build(redis_client, now, limit=10).hit(ACTION, 42) for _ in range(30)))

    assert sum(decision.allowed for decision in decisions) == 10


async def test_an_unreachable_redis_allows_the_request_and_logs_a_warning(caplog):
    dead = Redis.from_url("redis://localhost:1/0", decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5)
    limiter = build(dead, [AT], limit=1)

    with caplog.at_level(logging.WARNING, logger="app.infrastructure.repositories.redis_rate_limiter"):
        decision = await limiter.hit(ACTION, 42)
    await dead.aclose()

    assert decision.allowed is True
    assert len([record for record in caplog.records if record.levelno == logging.WARNING]) == 1
