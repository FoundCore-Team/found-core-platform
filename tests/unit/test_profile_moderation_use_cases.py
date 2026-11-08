"""HideProfileUseCase / UnhideProfileUseCase with in-memory fakes. A lost race is simulated by a repository that lets
"another request" act right before the conditional write."""

from datetime import datetime, timedelta, timezone

import pytest

from app.application.use_cases import HideProfileUseCase, UnhideProfileUseCase
from app.domain.enums import AdminActionType, AdminTargetType, ProfileStatus, ReportStatus
from app.domain.exceptions import ProfileNotFoundError
from tests.fixtures.factories import make_profile_entity, make_report_entity, make_user_entity
from tests.fixtures.fake_admin_action_repository import FakeAdminActionRepository
from tests.fixtures.fake_profile_repository import FakeProfileRepository
from tests.fixtures.fake_report_repository import FakeReportRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.fixtures.fake_user_repository import FakeUserRepository

BASE_TIME = datetime(2026, 11, 8, 9, 0, tzinfo=timezone.utc)
ADMIN = make_user_entity(id=100, telegram_id=1000, is_admin=True)
OWNER_ID = 1


def profile(profile_id: int, *, status=ProfileStatus.ACTIVE, user_id: int = OWNER_ID, age_hours: int = 0):
    # A higher id and a smaller age mean a newer profile: the pointer moves to the newest remaining active one.
    return make_profile_entity(
        id=profile_id, user_id=user_id, status=status, created_at=BASE_TIME + timedelta(hours=profile_id - age_hours)
    )


class HidesElsewhereFirst(FakeProfileRepository):
    """Another admin hides the profile between our read and our conditional write: the write then changes nothing."""

    async def hide(self, profile_id):
        await super().hide(profile_id)
        return False


class UnhidesElsewhereFirst(FakeProfileRepository):
    async def unhide(self, profile_id):
        await super().unhide(profile_id)
        return False


class DeletedBeforeTheWrite(FakeProfileRepository):
    """The owner deletes the profile between our read and our conditional write."""

    async def hide(self, profile_id):
        await self.delete(profile_id)
        return await super().hide(profile_id)


class Env:
    def __init__(self, profiles, *, active_profile_id=None, reports=None, profile_repository=None) -> None:
        self.profiles = profile_repository or FakeProfileRepository(profiles)
        if profile_repository is not None:
            for p in profiles:
                profile_repository._profiles[p.id] = p
        self.users = FakeUserRepository(
            [ADMIN, make_user_entity(id=OWNER_ID, telegram_id=555, active_profile_id=active_profile_id)]
        )
        self.reports = FakeReportRepository(
            reports, profile_owners={p.id: p.user_id for p in profiles}
        )
        self.journal = FakeAdminActionRepository()
        self.uow = FakeUnitOfWork()

    def hide_use_case(self) -> HideProfileUseCase:
        return HideProfileUseCase(self.profiles, self.users, self.reports, self.journal, self.uow)

    def unhide_use_case(self) -> UnhideProfileUseCase:
        return UnhideProfileUseCase(self.profiles, self.journal, self.uow)

    async def status(self, profile_id: int) -> ProfileStatus:
        return (await self.profiles.get_by_id(profile_id)).status

    async def pointer(self) -> int | None:
        return (await self.users.get_by_id(OWNER_ID)).active_profile_id


# ---------- HideProfileUseCase ----------


async def test_hiding_an_active_profile_hides_it_and_writes_one_journal_row_in_one_commit():
    env = Env([profile(1)], active_profile_id=None)

    result = await env.hide_use_case().execute(ADMIN, 1, "spam in bio")

    assert result.status is ProfileStatus.HIDDEN_BY_ADMIN
    assert await env.status(1) is ProfileStatus.HIDDEN_BY_ADMIN
    [action] = env.journal.actions
    assert action.actor_user_id == ADMIN.id
    assert action.action is AdminActionType.HIDE_PROFILE
    assert (action.target_type, action.target_id) == (AdminTargetType.PROFILE, 1)
    assert action.reason == "spam in bio"
    assert action.details == {"owner_user_id": OWNER_ID, "previous_status": "active", "resolved_reports": 0}
    assert env.uow.commits == 1


async def test_hiding_a_paused_profile_records_the_previous_status():
    env = Env([profile(1, status=ProfileStatus.PAUSED)])

    await env.hide_use_case().execute(ADMIN, 1, "reason")

    assert env.journal.actions[0].details["previous_status"] == "paused"
    assert await env.status(1) is ProfileStatus.HIDDEN_BY_ADMIN


async def test_hiding_the_active_profile_moves_the_pointer_to_the_newest_other_active_profile():
    env = Env([profile(1), profile(2), profile(3, status=ProfileStatus.PAUSED)], active_profile_id=1)

    await env.hide_use_case().execute(ADMIN, 1, "reason")

    assert await env.pointer() == 2  # 3 is newer but paused


async def test_hiding_the_only_active_profile_clears_the_pointer():
    env = Env([profile(1), profile(2, status=ProfileStatus.PAUSED)], active_profile_id=1)

    await env.hide_use_case().execute(ADMIN, 1, "reason")

    assert await env.pointer() is None


async def test_hiding_a_profile_that_is_not_the_active_one_leaves_the_pointer_alone():
    env = Env([profile(1), profile(2)], active_profile_id=1)

    await env.hide_use_case().execute(ADMIN, 2, "reason")

    assert await env.pointer() == 1


async def test_hiding_a_paused_profile_never_touches_the_pointer():
    env = Env([profile(1), profile(2, status=ProfileStatus.PAUSED)], active_profile_id=1)

    await env.hide_use_case().execute(ADMIN, 2, "reason")

    assert await env.pointer() == 1


async def test_hiding_resolves_the_open_reports_on_that_profile_only():
    reports = [
        make_report_entity(id=1, profile_id=1, reporter_user_id=50),
        make_report_entity(id=2, profile_id=1, reporter_user_id=51),
        make_report_entity(id=3, profile_id=1, reporter_user_id=52, status=ReportStatus.DISMISSED),
        make_report_entity(id=4, profile_id=2, reporter_user_id=50),
    ]
    env = Env([profile(1), profile(2)], reports=reports)

    await env.hide_use_case().execute(ADMIN, 1, "reason")

    by_id = {r.id: r for r in env.reports.reports}
    assert (by_id[1].status, by_id[2].status) == (ReportStatus.RESOLVED, ReportStatus.RESOLVED)
    assert by_id[3].status is ReportStatus.DISMISSED  # already closed, untouched
    assert by_id[4].status is ReportStatus.OPEN  # another profile
    [action] = env.journal.actions
    assert action.details["resolved_reports"] == 2
    assert by_id[1].resolved_at == action.created_at  # one instant for the report and the journal row


async def test_hiding_an_already_hidden_profile_writes_nothing_and_does_not_commit():
    env = Env([profile(1, status=ProfileStatus.HIDDEN_BY_ADMIN)], reports=[make_report_entity(profile_id=1)])

    result = await env.hide_use_case().execute(ADMIN, 1, "again")

    assert result.status is ProfileStatus.HIDDEN_BY_ADMIN
    assert env.journal.actions == []
    assert env.uow.commits == 0
    assert env.reports.reports[0].status is ReportStatus.OPEN  # not this call's business


async def test_an_unknown_profile_is_not_found():
    env = Env([profile(1)])

    with pytest.raises(ProfileNotFoundError):
        await env.hide_use_case().execute(ADMIN, 999, "reason")

    assert env.uow.commits == 0


async def test_losing_the_race_to_another_admin_answers_for_the_current_state_without_a_second_journal_row():
    env = Env([profile(1)], profile_repository=HidesElsewhereFirst())

    result = await env.hide_use_case().execute(ADMIN, 1, "reason")

    assert result.status is ProfileStatus.HIDDEN_BY_ADMIN
    assert env.journal.actions == []
    assert env.uow.commits == 0


async def test_a_profile_deleted_between_the_read_and_the_write_is_not_found():
    env = Env([profile(1)], profile_repository=DeletedBeforeTheWrite())

    with pytest.raises(ProfileNotFoundError):
        await env.hide_use_case().execute(ADMIN, 1, "reason")

    assert env.uow.commits == 0


async def test_a_failing_journal_means_no_commit():
    env = Env([profile(1)])
    env.journal.fail_on_add = RuntimeError("the journal is down")

    with pytest.raises(RuntimeError):
        await env.hide_use_case().execute(ADMIN, 1, "reason")

    assert env.uow.commits == 0


# ---------- UnhideProfileUseCase ----------


async def test_unhiding_returns_the_profile_as_paused_never_as_active_and_leaves_the_pointer():
    env = Env([profile(1, status=ProfileStatus.HIDDEN_BY_ADMIN), profile(2)], active_profile_id=2)

    result = await env.unhide_use_case().execute(ADMIN, 1, "appeal accepted")

    assert result.status is ProfileStatus.PAUSED
    assert await env.status(1) is ProfileStatus.PAUSED
    assert await env.pointer() == 2
    [action] = env.journal.actions
    assert action.action is AdminActionType.UNHIDE_PROFILE
    assert (action.actor_user_id, action.target_type, action.target_id) == (ADMIN.id, AdminTargetType.PROFILE, 1)
    assert action.reason == "appeal accepted"
    assert action.details == {"owner_user_id": OWNER_ID}
    assert env.uow.commits == 1


@pytest.mark.parametrize("status", [ProfileStatus.ACTIVE, ProfileStatus.PAUSED])
async def test_unhiding_a_profile_that_is_not_hidden_writes_nothing(status):
    env = Env([profile(1, status=status)])

    result = await env.unhide_use_case().execute(ADMIN, 1, "reason")

    assert result.status is status
    assert env.journal.actions == []
    assert env.uow.commits == 0


async def test_unhiding_does_not_reopen_closed_reports():
    closed = make_report_entity(id=1, profile_id=1, status=ReportStatus.RESOLVED)
    env = Env([profile(1, status=ProfileStatus.HIDDEN_BY_ADMIN)], reports=[closed])

    await env.unhide_use_case().execute(ADMIN, 1, "reason")

    assert env.reports.reports[0].status is ReportStatus.RESOLVED


async def test_unhiding_an_unknown_profile_is_not_found():
    env = Env([profile(1)])

    with pytest.raises(ProfileNotFoundError):
        await env.unhide_use_case().execute(ADMIN, 999, "reason")


async def test_unhiding_loses_the_race_quietly():
    env = Env([profile(1, status=ProfileStatus.HIDDEN_BY_ADMIN)], profile_repository=UnhidesElsewhereFirst())

    result = await env.unhide_use_case().execute(ADMIN, 1, "reason")

    assert result.status is ProfileStatus.PAUSED
    assert env.journal.actions == []
    assert env.uow.commits == 0
