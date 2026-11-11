"""Races around ban / unban / grant, fired in parallel with asyncio.gather against real Postgres, and the promise that
the journal row and the change it records are committed together or not at all."""

import asyncio

import pytest
from sqlalchemy import text

from app.application.use_cases import BanUserUseCase
from app.core.composition.container import Container
from app.domain.exceptions import UserBannedError
from app.infrastructure.helpers import db_helper
from app.infrastructure.repositories import SqlAlchemyAdminActionRepository
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.db_ops import set_user_admin, set_user_banned
from tests.integration.test_admin_endpoints import ban, journal_rows, login, make_admin, unban, user_state
from tests.integration.test_feed_endpoints import fetch, make_viewer


async def test_parallel_bans_of_one_user_write_one_journal_row_and_raise_token_version_once(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 945001)
    target = await login(taxonomy_client, 945002)
    user_id = target["user"]["id"]

    responses = await asyncio.gather(
        *(ban(taxonomy_client, admin_headers, user_id, f"reason {i}") for i in range(10))
    )

    assert {response.status_code for response in responses} == {200}
    [row] = await journal_rows(session)
    state = await user_state(session, user_id)
    assert state.is_banned is True
    assert state.token_version == 1
    assert state.ban_reason == row.reason  # the reason of the one request that won


async def test_a_ban_and_an_unban_in_parallel_leave_a_state_that_matches_the_last_journal_row(
    taxonomy_client, session
):
    _, admin_headers = await make_admin(taxonomy_client, session, 945003)

    for round_number in range(12):
        target = await login(taxonomy_client, 945100 + round_number)
        user_id = target["user"]["id"]
        started_banned = round_number % 2 == 1
        if started_banned:
            await set_user_banned(session, user_id, "banned before the round")

        responses = await asyncio.gather(
            ban(taxonomy_client, admin_headers, user_id), unban(taxonomy_client, admin_headers, user_id)
        )

        assert {response.status_code for response in responses} == {200}, f"round {round_number}"
        rows = [row for row in await journal_rows(session) if row.target_id == user_id]
        state = await user_state(session, user_id)
        expected_banned = rows[-1].action == "ban_user" if rows else started_banned
        assert state.is_banned is expected_banned, f"round {round_number}: state and journal disagree"
        assert state.token_version == sum(row.action == "ban_user" for row in rows), f"round {round_number}"


async def test_feed_requests_in_parallel_with_a_ban_answer_only_200_or_401(taxonomy_client, session, taxonomy_data):
    _, admin_headers = await make_admin(taxonomy_client, session, 945004)
    viewer = await make_viewer(session, taxonomy_data, telegram_id=945005)

    async def requests_then_ban():
        return await asyncio.gather(
            *(fetch(taxonomy_client, viewer, mode="discovery") for _ in range(8)),
            ban(taxonomy_client, admin_headers, viewer.user_id),
            *(fetch(taxonomy_client, viewer, mode="discovery") for _ in range(8)),
        )

    responses = await requests_then_ban()

    feed_statuses = {r.status_code for i, r in enumerate(responses) if i != 8}
    assert feed_statuses <= {200, 401, 403}
    assert responses[8].status_code == 200
    assert (await fetch(taxonomy_client, viewer, mode="discovery")).status_code == 401  # the old token is dead now


async def test_a_ban_and_a_grant_of_the_admin_role_racing_never_leave_a_banned_admin(
    taxonomy_client, session, redis_client
):
    _, admin_headers = await make_admin(taxonomy_client, session, 945006)

    async def grant(telegram_id: int):
        async with db_helper.session_factory() as s:
            container = Container(session=s, redis_client=redis_client)
            return await container.set_admin_use_case().execute(telegram_id, True, "race")

    for round_number in range(12):
        telegram_id = 945200 + round_number
        target = await login(taxonomy_client, telegram_id)
        user_id = target["user"]["id"]

        ban_response, grant_result = await asyncio.gather(
            ban(taxonomy_client, admin_headers, user_id), grant(telegram_id), return_exceptions=True
        )

        assert ban_response.status_code in (200, 409), f"round {round_number}"
        # The grant either went through or was refused because the ban got there first; anything else is a bug.
        assert isinstance(grant_result, tuple) or isinstance(grant_result, UserBannedError), f"round {round_number}"
        row = (await session.execute(text("SELECT is_admin, is_banned FROM users WHERE id = :id"), {"id": user_id})).one()
        assert not (row.is_admin and row.is_banned), f"round {round_number}: the user is both banned and an admin"
        actions = {r.action for r in await journal_rows(session) if r.target_id == user_id}
        assert not {"ban_user", "grant_admin"} <= actions, f"round {round_number}: both actions were recorded"


# ---------- the journal row and the change are one transaction ----------


class BrokenJournal(SqlAlchemyAdminActionRepository):
    async def add(self, action):
        raise RuntimeError("the journal is down")


async def test_a_ban_waits_for_a_transaction_that_holds_a_key_share_lock_on_the_user(session, container):
    # `FOR KEY SHARE` (what a foreign-key insert such as a new report takes) does not block the plain UPDATE of
    # `is_banned`, but it does block `FOR UPDATE`: if the ban did not take that lock first, it would finish at once.
    admin, _ = await create_user_with_headers(session, 946010)
    await set_user_admin(session, admin.id)
    admin = await container.user_repo().get_by_id(admin.id)
    target, _ = await create_user_with_headers(session, 946011)

    async with db_helper.session_factory() as holder:
        await holder.execute(text("SELECT id FROM users WHERE id = :id FOR KEY SHARE"), {"id": target.id})
        ban_task = asyncio.create_task(container.ban_user_use_case().execute(admin, target.id, "spam"))
        await asyncio.sleep(0.3)
        assert not ban_task.done()

        await holder.commit()
        banned = await asyncio.wait_for(ban_task, timeout=5)

    assert banned.is_banned is True


async def test_an_unban_waits_for_a_transaction_that_holds_a_key_share_lock_on_the_user(session, container):
    admin, _ = await create_user_with_headers(session, 946012)
    await set_user_admin(session, admin.id)
    admin = await container.user_repo().get_by_id(admin.id)
    target, _ = await create_user_with_headers(session, 946013)
    await set_user_banned(session, target.id, "spam")

    async with db_helper.session_factory() as holder:
        await holder.execute(text("SELECT id FROM users WHERE id = :id FOR KEY SHARE"), {"id": target.id})
        unban_task = asyncio.create_task(container.unban_user_use_case().execute(admin, target.id, "appeal accepted"))
        await asyncio.sleep(0.3)
        assert not unban_task.done()

        await holder.commit()
        unbanned = await asyncio.wait_for(unban_task, timeout=5)

    assert unbanned.is_banned is False


async def test_a_journal_failure_leaves_no_ban_no_token_bump_and_no_row_committed(session, container):
    admin, _ = await create_user_with_headers(session, 946001)
    await set_user_admin(session, admin.id)
    admin = await container.user_repo().get_by_id(admin.id)
    target, _ = await create_user_with_headers(session, 946002)
    use_case = BanUserUseCase(
        user_repository=container.user_repo(),
        report_repository=container.report_repo(),
        admin_action_repository=BrokenJournal(session),
        unit_of_work=container.unit_of_work(),
    )

    with pytest.raises(RuntimeError):
        await use_case.execute(admin, target.id, "spam")

    async with db_helper.session_factory() as fresh:  # another connection: only committed data is visible
        state = (
            await fresh.execute(
                text("SELECT is_banned, ban_reason, token_version FROM users WHERE id = :id"), {"id": target.id}
            )
        ).one()
        journal_count = (await fresh.execute(text("SELECT count(*) FROM admin_actions"))).scalar_one()
    assert tuple(state) == (False, None, 0)
    assert journal_count == 0
    await session.rollback()  # what closing the request-scoped session does in production


async def test_a_successful_ban_commits_the_change_and_the_journal_row_together(session, container):
    admin, _ = await create_user_with_headers(session, 946003)
    await set_user_admin(session, admin.id)
    admin = await container.user_repo().get_by_id(admin.id)
    target, _ = await create_user_with_headers(session, 946004)

    await container.ban_user_use_case().execute(admin, target.id, "spam")

    async with db_helper.session_factory() as fresh:
        state = (await fresh.execute(text("SELECT is_banned, token_version FROM users WHERE id = :id"), {"id": target.id})).one()
        rows = (await fresh.execute(text("SELECT actor_user_id, action, target_id FROM admin_actions"))).all()
    assert tuple(state) == (True, 1)
    assert [tuple(row) for row in rows] == [(admin.id, "ban_user", target.id)]
