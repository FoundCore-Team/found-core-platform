"""The notification worker against real Postgres and the broker: the async cores of the tasks use their own engine and
the stub sender (the default outside prod), exactly as in the notification_worker container."""

import asyncio
import base64
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from app.core.composition.container import Container
from app.core.config import settings
from app.domain.enums import NotificationStatus
from app.domain.exceptions import NotificationRejectedError, NotificationUnavailableError
from app.infrastructure.models import ContactOpenModel
from app.infrastructure.queues import CeleryNotificationJobQueue
from app.infrastructure.repositories import SqlAlchemyContactOpenRepository
from app.worker.celery_app import SEND_CONTACT_NOTIFICATION_TASK, celery_app
from app.worker.tasks import (
    run_send_contact_notification,
    run_sweep_stale_notifications,
    send_contact_notification,
)
from tests.fixtures.fake_notification import FakeNotificationSender
from tests.fixtures.feed_data import add_profile, add_user


@pytest.fixture(autouse=True)
def _patient_enqueue(monkeypatch) -> None:
    # The sweep enqueues through the real queue; the production timeout is short on purpose, and a slow first
    # connection on a developer machine must not make these tests flaky.
    monkeypatch.setattr(settings.notification, "enqueue_timeout_seconds", 5.0)


@dataclass
class Pair:
    opener_user_id: int
    owner_user_id: int
    opener_profile_id: int
    owner_profile_id: int


async def make_pair(session, taxonomy_data, base: int = 1, **owner_kwargs) -> Pair:
    t = taxonomy_data
    opener_user = await add_user(session, 100 * base + 1, username=f"opener{base}")
    owner_user = await add_user(session, 100 * base + 2, username=f"owner{base}", **owner_kwargs)
    return Pair(
        opener_user_id=opener_user,
        owner_user_id=owner_user,
        opener_profile_id=await add_profile(session, opener_user, t.tech_category_id, t.engineering_role_id),
        owner_profile_id=await add_profile(session, owner_user, t.edu_category_id, t.study_mate_role_id),
    )


async def add_open(
    session, pair: Pair, *, age: timedelta = timedelta(minutes=1), status: NotificationStatus = NotificationStatus.PENDING
) -> None:
    created_at = datetime.now(timezone.utc) - age
    session.add(
        ContactOpenModel(
            opener_profile_id=pair.opener_profile_id,
            opened_profile_id=pair.owner_profile_id,
            created_at=created_at,
            notification_status=status,
            notification_updated_at=created_at,
        )
    )
    await session.commit()


async def status_of(session, pair: Pair) -> NotificationStatus:
    session.expire_all()
    row = (
        await session.execute(
            select(ContactOpenModel).where(
                ContactOpenModel.opener_profile_id == pair.opener_profile_id,
                ContactOpenModel.opened_profile_id == pair.owner_profile_id,
            )
        )
    ).scalar_one()
    status = row.notification_status
    await session.rollback()
    return status


async def run(pair: Pair, is_last_attempt: bool = False) -> None:
    await run_send_contact_notification(pair.opener_profile_id, pair.owner_profile_id, is_last_attempt)


# ---------- the context query ----------


async def test_the_context_has_both_sides_with_names_titles_and_the_chat_id(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)

    context = await SqlAlchemyContactOpenRepository(session).get_notification_context(
        pair.opener_profile_id, pair.owner_profile_id
    )

    assert context is not None
    n = context.notification
    assert (n.opener_first_name, n.opener_last_name, n.opener_username) == ("User101", None, "opener1")
    assert (n.opener_category_title, n.opener_role_title) == ("Tech & Product", "Engineering")
    assert (n.owner_category_title, n.owner_role_title) == ("Edu & Growth", "Study mate")
    assert (n.owner_telegram_id, n.opener_profile_id) == (102, pair.opener_profile_id)
    assert context.notification_status is NotificationStatus.PENDING
    assert not any([context.opener_is_banned, context.opener_is_deleted, context.owner_is_banned, context.owner_is_deleted])


async def test_the_context_of_a_missing_row_is_none(session, taxonomy_data):
    assert await SqlAlchemyContactOpenRepository(session).get_notification_context(1, 2) is None


async def test_settling_works_once_and_never_overwrites_an_outcome(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    repository = SqlAlchemyContactOpenRepository(session)
    now = datetime.now(timezone.utc)

    first = await repository.settle_notification(
        pair.opener_profile_id, pair.owner_profile_id, NotificationStatus.SENT, now
    )
    second = await repository.settle_notification(
        pair.opener_profile_id, pair.owner_profile_id, NotificationStatus.FAILED, now
    )
    await session.commit()

    assert (first, second) == (True, False)
    assert await status_of(session, pair) is NotificationStatus.SENT


# ---------- the send core ----------


async def test_a_pending_open_is_sent_and_the_stub_logs_the_message(session, taxonomy_data, caplog):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)

    with caplog.at_level(logging.INFO):
        await run(pair)

    assert await status_of(session, pair) is NotificationStatus.SENT
    assert "User101 открыл(а) ваш контакт" in caplog.text
    assert "Анкета: Tech & Product · Engineering" in caplog.text
    assert "Ваша анкета: Edu & Growth · Study mate" in caplog.text
    assert "Написать: @opener1" in caplog.text


async def test_a_second_run_sends_nothing_again(session, taxonomy_data, caplog):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    await run(pair)
    caplog.clear()

    with caplog.at_level(logging.INFO):
        await run(pair)

    assert "[stub notification]" not in caplog.text
    assert await status_of(session, pair) is NotificationStatus.SENT


async def test_a_banned_owner_is_skipped(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data, is_banned=True)
    await add_open(session, pair)

    await run(pair)

    assert await status_of(session, pair) is NotificationStatus.SKIPPED


async def test_an_opener_who_lost_the_username_is_skipped(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    await session.execute(text("UPDATE users SET username = NULL WHERE id = :id"), {"id": pair.opener_user_id})
    await session.commit()

    await run(pair)

    assert await status_of(session, pair) is NotificationStatus.SKIPPED


async def test_a_deleted_opener_is_skipped(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    await session.execute(text("UPDATE users SET deleted_at = now() WHERE id = :id"), {"id": pair.opener_user_id})
    await session.commit()

    await run(pair)

    assert await status_of(session, pair) is NotificationStatus.SKIPPED


async def test_a_paused_owner_profile_still_gets_the_message(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    await session.execute(text("UPDATE profiles SET status = 'paused' WHERE id = :id"), {"id": pair.owner_profile_id})
    await session.commit()

    await run(pair)

    assert await status_of(session, pair) is NotificationStatus.SENT


async def test_an_open_older_than_the_threshold_is_skipped(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair, age=timedelta(seconds=settings.notification.stale_after_seconds + 60))

    await run(pair)

    assert await status_of(session, pair) is NotificationStatus.SKIPPED


async def test_a_deleted_profile_means_no_row_and_the_job_ends_quietly(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    await session.execute(text("DELETE FROM profiles WHERE id = :id"), {"id": pair.owner_profile_id})
    await session.commit()

    await run(pair)  # no exception

    count = (await session.execute(text("SELECT count(*) FROM contact_opens"))).scalar_one()
    assert count == 0


@pytest.mark.parametrize(
    ("error", "is_last_attempt", "expected"),
    [
        (NotificationRejectedError(), False, NotificationStatus.FAILED),
        (NotificationUnavailableError(), True, NotificationStatus.FAILED),
    ],
)
async def test_telegram_failures_end_as_failed(session, taxonomy_data, monkeypatch, error, is_last_attempt, expected):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    monkeypatch.setattr(Container, "notification_sender", lambda self: FakeNotificationSender(error=error))

    await run(pair, is_last_attempt=is_last_attempt)

    assert await status_of(session, pair) is expected


async def test_an_unavailable_telegram_before_the_last_attempt_leaves_the_row_pending(
    session, taxonomy_data, monkeypatch
):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)
    monkeypatch.setattr(
        Container, "notification_sender", lambda self: FakeNotificationSender(error=NotificationUnavailableError())
    )

    with pytest.raises(NotificationUnavailableError):
        await run(pair, is_last_attempt=False)

    assert await status_of(session, pair) is NotificationStatus.PENDING


async def test_the_celery_task_runs_the_whole_path(session, taxonomy_data):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair)

    # The task starts its own event loop, so it cannot run inside the test's one: a thread of its own.
    result = await asyncio.to_thread(send_contact_notification.apply, args=[pair.opener_profile_id, pair.owner_profile_id])

    assert result.successful()
    assert await status_of(session, pair) is NotificationStatus.SENT


# ---------- the sweep ----------


async def test_the_sweep_enqueues_only_stale_pending_rows(session, taxonomy_data, broker_client):
    stale = await make_pair(session, taxonomy_data, base=1)
    fresh = await make_pair(session, taxonomy_data, base=2)
    failed = await make_pair(session, taxonomy_data, base=3)
    sent = await make_pair(session, taxonomy_data, base=4)
    await add_open(session, stale, age=timedelta(minutes=10))
    await add_open(session, fresh, age=timedelta(seconds=10))
    await add_open(session, failed, age=timedelta(hours=1), status=NotificationStatus.FAILED)
    await add_open(session, sent, age=timedelta(hours=1), status=NotificationStatus.SENT)

    claimed = await run_sweep_stale_notifications()

    assert claimed == 1
    messages = await broker_client.lrange(settings.notification.queue_name, 0, -1)
    assert len(messages) == 1
    message = json.loads(messages[0])
    assert message["headers"]["task"] == SEND_CONTACT_NOTIFICATION_TASK
    args, _, _ = json.loads(base64.b64decode(message["body"]))
    assert args == [stale.opener_profile_id, stale.owner_profile_id]
    assert await status_of(session, failed) is NotificationStatus.FAILED


async def test_a_claimed_row_is_not_claimed_again_at_once(session, taxonomy_data, broker_client):
    pair = await make_pair(session, taxonomy_data)
    await add_open(session, pair, age=timedelta(minutes=10))

    assert await run_sweep_stale_notifications() == 1
    assert await run_sweep_stale_notifications() == 0
    assert await broker_client.llen(settings.notification.queue_name) == 1


async def test_the_batch_size_limits_one_sweep(session, taxonomy_data, broker_client, monkeypatch):
    monkeypatch.setattr(settings.notification, "sweep_batch_size", 2)
    for base in (1, 2, 3):
        await add_open(session, await make_pair(session, taxonomy_data, base=base), age=timedelta(minutes=10))

    assert await run_sweep_stale_notifications() == 2
    assert await run_sweep_stale_notifications() == 1
    assert await broker_client.llen(settings.notification.queue_name) == 3


# ---------- the queue ----------


async def test_enqueue_puts_the_task_on_the_notifications_queue(broker_client):
    queue = CeleryNotificationJobQueue(celery_app, settings.notification.queue_name, timeout_seconds=5.0)

    await queue.enqueue(3, 42)

    messages = await broker_client.lrange(settings.notification.queue_name, 0, -1)
    assert len(messages) == 1
    message = json.loads(messages[0])
    assert message["headers"]["task"] == SEND_CONTACT_NOTIFICATION_TASK
    args, kwargs, _ = json.loads(base64.b64decode(message["body"]))
    assert (args, kwargs) == ([3, 42], {})
    # The two queues do not mix: nothing landed where the embedding worker listens.
    assert await broker_client.llen(settings.embedding.queue_name) == 0
