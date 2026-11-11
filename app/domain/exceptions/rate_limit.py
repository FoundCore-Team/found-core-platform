from app.domain.enums import RateLimitAction


class RateLimitExceededError(Exception):
    message = "Too many requests."

    def __init__(self, action: RateLimitAction, retry_after_seconds: int) -> None:
        super().__init__(self.message)
        self.action = action
        self.retry_after_seconds = retry_after_seconds
