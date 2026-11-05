from enum import StrEnum


class NotificationStatus(StrEnum):
    """Where the bot notification of a contact open stands (written in 7a, driven by the worker from 7b)."""

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"
