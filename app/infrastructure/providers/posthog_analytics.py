import logging
from collections.abc import Mapping

from posthog import Posthog

from app.domain.enums import AnalyticsEvent
from app.domain.interfaces import IAnalyticsTracker
from app.domain.interfaces.analytics import AnalyticsValue

logger = logging.getLogger(__name__)


class PostHogAnalyticsTracker(IAnalyticsTracker):
    """Sends events through the PostHog SDK. `capture` only puts the event into the SDK's in-memory queue (a full queue
    drops it without blocking); a background thread of the SDK sends the batches.

    The client is created by `analytics_helper`, once per process, and passed in, so the tests can give a client that
    sends nothing.
    """

    def __init__(self, client: Posthog, env: str, flush_timeout_seconds: float) -> None:
        self._client = client
        self._env = env
        self._flush_timeout_seconds = flush_timeout_seconds

    def track(
        self,
        event: AnalyticsEvent,
        user_id: int,
        properties: Mapping[str, AnalyticsValue],
        *,
        person_properties: Mapping[str, AnalyticsValue] | None = None,
    ) -> None:
        # `env` goes last so no caller can override it: dev and prod ids overlap (1, 2, 3...), and `env` is how
        # events sent to the wrong project are told apart.
        event_properties: dict[str, object] = {**properties, "env": self._env}
        if person_properties:
            # PostHog applies `$set` of any event to the person it belongs to.
            event_properties["$set"] = dict(person_properties)
        try:
            self._client.capture(
                event.value,
                distinct_id=str(user_id),
                properties=event_properties,
                disable_geoip=True,
            )
        except Exception:
            # The SDK already swallows its own errors unless `debug` is on; this is the port's promise either way.
            logger.warning("Analytics event %s was not queued", event.value, exc_info=True)

    def flush(self) -> None:
        # `flush` has its own time limit and keeps the client usable; `shutdown` would wait for the queue without one
        # (with retries while PostHog is down). The SDK's daemon thread is stopped by its own `atexit` hook.
        try:
            self._client.flush(timeout_seconds=self._flush_timeout_seconds)
        except Exception:
            logger.warning("Analytics flush failed", exc_info=True)
