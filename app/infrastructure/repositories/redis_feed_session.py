import json
import logging
from datetime import datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.entities import FeedRankedItem, FeedReason, FeedSession
from app.domain.enums import FeedReasonCode, FeedTier
from app.domain.interfaces import IFeedSessionRepository

logger = logging.getLogger(__name__)

_KEY_TEMPLATE = "feed:session:{session_key}"


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("unexpected shape of a feed session")


def _int_list(value: object) -> list[int]:
    _require(type(value) is list and all(type(item) is int for item in value))
    return value  # type: ignore[return-value]


def _str_list(value: object) -> list[str]:
    _require(type(value) is list and all(type(item) is str for item in value))
    return value  # type: ignore[return-value]


def _parse_reason(raw: object) -> FeedReason:
    _require(type(raw) is dict)
    return FeedReason(
        code=FeedReasonCode(raw["code"]),  # type: ignore[index]
        tag_ids=_int_list(raw["tag_ids"]),  # type: ignore[index]
        matched=_str_list(raw["matched"]),  # type: ignore[index]
        missed=_str_list(raw["missed"]),  # type: ignore[index]
    )


def _parse_item(raw: object) -> FeedRankedItem:
    _require(type(raw) is list and len(raw) == 4)
    profile_id, tier, score, reasons = raw  # type: ignore[misc]
    _require(type(profile_id) is int and type(score) in (int, float) and type(reasons) is list)
    return FeedRankedItem(
        profile_id=profile_id,
        tier=FeedTier(tier),
        score=float(score),
        reasons=[_parse_reason(reason) for reason in reasons],
    )


def _parse_session(data: object) -> FeedSession:
    _require(type(data) is dict)
    _require(type(data["viewer_profile_id"]) is int and type(data["params_hash"]) is str)  # type: ignore[index]
    _require(type(data["items"]) is list)  # type: ignore[index]
    return FeedSession(
        viewer_profile_id=data["viewer_profile_id"],  # type: ignore[index]
        params_hash=data["params_hash"],  # type: ignore[index]
        items=[_parse_item(item) for item in data["items"]],  # type: ignore[index]
        built_at=datetime.fromisoformat(data["built_at"]),  # type: ignore[index]
    )


def _serialize_session(session: FeedSession) -> str:
    return json.dumps(
        {
            "viewer_profile_id": session.viewer_profile_id,
            "params_hash": session.params_hash,
            "built_at": session.built_at.isoformat(),
            "items": [
                [
                    item.profile_id,
                    item.tier.value,
                    item.score,
                    [
                        {
                            "code": reason.code.value,
                            "tag_ids": reason.tag_ids,
                            "matched": reason.matched,
                            "missed": reason.missed,
                        }
                        for reason in item.reasons
                    ],
                ]
                for item in session.items
            ],
        }
    )


class RedisFeedSessionRepository(IFeedSessionRepository):
    def __init__(self, redis_client: Redis, ttl_seconds: int) -> None:
        self._redis = redis_client
        self._ttl_seconds = ttl_seconds
        # One instance lives for one request: after the first Redis failure the rest of the request skips Redis
        # instead of waiting for another timeout.
        self._unavailable = False

    async def get(self, session_key: str) -> FeedSession | None:
        if self._unavailable:
            return None
        key = _KEY_TEMPLATE.format(session_key=session_key)
        try:
            raw = await self._redis.get(key)
        except RedisError:
            self._unavailable = True
            logger.warning("Redis unavailable, feed session read skipped for key %s", key, exc_info=True)
            return None
        if not raw:
            return None
        try:
            return _parse_session(json.loads(raw))
        except (TypeError, KeyError, ValueError, RecursionError):
            # A corrupt or outdated value is a miss; the use case rebuilds the feed and overwrites it.
            logger.warning("Discarding unreadable feed session for key %s", key, exc_info=True)
            return None

    async def set(self, session_key: str, session: FeedSession) -> None:
        if self._unavailable:
            return
        key = _KEY_TEMPLATE.format(session_key=session_key)
        try:
            await self._redis.set(key, _serialize_session(session), ex=self._ttl_seconds)
        except RedisError:
            self._unavailable = True
            logger.warning("Redis unavailable, feed session write skipped for key %s", key, exc_info=True)
