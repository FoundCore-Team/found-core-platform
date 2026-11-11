from app.domain.entities import RateLimitDecision
from app.domain.enums import RateLimitAction
from app.domain.interfaces import IRateLimiter


class FakeRateLimiter(IRateLimiter):
    """Counts nothing: it answers from `denied` and remembers every call, so a test can check what was asked."""

    def __init__(self, denied: set[RateLimitAction] | None = None, retry_after_seconds: int = 7) -> None:
        self.denied = denied or set()
        self.retry_after_seconds = retry_after_seconds
        self.hits: list[tuple[RateLimitAction, int]] = []

    async def hit(self, action: RateLimitAction, user_id: int) -> RateLimitDecision:
        self.hits.append((action, user_id))
        return RateLimitDecision(allowed=action not in self.denied, retry_after_seconds=self.retry_after_seconds)
