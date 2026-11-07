"""scripts/make_admin.py against real Postgres: `main()` is called directly and returns the process exit code."""

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.domain.constants import ADMIN_REASON_MAX_LENGTH
from scripts.make_admin import main
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.db_ops import set_user_banned, set_user_deleted
from tests.integration.test_admin_endpoints import ACTIONS_URL, bearer, login


async def journal(session) -> list:
    return list((await session.execute(text("SELECT actor_user_id, action, target_type, target_id, reason, details FROM admin_actions ORDER BY id"))).all())


async def is_admin(session, user_id: int) -> bool:
    return (await session.execute(text("SELECT is_admin FROM users WHERE id = :id"), {"id": user_id})).scalar_one()


async def test_granting_makes_the_user_an_admin_and_journals_it_without_an_actor(session, capsys):
    user, _ = await create_user_with_headers(session, 947001)

    code = await main(947001, True, "first admin")

    assert code == 0
    assert await is_admin(session, user.id) is True
    [row] = await journal(session)
    assert row.actor_user_id is None
    assert (row.action, row.target_type, row.target_id) == ("grant_admin", "user", user.id)
    assert (row.reason, row.details) == ("first admin", {"telegram_id": 947001})
    assert "Done" in capsys.readouterr().out


async def test_running_it_again_changes_nothing_and_still_exits_0(session, capsys):
    user, _ = await create_user_with_headers(session, 947002)
    await main(947002, True, "first admin")
    capsys.readouterr()

    code = await main(947002, True, "first admin again")

    assert code == 0
    assert "Nothing changed" in capsys.readouterr().out
    assert len(await journal(session)) == 1


async def test_revoking_takes_the_role_away_and_journals_it(session):
    user, _ = await create_user_with_headers(session, 947003)
    await main(947003, True, "grant")

    code = await main(947003, False, "left the team")

    assert code == 0
    assert await is_admin(session, user.id) is False
    assert [row.action for row in await journal(session)] == ["grant_admin", "revoke_admin"]


async def test_revoking_from_a_user_who_is_not_an_admin_changes_nothing(session, capsys):
    await create_user_with_headers(session, 947004)

    code = await main(947004, False, "never was")

    assert code == 0
    assert "Nothing changed" in capsys.readouterr().out
    assert await journal(session) == []


async def test_a_user_who_never_logged_in_is_an_error(session, capsys):
    code = await main(947999, True, "who")

    assert code == 1
    assert "log in" in capsys.readouterr().err
    assert await journal(session) == []


async def test_a_banned_user_cannot_become_an_admin(session, capsys):
    user, _ = await create_user_with_headers(session, 947005)
    await set_user_banned(session, user.id, "spam")

    code = await main(947005, True, "no")

    assert code == 1
    assert "banned" in capsys.readouterr().err
    assert await is_admin(session, user.id) is False
    assert await journal(session) == []


async def test_a_deleted_user_is_an_error(session):
    user, _ = await create_user_with_headers(session, 947006)
    await set_user_deleted(session, user.id)

    assert await main(947006, True, "gone") == 1
    assert await journal(session) == []


@pytest.mark.parametrize("reason", ["", "    ", "x" * (ADMIN_REASON_MAX_LENGTH + 1), "a\x00b"])
async def test_a_bad_reason_is_an_error_and_nothing_is_written(session, reason):
    user, _ = await create_user_with_headers(session, 947007)

    code = await main(947007, True, reason)

    assert code == 1
    assert await is_admin(session, user.id) is False
    assert await journal(session) == []


async def test_the_reason_is_trimmed_and_the_longest_allowed_one_is_accepted(session):
    await create_user_with_headers(session, 947008)
    await create_user_with_headers(session, 947009)

    assert await main(947008, True, "  first admin  ") == 0
    assert await main(947009, True, "x" * ADMIN_REASON_MAX_LENGTH) == 0

    reasons = [row.reason for row in await journal(session)]
    assert reasons == ["first admin", "x" * ADMIN_REASON_MAX_LENGTH]


async def test_it_works_in_production_on_purpose(session, monkeypatch):
    # Unlike the dev seeds, this is an operations tool: there is no "refuse in prod" check.
    user, _ = await create_user_with_headers(session, 947010)
    monkeypatch.setattr(settings.run, "env", "prod")

    code = await main(947010, True, "first admin in prod")

    assert code == 0
    assert await is_admin(session, user.id) is True


async def test_a_new_admin_gets_in_at_once_with_the_token_they_already_had(taxonomy_client):
    old_token = await login(taxonomy_client, 947011)
    assert (await taxonomy_client.get(ACTIONS_URL, headers=bearer(old_token))).status_code == 403

    assert await main(947011, True, "first admin") == 0

    response = await taxonomy_client.get(ACTIONS_URL, headers=bearer(old_token))
    assert response.status_code == 200
    [item] = response.json()["items"]
    assert (item["actor_user_id"], item["action"], item["details"]) == (None, "grant_admin", {"telegram_id": 947011})


async def test_a_revoked_admin_loses_access_with_the_token_they_already_had(taxonomy_client):
    token = await login(taxonomy_client, 947012)
    await main(947012, True, "grant")
    assert (await taxonomy_client.get(ACTIONS_URL, headers=bearer(token))).status_code == 200

    await main(947012, False, "revoke")

    assert (await taxonomy_client.get(ACTIONS_URL, headers=bearer(token))).status_code == 403
