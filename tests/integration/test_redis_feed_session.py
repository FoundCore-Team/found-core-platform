import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.domain.entities import FeedRankedItem, FeedReason, FeedSession
from app.domain.enums import FeedReasonCode, FeedTier
from app.infrastructure.repositories import RedisFeedSessionRepository

TTL = 120
KEY = "k" * 64

SESSION = FeedSession(
    viewer_profile_id=7,
    params_hash="a" * 64,
    items=[
        FeedRankedItem(
            101,
            FeedTier.EXACT_MATCH,
            0.688,
            [
                FeedReason(FeedReasonCode.MATCHED_FILTERS, matched=["role"], missed=["tags"]),
                FeedReason(FeedReasonCode.SHARED_TAGS, tag_ids=[3, 4]),
                FeedReason(FeedReasonCode.SAME_ROLE),
            ],
        ),
        FeedRankedItem(102, FeedTier.RECOMMENDED_BY_BIO, 0.25, []),
    ],
    built_at=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc),
)


def stored_form() -> dict:
    return {
        "viewer_profile_id": 7,
        "params_hash": "a" * 64,
        "built_at": "2026-10-28T12:00:00+00:00",
        "items": [[101, "EXACT_MATCH", 0.5, [{"code": "same_role", "tag_ids": [], "matched": [], "missed": []}]]],
    }


@pytest.fixture
def repository(redis_client: Redis) -> RedisFeedSessionRepository:
    return RedisFeedSessionRepository(redis_client=redis_client, ttl_seconds=TTL)


async def test_a_session_round_trips_unchanged(repository):
    await repository.set(KEY, SESSION)

    assert await repository.get(KEY) == SESSION


async def test_the_key_has_the_documented_name_and_a_ttl(repository, redis_client):
    await repository.set(KEY, SESSION)

    assert await redis_client.exists(f"feed:session:{KEY}") == 1
    assert 0 < await redis_client.ttl(f"feed:session:{KEY}") <= TTL


async def test_a_missing_session_is_a_miss(repository):
    assert await repository.get("nothing-here") is None


@pytest.mark.parametrize(
    "stored",
    [
        "not json",
        "[]",
        "null",
        "{}",
        json.dumps({**stored_form(), "items": "nope"}),
        json.dumps({**stored_form(), "items": [[101, "EXACT_MATCH", 0.5]]}),
        json.dumps({**stored_form(), "items": [["101", "EXACT_MATCH", 0.5, []]]}),
        json.dumps({**stored_form(), "items": [[101, "EXACT_MATCH", "high", []]]}),
        json.dumps({**stored_form(), "items": [[101, "NOT_A_TIER", 0.5, []]]}),
        json.dumps({**stored_form(), "items": [[101, "EXACT_MATCH", 0.5, [{"code": "who_knows"}]]]}),
        json.dumps({**stored_form(), "items": [[101, "EXACT_MATCH", 0.5, [{"code": "same_role"}]]]}),
        json.dumps({**stored_form(), "viewer_profile_id": "7"}),
        json.dumps({**stored_form(), "viewer_profile_id": True}),
        json.dumps({**stored_form(), "params_hash": 5}),
        json.dumps({**stored_form(), "built_at": "yesterday"}),
        "[" * 100_000,
    ],
)
async def test_an_unreadable_value_is_a_miss_not_an_error(repository, redis_client, stored):
    await redis_client.set(f"feed:session:{KEY}", stored)

    assert await repository.get(KEY) is None


async def test_a_corrupt_value_is_overwritten_by_the_next_write(repository, redis_client):
    await redis_client.set(f"feed:session:{KEY}", "garbage")
    assert await repository.get(KEY) is None

    await repository.set(KEY, SESSION)

    assert await repository.get(KEY) == SESSION


async def test_after_the_first_redis_error_the_rest_of_the_request_skips_the_network():
    client = MagicMock()
    client.get = AsyncMock(side_effect=RedisConnectionError("down"))
    client.set = AsyncMock()
    repository = RedisFeedSessionRepository(redis_client=client, ttl_seconds=TTL)

    assert await repository.get(KEY) is None
    assert await repository.get(KEY) is None
    await repository.set(KEY, SESSION)

    assert client.get.await_count == 1
    assert client.set.await_count == 0


async def test_a_failed_write_does_not_raise_and_stops_further_redis_calls():
    client = MagicMock()
    client.set = AsyncMock(side_effect=RedisConnectionError("down"))
    client.get = AsyncMock()
    repository = RedisFeedSessionRepository(redis_client=client, ttl_seconds=TTL)

    await repository.set(KEY, SESSION)
    await repository.set(KEY, SESSION)
    assert await repository.get(KEY) is None

    assert client.set.await_count == 1
    assert client.get.await_count == 0


async def test_a_new_instance_tries_redis_again():
    client = MagicMock()
    client.get = AsyncMock(side_effect=RedisConnectionError("down"))

    await RedisFeedSessionRepository(redis_client=client, ttl_seconds=TTL).get(KEY)
    await RedisFeedSessionRepository(redis_client=client, ttl_seconds=TTL).get(KEY)

    assert client.get.await_count == 2


async def test_an_unreachable_redis_reads_as_a_miss_and_writes_do_not_raise():
    dead = Redis.from_url("redis://localhost:1/0", decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5)
    repository = RedisFeedSessionRepository(redis_client=dead, ttl_seconds=TTL)
    try:
        assert await repository.get(KEY) is None
        await repository.set(KEY, SESSION)
    finally:
        await dead.aclose()


# ---------- the viewer's last session pointer ----------

SESSION_KEY = "0123456789abcdef" * 4


async def test_the_last_session_pointer_round_trips_per_viewer(repository):
    await repository.set_last_session_key(7, SESSION_KEY)
    await repository.set_last_session_key(8, "f" * 64)

    assert await repository.get_last_session_key(7) == SESSION_KEY
    assert await repository.get_last_session_key(8) == "f" * 64
    assert await repository.get_last_session_key(9) is None


async def test_the_pointer_is_overwritten_by_the_next_page(repository):
    await repository.set_last_session_key(7, SESSION_KEY)
    await repository.set_last_session_key(7, "f" * 64)

    assert await repository.get_last_session_key(7) == "f" * 64


async def test_the_pointer_has_the_documented_name_and_the_session_ttl(repository, redis_client):
    await repository.set_last_session_key(7, SESSION_KEY)

    assert await redis_client.get("feed:last:7") == SESSION_KEY
    assert 0 < await redis_client.ttl("feed:last:7") <= TTL


@pytest.mark.parametrize("stored", ["", "garbage", "A" * 64, "0" * 63, "0" * 65, SESSION_KEY + "\n"])
async def test_an_unreadable_pointer_is_a_miss(repository, redis_client, stored):
    await redis_client.set("feed:last:7", stored)

    assert await repository.get_last_session_key(7) is None


async def test_a_pointer_failure_does_not_raise_and_stops_further_redis_calls():
    client = MagicMock()
    client.set = AsyncMock(side_effect=RedisConnectionError("down"))
    client.get = AsyncMock()
    repository = RedisFeedSessionRepository(redis_client=client, ttl_seconds=TTL)

    await repository.set_last_session_key(7, SESSION_KEY)
    assert await repository.get_last_session_key(7) is None
    assert await repository.get(KEY) is None

    assert client.set.await_count == 1
    assert client.get.await_count == 0
