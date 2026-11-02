from abc import ABC, abstractmethod

from app.domain.entities import FeedSession


class IFeedSessionRepository(ABC):
    """Cache of a ranked feed. A miss, an unreadable value and an outage of the store all look like None."""

    @abstractmethod
    async def get(self, session_key: str) -> FeedSession | None: ...

    @abstractmethod
    async def set(self, session_key: str, session: FeedSession) -> None:
        """Never raises: if the store is down the feed is simply rebuilt on the next request."""
