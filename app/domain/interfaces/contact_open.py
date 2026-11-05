from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.entities import ContactNotificationContext, ContactOpenEntity, ContactWindowUsage, FeedViewer
from app.domain.enums import NotificationStatus


class IContactOpenRepository(ABC):
    @abstractmethod
    async def get_visible_contact(self, viewer: FeedViewer, profile_id: int) -> str | None:
        """The `username` of the profile's owner if the viewer may see that profile (the feed's visibility rule),
        otherwise None. One query, so the check and the read cannot disagree."""

    @abstractmethod
    async def exists(self, opener_profile_id: int, opened_profile_id: int) -> bool: ...

    @abstractmethod
    async def get_window_usage(self, user_id: int, since: datetime) -> ContactWindowUsage:
        """First-time opens made since `since` by any profile of the user."""

    @abstractmethod
    async def add(self, opener_profile_id: int, opened_profile_id: int, now: datetime) -> ContactOpenEntity | None:
        """Records the pair as `pending`. None if the pair already exists (a parallel double click)."""

    @abstractmethod
    async def get_notification_context(
        self, opener_profile_id: int, opened_profile_id: int
    ) -> ContactNotificationContext | None:
        """The open with the current state of both people, or None if the row is gone (a profile was deleted)."""

    @abstractmethod
    async def settle_notification(
        self, opener_profile_id: int, opened_profile_id: int, status: NotificationStatus, now: datetime
    ) -> bool:
        """Moves a `pending` row to `sent`, `failed` or `skipped`. False if the row is not pending any more, so a
        notification that was settled by a parallel job is never overwritten."""

    @abstractmethod
    async def claim_stale_pending(self, pending_before: datetime, now: datetime, limit: int) -> list[tuple[int, int]]:
        """Pairs still `pending` since before `pending_before`, oldest first. Their `notification_updated_at` is set to
        `now` in the same statement, so a concurrent sweep does not claim them again at once."""
