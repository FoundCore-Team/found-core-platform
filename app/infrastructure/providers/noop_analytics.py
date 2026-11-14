from collections.abc import Mapping

from app.domain.enums import AnalyticsEvent
from app.domain.interfaces import IAnalyticsTracker
from app.domain.interfaces.analytics import AnalyticsValue


class NoopAnalyticsTracker(IAnalyticsTracker):
    """Drops every event. The `disabled` provider (allowed in prod until the terms of use and the legal review are
    done), and the tracker of the Celery workers and the scripts, which send no events."""

    def track(
        self,
        event: AnalyticsEvent,
        user_id: int,
        properties: Mapping[str, AnalyticsValue],
        *,
        person_properties: Mapping[str, AnalyticsValue] | None = None,
    ) -> None:
        pass

    def flush(self) -> None:
        pass
