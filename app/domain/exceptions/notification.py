class NotificationUnavailableError(Exception):
    # Temporary failure (timeout, network, 429, 5xx, an answer that is not JSON): the job is retried.
    message = "Notification channel is temporarily unavailable."

    def __init__(self, retry_after: float | None = None) -> None:
        super().__init__(self.message)
        # Seconds Telegram asked to wait (429); the retry delay is never shorter than this.
        self.retry_after = retry_after


class NotificationRejectedError(Exception):
    # Permanent failure (the user blocked the bot, never pressed /start, chat not found, bad request): a retry cannot
    # help, the notification is marked failed.
    message = "Notification channel rejected the message."

    def __init__(self) -> None:
        super().__init__(self.message)
