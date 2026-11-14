"""The feed over real HTTP, Postgres and Redis. Vectors have a known cosine, so the order is predictable."""

from dataclasses import dataclass

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import Depends
from httpx import ASGITransport
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.enums import ProfileStatus, TagStatus
from app.infrastructure.helpers import db_helper
from app.infrastructure.providers import NoopAnalyticsTracker
from app.main import main_app
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.db_ops import set_user_banned
from tests.fixtures.feed_data import add_profile, add_user, add_vector, basis_vector, vector_with_cosine
from tests.fixtures.taxonomy_data import TaxonomyIds, add_tag

BASE = "/api/v1/feed"
PUBLIC_PROFILE_KEYS = {
    "id",
    "first_name",
    "photo_url",
    "category_id",
    "role_id",
    "country_code",
    "timezone",
    "bio",
    "goals_description",
    "extra_attributes",
    "tags",
}


@dataclass
class Viewer:
    user_id: int
    profile_id: int
    headers: dict[str, str]


async def make_viewer(
    session,
    taxonomy: TaxonomyIds,
    telegram_id: int = 9000,
    *,
    username: str | None = "viewer",
    with_vector: bool = True,
    timezone_id: str = "Europe/Moscow",
    tag_ids: tuple[int, ...] = (),
) -> Viewer:
    user, headers = await create_user_with_headers(session, telegram_id, username=username)
    profile_id = await add_profile(
        session,
        user.id,
        taxonomy.tech_category_id,
        taxonomy.engineering_role_id,
        timezone_id=timezone_id,
        tag_ids=tag_ids,
    )
    await session.execute(
        text("UPDATE users SET active_profile_id = :profile_id WHERE id = :user_id"),
        {"profile_id": profile_id, "user_id": user.id},
    )
    await session.commit()
    if with_vector:
        await add_vector(session, profile_id, basis_vector())
    return Viewer(user.id, profile_id, headers)


async def add_candidate(
    session, taxonomy: TaxonomyIds, telegram_id: int, *, role: str = "engineering", cosine: float | None = None, **kwargs
) -> int:
    role_id = {
        "engineering": taxonomy.engineering_role_id,
        "design": taxonomy.design_role_id,
        "study_mate": taxonomy.study_mate_role_id,
    }[role]
    category_id = taxonomy.edu_category_id if role == "study_mate" else taxonomy.tech_category_id
    user_id = await add_user(session, telegram_id, **kwargs.pop("user", {}))
    profile_id = await add_profile(session, user_id, category_id, role_id, **kwargs)
    if cosine is not None:
        await add_vector(session, profile_id, vector_with_cosine(cosine))
    return profile_id


async def fetch(client, viewer: Viewer, **params) -> httpx.Response:
    return await client.get(BASE, params=params, headers=viewer.headers)


async def read_all_pages(client, viewer: Viewer, limit: int = 2, **params) -> list[dict]:
    pages, cursor = [], None
    while True:
        query = {**params, "limit": limit, **({"cursor": cursor} if cursor else {})}
        response = await fetch(client, viewer, **query)
        assert response.status_code == 200, response.text
        pages.append(response.json())
        cursor = pages[-1]["next_cursor"]
        if cursor is None:
            return pages


def profile_ids(pages: list[dict]) -> list[int]:
    return [item["profile"]["id"] for page in pages for item in page["items"]]


@pytest.fixture
async def viewer(session, taxonomy_data) -> Viewer:
    return await make_viewer(session, taxonomy_data)


@pytest.fixture
async def client_without_redis():
    dead = Redis.from_url("redis://localhost:1/0", decode_responses=True, socket_connect_timeout=0.3, socket_timeout=0.3)

    async def _container(session=Depends(db_helper.session_getter)) -> Container:
        return Container(session=session, redis_client=dead, analytics_tracker=NoopAnalyticsTracker())

    main_app.dependency_overrides[get_container] = _container
    try:
        async with LifespanManager(main_app):
            async with httpx.AsyncClient(transport=ASGITransport(app=main_app), base_url="http://test") as client:
                yield client
    finally:
        main_app.dependency_overrides.pop(get_container, None)
        await dead.aclose()


async def call_all_three(client, headers) -> list[httpx.Response]:
    return [
        await client.get(BASE, params={"mode": "discovery"}, headers=headers),
        await client.get(f"{BASE}/cards/1", headers=headers),
        await client.post(f"{BASE}/views", json={"profile_id": 1}, headers=headers),
    ]


# ---------- who may use the feed ----------


@pytest.mark.parametrize(
    ("method", "path"), [("GET", ""), ("GET", "/cards/1"), ("POST", "/views")]
)
async def test_a_missing_token_is_401(taxonomy_client, method, path):
    assert (await taxonomy_client.request(method, f"{BASE}{path}")).status_code == 401


async def test_a_user_without_a_username_gets_a_machine_readable_409_everywhere(session, taxonomy_client, taxonomy_data):
    _, headers = await create_user_with_headers(session, 9100, username=None)

    for response in await call_all_three(taxonomy_client, headers):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "username_required"


async def test_the_username_is_checked_before_the_active_profile(session, taxonomy_client):
    # No profile at all and no username: the username error wins.
    _, headers = await create_user_with_headers(session, 9101, username="")

    for response in await call_all_three(taxonomy_client, headers):
        assert response.json()["detail"]["code"] == "username_required"


async def test_a_user_without_an_active_profile_gets_409_no_active_profile(session, taxonomy_client):
    _, headers = await create_user_with_headers(session, 9102, username="someone")

    for response in await call_all_three(taxonomy_client, headers):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "no_active_profile"


async def test_a_paused_active_profile_is_not_enough(session, taxonomy_client, viewer):
    await session.execute(text("UPDATE profiles SET status = 'paused' WHERE id = :id"), {"id": viewer.profile_id})
    await session.commit()

    for response in await call_all_three(taxonomy_client, viewer.headers):
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "no_active_profile"


async def test_a_banned_viewer_is_refused_with_403(session, taxonomy_client, viewer):
    await set_user_banned(session, viewer.user_id, "spam")

    for response in await call_all_three(taxonomy_client, viewer.headers):
        assert response.status_code == 403


# ---------- discovery ----------


async def test_discovery_returns_public_cards_best_match_first(session, taxonomy_client, taxonomy_data, viewer):
    best = await add_candidate(session, taxonomy_data, 10, cosine=0.9)
    middle = await add_candidate(session, taxonomy_data, 11, cosine=0.5)
    worst = await add_candidate(session, taxonomy_data, 12, cosine=0.1)
    await add_candidate(session, taxonomy_data, 13, role="study_mate", cosine=0.99)  # other category

    response = await fetch(taxonomy_client, viewer, mode="discovery")

    assert response.status_code == 200
    body = response.json()
    assert [item["profile"]["id"] for item in body["items"]] == [best, middle, worst]
    scores = [item["match_score"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 100 for s in scores)
    assert {item["match_type"] for item in body["items"]} == {"DISCOVERY"}
    assert [item["is_viewed"] for item in body["items"]] == [False, False, False]
    assert body["has_more"] is False and body["end_reason"] == "END_OF_FEED" and body["next_cursor"] is None
    for item in body["items"]:
        # No username and no last name: the contact opens in stage 7.
        assert set(item["profile"]) == PUBLIC_PROFILE_KEYS


async def test_a_card_explains_itself_with_reasons_that_carry_the_tag_titles(session, taxonomy_client, taxonomy_data):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    python = await add_tag(session, "Python", scopes=scope)
    viewer = await make_viewer(session, taxonomy_data, tag_ids=(python.id,))
    await add_candidate(session, taxonomy_data, 10, cosine=0.8, tag_ids=(python.id,))

    [item] = (await fetch(taxonomy_client, viewer, mode="discovery")).json()["items"]

    assert item["reasons"][0] == {"code": "shared_tags", "tags": [{"id": python.id, "title": "Python"}]}
    codes = [reason["code"] for reason in item["reasons"]]
    assert codes == ["shared_tags", "same_role", "same_country", "same_timezone", "similar_bio", "recently_active"]


async def test_discovery_never_shows_the_viewers_own_profiles_or_invisible_ones(
    session, taxonomy_client, taxonomy_data, viewer
):
    visible = await add_candidate(session, taxonomy_data, 10)
    await add_profile(session, viewer.user_id, taxonomy_data.tech_category_id, taxonomy_data.design_role_id)
    await add_candidate(session, taxonomy_data, 11, status=ProfileStatus.PAUSED)
    await add_candidate(session, taxonomy_data, 12, user={"is_banned": True})
    await add_candidate(session, taxonomy_data, 13, user={"username": None})

    body = (await fetch(taxonomy_client, viewer, mode="discovery")).json()

    assert [item["profile"]["id"] for item in body["items"]] == [visible]


async def test_an_empty_feed_says_nobody_yet_instead_of_showing_random_people(session, taxonomy_client, viewer, taxonomy_data):
    await add_candidate(session, taxonomy_data, 10, role="study_mate", cosine=0.9)

    response = await fetch(taxonomy_client, viewer, mode="discovery")

    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None, "has_more": False, "end_reason": "NO_CANDIDATES_YET"}


# ---------- paging ----------


async def make_five_candidates(session, taxonomy) -> list[int]:
    return [await add_candidate(session, taxonomy, 100 + n, cosine=0.9 - 0.15 * n) for n in range(5)]


async def test_pages_follow_each_other_without_repeats_and_the_last_one_ends_the_feed(
    session, taxonomy_client, taxonomy_data, viewer
):
    created = await make_five_candidates(session, taxonomy_data)

    pages = await read_all_pages(taxonomy_client, viewer, limit=2, mode="discovery")

    assert [len(page["items"]) for page in pages] == [2, 2, 1]
    assert profile_ids(pages) == created
    assert [page["has_more"] for page in pages] == [True, True, False]
    assert pages[-1]["end_reason"] == "END_OF_FEED" and pages[0]["end_reason"] is None


async def test_the_order_is_stable_between_requests_and_after_the_session_is_lost(
    session, taxonomy_client, redis_client, taxonomy_data, viewer
):
    await make_five_candidates(session, taxonomy_data)

    first = profile_ids(await read_all_pages(taxonomy_client, viewer, mode="discovery"))
    assert len(await redis_client.keys("feed:session:*")) == 1
    again = profile_ids(await read_all_pages(taxonomy_client, viewer, mode="discovery"))
    await redis_client.flushdb()
    rebuilt = profile_ids(await read_all_pages(taxonomy_client, viewer, mode="discovery"))

    assert first == again == rebuilt
    assert len(set(first)) == 5


async def test_the_feed_works_while_redis_is_down(session, client_without_redis, taxonomy_data, viewer):
    created = await make_five_candidates(session, taxonomy_data)

    pages = await read_all_pages(client_without_redis, viewer, limit=2, mode="discovery")

    assert profile_ids(pages) == created


async def test_scrolling_survives_the_viewer_editing_their_profile(session, taxonomy_client, taxonomy_data, viewer):
    created = await make_five_candidates(session, taxonomy_data)
    first = (await fetch(taxonomy_client, viewer, mode="discovery", limit=2)).json()
    await session.execute(
        text("UPDATE profiles SET updated_at = updated_at + interval '1 minute' WHERE id = :id"),
        {"id": viewer.profile_id},
    )
    await session.commit()

    second = await fetch(taxonomy_client, viewer, mode="discovery", limit=2, cursor=first["next_cursor"])

    assert second.status_code == 200
    assert profile_ids([first, second.json()]) == created[:4]


async def test_scrolling_survives_the_viewers_own_vector_arriving(session, taxonomy_client, taxonomy_data):
    viewer = await make_viewer(session, taxonomy_data, with_vector=False)
    created = await make_five_candidates(session, taxonomy_data)
    first = (await fetch(taxonomy_client, viewer, mode="discovery", limit=2)).json()
    await add_vector(session, viewer.profile_id, basis_vector())

    second = await fetch(taxonomy_client, viewer, mode="discovery", limit=2, cursor=first["next_cursor"])

    assert second.status_code == 200
    assert profile_ids([first, second.json()]) == created[:4]


# ---------- views ----------


async def test_a_viewed_card_keeps_its_place_and_turns_grey(session, taxonomy_client, taxonomy_data, viewer):
    created = await make_five_candidates(session, taxonomy_data)
    before = (await fetch(taxonomy_client, viewer, mode="discovery")).json()

    opened = await taxonomy_client.post(f"{BASE}/views", json={"profile_id": created[1]}, headers=viewer.headers)
    after = (await fetch(taxonomy_client, viewer, mode="discovery")).json()

    assert opened.status_code == 204 and opened.content == b""
    assert [i["profile"]["id"] for i in after["items"]] == [i["profile"]["id"] for i in before["items"]]
    assert [i["is_viewed"] for i in after["items"]] == [False, True, False, False, False]


async def test_a_listing_alone_marks_nothing_as_viewed(session, taxonomy_client, taxonomy_data, viewer):
    await make_five_candidates(session, taxonomy_data)

    await fetch(taxonomy_client, viewer, mode="discovery")
    await fetch(taxonomy_client, viewer, mode="discovery")

    assert (await session.execute(text("SELECT count(*) FROM profile_views"))).scalar_one() == 0


async def test_opening_a_profile_twice_stores_one_view(session, taxonomy_client, taxonomy_data, viewer):
    candidate = await add_candidate(session, taxonomy_data, 10)

    for _ in range(2):
        response = await taxonomy_client.post(f"{BASE}/views", json={"profile_id": candidate}, headers=viewer.headers)
        assert response.status_code == 204

    assert (await session.execute(text("SELECT count(*) FROM profile_views"))).scalar_one() == 1


async def test_a_view_belongs_to_the_active_profile_at_the_time(session, taxonomy_client, taxonomy_data, viewer):
    candidate = await add_candidate(session, taxonomy_data, 10)

    await taxonomy_client.post(f"{BASE}/views", json={"profile_id": candidate}, headers=viewer.headers)

    row = (await session.execute(text("SELECT viewer_profile_id, viewed_profile_id FROM profile_views"))).one()
    assert (row.viewer_profile_id, row.viewed_profile_id) == (viewer.profile_id, candidate)


async def test_profiles_that_are_not_visible_cannot_be_viewed_or_opened(session, taxonomy_client, taxonomy_data, viewer):
    hidden = [
        viewer.profile_id,
        await add_candidate(session, taxonomy_data, 10, status=ProfileStatus.PAUSED),
        await add_candidate(session, taxonomy_data, 11, user={"is_banned": True}),
        await add_candidate(session, taxonomy_data, 12, user={"username": None}),
        999_999,
    ]

    for profile_id in hidden:
        view = await taxonomy_client.post(f"{BASE}/views", json={"profile_id": profile_id}, headers=viewer.headers)
        card = await taxonomy_client.get(f"{BASE}/cards/{profile_id}", headers=viewer.headers)
        assert (view.status_code, card.status_code) == (404, 404), profile_id

    assert (await session.execute(text("SELECT count(*) FROM profile_views"))).scalar_one() == 0


# ---------- card by id ----------


async def test_a_card_by_id_works_for_any_category_and_has_no_tier(session, taxonomy_client, taxonomy_data, viewer):
    other_category = await add_candidate(session, taxonomy_data, 10, role="study_mate", cosine=0.6)

    response = await taxonomy_client.get(f"{BASE}/cards/{other_category}", headers=viewer.headers)

    assert response.status_code == 200
    item = response.json()
    assert item["match_type"] is None and item["is_viewed"] is False
    assert 0 <= item["match_score"] <= 100
    assert set(item["profile"]) == PUBLIC_PROFILE_KEYS


# ---------- search ----------


async def test_search_falls_through_exact_partial_and_bio_tiers(session, taxonomy_client, taxonomy_data, viewer):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    go = await add_tag(session, "Go", scopes=scope)
    eng = taxonomy_data.engineering_role_id
    exact = await add_candidate(session, taxonomy_data, 10, extra_attributes={"grade": "junior"}, tag_ids=(go.id,))
    two_of_three = await add_candidate(session, taxonomy_data, 11, extra_attributes={"grade": "middle"}, tag_ids=(go.id,))
    one_of_three = await add_candidate(session, taxonomy_data, 12, role="design", tag_ids=(go.id,))
    similar_designer = await add_candidate(session, taxonomy_data, 13, role="design", cosine=0.8)
    similar_other_category = await add_candidate(session, taxonomy_data, 14, role="study_mate", cosine=0.6)
    await add_candidate(session, taxonomy_data, 15, role="design", cosine=0.1)  # too far, no filter matched

    response = await fetch(
        taxonomy_client, viewer, mode="search", role_id=eng, attr="grade:junior", tag_ids=go.id
    )

    assert response.status_code == 200
    items = response.json()["items"]
    assert [i["profile"]["id"] for i in items] == [
        exact,
        two_of_three,
        one_of_three,
        similar_designer,
        similar_other_category,
    ]
    assert [i["match_type"] for i in items] == [
        "EXACT_MATCH",
        "PARTIAL_MATCH",
        "PARTIAL_MATCH",
        "RECOMMENDED_BY_BIO",
        "RECOMMENDED_BY_BIO",
    ]
    assert items[0]["reasons"][0] == {"code": "matched_filters", "matched": ["role", "attr:grade", "tags"], "missed": []}
    assert items[1]["reasons"][0] == {"code": "matched_filters", "matched": ["role", "tags"], "missed": ["attr:grade"]}
    assert items[2]["reasons"][0] == {"code": "matched_filters", "matched": ["tags"], "missed": ["role", "attr:grade"]}
    assert all(r["code"] != "matched_filters" for r in items[3]["reasons"])


async def test_search_with_nothing_found_is_an_empty_screen_not_random_people(
    session, taxonomy_client, taxonomy_data, viewer
):
    await add_candidate(session, taxonomy_data, 10)  # Russian, no vector: neither a match nor similar

    response = await fetch(taxonomy_client, viewer, mode="search", country_code="de")

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["end_reason"] == "NO_CANDIDATES_YET"


async def test_the_timezone_filter_keeps_every_zone_with_the_viewers_offset(
    session, taxonomy_client, taxonomy_data, viewer
):
    moscow = await add_candidate(session, taxonomy_data, 10, timezone_id="Europe/Moscow")
    kirov = await add_candidate(session, taxonomy_data, 11, timezone_id="Europe/Kirov")
    await add_candidate(session, taxonomy_data, 12, country_code="DE", timezone_id="Europe/Berlin")

    response = await fetch(taxonomy_client, viewer, mode="search", same_timezone="true")

    items = response.json()["items"]
    assert sorted(i["profile"]["id"] for i in items) == sorted([moscow, kirov])
    assert {i["match_type"] for i in items} == {"EXACT_MATCH"}


async def test_search_pages_do_not_repeat_across_tiers(session, taxonomy_client, taxonomy_data, viewer):
    for n in range(4):
        await add_candidate(session, taxonomy_data, 100 + n, cosine=0.9 - 0.1 * n)
    await add_candidate(session, taxonomy_data, 200, role="study_mate", cosine=0.7)

    pages = await read_all_pages(
        taxonomy_client, viewer, limit=2, mode="search", role_id=taxonomy_data.engineering_role_id
    )

    ids = profile_ids(pages)
    assert len(ids) == len(set(ids)) == 5
    assert pages[-1]["end_reason"] == "END_OF_FEED"


@pytest.mark.parametrize(
    "params",
    [
        {"role_id": "study_mate"},
        {"role_id": 999_999},
        {"attr": "grade:junior"},
        {"role_id": "engineering", "attr": "nocolon"},
        {"role_id": "engineering", "attr": "grade:"},
        {"role_id": "engineering", "attr": ["grade:junior", "grade:middle"]},
        {"role_id": "engineering", "attr": "unknown:x"},
        {"role_id": "engineering", "attr": "grade:lead"},
        {"country_code": "ZZ"},
        {"country_code": "\x00"},
        {"tag_ids": 999_999},
        {"tag_ids": "pending"},
        {"tag_ids": "rejected"},
    ],
)
async def test_an_invalid_filter_is_a_400_with_a_code(session, taxonomy_client, taxonomy_data, viewer, params):
    scope = [(taxonomy_data.tech_category_id, taxonomy_data.engineering_role_id)]
    pending = await add_tag(session, "Zig", status=TagStatus.PENDING, scopes=scope)
    rejected = await add_tag(session, "Spam", status=TagStatus.REJECTED, scopes=scope)
    names = {"study_mate": taxonomy_data.study_mate_role_id, "engineering": taxonomy_data.engineering_role_id}
    ids = {"pending": pending.id, "rejected": rejected.id}
    params = {k: names.get(v, ids.get(v, v)) if isinstance(v, str) else v for k, v in params.items()}

    response = await fetch(taxonomy_client, viewer, mode="search", **params)

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "invalid_filter"


async def test_search_needs_a_filter_and_discovery_refuses_them(taxonomy_client, taxonomy_data, viewer):
    assert (await fetch(taxonomy_client, viewer, mode="search")).status_code == 422
    assert (await fetch(taxonomy_client, viewer, mode="discovery", country_code="RU")).status_code == 422
    assert (await fetch(taxonomy_client, viewer, mode="discovery", same_timezone="true")).status_code == 422


# ---------- cursor ----------


async def test_a_cursor_from_other_filters_is_refused(session, taxonomy_client, taxonomy_data, viewer):
    await make_five_candidates(session, taxonomy_data)
    discovery = (await fetch(taxonomy_client, viewer, mode="discovery", limit=2)).json()

    response = await fetch(
        taxonomy_client,
        viewer,
        mode="search",
        role_id=taxonomy_data.engineering_role_id,
        cursor=discovery["next_cursor"],
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_cursor"


@pytest.mark.parametrize("cursor", ["garbage", "!!!", "é", "e30", "WzFd", "a" * 400])
async def test_a_forged_cursor_is_a_400(taxonomy_client, viewer, cursor):
    response = await fetch(taxonomy_client, viewer, mode="discovery", cursor=cursor)

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_cursor"


async def test_a_cursor_of_somebody_else_cannot_open_their_session(session, taxonomy_client, taxonomy_data, viewer):
    await make_five_candidates(session, taxonomy_data)
    mine = (await fetch(taxonomy_client, viewer, mode="discovery", limit=2)).json()
    other = await make_viewer(session, taxonomy_data, 9001, username="other")

    response = await fetch(taxonomy_client, other, mode="discovery", limit=2, cursor=mine["next_cursor"])

    # Their params hash differs (it contains the profile id), so the cursor is not even looked at.
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_cursor"


# ---------- hostile input ----------


@pytest.mark.parametrize(
    "params",
    [
        {"mode": "bogus"},
        {},
        {"mode": "discovery", "limit": 0},
        {"mode": "discovery", "limit": -1},
        {"mode": "discovery", "limit": 51},
        {"mode": "discovery", "limit": "abc"},
        {"mode": "search", "role_id": 0},
        {"mode": "search", "role_id": 2**63},
        {"mode": "search", "role_id": "x"},
        {"mode": "search", "tag_ids": 0},
        {"mode": "search", "tag_ids": "abc"},
        {"mode": "search", "tag_ids": [1] * 16},
        {"mode": "search", "attr": [f"k{n}:v" for n in range(11)]},
        {"mode": "search", "attr": "k:" + "v" * 300},
        {"mode": "search", "country_code": "x" * 17},
        {"mode": "search", "same_timezone": "maybe"},
        {"mode": "discovery", "cursor": "x" * 513},
    ],
)
async def test_hostile_feed_parameters_are_422(taxonomy_client, viewer, params):
    response = await taxonomy_client.get(BASE, params=params, headers=viewer.headers)

    assert response.status_code == 422, response.text


@pytest.mark.parametrize("profile_id", ["0", "-1", str(2**63), "abc", "1.5"])
async def test_hostile_card_ids_are_422(taxonomy_client, viewer, profile_id):
    assert (await taxonomy_client.get(f"{BASE}/cards/{profile_id}", headers=viewer.headers)).status_code == 422


@pytest.mark.parametrize(
    "body",
    [{}, {"profile_id": 0}, {"profile_id": -1}, {"profile_id": 2**63}, {"profile_id": "x"}, {"profile_id": None},
     {"profile_id": 1, "extra": 1}, [], "text"],
)
async def test_hostile_view_bodies_are_422(taxonomy_client, viewer, body):
    response = await taxonomy_client.post(f"{BASE}/views", json=body, headers=viewer.headers)

    assert response.status_code == 422, response.text
