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
class ContactNotification:
    """Everything the text and the button of a "someone opened your contact" message are made of."""

    # Where it goes: `users.telegram_id` of the profile's owner is also their private chat id with the bot.
    owner_telegram_id: int
    opener_profile_id: int
    opener_first_name: str
    opener_last_name: str | None
    opener_username: str
    # "Category · Role" of the opener's profile and of the owner's profile.
    opener_category_title: str
    opener_role_title: str
    owner_category_title: str
    owner_role_title: str


@dataclass
class ContactNotificationContext:
    """The open plus the current state of both people, read at send time (not when the open happened).

    The worker may run minutes after the open (retries, sweep), so it decides on fresh facts: see
    `SendContactNotificationUseCase`.
    """

    notification_status: NotificationStatus
    created_at: datetime
    opener_is_banned: bool
    opener_is_deleted: bool
    # The opener's profile was hidden by an admin. Only the opener's: a hidden profile of the owner does not stop the
    # news that somebody opened their contact.
    opener_profile_is_hidden: bool
    owner_is_banned: bool
    owner_is_deleted: bool
    notification: ContactNotification


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
