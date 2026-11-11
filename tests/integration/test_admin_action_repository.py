"""SqlAlchemyAdminActionRepository against real Postgres: the journal is written without a commit, read newest first
with a stable order, filtered by target, and outlives the people and rows it mentions."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text

from app.domain.entities import NewAdminActionEntity
from app.domain.enums import AdminActionType, AdminTargetType
from app.infrastructure.helpers import db_helper
from app.infrastructure.models import AdminActionModel
from app.infrastructure.repositories import SqlAlchemyAdminActionRepository
from tests.fixtures.auth import create_user_with_headers

T0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def new_action(
    *,
    actor_user_id: int | None = None,
    action: AdminActionType = AdminActionType.BAN_USER,
    target_type: AdminTargetType = AdminTargetType.USER,
    target_id: int = 1,
    reason: str = "reason",
    details: dict | None = None,
    minutes: int = 0,
) -> NewAdminActionEntity:
    return NewAdminActionEntity(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        details=details if details is not None else {},
        created_at=T0 + timedelta(minutes=minutes),
    )


async def count_rows(session) -> int:
    return (await session.execute(select(func.count()).select_from(AdminActionModel))).scalar_one()


async def test_add_returns_the_stored_row_with_an_id(session):
    repo = SqlAlchemyAdminActionRepository(session)

    stored = await repo.add(new_action(action=AdminActionType.HIDE_PROFILE, target_type=AdminTargetType.PROFILE, target_id=7))

    assert stored.id > 0
    assert (stored.actor_user_id, stored.action, stored.target_type, stored.target_id) == (
        None,
        AdminActionType.HIDE_PROFILE,
        AdminTargetType.PROFILE,
        7,
    )
    assert stored.reason == "reason"
    assert stored.created_at == T0


async def test_add_does_not_commit(session):
    await SqlAlchemyAdminActionRepository(session).add(new_action())

    assert await count_rows(session) == 1  # visible to its own session
    async with db_helper.session_factory() as fresh:
        assert await count_rows(fresh) == 0  # but not committed: the use case does that


async def test_details_survive_a_round_trip_with_nesting_and_unicode(session):
    repo = SqlAlchemyAdminActionRepository(session)
    details = {"resolved_reports": 3, "nested": {"ids": [1, 2]}, "note": "Привет"}
    await repo.add(new_action(details=details))
    await session.commit()

    [stored] = await repo.list_actions(limit=10, offset=0, target_type=None, target_id=None)

    assert stored.details == details


async def test_the_journal_is_read_newest_first(session):
    repo = SqlAlchemyAdminActionRepository(session)
    for minutes in (1, 3, 2):
        await repo.add(new_action(minutes=minutes))

    actions = await repo.list_actions(limit=10, offset=0, target_type=None, target_id=None)

    assert [a.created_at for a in actions] == [T0 + timedelta(minutes=m) for m in (3, 2, 1)]


async def test_rows_created_in_the_same_instant_come_back_in_one_order_on_every_page(session):
    repo = SqlAlchemyAdminActionRepository(session)
    for _ in range(5):
        await repo.add(new_action(minutes=0))

    whole = await repo.list_actions(limit=10, offset=0, target_type=None, target_id=None)
    paged = []
    for offset in (0, 2, 4):
        paged += await repo.list_actions(limit=2, offset=offset, target_type=None, target_id=None)

    assert [a.id for a in whole] == sorted((a.id for a in whole), reverse=True)
    assert [a.id for a in paged] == [a.id for a in whole]  # no row repeated or skipped between pages


async def test_filter_by_target_type_and_id_and_count_follows_it(session):
    repo = SqlAlchemyAdminActionRepository(session)
    await repo.add(new_action(target_type=AdminTargetType.USER, target_id=1))
    await repo.add(new_action(target_type=AdminTargetType.USER, target_id=2))
    await repo.add(new_action(target_type=AdminTargetType.PROFILE, target_id=1))

    one = await repo.list_actions(limit=10, offset=0, target_type=AdminTargetType.USER, target_id=1)
    users = await repo.list_actions(limit=10, offset=0, target_type=AdminTargetType.USER, target_id=None)

    assert [(a.target_type, a.target_id) for a in one] == [(AdminTargetType.USER, 1)]
    assert len(users) == 2
    assert await repo.count(None, None) == 3
    assert await repo.count(AdminTargetType.USER, None) == 2
    assert await repo.count(AdminTargetType.USER, 1) == 1
    assert await repo.count(AdminTargetType.TAG, 1) == 0


async def test_an_offset_past_the_end_is_an_empty_page(session):
    repo = SqlAlchemyAdminActionRepository(session)
    await repo.add(new_action())

    assert await repo.list_actions(limit=10, offset=5, target_type=None, target_id=None) == []


async def test_the_row_survives_the_deletion_of_the_admin_and_forgets_who_they_were(session):
    admin, _ = await create_user_with_headers(session, 920001)
    repo = SqlAlchemyAdminActionRepository(session)
    await repo.add(new_action(actor_user_id=admin.id))
    await session.commit()

    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": admin.id})
    await session.commit()

    [stored] = await repo.list_actions(limit=10, offset=0, target_type=None, target_id=None)
    assert stored.actor_user_id is None


async def test_a_row_may_point_at_a_target_that_does_not_exist(session):
    # No foreign key on target_id: the journal is an audit trail and must outlive a deleted profile or user.
    repo = SqlAlchemyAdminActionRepository(session)

    await repo.add(new_action(target_type=AdminTargetType.PROFILE, target_id=987654321))
    await session.commit()

    assert await repo.count(AdminTargetType.PROFILE, 987654321) == 1
