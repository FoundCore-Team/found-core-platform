class NoActiveProfileError(Exception):
    message = "You need an active profile to use the feed."

    def __init__(self) -> None:
        super().__init__(self.message)


class UsernameRequiredError(Exception):
    message = "Set a Telegram username to use the feed."

    def __init__(self) -> None:
        super().__init__(self.message)


class InvalidFeedFilterError(Exception):
    message = "The feed filter is invalid."

    def __init__(self, reason: str) -> None:
        super().__init__(self.message)
        self.reason = reason


class InvalidFeedCursorError(Exception):
    message = "The feed cursor is invalid."

    def __init__(self) -> None:
        super().__init__(self.message)


class FeedProfileNotFoundError(Exception):
    # Also raised for an own, paused, banned or username-less profile, so none of those can be told apart.
    message = "Profile not found."

    def __init__(self) -> None:
        super().__init__(self.message)
