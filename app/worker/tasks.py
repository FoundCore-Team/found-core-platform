import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.composition.container import Container
from app.core.config import settings
from app.domain.exceptions import (
    EmbeddingProviderUnavailableError,
    NotificationUnavailableError,
    ProfileChangedDuringEmbeddingError,
)
from app.infrastructure.providers import NoopAnalyticsTracker
from app.worker.celery_app import (
    EMBED_PROFILE_TASK,
    SEND_CONTACT_NOTIFICATION_TASK,
    SWEEP_STALE_EMBEDDINGS_TASK,
    SWEEP_STALE_NOTIFICATIONS_TASK,
    celery_app,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def _run_with_container(work: Callable[[Container], Awaitable[T]]) -> T:
    # Every task runs in its own event loop (`asyncio.run`), and pooled connections are bound to the loop that
    # opened them, so the shared engine of the web app cannot be reused: a fresh engine without a pool is used.
    engine = create_async_engine(str(settings.db.url), poolclass=NullPool)
    redis_client = Redis.from_url(str(settings.redis.url), socket_timeout=settings.redis.socket_timeout)
    try:
        session_factory = async_sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        async with session_factory() as session:
            # Workers send no analytics yet: their own client would have to start after Celery's fork and be flushed
            # when the worker stops (a follow-up of stage 9).
            container = Container(session=session, redis_client=redis_client, analytics_tracker=NoopAnalyticsTracker())
            return await work(container)
    finally:
        await redis_client.aclose()
        await engine.dispose()


async def run_embed_profile(profile_id: int, is_last_attempt: bool) -> None:
    await _run_with_container(
        lambda container: container.embed_profile_use_case().execute(profile_id, is_last_attempt)
    )


async def run_sweep_stale_embeddings() -> int:
    return await _run_with_container(lambda container: container.sweep_stale_embeddings_use_case().execute())


async def run_send_contact_notification(opener_profile_id: int, opened_profile_id: int, is_last_attempt: bool) -> None:
    await _run_with_container(
        lambda container: container.send_contact_notification_use_case().execute(
            opener_profile_id, opened_profile_id, is_last_attempt
        )
    )


async def run_sweep_stale_notifications() -> int:
    return await _run_with_container(lambda container: container.sweep_stale_notifications_use_case().execute())


@celery_app.task(name=EMBED_PROFILE_TASK, bind=True, max_retries=settings.embedding.task_max_retries)
def embed_profile(self, profile_id: int) -> None:
    # On the last attempt the use case records the failure itself instead of asking for another retry.
    is_last_attempt = self.request.retries >= self.max_retries
    try:
        asyncio.run(run_embed_profile(profile_id, is_last_attempt))
    except (EmbeddingProviderUnavailableError, ProfileChangedDuringEmbeddingError) as error:
        # Exponential backoff. If the retries run out here (an edit raced the last attempt), the error is
        # re-raised, the row stays pending and the sweep picks it up.
        raise self.retry(exc=error, countdown=settings.embedding.retry_base_delay_seconds * 2**self.request.retries)


@celery_app.task(name=SWEEP_STALE_EMBEDDINGS_TASK)
def sweep_stale_embeddings() -> None:
    claimed = asyncio.run(run_sweep_stale_embeddings())
    if claimed:
        logger.info("Sweep re-enqueued %s embedding job(s)", claimed)


@celery_app.task(
    name=SEND_CONTACT_NOTIFICATION_TASK, bind=True, max_retries=settings.notification.task_max_retries
)
def send_contact_notification(self, opener_profile_id: int, opened_profile_id: int) -> None:
    # On the last attempt the use case records the failure itself instead of asking for another retry.
    is_last_attempt = self.request.retries >= self.max_retries
    try:
        asyncio.run(run_send_contact_notification(opener_profile_id, opened_profile_id, is_last_attempt))
    except NotificationUnavailableError as error:
        # Exponential backoff, or what Telegram asked for if that is longer.
        countdown = settings.notification.retry_base_delay_seconds * 2**self.request.retries
        if error.retry_after is not None:
            countdown = max(countdown, error.retry_after)
        raise self.retry(exc=error, countdown=countdown)


@celery_app.task(name=SWEEP_STALE_NOTIFICATIONS_TASK)
def sweep_stale_notifications() -> None:
    claimed = asyncio.run(run_sweep_stale_notifications())
    if claimed:
        logger.info("Sweep re-enqueued %s notification job(s)", claimed)
