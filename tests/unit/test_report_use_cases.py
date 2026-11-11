"""CreateReportUseCase and the admin report use cases with in-memory fakes. The admin queue (`ListReportGroups`) joins
profiles and users, so it is checked against real Postgres in the integration tests."""

from datetime import datetime, timedelta, timezone

import pytest

from app.application.use_cases import (
    CreateReportUseCase,
    DismissProfileReportsUseCase,
    GetProfileReportsUseCase,
)
from app.domain.enums import AdminActionType, AdminTargetType, ProfileStatus, ReportReason, ReportStatus
from app.domain.exceptions import ProfileNotFoundError, ReportProfileNotFoundError
from tests.fixtures.factories import make_profile_entity, make_report_entity, make_user_entity
from tests.fixtures.fake_admin_action_repository import FakeAdminActionRepository
from tests.fixtures.fake_profile_repository import FakeProfileRepository
from tests.fixtures.fake_report_repository import FakeReportRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.fixtures.fake_user_repository import FakeUserRepository

REPORTER = make_user_entity(id=50, telegram_id=5000, username=None)  # no username: reporting must not need one
ADMIN = make_user_entity(id=100, telegram_id=1000, is_admin=True)
OWNER = make_user_entity(id=1, telegram_id=555)
T0 = datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc)


def build_create(reports=None, visible=None, repository=None):
    repository = repository or FakeReportRepository(reports, visible=visible if visible is not None else {50: {1}})
    uow = FakeUnitOfWork()
    return CreateReportUseCase(repository, uow), repository, uow


# ---------- CreateReportUseCase ----------


async def test_a_visible_profile_gets_a_new_open_report_in_one_commit():
    use_case, repository, uow = build_create()

    report, is_new = await use_case.execute(REPORTER, 1, ReportReason.FAKE, "looks like a bot")

    assert is_new is True
    assert (report.reporter_user_id, report.profile_id) == (50, 1)
    assert (report.reason, report.comment) == (ReportReason.FAKE, "looks like a bot")
    assert report.status is ReportStatus.OPEN
    assert report.resolved_at is None
    assert report.created_at.tzinfo is not None
    assert len(repository.reports) == 1
    assert uow.commits == 1


async def test_a_report_works_without_a_username_and_without_an_active_profile():
    use_case, _, _ = build_create()

    assert REPORTER.username is None and REPORTER.active_profile_id is None
    _, is_new = await use_case.execute(REPORTER, 1, ReportReason.SPAM, None)

    assert is_new is True


@pytest.mark.parametrize(
    "visible",
    [{}, {50: set()}, {50: {2}}, {51: {1}}],
    ids=["nobody sees anything", "reporter sees nothing", "other profile only", "someone else sees it"],
)
async def test_an_invisible_profile_is_not_found_and_nothing_is_written(visible):
    use_case, repository, uow = build_create(visible=visible)

    with pytest.raises(ReportProfileNotFoundError):
        await use_case.execute(REPORTER, 1, ReportReason.SPAM, None)

    assert repository.reports == []
    assert uow.commits == 0


async def test_a_repeat_while_the_report_is_open_returns_it_unchanged_and_writes_nothing():
    use_case, repository, uow = build_create()
    first, _ = await use_case.execute(REPORTER, 1, ReportReason.SPAM, "first")
    uow.commits = 0

    report, is_new = await use_case.execute(REPORTER, 1, ReportReason.OFFENSIVE, "second")

    assert is_new is False
    assert report.id == first.id
    assert (report.reason, report.comment) == (ReportReason.SPAM, "first")  # not overwritten
    assert len(repository.reports) == 1
    assert uow.commits == 0


async def test_a_closed_report_does_not_block_a_new_one_and_stays_for_history():
    closed = make_report_entity(id=1, profile_id=1, reporter_user_id=50, status=ReportStatus.DISMISSED)
    use_case, repository, _ = build_create(reports=[closed])

    report, is_new = await use_case.execute(REPORTER, 1, ReportReason.SPAM, None)

    assert is_new is True
    assert report.id != closed.id
    assert {r.status for r in repository.reports} == {ReportStatus.OPEN, ReportStatus.DISMISSED}
    assert len(repository.reports) == 2


async def test_two_reporters_may_each_have_an_open_report_on_the_same_profile():
    other = make_report_entity(id=1, profile_id=1, reporter_user_id=77)
    use_case, repository, _ = build_create(reports=[other])

    _, is_new = await use_case.execute(REPORTER, 1, ReportReason.SPAM, None)

    assert is_new is True
    assert len(repository.reports) == 2


class ClosedWhileWeLook(FakeReportRepository):
    """An admin closes the open report after our insert was refused and before we read it."""

    async def get_open_by_reporter_and_profile(self, reporter_user_id, profile_id):
        await self.close_open_for_profile(profile_id, ReportStatus.DISMISSED, T0)
        return await super().get_open_by_reporter_and_profile(reporter_user_id, profile_id)


async def test_when_the_open_report_is_closed_meanwhile_the_insert_is_tried_once_more():
    open_report = make_report_entity(id=1, profile_id=1, reporter_user_id=50)
    repository = ClosedWhileWeLook([open_report], visible={50: {1}})
    use_case, _, uow = build_create(repository=repository)

    report, is_new = await use_case.execute(REPORTER, 1, ReportReason.SPAM, None)

    assert is_new is True
    assert report.id != open_report.id
    assert uow.commits == 1


class AlwaysRefusedAndNeverFound(FakeReportRepository):
    async def create(self, report):
        return None

    async def get_open_by_reporter_and_profile(self, reporter_user_id, profile_id):
        return None


async def test_giving_up_after_two_attempts_is_an_error_not_a_silent_none():
    use_case, _, uow = build_create(repository=AlwaysRefusedAndNeverFound(visible={50: {1}}))

    with pytest.raises(RuntimeError):
        await use_case.execute(REPORTER, 1, ReportReason.SPAM, None)

    assert uow.commits == 0


# ---------- DismissProfileReportsUseCase ----------


def build_admin(profiles, reports):
    profile_repository = FakeProfileRepository(profiles)
    report_repository = FakeReportRepository(reports, profile_owners={p.id: p.user_id for p in profiles})
    journal = FakeAdminActionRepository()
    uow = FakeUnitOfWork()
    return profile_repository, report_repository, journal, uow


async def test_dismissing_closes_the_open_reports_of_that_profile_and_writes_the_journal():
    profiles = [make_profile_entity(id=1, user_id=1), make_profile_entity(id=2, user_id=1)]
    reports = [
        make_report_entity(id=1, profile_id=1, reporter_user_id=50),
        make_report_entity(id=2, profile_id=1, reporter_user_id=51),
        make_report_entity(id=3, profile_id=1, reporter_user_id=52, status=ReportStatus.RESOLVED),
        make_report_entity(id=4, profile_id=2, reporter_user_id=50),
    ]
    profile_repository, report_repository, journal, uow = build_admin(profiles, reports)
    use_case = DismissProfileReportsUseCase(profile_repository, report_repository, journal, uow)

    dismissed = await use_case.execute(ADMIN, 1, "no violation")

    assert dismissed == 2
    by_id = {r.id: r for r in report_repository.reports}
    assert (by_id[1].status, by_id[2].status) == (ReportStatus.DISMISSED, ReportStatus.DISMISSED)
    assert by_id[3].status is ReportStatus.RESOLVED  # not turned into "dismissed"
    assert by_id[4].status is ReportStatus.OPEN
    [action] = journal.actions
    assert action.action is AdminActionType.DISMISS_REPORTS
    assert (action.actor_user_id, action.target_type, action.target_id) == (ADMIN.id, AdminTargetType.PROFILE, 1)
    assert action.reason == "no violation"
    assert action.details == {"owner_user_id": 1, "dismissed_reports": 2}
    assert by_id[1].resolved_at == action.created_at
    assert uow.commits == 1


async def test_dismissing_with_no_open_reports_writes_nothing_and_does_not_commit():
    profile_repository, report_repository, journal, uow = build_admin([make_profile_entity(id=1)], [])
    use_case = DismissProfileReportsUseCase(profile_repository, report_repository, journal, uow)

    assert await use_case.execute(ADMIN, 1, "nothing to do") == 0

    assert journal.actions == []
    assert uow.commits == 0


async def test_dismissing_an_unknown_profile_is_not_found():
    profile_repository, report_repository, journal, uow = build_admin([make_profile_entity(id=1)], [])
    use_case = DismissProfileReportsUseCase(profile_repository, report_repository, journal, uow)

    with pytest.raises(ProfileNotFoundError):
        await use_case.execute(ADMIN, 999, "reason")


async def test_a_failing_journal_means_no_commit_on_dismiss():
    profile_repository, report_repository, journal, uow = build_admin(
        [make_profile_entity(id=1)], [make_report_entity(profile_id=1)]
    )
    journal.fail_on_add = RuntimeError("the journal is down")
    use_case = DismissProfileReportsUseCase(profile_repository, report_repository, journal, uow)

    with pytest.raises(RuntimeError):
        await use_case.execute(ADMIN, 1, "reason")

    assert uow.commits == 0


# ---------- GetProfileReportsUseCase ----------


def build_get(profiles, reports, users=(OWNER,)):
    return GetProfileReportsUseCase(
        FakeProfileRepository(profiles),
        FakeUserRepository(list(users)),
        FakeReportRepository(reports),
    )


async def test_the_details_hold_the_profile_its_owner_and_only_the_open_reports_oldest_first():
    profiles = [make_profile_entity(id=1, user_id=1)]
    reports = [
        make_report_entity(id=1, profile_id=1, reporter_user_id=51, created_at=T0 + timedelta(minutes=5)),
        make_report_entity(id=2, profile_id=1, reporter_user_id=52, created_at=T0),
        make_report_entity(id=3, profile_id=1, reporter_user_id=53, status=ReportStatus.DISMISSED),
        make_report_entity(id=4, profile_id=2, reporter_user_id=51),
    ]

    profile, owner, listed = await build_get(profiles, reports).execute(1)

    assert (profile.id, owner.id) == (1, 1)
    assert [r.id for r in listed] == [2, 1]


@pytest.mark.parametrize("status", [ProfileStatus.PAUSED, ProfileStatus.HIDDEN_BY_ADMIN])
async def test_an_admin_sees_a_paused_or_hidden_profile(status):
    profile, _, _ = await build_get([make_profile_entity(id=1, user_id=1, status=status)], []).execute(1)

    assert profile.status is status


async def test_details_of_an_unknown_profile_are_not_found():
    with pytest.raises(ProfileNotFoundError):
        await build_get([make_profile_entity(id=1, user_id=1)], []).execute(999)
