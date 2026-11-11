from datetime import datetime, timedelta, timezone

import pytest

from app.application.use_cases import SendContactNotificationUseCase, SweepStaleNotificationsUseCase
from app.domain.entities import ContactNotificationContext
from app.domain.enums import NotificationStatus
from app.domain.exceptions import NotificationRejectedError, NotificationUnavailableError
from tests.fixtures.fake_contact_open_repository import FakeContactOpenRepository
from tests.fixtures.fake_notification import FakeNotificationQueue, FakeNotificationSender
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.unit.test_contact_notification_text import make_notification

OPENER, OPENED = 1, 201
STALE_AFTER = timedelta(hours=24)


def make_context(**overrides) -> ContactNotificationContext:
    defaults = dict(
        notification_status=NotificationStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        opener_is_banned=False,
        opener_is_deleted=False,
        opener_profile_is_hidden=False,
        owner_is_banned=False,
        owner_is_deleted=False,
        notification=make_notification(),
    )
    defaults.update(overrides)
    return ContactNotificationContext(**defaults)


class Env:
    def __init__(self, *, sender_error: Exception | None = None, created_ago: timedelta = timedelta(minutes=1), **facts):
        created_at = datetime.now(timezone.utc) - created_ago
        self.contacts = FakeContactOpenRepository(usernames={}, owner_of_opener={})
        self.contacts.seed_open(OPENER, OPENED, created_at)
        self.contacts.set_context(OPENER, OPENED, make_context(created_at=created_at, **facts))
        self.sender = FakeNotificationSender(error=sender_error)
        self.uow = FakeUnitOfWork()
        self.use_case = SendContactNotificationUseCase(self.contacts, self.sender, self.uow, STALE_AFTER)

    async def send(self, is_last_attempt: bool = False) -> None:
        await self.use_case.execute(OPENER, OPENED, is_last_attempt)

    @property
    def status(self) -> NotificationStatus:
        return self.contacts.status_of(OPENER, OPENED)


# ---------- sent ----------


async def test_a_pending_notification_is_sent_with_the_built_text_and_marked_sent():
    env = Env()

    await env.send()

    [(notification, text)] = env.sender.sent
    assert notification.owner_telegram_id == 555 and notification.opener_profile_id == 7
    assert text.startswith("Анна Иванова открыл(а) ваш контакт")
    assert (env.status, env.uow.commits) == (NotificationStatus.SENT, 1)


async def test_a_paused_owner_still_gets_the_message():
    # The context carries no pause flag on purpose: only a ban or a deleted account stops the message.
    env = Env()

    await env.send()

    assert env.status is NotificationStatus.SENT


# ---------- skipped ----------


@pytest.mark.parametrize(
    "facts",
    [
        dict(opener_is_banned=True),
        dict(opener_is_deleted=True),
        dict(opener_profile_is_hidden=True),
        dict(owner_is_banned=True),
        dict(owner_is_deleted=True),
        dict(notification=make_notification(opener_username="")),
    ],
    ids=[
        "opener banned",
        "opener deleted",
        "opener profile hidden",
        "owner banned",
        "owner deleted",
        "opener lost the username",
    ],
)
async def test_a_changed_situation_skips_the_notification_without_calling_telegram(facts):
    env = Env(**facts)

    await env.send()

    assert env.sender.attempts == 0
    assert (env.status, env.uow.commits) == (NotificationStatus.SKIPPED, 1)


async def test_a_notification_older_than_the_threshold_is_skipped():
    env = Env(created_ago=STALE_AFTER + timedelta(minutes=1))

    await env.send()

    assert env.sender.attempts == 0
    assert env.status is NotificationStatus.SKIPPED


async def test_a_notification_just_inside_the_threshold_is_still_sent():
    env = Env(created_ago=STALE_AFTER - timedelta(minutes=1))

    await env.send()

    assert env.status is NotificationStatus.SENT


# ---------- failed and retried ----------


async def test_a_rejection_is_final_even_on_the_first_attempt():
    # 403 (blocked the bot, never pressed /start), 400 and the other 4xx: a retry cannot help.
    env = Env(sender_error=NotificationRejectedError())

    await env.send(is_last_attempt=False)

    assert (env.status, env.sender.attempts, env.uow.commits) == (NotificationStatus.FAILED, 1, 1)


async def test_an_unavailable_channel_is_raised_for_a_retry_and_nothing_is_settled():
    env = Env(sender_error=NotificationUnavailableError(retry_after=30))

    with pytest.raises(NotificationUnavailableError) as error:
        await env.send(is_last_attempt=False)

    assert error.value.retry_after == 30
    assert (env.status, env.uow.commits) == (NotificationStatus.PENDING, 0)


async def test_the_last_attempt_turns_unavailable_into_a_final_failed():
    env = Env(sender_error=NotificationUnavailableError())

    await env.send(is_last_attempt=True)

    assert (env.status, env.uow.commits) == (NotificationStatus.FAILED, 1)


# ---------- nothing to do ----------


@pytest.mark.parametrize("status", [NotificationStatus.SENT, NotificationStatus.FAILED, NotificationStatus.SKIPPED])
async def test_an_already_settled_notification_is_left_alone(status):
    # A duplicate job (a retry racing the sweep) must not send a second message or overwrite the outcome.
    env = Env()
    env.contacts.seed_open(OPENER, OPENED, datetime.now(timezone.utc), status=status)

    await env.send()

    assert (env.sender.attempts, env.uow.commits, env.status) == (0, 0, status)


async def test_a_deleted_profile_means_no_row_and_nothing_happens():
    env = Env()

    await env.use_case.execute(OPENER, 999, is_last_attempt=False)

    assert (env.sender.attempts, env.uow.commits) == (0, 0)


# ---------- the sweep ----------


class SweepEnv:
    def __init__(self) -> None:
        self.contacts = FakeContactOpenRepository(usernames={}, owner_of_opener={})
        self.queue = FakeNotificationQueue()
        self.uow = FakeUnitOfWork()
        self.use_case = SweepStaleNotificationsUseCase(
            self.contacts, self.queue, self.uow, pending_after=timedelta(minutes=2), batch_size=2
        )

    def seed(self, opener: int, opened: int, age: timedelta, status=NotificationStatus.PENDING) -> None:
        self.contacts.seed_open(opener, opened, datetime.now(timezone.utc) - age, status=status)


async def test_the_sweep_enqueues_only_stale_pending_rows():
    env = SweepEnv()
    env.seed(1, 11, timedelta(minutes=5))
    env.seed(2, 12, timedelta(seconds=30))  # fresh: its own job is probably still on the way
    env.seed(3, 13, timedelta(hours=1), status=NotificationStatus.FAILED)  # failed is final
    env.seed(4, 14, timedelta(hours=1), status=NotificationStatus.SENT)

    claimed = await env.use_case.execute()

    assert claimed == 1
    assert env.queue.enqueued == [(1, 11)]


async def test_the_claim_is_committed_before_anything_is_enqueued():
    env = SweepEnv()
    env.seed(1, 11, timedelta(minutes=5))
    commits_at_enqueue: list[int] = []
    original = env.queue.enqueue

    async def enqueue(opener: int, opened: int) -> None:
        commits_at_enqueue.append(env.uow.commits)
        await original(opener, opened)

    env.queue.enqueue = enqueue  # type: ignore[method-assign]

    await env.use_case.execute()

    assert commits_at_enqueue == [1]


async def test_a_claimed_row_is_not_claimed_again_at_once():
    env = SweepEnv()
    env.seed(1, 11, timedelta(minutes=5))

    assert await env.use_case.execute() == 1
    assert await env.use_case.execute() == 0
    assert env.queue.enqueued == [(1, 11)]


async def test_the_batch_size_limits_one_sweep_and_the_oldest_go_first():
    env = SweepEnv()
    env.seed(1, 11, timedelta(minutes=10))
    env.seed(2, 12, timedelta(minutes=30))
    env.seed(3, 13, timedelta(minutes=20))

    assert await env.use_case.execute() == 2
    assert sorted(env.queue.enqueued) == [(2, 12), (3, 13)]
    assert await env.use_case.execute() == 1  # the third waits for the next sweep
    assert (1, 11) in env.queue.enqueued


async def test_an_empty_sweep_still_commits_and_enqueues_nothing():
    env = SweepEnv()

    assert await env.use_case.execute() == 0
    assert env.queue.enqueued == [] and env.uow.commits == 1
