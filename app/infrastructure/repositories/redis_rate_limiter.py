import logging
import time
from collections.abc import Callable, Mapping

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.entities import RateLimitDecision
from app.domain.enums import RateLimitAction
from app.domain.interfaces import IRateLimiter

logger = logging.getLogger(__name__)

_KEY_TEMPLATE = "rate:{action}:{user_id}:{window_index}"


class RedisRateLimiter(IRateLimiter):
    """Fixed-window counter per user and action. The window number is part of the key, so a new window is a new key
    and an old one just expires; nothing is ever reset by hand."""

    def __init__(
        self,
        redis_client: Redis,
        rules: Mapping[RateLimitAction, tuple[int, int]],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._redis = redis_client
        # action -> (limit, window_seconds), built by the container from `RateLimitConfig`.
        self._rules = rules
        self._clock = clock

    async def hit(self, action: RateLimitAction, user_id: int) -> RateLimitDecision:
        limit, window_seconds = self._rules[action]
        now = self._clock()
        retry_after_seconds = max(1, window_seconds - int(now % window_seconds))
        key = _KEY_TEMPLATE.format(action=action.value, user_id=user_id, window_index=int(now // window_seconds))
        try:
            # INCR is atomic and returns the new value, so parallel requests never read the same number. EXPIRE NX
            # sets the lifetime only if the key has none: a key that lost its expiry gets one on the next request.
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window_seconds, nx=True)
                count, _ = await pipeline.execute()
        except RedisError:
            logger.warning("Redis unavailable, rate limit %s skipped for user %s", action.value, user_id, exc_info=True)
            return RateLimitDecision(allowed=True, retry_after_seconds=retry_after_seconds)

        return RateLimitDecision(allowed=count <= limit, retry_after_seconds=retry_after_seconds)
