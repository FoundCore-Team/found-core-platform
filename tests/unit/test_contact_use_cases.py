from datetime import datetime, timedelta, timezone

import pytest

from app.application.services.feed_viewer_resolver import FeedViewerResolver
from app.application.use_cases import OpenContactUseCase
from app.domain.entities import ProfileEmbeddingEntity
from app.domain.enums import EmbeddingStatus, NotificationStatus, ProfileStatus
from app.domain.exceptions import ContactLimitReachedError, ContactProfileNotFoundError, NoActiveProfileError
from tests.fixtures.factories import make_profile_entity, make_user_entity
from tests.fixtures.fake_contact_open_repository import FakeContactOpenRepository
from tests.fixtures.fake_profile_embedding_repository import FakeProfileEmbeddingRepository
from tests.fixtures.fake_profile_repository import FakeProfileRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.fixtures.fake_user_repository import FakeUserRepository

MODEL = "stub-v1"
STAMP = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
WINDOW = timedelta(hours=24)
LIMIT = 3
VIEWER_PROFILE = 1
# Profiles 201.. belong to other people and are visible; 99 is a second profile of the viewer (not in `usernames`).


class Env:
    def __init__(self, *, limit: int = LIMIT, active: bool = True, profile_status=ProfileStatus.ACTIVE) -> None:
        self.user = make_user_entity(id=1, active_profile_id=VIEWER_PROFILE if active else None)
        profile = make_profile_entity(
            id=VIEWER_PROFILE, user_id=1, category_id=1, role_id=5, status=profile_status, updated_at=STAMP
        )
        self.profiles = FakeProfileRepository(profiles=[profile])
        embeddings = FakeProfileEmbeddingRepository(
            [ProfileEmbeddingEntity(VIEWER_PROFILE, "hash", MODEL, EmbeddingStatus.READY, STAMP, STAMP)]
        )
        self.users = FakeUserRepository([self.user])
        self.contacts = FakeContactOpenRepository(
            usernames={201: "anna", 202: "boris", 203: "clara", 204: "dmitri", 205: "elena"},
            owner_of_opener={VIEWER_PROFILE: 1, 99: 1},
        )
        self.uow = FakeUnitOfWork()
        self.use_case = OpenContactUseCase(
            viewer_resolver=FeedViewerResolver(self.profiles, embeddings, MODEL),
            contact_open_repository=self.contacts,
            user_repository=self.users,
            unit_of_work=self.uow,
            daily_limit=limit,
            window=WINDOW,
        )

    async def open(self, profile_id: int):
        return await self.use_case.execute(self.user, profile_id)

    def seed_opens(self, count: int, hours_ago: float = 1) -> None:
        """`count` earlier first-time opens, the oldest `hours_ago` hours back, one minute apart."""
        base = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
        for n in range(count):
            self.contacts.seed_open(VIEWER_PROFILE, 300 + n, base + timedelta(minutes=n))


# ---------- the first open ----------


async def test_the_first_open_returns_the_username_records_the_pair_and_commits_once():
    env = Env()

    contact = await env.open(201)

    assert (contact.username, contact.is_new_open, contact.remaining_today) == ("anna", True, LIMIT - 1)
    [row] = env.contacts.opens
    assert (row.opener_profile_id, row.opened_profile_id) == (VIEWER_PROFILE, 201)
    assert row.notification_status is NotificationStatus.PENDING
    assert env.uow.commits == 1


async def test_remaining_today_counts_down_over_different_profiles():
    env = Env()

    remaining = [(await env.open(pid)).remaining_today for pid in (201, 202, 203)]

    assert remaining == [2, 1, 0]


async def test_the_open_takes_the_row_lock_of_the_user_before_counting():
    env = Env()

    await env.open(201)

    assert env.users.locked_user_ids == [1]


# ---------- a repeat is free ----------


async def test_a_repeat_returns_the_username_without_a_record_a_commit_or_a_lock():
    env = Env()
    await env.open(201)
    commits, adds = env.uow.commits, env.contacts.add_calls
    env.users.locked_user_ids.clear()

    contact = await env.open(201)

    assert (contact.username, contact.is_new_open) == ("anna", False)
    assert contact.remaining_today == LIMIT - 1  # unchanged
    assert (env.uow.commits, env.contacts.add_calls, env.users.locked_user_ids) == (commits, adds, [])


async def test_a_repeat_works_when_the_limit_is_exhausted():
    env = Env()
    for pid in (201, 202, 203):
        await env.open(pid)

    contact = await env.open(202)

    assert (contact.is_new_open, contact.remaining_today) == (False, 0)


async def test_a_pair_that_a_parallel_request_committed_while_waiting_for_the_lock_is_a_repeat_not_a_429():
    # The limit is used up by this very pair's first open: the second request must not see "limit reached".
    env = Env(limit=1)
    env.contacts.pair_appears_after_exists_checks = 1  # the first check says "no", the one under the lock says "yes"

    contact = await env.open(201)

    assert (contact.is_new_open, contact.remaining_today) == (False, 0)
    assert env.contacts.add_calls == 0


async def test_a_lost_insert_race_is_answered_as_a_repeat():
    env = Env()
    original_exists = env.contacts.exists
    calls = 0

    async def exists_false_twice(opener, opened):
        nonlocal calls
        calls += 1
        if calls <= 2:
            return False
        return await original_exists(opener, opened)

    env.contacts.exists = exists_false_twice
    env.contacts.seed_open(VIEWER_PROFILE, 201, datetime.now(timezone.utc))  # the insert conflicts

    contact = await env.open(201)

    assert contact.is_new_open is False
    assert env.uow.commits == 0


# ---------- the limit ----------


async def test_the_open_over_the_limit_raises_with_the_reset_time_of_the_oldest_open():
    env = Env()
    env.seed_opens(LIMIT, hours_ago=5)
    oldest = min(o.created_at for o in env.contacts.opens)

    with pytest.raises(ContactLimitReachedError) as error:
        await env.open(201)

    assert error.value.limit == LIMIT
    assert error.value.resets_at == oldest + WINDOW
    assert env.contacts.add_calls == 0 and env.uow.commits == 0


async def test_opens_older_than_the_window_do_not_count():
    env = Env()
    env.seed_opens(LIMIT, hours_ago=30)

    contact = await env.open(201)

    assert (contact.is_new_open, contact.remaining_today) == (True, LIMIT - 1)


async def test_the_limit_is_per_person_so_a_second_profile_does_not_get_a_second_one():
    env = Env()
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    for n in range(LIMIT):  # opens made by the other profile (99) of the same user
        env.contacts.seed_open(99, 300 + n, base + timedelta(minutes=n))

    with pytest.raises(ContactLimitReachedError):
        await env.open(201)


# ---------- who can be opened ----------


@pytest.mark.parametrize("profile_id", [VIEWER_PROFILE, 99, 12345])
async def test_an_own_or_unknown_profile_is_404(profile_id):
    env = Env()

    with pytest.raises(ContactProfileNotFoundError):
        await env.open(profile_id)

    assert env.uow.commits == 0 and env.contacts.add_calls == 0


async def test_a_profile_that_became_invisible_is_404_even_if_it_was_opened_before():
    env = Env()
    await env.open(201)
    env.contacts.hide(201)

    with pytest.raises(ContactProfileNotFoundError):
        await env.open(201)


async def test_404_is_decided_before_the_limit():
    env = Env()
    env.seed_opens(LIMIT)

    with pytest.raises(ContactProfileNotFoundError):
        await env.open(12345)


@pytest.mark.parametrize(
    "env_kwargs",
    [
        dict(active=False),
        dict(profile_status=ProfileStatus.PAUSED),
        dict(profile_status=ProfileStatus.HIDDEN_BY_ADMIN),
    ],
)
async def test_without_an_active_profile_nothing_is_opened(env_kwargs):
    env = Env(**env_kwargs)

    with pytest.raises(NoActiveProfileError):
        await env.open(201)

    assert env.contacts.opens == [] and env.uow.commits == 0
