from app.domain.enums import RateLimitAction
from app.domain.exceptions import RateLimitExceededError
from app.domain.interfaces import IRateLimiter


class RateLimitGuard:
    """Turns the limiter's decision into an error. Shared by the router dependency and the use cases, so the rule
    "a refusal is a RateLimitExceededError" exists once."""

    def __init__(self, rate_limiter: IRateLimiter) -> None:
        self._rate_limiter = rate_limiter

    async def check(self, action: RateLimitAction, user_id: int) -> None:
        decision = await self._rate_limiter.hit(action, user_id)
        if not decision.allowed:
            raise RateLimitExceededError(action, decision.retry_after_seconds)
