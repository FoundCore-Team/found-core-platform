import asyncio
import logging

from celery import Celery

from app.domain.interfaces import INotificationJobQueue
from app.worker.celery_app import SEND_CONTACT_NOTIFICATION_TASK

logger = logging.getLogger(__name__)


class CeleryNotificationJobQueue(INotificationJobQueue):
    def __init__(self, celery_app: Celery, queue_name: str, timeout_seconds: float) -> None:
        self._celery_app = celery_app
        self._queue_name = queue_name
        self._timeout_seconds = timeout_seconds

    async def enqueue(self, opener_profile_id: int, opened_profile_id: int) -> None:
        # Same approach as the embeddings queue: `send_task` is synchronous, so it runs in a thread under a timeout,
        # without retries, and a failure is only logged. The row stays `pending` and the sweep finds it.
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._celery_app.send_task,
                    SEND_CONTACT_NOTIFICATION_TASK,
                    args=[opener_profile_id, opened_profile_id],
                    queue=self._queue_name,
                    retry=False,
                ),
                timeout=self._timeout_seconds,
            )
        except Exception:
            logger.warning(
                "Could not enqueue the contact notification for profiles %s -> %s",
                opener_profile_id,
                opened_profile_id,
                exc_info=True,
            )
