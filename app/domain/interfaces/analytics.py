from abc import ABC, abstractmethod
from collections.abc import Mapping

from app.domain.enums import AnalyticsEvent

# Only flat primitive values: a list becomes boolean flags or one comma-separated string. Never names, usernames,
# telegram ids, profile texts or tag titles; internal ids (users, profiles, tags) are allowed.
AnalyticsValue = str | int | float | bool | None


class IAnalyticsTracker(ABC):
    """Product analytics. Synchronous and never raises: an event is only queued in memory, and a lost event must
    never fail or slow down the request that produced it."""

    @abstractmethod
    def track(
        self,
        event: AnalyticsEvent,
        user_id: int,
        properties: Mapping[str, AnalyticsValue],
        *,
        person_properties: Mapping[str, AnalyticsValue] | None = None,
    ) -> None:
        """`user_id` is `users.id`, the person the event belongs to. `person_properties` are set on that person
        (only the login flow sends them, for `is_admin`)."""

    @abstractmethod
    def flush(self) -> None:
        """Tries to deliver what is queued within a bounded time; called once when the process stops."""
