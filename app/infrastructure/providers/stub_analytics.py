import logging
from collections.abc import Mapping

from app.domain.enums import AnalyticsEvent
from app.domain.interfaces import IAnalyticsTracker
from app.domain.interfaces.analytics import AnalyticsValue

logger = logging.getLogger(__name__)


class StubAnalyticsTracker(IAnalyticsTracker):
    """Writes every event to the log instead of sending it, so events can be checked without a PostHog key.
    The default outside prod."""

    def track(
        self,
        event: AnalyticsEvent,
        user_id: int,
        properties: Mapping[str, AnalyticsValue],
        *,
        person_properties: Mapping[str, AnalyticsValue] | None = None,
    ) -> None:
        logger.info(
            "[stub analytics] %s user_id=%s properties=%s person_properties=%s",
            event.value,
            user_id,
            dict(properties),
            dict(person_properties) if person_properties is not None else None,
        )

    def flush(self) -> None:
        pass
