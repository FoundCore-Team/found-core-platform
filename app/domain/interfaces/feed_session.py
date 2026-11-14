from abc import ABC, abstractmethod

from app.domain.entities import FeedSession


class IFeedSessionRepository(ABC):
    """Cache of a ranked feed. A miss, an unreadable value and an outage of the store all look like None."""

    @abstractmethod
    async def get(self, session_key: str) -> FeedSession | None: ...

    @abstractmethod
    async def set(self, session_key: str, session: FeedSession) -> None:
        """Never raises: if the store is down the feed is simply rebuilt on the next request."""

    @abstractmethod
    async def get_last_session_key(self, viewer_profile_id: int) -> str | None:
        """The key of the session the viewer was last served a page from, or None."""

    @abstractmethod
    async def set_last_session_key(self, viewer_profile_id: int, session_key: str) -> None:
        """Never raises. Written by the server on every page, so a client cannot point it at another session; a
        profile view reads it to know the tier and the score the opened card had in the feed."""
