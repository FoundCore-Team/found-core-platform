"""Banning a user resolves the open reports on all of their profiles, in the same commit as the ban."""

import pytest

from app.application.use_cases import BanUserUseCase
from app.domain.enums import AdminActionType, ReportStatus
from tests.fixtures.factories import make_report_entity, make_user_entity
from tests.fixtures.fake_admin_action_repository import FakeAdminActionRepository
from tests.fixtures.fake_report_repository import FakeReportRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.fixtures.fake_user_repository import FakeUserRepository

ADMIN = make_user_entity(id=100, telegram_id=1000, is_admin=True)
TARGET_ID, OTHER_ID = 1, 2
# Profiles 10 and 11 belong to the user who gets banned, 20 to somebody else.
PROFILE_OWNERS = {10: TARGET_ID, 11: TARGET_ID, 20: OTHER_ID}


def build(target_overrides=None, reports=None):
    users = FakeUserRepository(
        [
            ADMIN,
            make_user_entity(id=TARGET_ID, telegram_id=555, **(target_overrides or {})),
            make_user_entity(id=OTHER_ID, telegram_id=556),
        ]
    )
    report_repository = FakeReportRepository(reports, profile_owners=PROFILE_OWNERS)
    journal = FakeAdminActionRepository()
    uow = FakeUnitOfWork()
    return BanUserUseCase(users, report_repository, journal, uow), report_repository, journal, uow


def reports_set():
    return [
        make_report_entity(id=1, profile_id=10, reporter_user_id=50),
        make_report_entity(id=2, profile_id=11, reporter_user_id=51),
        make_report_entity(id=3, profile_id=11, reporter_user_id=52, status=ReportStatus.DISMISSED),
        make_report_entity(id=4, profile_id=20, reporter_user_id=50),
    ]


async def test_a_ban_resolves_open_reports_on_every_profile_of_the_user_and_nobody_elses():
    use_case, report_repository, journal, uow = build(reports=reports_set())

    await use_case.execute(ADMIN, TARGET_ID, "spam")

    by_id = {r.id: r for r in report_repository.reports}
    assert (by_id[1].status, by_id[2].status) == (ReportStatus.RESOLVED, ReportStatus.RESOLVED)
    assert by_id[3].status is ReportStatus.DISMISSED  # was closed before, stays as it was
    assert by_id[4].status is ReportStatus.OPEN  # another user's profile
    [action] = journal.actions
    assert action.action is AdminActionType.BAN_USER
    assert action.details == {"resolved_reports": 2}
    assert by_id[1].resolved_at == action.created_at
    assert uow.commits == 1


async def test_a_ban_with_no_open_reports_records_zero():
    use_case, _, journal, _ = build(reports=[])

    await use_case.execute(ADMIN, TARGET_ID, "spam")

    assert journal.actions[0].details == {"resolved_reports": 0}


async def test_banning_an_already_banned_user_leaves_the_reports_alone_and_writes_nothing():
    use_case, report_repository, journal, uow = build(
        target_overrides=dict(is_banned=True, ban_reason="first"), reports=reports_set()
    )

    await use_case.execute(ADMIN, TARGET_ID, "second")

    still_open = [r.status for r in report_repository.reports if r.id in (1, 2)]
    assert still_open == [ReportStatus.OPEN, ReportStatus.OPEN]
    assert journal.actions == []
    assert uow.commits == 0


async def test_a_failing_journal_means_the_ban_is_never_committed():
    # The fake does not roll back, so only the missing commit is checked here; that the closed reports really are not
    # kept is checked against real Postgres in the integration tests.
    use_case, _, journal, uow = build(reports=reports_set())
    journal.fail_on_add = RuntimeError("the journal is down")

    with pytest.raises(RuntimeError):
        await use_case.execute(ADMIN, TARGET_ID, "spam")

    assert uow.commits == 0
