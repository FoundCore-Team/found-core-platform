from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.entities import ContactOpenEntity, ContactWindowUsage, FeedViewer


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
