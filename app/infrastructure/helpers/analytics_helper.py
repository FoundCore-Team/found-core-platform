import asyncio

from posthog import Posthog

from app.core.config import AnalyticsConfig, settings
from app.domain.interfaces import IAnalyticsTracker
from app.infrastructure.providers import NoopAnalyticsTracker, PostHogAnalyticsTracker, StubAnalyticsTracker


class AnalyticsHelper:
    """One tracker per process. `Container` is built for every request, and a PostHog client with a background thread
    per request would never flush. The tracker is created on first use, not at import: a process that never sends an
    event (a Celery worker, a script, the tests) gets no client and no thread. The first use is in `get_container`,
    which runs on the event loop thread, so two requests cannot create two clients."""

    def __init__(self, config: AnalyticsConfig, env: str) -> None:
        self._config = config
        self._env = env
        self._tracker: IAnalyticsTracker | None = None

    def _build_tracker(self) -> IAnalyticsTracker:
        config = self._config
        if config.provider == "disabled":
            return NoopAnalyticsTracker()
        if config.provider == "stub":
            return StubAnalyticsTracker()
        # AnalyticsConfig refuses `posthog` without a key, so the key is set here.
        client = Posthog(
            config.project_api_key.get_secret_value(),
            host=config.host,
            max_queue_size=config.max_queue_size,
            flush_at=config.flush_at,
            flush_interval=config.flush_interval_seconds,
            timeout=config.request_timeout_seconds,
        )
        return PostHogAnalyticsTracker(client=client, env=self._env, flush_timeout_seconds=config.flush_timeout_seconds)

    @property
    def tracker(self) -> IAnalyticsTracker:
        if self._tracker is None:
            self._tracker = self._build_tracker()
        return self._tracker

    async def dispose(self) -> None:
        # A tracker that was never created has nothing to deliver; creating one just to flush it would start a thread.
        if self._tracker is not None:
            # `flush` blocks up to `flush_timeout_seconds`; keep the event loop free meanwhile.
            await asyncio.to_thread(self._tracker.flush)


analytics_helper = AnalyticsHelper(config=settings.analytics, env=settings.run.env)
