from dataclasses import dataclass


@dataclass
class RateLimitDecision:
    """The answer of the rate limiter to one request."""

    allowed: bool
    # Seconds until the current window ends and the counter starts again; at least 1.
    retry_after_seconds: int
