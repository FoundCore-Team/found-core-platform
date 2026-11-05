from datetime import datetime


class ContactProfileNotFoundError(Exception):
    # Also raised for an own, paused, banned or username-less profile, so none of those can be told apart.
    message = "Profile not found."

    def __init__(self) -> None:
        super().__init__(self.message)


class ContactLimitReachedError(Exception):
    message = "The daily limit of opened contacts is reached."

    def __init__(self, limit: int, resets_at: datetime) -> None:
        super().__init__(self.message)
        self.limit = limit
        # When the oldest open in the window leaves it, i.e. when the next contact can be opened.
        self.resets_at = resets_at
