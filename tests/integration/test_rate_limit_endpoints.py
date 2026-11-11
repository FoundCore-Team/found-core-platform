"""Per-user rate limits over real HTTP, Postgres and Redis. The autouse fixture in conftest lifts every limit out of
reach; each test lowers the one it checks with `set_limit`."""

import pytest

from app.core.config import RateLimitRule, settings
from app.domain.enums import RateLimitAction
from tests.fixtures.auth import create_user_with_headers
from tests.integration.test_admin_endpoints import ACTIONS_URL, make_admin
from tests.integration.test_feed_endpoints import (  # noqa: F401  (the fixture is used by name)
    BASE as FEED,
    add_candidate,
    client_without_redis,
    fetch,
    make_viewer,
)

SUGGEST = "/api/v1/taxonomy/tags/suggest"
CUSTOM_TAG = "/api/v1/taxonomy/tags/custom"
REPORTS = "/api/v1/reports"
PROFILES = "/api/v1/profiles"


def set_limit(monkeypatch, action: RateLimitAction, limit: int) -> None:
    window_seconds = getattr(settings.rate_limit, action.value).window_seconds
    monkeypatch.setattr(settings.rate_limit, action.value, RateLimitRule(limit=limit, window_seconds=window_seconds))


def assert_rate_limited(response, action: RateLimitAction) -> None:
    assert response.status_code == 429, response.text
    detail = response.json()["detail"]
    assert (detail["code"], detail["action"]) == ("rate_limited", action.value)
    assert 1 <= detail["retry_after_seconds"] <= getattr(settings.rate_limit, action.value).window_seconds
    assert response.headers["Retry-After"] == str(detail["retry_after_seconds"])


async def rate_keys(redis_client) -> list[str]:
    return [key async for key in redis_client.scan_iter("rate:*")]


async def suggest(client, headers, taxonomy_data):
    params = {"q": "py", "category_id": taxonomy_data.tech_category_id}
    return await client.get(SUGGEST, params=params, headers=headers)


@pytest.fixture
async def viewer(session, taxonomy_data):
    return await make_viewer(session, taxonomy_data)


@pytest.fixture
async def candidate(session, taxonomy_data) -> int:
    return await add_candidate(session, taxonomy_data, 2001, user={"username": "anna"})


# ---------- one test per rule ----------


async def test_tag_suggest_is_limited_per_user(session, taxonomy_client, taxonomy_data, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.TAG_SUGGEST, 2)
    _, headers = await create_user_with_headers(session, 7001)
    _, other_headers = await create_user_with_headers(session, 7002)

    statuses = [(await suggest(taxonomy_client, headers, taxonomy_data)).status_code for _ in range(2)]
    refused = await suggest(taxonomy_client, headers, taxonomy_data)

    assert statuses == [200, 200]
    assert_rate_limited(refused, RateLimitAction.TAG_SUGGEST)
    # Another user has a count of their own.
    assert (await suggest(taxonomy_client, other_headers, taxonomy_data)).status_code == 200


async def test_tag_create_has_a_limit_per_minute(session, taxonomy_client, taxonomy_data, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.TAG_CREATE, 2)
    _, headers = await create_user_with_headers(session, 7003)
    category_id = taxonomy_data.tech_category_id

    responses = [
        await taxonomy_client.post(CUSTOM_TAG, json={"title": title, "category_id": category_id}, headers=headers)
        for title in ("Alpha", "Beta", "Gamma")
    ]

    assert [response.status_code for response in responses[:2]] == [200, 200]
    assert_rate_limited(responses[2], RateLimitAction.TAG_CREATE)


async def test_tag_create_has_a_separate_daily_limit(session, taxonomy_client, taxonomy_data, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.TAG_CREATE_DAILY, 1)
    _, headers = await create_user_with_headers(session, 7004)
    category_id = taxonomy_data.tech_category_id

    first = await taxonomy_client.post(CUSTOM_TAG, json={"title": "Alpha", "category_id": category_id}, headers=headers)
    second = await taxonomy_client.post(CUSTOM_TAG, json={"title": "Beta", "category_id": category_id}, headers=headers)

    assert first.status_code == 200
    assert_rate_limited(second, RateLimitAction.TAG_CREATE_DAILY)


async def test_a_repeat_of_an_open_report_is_counted_too(session, taxonomy_client, candidate, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.REPORT_CREATE, 1)
    _, headers = await create_user_with_headers(session, 7005, username=None)

    first = await taxonomy_client.post(REPORTS, json={"profile_id": candidate, "reason": "spam"}, headers=headers)
    repeat = await taxonomy_client.post(REPORTS, json={"profile_id": candidate, "reason": "spam"}, headers=headers)

    assert first.status_code == 201
    assert_rate_limited(repeat, RateLimitAction.REPORT_CREATE)


async def test_creating_and_updating_a_profile_share_one_write_limit_and_a_422_spends_it(
    session, taxonomy_client, monkeypatch
):
    set_limit(monkeypatch, RateLimitAction.PROFILE_WRITE, 1)
    _, headers = await create_user_with_headers(session, 7006)

    # An empty body is a 422, but the limit is checked before the body and has been spent.
    create = await taxonomy_client.post(PROFILES, json={}, headers=headers)
    update = await taxonomy_client.put(f"{PROFILES}/1", json={}, headers=headers)

    assert create.status_code == 422
    assert_rate_limited(update, RateLimitAction.PROFILE_WRITE)


async def test_other_profile_actions_have_no_write_limit(session, taxonomy_client, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.PROFILE_WRITE, 1)
    _, headers = await create_user_with_headers(session, 7007)

    statuses = [(await taxonomy_client.post(f"{PROFILES}/1/pause", headers=headers)).status_code for _ in range(3)]

    assert statuses == [404, 404, 404]


async def test_contact_open_counts_every_call_and_its_429_is_not_the_daily_limit(
    taxonomy_client, viewer, candidate, monkeypatch
):
    set_limit(monkeypatch, RateLimitAction.CONTACT_OPEN, 2)
    url = f"{PROFILES}/{candidate}/contact"

    first = await taxonomy_client.post(url, headers=viewer.headers)
    repeat = await taxonomy_client.post(url, headers=viewer.headers)
    refused = await taxonomy_client.post(url, headers=viewer.headers)

    assert (first.status_code, repeat.status_code) == (201, 200)
    assert_rate_limited(refused, RateLimitAction.CONTACT_OPEN)
    assert refused.json()["detail"]["code"] != "contact_limit_reached"


async def test_feed_requests_are_limited(taxonomy_client, viewer, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.FEED_REQUEST, 2)

    statuses = [(await fetch(taxonomy_client, viewer, mode="discovery")).status_code for _ in range(2)]
    refused = await fetch(taxonomy_client, viewer, mode="discovery")

    assert statuses == [200, 200]
    assert_rate_limited(refused, RateLimitAction.FEED_REQUEST)


async def test_only_building_a_feed_session_counts_against_the_build_limit(
    session, taxonomy_client, taxonomy_data, viewer, redis_client, monkeypatch
):
    set_limit(monkeypatch, RateLimitAction.FEED_SESSION_BUILD, 1)
    for n in range(3):
        await add_candidate(session, taxonomy_data, 2100 + n, cosine=0.9 - 0.1 * n, user={"username": f"u{n}"})

    first = await fetch(taxonomy_client, viewer, mode="discovery", limit=1)
    second = await fetch(taxonomy_client, viewer, mode="discovery", limit=1, cursor=first.json()["next_cursor"])
    third = await fetch(taxonomy_client, viewer, mode="discovery", limit=1)
    # A new set of filters needs a new session, which is a second build.
    other_filters = await fetch(taxonomy_client, viewer, mode="search", role_id=taxonomy_data.design_role_id)

    assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)
    assert_rate_limited(other_filters, RateLimitAction.FEED_SESSION_BUILD)


async def test_a_card_has_a_limit_per_minute_and_a_daily_one(taxonomy_client, viewer, candidate, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.FEED_CARD, 1)
    url = f"{FEED}/cards/{candidate}"

    first = await taxonomy_client.get(url, headers=viewer.headers)
    refused = await taxonomy_client.get(url, headers=viewer.headers)

    assert first.status_code == 200
    assert_rate_limited(refused, RateLimitAction.FEED_CARD)


async def test_the_daily_card_limit_is_its_own_counter(taxonomy_client, viewer, candidate, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.FEED_CARD_DAILY, 1)
    url = f"{FEED}/cards/{candidate}"

    first = await taxonomy_client.get(url, headers=viewer.headers)
    refused = await taxonomy_client.get(url, headers=viewer.headers)

    assert first.status_code == 200
    assert_rate_limited(refused, RateLimitAction.FEED_CARD_DAILY)


async def test_recording_views_is_limited(taxonomy_client, viewer, candidate, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.FEED_VIEW, 1)

    first = await taxonomy_client.post(f"{FEED}/views", json={"profile_id": candidate}, headers=viewer.headers)
    refused = await taxonomy_client.post(f"{FEED}/views", json={"profile_id": candidate}, headers=viewer.headers)

    assert first.status_code == 204
    assert_rate_limited(refused, RateLimitAction.FEED_VIEW)


# ---------- order of the checks and who is limited ----------


async def test_an_invalid_token_never_reaches_a_counter(
    session, taxonomy_client, taxonomy_data, redis_client, monkeypatch
):
    set_limit(monkeypatch, RateLimitAction.TAG_SUGGEST, 1)
    params = {"q": "py", "category_id": taxonomy_data.tech_category_id}
    bad = {"Authorization": "Bearer not-a-token"}

    statuses = [(await taxonomy_client.get(SUGGEST, params=params, headers=bad)).status_code for _ in range(3)]

    assert statuses == [401, 401, 401]
    assert await rate_keys(redis_client) == []


async def test_the_limit_is_checked_before_the_username(session, taxonomy_client, monkeypatch):
    set_limit(monkeypatch, RateLimitAction.CONTACT_OPEN, 1)
    _, headers = await create_user_with_headers(session, 7008, username=None)
    url = f"{PROFILES}/1/contact"

    first = await taxonomy_client.post(url, headers=headers)
    second = await taxonomy_client.post(url, headers=headers)

    assert first.status_code == 409 and first.json()["detail"]["code"] == "username_required"
    assert_rate_limited(second, RateLimitAction.CONTACT_OPEN)


async def test_an_admin_is_limited_on_user_endpoints_like_anyone(
    session, taxonomy_client, taxonomy_data, monkeypatch
):
    set_limit(monkeypatch, RateLimitAction.TAG_SUGGEST, 1)
    _, headers = await make_admin(taxonomy_client, session, 7009)

    first = await suggest(taxonomy_client, headers, taxonomy_data)
    refused = await suggest(taxonomy_client, headers, taxonomy_data)

    assert first.status_code == 200
    assert_rate_limited(refused, RateLimitAction.TAG_SUGGEST)


async def test_admin_routes_do_not_touch_any_counter(session, taxonomy_client, redis_client):
    _, headers = await make_admin(taxonomy_client, session, 7010)

    statuses = [(await taxonomy_client.get(ACTIONS_URL, headers=headers)).status_code for _ in range(3)]

    assert statuses == [200, 200, 200]
    assert await rate_keys(redis_client) == []


async def test_counters_live_under_the_action_user_and_window_and_expire(
    session, taxonomy_client, taxonomy_data, redis_client
):
    user, headers = await create_user_with_headers(session, 7011)

    await suggest(taxonomy_client, headers, taxonomy_data)

    [key] = await rate_keys(redis_client)
    action, user_id, _window = key.removeprefix("rate:").split(":")
    assert (action, int(user_id)) == ("tag_suggest", user.id)
    assert 0 < await redis_client.ttl(key) <= settings.rate_limit.tag_suggest.window_seconds


# ---------- Redis is down ----------


async def test_with_redis_down_the_endpoints_answer_as_before(
    session, client_without_redis, taxonomy_data, monkeypatch
):
    # Every request is over the lowered limit, yet none is refused: a lost limiter lets requests through.
    set_limit(monkeypatch, RateLimitAction.TAG_SUGGEST, 1)
    _, headers = await create_user_with_headers(session, 7012)

    statuses = [(await suggest(client_without_redis, headers, taxonomy_data)).status_code for _ in range(3)]

    assert statuses == [200, 200, 200]
