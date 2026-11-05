from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import NotificationStatus


@dataclass
class ContactOpenEntity:
    """One opened pair: the opener's active profile -> the opened profile."""

    opener_profile_id: int
    opened_profile_id: int
    created_at: datetime
    notification_status: NotificationStatus
    notification_updated_at: datetime


@dataclass
class ContactWindowUsage:
    """How much of the daily limit a user has used: first-time opens inside the sliding window."""

    count: int
    # The oldest open inside the window; None when `count` is 0. The limit frees up 24 h after it.
    oldest_opened_at: datetime | None


@dataclass
class OpenedContact:
    """The result of `OpenContactUseCase`."""

    username: str
    # True for the first open of the pair (a record, a notification, one less in the limit); False for a repeat.
    is_new_open: bool
    remaining_today: int
