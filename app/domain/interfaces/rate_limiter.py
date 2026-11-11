from abc import ABC, abstractmethod

from app.domain.entities import RateLimitDecision
from app.domain.enums import RateLimitAction


class IRateLimiter(ABC):
    """Per-user request counter. It only decides; turning a refusal into an error is the caller's job."""

    @abstractmethod
    async def hit(self, action: RateLimitAction, user_id: int) -> RateLimitDecision:
        """Counts one request of the user and says whether it may go on. Never raises: if the counter store is down
        the request is allowed, because a lost limiter must not take the API down with it."""
