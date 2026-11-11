"""SetAdmin / Ban / Unban / ListAdminActions with in-memory fakes. A lost race is simulated by a repository that
lets "another request" change the user right before the conditional write, which is exactly the window the
conditional UPDATE exists for."""

from datetime import datetime, timedelta, timezone

import pytest

from app.application.use_cases import BanUserUseCase, ListAdminActionsUseCase, SetAdminUseCase, UnbanUserUseCase
from app.domain.entities import NewAdminActionEntity
from app.domain.enums import AdminActionType, AdminTargetType
from app.domain.exceptions import CannotBanAdminError, UserBannedError, UserNotFoundError
from tests.fixtures.fake_admin_action_repository import FakeAdminActionRepository
from tests.fixtures.fake_report_repository import FakeReportRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.fixtures.fake_user_repository import FakeUserRepository
from tests.fixtures.factories import make_user_entity

ADMIN = make_user_entity(id=100, telegram_id=1000, is_admin=True)
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


class ParallelChangeUserRepository(FakeUserRepository):
    """Another request changes the user between the use case's read and its conditional write."""

    def __init__(self, users, parallel_change) -> None:
        super().__init__(users)
        self._parallel_change = parallel_change

    async def set_ban(self, user_id, reason):
        self._parallel_change(self._users[user_id])
        return await super().set_ban(user_id, reason)

    async def clear_ban(self, user_id):
        self._parallel_change(self._users[user_id])
        return await super().clear_ban(user_id)

    async def set_admin(self, user_id, is_admin):
        self._parallel_change(self._users[user_id])
        return await super().set_admin(user_id, is_admin)


def banned_by_someone_else(user) -> None:
    user.is_banned = True
    user.ban_reason = "first"
    user.token_version += 1


def unbanned_by_someone_else(user) -> None:
    user.is_banned = False
    user.ban_reason = None


def made_admin_by_someone_else(user) -> None:
    user.is_admin = True


def deleted_by_someone_else(user) -> None:
    user.deleted_at = NOW


def banned_meanwhile(user) -> None:
    user.is_banned = True
    user.ban_reason = "spam"


def build(use_case_class, users, user_repository=None):
    user_repository = user_repository or FakeUserRepository(users)
    journal = FakeAdminActionRepository()
    unit_of_work = FakeUnitOfWork()
    # Only a ban touches reports.
    extra = {"report_repository": FakeReportRepository()} if use_case_class is BanUserUseCase else {}
    use_case = use_case_class(
        user_repository=user_repository, admin_action_repository=journal, unit_of_work=unit_of_work, **extra
    )
    return use_case, user_repository, journal, unit_of_work


# ---------- SetAdminUseCase ----------


async def test_granting_the_role_changes_the_user_and_writes_a_journal_row_without_an_actor():
    use_case, users, journal, unit_of_work = build(SetAdminUseCase, [make_user_entity(id=1, telegram_id=555)])

    user, changed = await use_case.execute(555, True, "first admin")

    assert (user.is_admin, changed) == (True, True)
    assert (await users.get_by_id(1)).is_admin is True
    [action] = journal.actions
    assert action.actor_user_id is None
    assert action.action is AdminActionType.GRANT_ADMIN
    assert (action.target_type, action.target_id) == (AdminTargetType.USER, 1)
    assert action.reason == "first admin"
    assert action.details == {"telegram_id": 555}
    assert action.created_at.tzinfo is not None
    assert unit_of_work.commits == 1


async def test_revoking_the_role_writes_a_revoke_row():
    use_case, users, journal, unit_of_work = build(
        SetAdminUseCase, [make_user_entity(id=1, telegram_id=555, is_admin=True)]
    )

    user, changed = await use_case.execute(555, False, "left the team")

    assert (user.is_admin, changed) == (False, True)
    assert (await users.get_by_id(1)).is_admin is False
    assert [a.action for a in journal.actions] == [AdminActionType.REVOKE_ADMIN]
    assert unit_of_work.commits == 1


@pytest.mark.parametrize("make_admin, is_admin", [(True, True), (False, False)])
async def test_asking_for_the_state_the_user_already_has_changes_nothing(make_admin, is_admin):
    use_case, _, journal, unit_of_work = build(
        SetAdminUseCase, [make_user_entity(id=1, telegram_id=555, is_admin=is_admin)]
    )

    user, changed = await use_case.execute(555, make_admin, "again")

    assert (user.is_admin, changed) == (is_admin, False)
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_an_unknown_user_is_not_found():
    use_case, _, journal, unit_of_work = build(SetAdminUseCase, [])

    with pytest.raises(UserNotFoundError):
        await use_case.execute(555, True, "who")

    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_deleted_user_is_not_found():
    use_case, _, journal, unit_of_work = build(
        SetAdminUseCase, [make_user_entity(id=1, telegram_id=555, deleted_at=NOW)]
    )

    with pytest.raises(UserNotFoundError):
        await use_case.execute(555, True, "gone")

    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_banned_user_cannot_become_an_admin_and_the_ban_reason_travels_with_the_error():
    use_case, users, journal, unit_of_work = build(
        SetAdminUseCase, [make_user_entity(id=1, telegram_id=555, is_banned=True, ban_reason="spam")]
    )

    with pytest.raises(UserBannedError) as error:
        await use_case.execute(555, True, "no")

    assert error.value.ban_reason == "spam"
    assert (await users.get_by_id(1)).is_admin is False
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_parallel_grant_makes_this_one_a_no_op():
    users = ParallelChangeUserRepository([make_user_entity(id=1, telegram_id=555)], made_admin_by_someone_else)
    use_case, _, journal, unit_of_work = build(SetAdminUseCase, [], user_repository=users)

    user, changed = await use_case.execute(555, True, "second run")

    assert (user.is_admin, changed) == (True, False)
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_ban_between_the_read_and_the_write_stops_the_grant():
    users = ParallelChangeUserRepository([make_user_entity(id=1, telegram_id=555)], banned_meanwhile)
    use_case, _, journal, unit_of_work = build(SetAdminUseCase, [], user_repository=users)

    with pytest.raises(UserBannedError) as error:
        await use_case.execute(555, True, "too late")

    assert error.value.ban_reason == "spam"
    assert (await users.get_by_id(1)).is_admin is False
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_journal_failure_leaves_the_grant_uncommitted():
    use_case, _, journal, unit_of_work = build(SetAdminUseCase, [make_user_entity(id=1, telegram_id=555)])
    journal.fail_on_add = RuntimeError("journal down")

    with pytest.raises(RuntimeError):
        await use_case.execute(555, True, "reason")

    assert unit_of_work.commits == 0


# ---------- BanUserUseCase ----------


async def test_a_ban_sets_the_flag_and_reason_raises_token_version_and_writes_one_journal_row():
    use_case, users, journal, unit_of_work = build(
        BanUserUseCase, [ADMIN, make_user_entity(id=1, telegram_id=555, token_version=3)]
    )

    banned = await use_case.execute(ADMIN, 1, "spam in bio")

    assert (banned.is_banned, banned.ban_reason, banned.token_version) == (True, "spam in bio", 4)
    stored = await users.get_by_id(1)
    assert (stored.is_banned, stored.ban_reason, stored.token_version) == (True, "spam in bio", 4)
    [action] = journal.actions
    assert action.actor_user_id == ADMIN.id
    assert action.action is AdminActionType.BAN_USER
    assert (action.target_type, action.target_id) == (AdminTargetType.USER, 1)
    assert action.reason == "spam in bio"
    assert unit_of_work.commits == 1


async def test_an_unknown_user_cannot_be_banned():
    use_case, _, journal, unit_of_work = build(BanUserUseCase, [ADMIN])

    with pytest.raises(UserNotFoundError):
        await use_case.execute(ADMIN, 999, "who")

    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_deleted_user_cannot_be_banned():
    use_case, users, journal, unit_of_work = build(
        BanUserUseCase, [ADMIN, make_user_entity(id=1, telegram_id=555, deleted_at=NOW)]
    )

    with pytest.raises(UserNotFoundError):
        await use_case.execute(ADMIN, 1, "gone")

    assert (await users.get_by_id(1)).is_banned is False
    assert unit_of_work.commits == 0


async def test_another_admin_cannot_be_banned():
    other_admin = make_user_entity(id=2, telegram_id=556, is_admin=True)
    use_case, users, journal, unit_of_work = build(BanUserUseCase, [ADMIN, other_admin])

    with pytest.raises(CannotBanAdminError):
        await use_case.execute(ADMIN, 2, "no")

    stored = await users.get_by_id(2)
    assert (stored.is_banned, stored.token_version) == (False, 0)
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_an_admin_cannot_ban_themselves():
    use_case, users, journal, unit_of_work = build(BanUserUseCase, [ADMIN])

    with pytest.raises(CannotBanAdminError):
        await use_case.execute(ADMIN, ADMIN.id, "oops")

    assert (await users.get_by_id(ADMIN.id)).is_banned is False
    assert unit_of_work.commits == 0


async def test_banning_a_banned_user_changes_nothing_and_keeps_the_first_reason():
    use_case, users, journal, unit_of_work = build(
        BanUserUseCase,
        [ADMIN, make_user_entity(id=1, telegram_id=555, is_banned=True, ban_reason="first", token_version=4)],
    )

    result = await use_case.execute(ADMIN, 1, "second reason")

    assert (result.is_banned, result.ban_reason, result.token_version) == (True, "first", 4)
    assert (await users.get_by_id(1)).ban_reason == "first"
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_parallel_ban_wins_and_this_request_answers_with_its_result_without_a_second_row():
    users = ParallelChangeUserRepository(
        [ADMIN, make_user_entity(id=1, telegram_id=555, token_version=0)], banned_by_someone_else
    )
    use_case, _, journal, unit_of_work = build(BanUserUseCase, [], user_repository=users)

    result = await use_case.execute(ADMIN, 1, "second")

    assert (result.is_banned, result.ban_reason, result.token_version) == (True, "first", 1)
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_parallel_grant_of_the_admin_role_beats_the_ban():
    users = ParallelChangeUserRepository([ADMIN, make_user_entity(id=1, telegram_id=555)], made_admin_by_someone_else)
    use_case, _, journal, unit_of_work = build(BanUserUseCase, [], user_repository=users)

    with pytest.raises(CannotBanAdminError):
        await use_case.execute(ADMIN, 1, "late")

    assert (await users.get_by_id(1)).is_banned is False
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_parallel_deletion_makes_the_user_not_found():
    users = ParallelChangeUserRepository([ADMIN, make_user_entity(id=1, telegram_id=555)], deleted_by_someone_else)
    use_case, _, journal, unit_of_work = build(BanUserUseCase, [], user_repository=users)

    with pytest.raises(UserNotFoundError):
        await use_case.execute(ADMIN, 1, "late")

    assert unit_of_work.commits == 0


async def test_a_ban_takes_the_user_row_lock_before_it_reads_the_user():
    use_case, users, _, _ = build(BanUserUseCase, [ADMIN, make_user_entity(id=1, telegram_id=555)])

    await use_case.execute(ADMIN, 1, "spam")

    assert users.locked_user_ids == [1]


async def test_an_unban_takes_the_user_row_lock_before_it_reads_the_user():
    use_case, users, _, _ = build(
        UnbanUserUseCase, [ADMIN, make_user_entity(id=1, telegram_id=555, is_banned=True, ban_reason="spam")]
    )

    await use_case.execute(ADMIN, 1, "appeal accepted")

    assert users.locked_user_ids == [1]


async def test_a_journal_failure_leaves_the_ban_uncommitted():
    use_case, _, journal, unit_of_work = build(BanUserUseCase, [ADMIN, make_user_entity(id=1, telegram_id=555)])
    journal.fail_on_add = RuntimeError("journal down")

    with pytest.raises(RuntimeError):
        await use_case.execute(ADMIN, 1, "reason")

    assert unit_of_work.commits == 0


# ---------- UnbanUserUseCase ----------


async def test_an_unban_clears_the_flag_and_reason_keeps_token_version_and_writes_a_journal_row():
    use_case, users, journal, unit_of_work = build(
        UnbanUserUseCase,
        [ADMIN, make_user_entity(id=1, telegram_id=555, is_banned=True, ban_reason="spam", token_version=4)],
    )

    result = await use_case.execute(ADMIN, 1, "appeal accepted")

    assert (result.is_banned, result.ban_reason, result.token_version) == (False, None, 4)
    stored = await users.get_by_id(1)
    assert (stored.is_banned, stored.ban_reason, stored.token_version) == (False, None, 4)
    [action] = journal.actions
    assert action.actor_user_id == ADMIN.id
    assert action.action is AdminActionType.UNBAN_USER
    assert (action.target_type, action.target_id, action.reason) == (AdminTargetType.USER, 1, "appeal accepted")
    assert unit_of_work.commits == 1


async def test_unbanning_a_user_who_is_not_banned_changes_nothing():
    use_case, _, journal, unit_of_work = build(UnbanUserUseCase, [ADMIN, make_user_entity(id=1, telegram_id=555)])

    result = await use_case.execute(ADMIN, 1, "again")

    assert result.is_banned is False
    assert journal.actions == []
    assert unit_of_work.commits == 0


@pytest.mark.parametrize("user_id, deleted_at", [(999, None), (1, NOW)])
async def test_an_unknown_or_deleted_user_cannot_be_unbanned(user_id, deleted_at):
    use_case, _, journal, unit_of_work = build(
        UnbanUserUseCase,
        [ADMIN, make_user_entity(id=1, telegram_id=555, is_banned=True, deleted_at=deleted_at)],
    )

    with pytest.raises(UserNotFoundError):
        await use_case.execute(ADMIN, user_id, "who")

    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_parallel_unban_makes_this_one_a_no_op_without_a_second_row():
    users = ParallelChangeUserRepository(
        [ADMIN, make_user_entity(id=1, telegram_id=555, is_banned=True, ban_reason="spam")], unbanned_by_someone_else
    )
    use_case, _, journal, unit_of_work = build(UnbanUserUseCase, [], user_repository=users)

    result = await use_case.execute(ADMIN, 1, "second")

    assert result.is_banned is False
    assert journal.actions == []
    assert unit_of_work.commits == 0


async def test_a_journal_failure_leaves_the_unban_uncommitted():
    use_case, _, journal, unit_of_work = build(
        UnbanUserUseCase, [ADMIN, make_user_entity(id=1, telegram_id=555, is_banned=True, ban_reason="spam")]
    )
    journal.fail_on_add = RuntimeError("journal down")

    with pytest.raises(RuntimeError):
        await use_case.execute(ADMIN, 1, "reason")

    assert unit_of_work.commits == 0


# ---------- ListAdminActionsUseCase ----------


def new_action(target_type=AdminTargetType.USER, target_id=1, minutes=0) -> NewAdminActionEntity:
    return NewAdminActionEntity(
        actor_user_id=100,
        action=AdminActionType.BAN_USER,
        target_type=target_type,
        target_id=target_id,
        reason="r",
        details={},
        created_at=NOW + timedelta(minutes=minutes),
    )


async def test_the_journal_is_listed_newest_first_with_the_total_and_pages():
    journal = FakeAdminActionRepository()
    for minutes in (1, 3, 2):
        await journal.add(new_action(minutes=minutes))
    use_case = ListAdminActionsUseCase(admin_action_repository=journal)

    first_page, total = await use_case.execute(limit=2, offset=0, target_type=None, target_id=None)
    second_page, _ = await use_case.execute(limit=2, offset=2, target_type=None, target_id=None)

    assert total == 3
    assert [a.created_at for a in first_page] == [NOW + timedelta(minutes=3), NOW + timedelta(minutes=2)]
    assert [a.created_at for a in second_page] == [NOW + timedelta(minutes=1)]


async def test_the_journal_can_be_filtered_by_target_and_the_total_follows_the_filter():
    journal = FakeAdminActionRepository()
    await journal.add(new_action(AdminTargetType.USER, 1))
    await journal.add(new_action(AdminTargetType.USER, 2))
    await journal.add(new_action(AdminTargetType.PROFILE, 1))
    use_case = ListAdminActionsUseCase(admin_action_repository=journal)

    actions, total = await use_case.execute(limit=10, offset=0, target_type=AdminTargetType.USER, target_id=1)

    assert total == 1
    assert [(a.target_type, a.target_id) for a in actions] == [(AdminTargetType.USER, 1)]


async def test_rows_created_in_the_same_instant_keep_a_stable_order_by_id():
    journal = FakeAdminActionRepository()
    for _ in range(3):
        await journal.add(new_action(minutes=0))
    use_case = ListAdminActionsUseCase(admin_action_repository=journal)

    actions, _ = await use_case.execute(limit=10, offset=0, target_type=None, target_id=None)

    assert [a.id for a in actions] == [3, 2, 1]
