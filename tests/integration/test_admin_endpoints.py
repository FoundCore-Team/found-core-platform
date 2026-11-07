"""/api/v1/admin/* over real HTTP, Postgres and Redis: who may call it, ban and unban end to end, the journal, and
hostile input (always a 4xx, never a 5xx)."""

import re
import time

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.domain.constants import ADMIN_REASON_MAX_LENGTH, MAX_INT64
from app.domain.enums import ProfileStatus
from app.main import main_app
from scripts.dev_gen_init_data import build_init_data
from tests.fixtures.db_ops import revoke_user_admin, set_user_admin, set_user_banned, set_user_deleted
from tests.fixtures.feed_data import add_profile
from tests.fixtures.init_data import BOT_TOKEN

LOGIN_URL = "/api/v1/auth/telegram"
PROTECTED_URL = "/api/v1/taxonomy/categories"
ACTIONS_URL = "/api/v1/admin/actions"


def _admin_routes() -> list[tuple[str, str]]:
    # Read from the OpenAPI schema, the public description of the routes, not from `main_app.routes`: how included
    # routers show up there depends on the FastAPI version. A route hidden from the schema would not be walked, and
    # `test_the_walk_finds_the_admin_routes` below would say so.
    routes = []
    for path, operations in main_app.openapi()["paths"].items():
        if path.startswith("/api/v1/admin"):
            for method in sorted(operations):
                routes.append((method.upper(), re.sub(r"\{[^}]+\}", "1", path)))
    return routes


ADMIN_ROUTES = _admin_routes()


def body_for(method: str) -> dict:
    return {} if method == "GET" else {"json": {"reason": "x"}}


async def login(client, telegram_id: int) -> dict:
    raw = build_init_data(BOT_TOKEN, telegram_id, int(time.time()), bad_hash=False)
    response = await client.post(LOGIN_URL, json={"init_data": raw})
    assert response.status_code == 200
    return response.json()


def bearer(login_body: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {login_body['access_token']}"}


async def make_admin(client, session, telegram_id: int) -> tuple[dict, dict[str, str]]:
    """Logs a user in and makes them an admin straight in the database. The token stays valid: admin rights are
    read from the database on every request, not from the token."""
    body = await login(client, telegram_id)
    await set_user_admin(session, body["user"]["id"])
    return body, bearer(body)


async def ban(client, headers, user_id, reason: str = "spam in bio"):
    return await client.post(f"/api/v1/admin/users/{user_id}/ban", json={"reason": reason}, headers=headers)


async def unban(client, headers, user_id, reason: str = "appeal accepted"):
    return await client.post(f"/api/v1/admin/users/{user_id}/unban", json={"reason": reason}, headers=headers)


async def journal_rows(session) -> list:
    return list(
        (await session.execute(text("SELECT id, actor_user_id, action, target_type, target_id, reason, details FROM admin_actions ORDER BY id"))).all()
    )


async def user_state(session, user_id: int):
    return (
        await session.execute(
            text("SELECT is_banned, ban_reason, token_version FROM users WHERE id = :id"), {"id": user_id}
        )
    ).one()


# ---------- the route walk must not be empty ----------


def test_the_walk_finds_the_admin_routes():
    # Otherwise every parametrized test below would pass by testing nothing.
    assert {path for _, path in ADMIN_ROUTES} >= {
        "/api/v1/admin/users/1/ban",
        "/api/v1/admin/users/1/unban",
        "/api/v1/admin/actions",
    }


# ---------- access: every admin route ----------


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
async def test_no_token_is_401(taxonomy_client, method, path):
    response = await taxonomy_client.request(method, path, **body_for(method))

    assert response.status_code == 401


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
async def test_a_regular_user_gets_403_with_admin_required(taxonomy_client, method, path):
    user = await login(taxonomy_client, 941001)

    response = await taxonomy_client.request(method, path, headers=bearer(user), **body_for(method))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "admin_required"


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
async def test_a_regular_user_with_a_garbage_body_still_gets_403_not_422(taxonomy_client, method, path):
    user = await login(taxonomy_client, 941002)

    response = await taxonomy_client.request(method, path, headers=bearer(user), json={"nonsense": [1, 2]})

    assert response.status_code == 403


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
async def test_a_banned_admin_gets_the_403_with_the_ban_reason(taxonomy_client, session, method, path):
    body, headers = await make_admin(taxonomy_client, session, 941003)
    await set_user_banned(session, body["user"]["id"], "was an admin once")

    response = await taxonomy_client.request(method, path, headers=headers, **body_for(method))

    assert response.status_code == 403
    assert response.json()["detail"] == "was an admin once"  # the ban's 403 is a string, not {"code": ...}


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
async def test_an_admin_demoted_after_the_token_was_issued_loses_access_at_once(
    taxonomy_client, session, method, path
):
    body, headers = await make_admin(taxonomy_client, session, 941004)
    await revoke_user_admin(session, body["user"]["id"])

    response = await taxonomy_client.request(method, path, headers=headers, **body_for(method))

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "admin_required"


async def test_an_admin_is_let_in(taxonomy_client, session):
    _, headers = await make_admin(taxonomy_client, session, 941005)

    response = await taxonomy_client.get(ACTIONS_URL, headers=headers)

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": settings.moderation.page_size_default, "offset": 0}


async def test_the_login_response_tells_the_client_whether_the_user_is_an_admin(taxonomy_client, session):
    body, _ = await make_admin(taxonomy_client, session, 941006)

    again = await login(taxonomy_client, 941006)

    assert body["user"]["is_admin"] is False  # the first login came before the grant
    assert again["user"]["is_admin"] is True


# ---------- ban and unban ----------


async def test_a_ban_revokes_old_tokens_but_the_user_can_log_in_and_sees_why(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 942001)
    target = await login(taxonomy_client, 942002)
    assert (await taxonomy_client.get(PROTECTED_URL, headers=bearer(target))).status_code == 200

    response = await ban(taxonomy_client, admin_headers, target["user"]["id"], "spam in bio")

    assert response.status_code == 200
    assert response.json()["is_banned"] is True
    assert response.json()["ban_reason"] == "spam in bio"
    assert (await taxonomy_client.get(PROTECTED_URL, headers=bearer(target))).status_code == 401  # token_version moved
    again = await login(taxonomy_client, 942002)  # allowed: the frontend shows a ban screen
    assert again["user"]["is_banned"] is True
    assert again["user"]["ban_reason"] == "spam in bio"
    blocked = await taxonomy_client.get(PROTECTED_URL, headers=bearer(again))
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "spam in bio"


async def test_an_unban_lets_the_token_issued_during_the_ban_work_and_keeps_old_ones_dead(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 942003)
    before_ban = await login(taxonomy_client, 942004)
    user_id = before_ban["user"]["id"]
    await ban(taxonomy_client, admin_headers, user_id)
    during_ban = await login(taxonomy_client, 942004)

    response = await unban(taxonomy_client, admin_headers, user_id)

    assert response.status_code == 200
    assert response.json()["is_banned"] is False
    assert response.json()["ban_reason"] is None
    assert (await taxonomy_client.get(PROTECTED_URL, headers=bearer(during_ban))).status_code == 200
    assert (await taxonomy_client.get(PROTECTED_URL, headers=bearer(before_ban))).status_code == 401


async def test_a_ban_leaves_the_profile_statuses_alone(taxonomy_client, session, taxonomy_data):
    _, admin_headers = await make_admin(taxonomy_client, session, 942005)
    target = await login(taxonomy_client, 942006)
    profile_id = await add_profile(
        session, target["user"]["id"], taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id
    )

    await ban(taxonomy_client, admin_headers, target["user"]["id"])

    status = (await session.execute(text("SELECT status FROM profiles WHERE id = :id"), {"id": profile_id})).scalar_one()
    assert status == ProfileStatus.ACTIVE.value


async def test_banning_twice_answers_200_keeps_the_first_reason_and_writes_one_journal_row(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 942007)
    target = await login(taxonomy_client, 942008)
    user_id = target["user"]["id"]
    await ban(taxonomy_client, admin_headers, user_id, "first")

    second = await ban(taxonomy_client, admin_headers, user_id, "second")

    assert second.status_code == 200
    assert second.json()["ban_reason"] == "first"
    state = await user_state(session, user_id)
    assert (state.ban_reason, state.token_version) == ("first", 1)
    assert len(await journal_rows(session)) == 1


async def test_unbanning_a_user_who_is_not_banned_answers_200_without_a_journal_row(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 942009)
    target = await login(taxonomy_client, 942010)

    response = await unban(taxonomy_client, admin_headers, target["user"]["id"])

    assert response.status_code == 200
    assert response.json()["is_banned"] is False
    assert await journal_rows(session) == []


async def test_another_admin_cannot_be_banned(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 942011)
    other, _ = await make_admin(taxonomy_client, session, 942012)

    response = await ban(taxonomy_client, admin_headers, other["user"]["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "cannot_ban_admin"
    assert (await user_state(session, other["user"]["id"])).is_banned is False
    assert await journal_rows(session) == []


async def test_an_admin_cannot_ban_themselves(taxonomy_client, session):
    body, admin_headers = await make_admin(taxonomy_client, session, 942013)

    response = await ban(taxonomy_client, admin_headers, body["user"]["id"])

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "cannot_ban_admin"
    assert await journal_rows(session) == []


@pytest.mark.parametrize("action", [ban, unban])
async def test_an_unknown_user_is_404(taxonomy_client, session, action):
    _, admin_headers = await make_admin(taxonomy_client, session, 942014)

    response = await action(taxonomy_client, admin_headers, 999999)

    assert response.status_code == 404
    assert await journal_rows(session) == []


@pytest.mark.parametrize("action", [ban, unban])
async def test_a_deleted_user_is_404(taxonomy_client, session, action):
    _, admin_headers = await make_admin(taxonomy_client, session, 942015)
    target = await login(taxonomy_client, 942016)
    await set_user_deleted(session, target["user"]["id"])

    response = await action(taxonomy_client, admin_headers, target["user"]["id"])

    assert response.status_code == 404


async def test_the_journal_row_of_a_ban_says_who_what_whom_and_why(taxonomy_client, session):
    admin_body, admin_headers = await make_admin(taxonomy_client, session, 942017)
    target = await login(taxonomy_client, 942018)

    await ban(taxonomy_client, admin_headers, target["user"]["id"], "spam in bio")

    [row] = await journal_rows(session)
    assert row.actor_user_id == admin_body["user"]["id"]
    assert (row.action, row.target_type, row.target_id) == ("ban_user", "user", target["user"]["id"])
    assert (row.reason, row.details) == ("spam in bio", {})


async def test_the_reason_is_trimmed_and_may_be_unicode_and_exactly_the_longest_allowed(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 942019)
    first = await login(taxonomy_client, 942020)
    second = await login(taxonomy_client, 942021)

    trimmed = await ban(taxonomy_client, admin_headers, first["user"]["id"], "   Спам 😀  ")
    longest = await ban(taxonomy_client, admin_headers, second["user"]["id"], "x" * ADMIN_REASON_MAX_LENGTH)

    assert trimmed.status_code == 200
    assert trimmed.json()["ban_reason"] == "Спам 😀"
    assert longest.status_code == 200
    assert len(longest.json()["ban_reason"]) == ADMIN_REASON_MAX_LENGTH


# ---------- the journal endpoint ----------


async def test_the_journal_lists_actions_newest_first_with_total_and_filters(taxonomy_client, session):
    admin_body, admin_headers = await make_admin(taxonomy_client, session, 943001)
    target = await login(taxonomy_client, 943002)
    other = await login(taxonomy_client, 943003)
    await ban(taxonomy_client, admin_headers, target["user"]["id"], "first")
    await unban(taxonomy_client, admin_headers, target["user"]["id"], "second")
    await ban(taxonomy_client, admin_headers, other["user"]["id"], "third")

    everything = (await taxonomy_client.get(ACTIONS_URL, headers=admin_headers)).json()
    of_target = (
        await taxonomy_client.get(
            ACTIONS_URL, params={"target_type": "user", "target_id": target["user"]["id"]}, headers=admin_headers
        )
    ).json()
    of_profiles = (await taxonomy_client.get(ACTIONS_URL, params={"target_type": "profile"}, headers=admin_headers)).json()

    assert everything["total"] == 3
    assert [item["reason"] for item in everything["items"]] == ["third", "second", "first"]
    assert everything["items"][0]["actor_user_id"] == admin_body["user"]["id"]
    assert set(everything["items"][0]) == {
        "id", "actor_user_id", "action", "target_type", "target_id", "reason", "details", "created_at",
    }
    assert [item["reason"] for item in of_target["items"]] == ["second", "first"]
    assert of_target["total"] == 2
    assert of_profiles == {"items": [], "total": 0, "limit": settings.moderation.page_size_default, "offset": 0}


async def test_the_journal_pages_do_not_overlap_and_total_stays_the_same(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 943004)
    for i in range(5):
        target = await login(taxonomy_client, 943100 + i)
        await ban(taxonomy_client, admin_headers, target["user"]["id"], f"reason {i}")

    seen = []
    for offset in (0, 2, 4):
        page = (await taxonomy_client.get(ACTIONS_URL, params={"limit": 2, "offset": offset}, headers=admin_headers)).json()
        assert page["total"] == 5
        seen += [item["id"] for item in page["items"]]

    assert len(seen) == 5 and len(set(seen)) == 5
    assert seen == sorted(seen, reverse=True)


# ---------- hostile input ----------


@pytest.mark.parametrize("user_id", ["0", "-1", str(MAX_INT64 + 1), "abc", "1.5", "%D0%BF"])
@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_a_bad_user_id_in_the_path_is_422(taxonomy_client, session, user_id, action):
    _, admin_headers = await make_admin(taxonomy_client, session, 944001)

    response = await taxonomy_client.post(
        f"/api/v1/admin/users/{user_id}/{action}", json={"reason": "x"}, headers=admin_headers
    )

    assert response.status_code == 422


BAD_BODIES = [
    {},
    {"reason": ""},
    {"reason": "    "},
    {"reason": "x" * (ADMIN_REASON_MAX_LENGTH + 1)},
    {"reason": "a\u0000b"},
    {"reason": 123},
    {"reason": None},
    {"reason": ["spam"]},
    {"reason": "ok", "extra": 1},
]


@pytest.mark.parametrize("body", BAD_BODIES)
@pytest.mark.parametrize("action", ["ban", "unban"])
async def test_a_bad_reason_body_is_422_and_writes_nothing(taxonomy_client, session, body, action):
    _, admin_headers = await make_admin(taxonomy_client, session, 944002)
    target = await login(taxonomy_client, 944003)

    response = await taxonomy_client.post(
        f"/api/v1/admin/users/{target['user']['id']}/{action}", json=body, headers=admin_headers
    )

    assert response.status_code == 422
    assert await journal_rows(session) == []
    assert (await user_state(session, target["user"]["id"])).is_banned is False


@pytest.mark.parametrize("content", [b"", b"not json", b"[1, 2]", b"\xff\xfe"])
async def test_a_body_that_is_not_a_json_object_is_a_4xx(taxonomy_client, session, content):
    _, admin_headers = await make_admin(taxonomy_client, session, 944004)
    target = await login(taxonomy_client, 944005)

    response = await taxonomy_client.post(
        f"/api/v1/admin/users/{target['user']['id']}/ban",
        content=content,
        headers={**admin_headers, "Content-Type": "application/json"},
    )

    # 422 for a body that parses but is wrong, 400 for bytes that are not even text: both are the client's fault.
    assert response.status_code in (400, 422)


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": settings.moderation.page_size_max + 1},
        {"limit": -1},
        {"limit": "abc"},
        {"offset": -1},
        {"offset": settings.moderation.max_offset + 1},
        {"offset": "abc"},
        {"target_id": 1},  # an id without a kind
        {"target_type": "user", "target_id": 0},
        {"target_type": "user", "target_id": MAX_INT64 + 1},
        {"target_type": "user", "target_id": "abc"},
        {"target_type": "bogus"},
        {"target_type": "пользователь"},
    ],
)
async def test_bad_journal_query_parameters_are_422(taxonomy_client, session, params):
    _, admin_headers = await make_admin(taxonomy_client, session, 944006)

    response = await taxonomy_client.get(ACTIONS_URL, params=params, headers=admin_headers)

    assert response.status_code == 422


async def test_the_journal_accepts_the_largest_allowed_paging_values(taxonomy_client, session):
    _, admin_headers = await make_admin(taxonomy_client, session, 944007)

    response = await taxonomy_client.get(
        ACTIONS_URL,
        params={"limit": settings.moderation.page_size_max, "offset": settings.moderation.max_offset},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


@pytest.mark.parametrize("query", ["limit=1&limit=2", "target_type=user&target_type=profile", "offset=1&offset=2"])
async def test_repeated_query_parameters_never_cause_a_5xx(taxonomy_client, session, query):
    _, admin_headers = await make_admin(taxonomy_client, session, 944008)

    response = await taxonomy_client.get(f"{ACTIONS_URL}?{query}", headers=admin_headers)

    assert response.status_code < 500
