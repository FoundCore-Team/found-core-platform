"""POST /reports over real HTTP, Postgres and Redis: who may report what, the one-open-report rule, and hostile input
(always a 4xx, never a 5xx)."""

import pytest
from sqlalchemy import text

from app.domain.constants import MAX_INT64, REPORT_COMMENT_MAX_LENGTH
from app.domain.enums import ProfileStatus
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.db_ops import set_user_banned
from tests.fixtures.feed_data import add_profile
from tests.integration.test_feed_endpoints import add_candidate

URL = "/api/v1/reports"


async def report(client, headers, profile_id, reason="spam", **extra):
    return await client.post(URL, json={"profile_id": profile_id, "reason": reason, **extra}, headers=headers)


async def report_rows(session) -> list:
    session.expire_all()
    return list(
        (
            await session.execute(
                text("SELECT id, reporter_user_id, profile_id, reason, comment, status, resolved_at FROM reports ORDER BY id")
            )
        ).all()
    )


@pytest.fixture
async def reporter(session):
    # Deliberately no username and no profile: reporting must not need either.
    user, headers = await create_user_with_headers(session, 8001, username=None)
    return user, headers


@pytest.fixture
async def target(session, taxonomy_data) -> int:
    return await add_candidate(session, taxonomy_data, 8002, user={"username": "target"})


# ---------- the happy path ----------


async def test_no_token_is_401(taxonomy_client, target):
    assert (await taxonomy_client.post(URL, json={"profile_id": target, "reason": "spam"})).status_code == 401


async def test_a_new_report_is_201_with_the_report_in_the_body(taxonomy_client, session, reporter, target):
    user, headers = reporter

    response = await report(taxonomy_client, headers, target, "fake", comment="  looks like a bot  ")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "profile_id", "reason", "comment", "created_at"}
    assert (body["profile_id"], body["reason"], body["comment"]) == (target, "fake", "looks like a bot")
    [row] = await report_rows(session)
    assert (row.id, row.reporter_user_id, row.profile_id, row.status) == (body["id"], user.id, target, "open")
    assert row.resolved_at is None


async def test_a_user_without_a_username_and_without_a_profile_can_report(taxonomy_client, reporter, target):
    user, headers = reporter

    assert user.username is None
    assert (await report(taxonomy_client, headers, target)).status_code == 201


async def test_every_reason_is_accepted(taxonomy_client, session, taxonomy_data, reporter):
    _, headers = reporter
    reasons = ["spam", "fake", "offensive", "inappropriate_contact", "other"]

    for i, reason in enumerate(reasons):
        profile_id = await add_candidate(session, taxonomy_data, 8100 + i, user={"username": f"t{i}"})
        assert (await report(taxonomy_client, headers, profile_id, reason)).status_code == 201

    assert [row.reason for row in await report_rows(session)] == reasons


async def test_a_repeat_while_the_report_is_open_is_200_with_the_same_report_and_nothing_new(
    taxonomy_client, session, reporter, target
):
    _, headers = reporter
    first = await report(taxonomy_client, headers, target, "spam", comment="first")

    second = await report(taxonomy_client, headers, target, "offensive", comment="second")

    assert (first.status_code, second.status_code) == (201, 200)
    assert second.json() == first.json()  # reason and comment are not overwritten
    assert len(await report_rows(session)) == 1


async def test_after_the_report_is_closed_a_new_one_is_201_and_the_old_one_stays(
    taxonomy_client, session, reporter, target
):
    _, headers = reporter
    first = await report(taxonomy_client, headers, target)
    await session.execute(text("UPDATE reports SET status = 'dismissed', resolved_at = now()"))
    await session.commit()

    second = await report(taxonomy_client, headers, target)

    assert second.status_code == 201
    assert second.json()["id"] != first.json()["id"]
    assert [row.status for row in await report_rows(session)] == ["dismissed", "open"]


async def test_two_different_users_each_get_their_own_report(taxonomy_client, session, reporter, target):
    _, headers = reporter
    _, other_headers = await create_user_with_headers(session, 8003)

    first = await report(taxonomy_client, headers, target)
    second = await report(taxonomy_client, other_headers, target)

    assert (first.status_code, second.status_code) == (201, 201)
    assert len(await report_rows(session)) == 2


# ---------- what cannot be reported: always the same 404 ----------


async def test_a_missing_profile_is_404(taxonomy_client, reporter):
    _, headers = reporter

    response = await report(taxonomy_client, headers, 987_654)

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found."


async def test_your_own_profile_is_404_like_a_missing_one(taxonomy_client, session, taxonomy_data, reporter):
    user, headers = reporter
    own = await add_profile(session, user.id, taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)

    response = await report(taxonomy_client, headers, own)

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found."


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(status=ProfileStatus.PAUSED),
        dict(status=ProfileStatus.HIDDEN_BY_ADMIN),
        dict(user={"is_banned": True}),
        dict(user={"username": None}),
        dict(user={"username": ""}),
        dict(user={"deleted": True}),
    ],
    ids=["paused", "hidden by admin", "owner banned", "owner without username", "owner empty username", "owner deleted"],
)
async def test_a_profile_that_is_not_visible_is_404_and_leaves_no_report(
    taxonomy_client, session, taxonomy_data, reporter, kwargs
):
    _, headers = reporter
    profile_id = await add_candidate(session, taxonomy_data, 8200, **{"user": {"username": "x"}, **kwargs})

    response = await report(taxonomy_client, headers, profile_id)

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found."
    assert await report_rows(session) == []


# ---------- the reporter ----------


async def test_a_banned_reporter_gets_the_403_with_the_ban_reason(taxonomy_client, session, reporter, target):
    user, headers = reporter
    await set_user_banned(session, user.id, "no more reports for you")

    response = await report(taxonomy_client, headers, target)

    assert response.status_code == 403
    assert response.json()["detail"] == "no more reports for you"
    assert await report_rows(session) == []


# ---------- the comment ----------


async def test_a_blank_comment_means_no_comment(taxonomy_client, reporter, target):
    _, headers = reporter

    response = await report(taxonomy_client, headers, target, comment="   ")

    assert response.status_code == 201
    assert response.json()["comment"] is None


async def test_no_comment_and_null_comment_are_both_fine(taxonomy_client, session, taxonomy_data, reporter, target):
    _, headers = reporter
    other = await add_candidate(session, taxonomy_data, 8300, user={"username": "other"})

    without = await report(taxonomy_client, headers, target)
    null = await report(taxonomy_client, headers, other, comment=None)

    assert (without.status_code, null.status_code) == (201, 201)
    assert (without.json()["comment"], null.json()["comment"]) == (None, None)


async def test_a_comment_of_exactly_the_longest_allowed_length_is_stored_whole(taxonomy_client, session, reporter, target):
    _, headers = reporter
    comment = "ж" * REPORT_COMMENT_MAX_LENGTH

    response = await report(taxonomy_client, headers, target, comment=comment)

    assert response.status_code == 201
    assert (await report_rows(session))[0].comment == comment


async def test_unicode_in_the_comment_survives(taxonomy_client, reporter, target):
    _, headers = reporter

    response = await report(taxonomy_client, headers, target, comment="Спам 🚫 и ссылки")

    assert response.json()["comment"] == "Спам 🚫 и ссылки"


# ---------- hostile input: 4xx, never 5xx ----------


@pytest.mark.parametrize(
    "payload",
    [
        {"profile_id": 0, "reason": "spam"},
        {"profile_id": -1, "reason": "spam"},
        {"profile_id": MAX_INT64 + 1, "reason": "spam"},
        {"profile_id": 10**30, "reason": "spam"},
        {"profile_id": "abc", "reason": "spam"},
        {"profile_id": None, "reason": "spam"},
        {"profile_id": [1], "reason": "spam"},
        {"profile_id": 1.5, "reason": "spam"},
        {"reason": "spam"},
        {"profile_id": 1},
        {"profile_id": 1, "reason": "unknown"},
        {"profile_id": 1, "reason": "SPAM"},
        {"profile_id": 1, "reason": ""},
        {"profile_id": 1, "reason": 123},
        {"profile_id": 1, "reason": None},
        {"profile_id": 1, "reason": ["spam"]},
        {"profile_id": 1, "reason": "спам"},
        {"profile_id": 1, "reason": "spam", "comment": "x" * (REPORT_COMMENT_MAX_LENGTH + 1)},
        {"profile_id": 1, "reason": "spam", "comment": "x" * 200_000},
        {"profile_id": 1, "reason": "spam", "comment": "bad\x00comment"},
        {"profile_id": 1, "reason": "spam", "comment": 5},
        {"profile_id": 1, "reason": "spam", "comment": ["a"]},
        {"profile_id": 1, "reason": "spam", "comment": {"a": 1}},
        {"profile_id": 1, "reason": "spam", "status": "resolved"},
        {"profile_id": 1, "reason": "spam", "reporter_user_id": 5},
        {},
        [],
        [{"profile_id": 1, "reason": "spam"}],
        "spam",
        None,
    ],
)
async def test_hostile_bodies_are_422_and_write_nothing(taxonomy_client, session, reporter, target, payload):
    _, headers = reporter

    response = await taxonomy_client.post(URL, json=payload, headers=headers)

    assert response.status_code == 422
    assert await report_rows(session) == []


async def test_a_body_that_is_not_json_is_422(taxonomy_client, reporter):
    _, headers = reporter

    response = await taxonomy_client.post(URL, content=b"{not json", headers={**headers, "Content-Type": "application/json"})

    assert response.status_code == 422


async def test_a_missing_body_is_422(taxonomy_client, reporter):
    _, headers = reporter

    assert (await taxonomy_client.post(URL, headers=headers)).status_code == 422


async def test_a_nul_in_the_comment_never_reaches_postgres(taxonomy_client, session, reporter, target):
    _, headers = reporter

    response = await report(taxonomy_client, headers, target, comment="a\x00b")

    assert response.status_code == 422
    assert await report_rows(session) == []
