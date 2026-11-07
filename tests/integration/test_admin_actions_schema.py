"""The physical shape of `admin_actions` as Postgres has it: indexes, the one foreign key and its ON DELETE rule, the
column size and the default of `details`. (The tests build the schema with create_all; `alembic check` is what compares
it with the migration.)"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.constants import ADMIN_REASON_MAX_LENGTH
from app.domain.enums import AdminActionType, AdminTargetType

INSERT = text(
    "INSERT INTO admin_actions (action, target_type, target_id, reason, created_at) "
    "VALUES ('ban_user', 'user', 1, :reason, now()) RETURNING details"
)


async def test_both_indexes_exist_on_the_expected_columns(session):
    rows = (await session.execute(text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'admin_actions'"))).all()
    definitions = {row.indexname: row.indexdef for row in rows}

    assert "(created_at, id)" in definitions["ix_admin_actions_created_at_id"]
    assert "(target_type, target_id)" in definitions["ix_admin_actions_target"]


async def test_the_only_foreign_key_is_the_actor_and_it_sets_null_on_delete(session):
    rows = (
        await session.execute(
            text(
                # confdeltype is the one-byte type "char": cast it, asyncpg would hand it over as bytes.
                "SELECT confrelid::regclass::text AS referenced, confdeltype::text AS confdeltype FROM pg_constraint "
                "WHERE conrelid = 'admin_actions'::regclass AND contype = 'f'"
            )
        )
    ).all()

    # No foreign key on target_id: the journal outlives its targets.
    assert [(row.referenced, row.confdeltype) for row in rows] == [("users", "n")]


async def test_details_defaults_to_an_empty_object(session):
    details = (await session.execute(INSERT, {"reason": "r"})).scalar_one()

    assert details == {}


async def test_reason_is_as_long_as_the_api_allows_and_not_longer(session):
    length = (
        await session.execute(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'admin_actions' AND column_name = 'reason'"
            )
        )
    ).scalar_one()
    assert length == ADMIN_REASON_MAX_LENGTH

    await session.execute(INSERT, {"reason": "x" * ADMIN_REASON_MAX_LENGTH})
    with pytest.raises(DBAPIError):
        await session.execute(INSERT, {"reason": "x" * (ADMIN_REASON_MAX_LENGTH + 1)})
    await session.rollback()


@pytest.mark.parametrize(
    "type_name, enum",
    [("admin_action_type", AdminActionType), ("admin_target_type", AdminTargetType)],
)
async def test_the_database_enums_have_exactly_the_members_of_the_python_enums(session, type_name, enum):
    labels = (
        await session.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = :name ORDER BY e.enumsortorder"
            ),
            {"name": type_name},
        )
    ).scalars().all()

    assert labels == [member.value for member in enum]
