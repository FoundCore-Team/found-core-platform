"""set_ban / clear_ban / set_admin are conditional UPDATEs: the old state is part of the WHERE clause, so the check
and the write are one atomic statement. These tests run them against real Postgres, including in parallel."""

import asyncio

from sqlalchemy import text

from app.infrastructure.helpers import db_helper
from app.infrastructure.repositories import SqlAlchemyUserRepository
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.db_ops import set_user_admin, set_user_banned, set_user_deleted


async def make_user(session, telegram_id: int):
    user, _ = await create_user_with_headers(session, telegram_id)
    return user


# ---------- set_ban ----------


async def test_set_ban_bans_stores_the_reason_and_raises_token_version(session):
    user = await make_user(session, 921001)

    banned = await SqlAlchemyUserRepository(session).set_ban(user.id, "spam")

    assert banned is not None
    assert (banned.is_banned, banned.ban_reason, banned.token_version) == (True, "spam", user.token_version + 1)


async def test_set_ban_on_a_banned_user_changes_nothing(session):
    user = await make_user(session, 921002)
    repo = SqlAlchemyUserRepository(session)
    await repo.set_ban(user.id, "first")

    second = await repo.set_ban(user.id, "second")

    assert second is None
    stored = await repo.get_by_id(user.id)
    assert (stored.ban_reason, stored.token_version) == ("first", user.token_version + 1)


async def test_set_ban_never_bans_an_admin(session):
    user = await make_user(session, 921003)
    await set_user_admin(session, user.id)
    repo = SqlAlchemyUserRepository(session)

    assert await repo.set_ban(user.id, "spam") is None

    stored = await repo.get_by_id(user.id)
    assert (stored.is_banned, stored.token_version) == (False, user.token_version)


async def test_set_ban_skips_a_deleted_user_and_an_unknown_id(session):
    user = await make_user(session, 921004)
    await set_user_deleted(session, user.id)
    repo = SqlAlchemyUserRepository(session)

    assert await repo.set_ban(user.id, "spam") is None
    assert await repo.set_ban(user.id + 100000, "spam") is None


async def test_set_ban_refreshes_a_user_this_session_has_already_loaded(session):
    user = await make_user(session, 921005)
    repo = SqlAlchemyUserRepository(session)
    await repo.get_by_id(user.id)  # now the row sits in the session's identity map as "not banned"

    banned = await repo.set_ban(user.id, "spam")

    assert banned.is_banned is True
    assert (await repo.get_by_id(user.id)).is_banned is True  # not the stale object


async def test_only_one_of_many_parallel_bans_applies_and_token_version_rises_once(session):
    user = await make_user(session, 921006)

    async def attempt(reason: str):
        async with db_helper.session_factory() as s:
            result = await SqlAlchemyUserRepository(s).set_ban(user.id, reason)
            await s.commit()
            return result

    results = await asyncio.gather(*(attempt(f"reason {i}") for i in range(10)))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    row = (await session.execute(text("SELECT is_banned, ban_reason, token_version FROM users WHERE id = :id"), {"id": user.id})).one()
    assert tuple(row) == (True, winners[0].ban_reason, user.token_version + 1)


# ---------- clear_ban ----------


async def test_clear_ban_lifts_the_ban_clears_the_reason_and_keeps_token_version(session):
    user = await make_user(session, 921010)
    repo = SqlAlchemyUserRepository(session)
    banned = await repo.set_ban(user.id, "spam")

    unbanned = await repo.clear_ban(user.id)

    assert (unbanned.is_banned, unbanned.ban_reason, unbanned.token_version) == (False, None, banned.token_version)


async def test_clear_ban_on_a_user_who_is_not_banned_changes_nothing(session):
    user = await make_user(session, 921011)

    assert await SqlAlchemyUserRepository(session).clear_ban(user.id) is None


# ---------- set_admin ----------


async def test_set_admin_grants_and_revokes(session):
    user = await make_user(session, 921020)
    repo = SqlAlchemyUserRepository(session)

    granted = await repo.set_admin(user.id, True)
    revoked = await repo.set_admin(user.id, False)

    assert granted.is_admin is True
    assert revoked.is_admin is False


async def test_set_admin_in_the_state_the_user_already_has_changes_nothing(session):
    user = await make_user(session, 921021)
    repo = SqlAlchemyUserRepository(session)

    assert await repo.set_admin(user.id, False) is None  # not an admin to begin with
    await repo.set_admin(user.id, True)
    assert await repo.set_admin(user.id, True) is None  # already an admin


async def test_set_admin_never_grants_the_role_to_a_banned_user(session):
    user = await make_user(session, 921022)
    await set_user_banned(session, user.id, "spam")

    assert await SqlAlchemyUserRepository(session).set_admin(user.id, True) is None


async def test_a_ban_and_a_grant_racing_never_leave_a_banned_admin(session):
    async def ban(user_id: int):
        async with db_helper.session_factory() as s:
            result = await SqlAlchemyUserRepository(s).set_ban(user_id, "spam")
            await s.commit()
            return result

    async def grant(user_id: int):
        async with db_helper.session_factory() as s:
            result = await SqlAlchemyUserRepository(s).set_admin(user_id, True)
            await s.commit()
            return result

    for round_number in range(15):
        user = await make_user(session, 921100 + round_number)

        await asyncio.gather(ban(user.id), grant(user.id))

        row = (await session.execute(text("SELECT is_admin, is_banned FROM users WHERE id = :id"), {"id": user.id})).one()
        assert not (row.is_admin and row.is_banned), f"round {round_number}: the user is both banned and an admin"
