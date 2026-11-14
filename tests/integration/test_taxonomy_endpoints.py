import asyncio

import pytest
from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.core.config import settings
from app.domain.enums import TagStatus
from app.infrastructure.helpers import db_helper
from app.infrastructure.providers import NoopAnalyticsTracker
from app.main import main_app
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.taxonomy_data import add_tag, count_tag_scopes, count_tags

BASE = "/api/v1/taxonomy"


@pytest.fixture
async def user_a(session):
    return await create_user_with_headers(session, telegram_id=920001)


@pytest.fixture
async def user_b(session):
    return await create_user_with_headers(session, telegram_id=920002)


async def _post_tag(client, headers, title, category_id, role_id=None):
    return await client.post(
        f"{BASE}/tags/custom",
        json={"title": title, "category_id": category_id, "role_id": role_id},
        headers=headers,
    )


class TestAuthIsRequired:
    @pytest.mark.parametrize(
        "method, path",
        [
            ("GET", "/categories"),
            ("GET", "/categories/1/roles"),
            ("GET", "/roles/1/fields"),
            ("GET", "/tags/suggest?q=py&category_id=1"),
            ("POST", "/tags/custom"),
        ],
    )
    async def test_missing_token_returns_401(self, taxonomy_client, method, path):
        response = await taxonomy_client.request(method, f"{BASE}{path}")

        assert response.status_code == 401


class TestReferenceEndpoints:
    async def test_categories_list(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(f"{BASE}/categories", headers=headers)

        assert response.status_code == 200
        assert [c["slug"] for c in response.json()] == ["tech_product", "edu_growth"]

    async def test_categories_second_call_is_served_from_redis(
        self, taxonomy_client, taxonomy_data, user_a, redis_client, session
    ):
        _, headers = user_a
        assert await redis_client.exists("taxonomy:categories") == 0

        first = await taxonomy_client.get(f"{BASE}/categories", headers=headers)
        assert await redis_client.exists("taxonomy:categories") == 1

        # Change Postgres behind the cache's back: a cached response must not notice.
        await session.execute(text("UPDATE categories SET title = 'Changed'"))
        await session.commit()
        second = await taxonomy_client.get(f"{BASE}/categories", headers=headers)

        assert second.json() == first.json()

    async def test_roles_and_fields_are_cached_in_redis_too(
        self, taxonomy_client, taxonomy_data, user_a, redis_client, session
    ):
        t = taxonomy_data
        _, headers = user_a
        roles_url = f"{BASE}/categories/{t.tech_category_id}/roles"
        fields_url = f"{BASE}/roles/{t.engineering_role_id}/fields"

        roles_first = await taxonomy_client.get(roles_url, headers=headers)
        fields_first = await taxonomy_client.get(fields_url, headers=headers)
        assert await redis_client.exists(f"taxonomy:roles:{t.tech_category_id}") == 1
        assert await redis_client.exists(f"taxonomy:role_fields:{t.engineering_role_id}") == 1

        await session.execute(text("UPDATE roles SET title = 'Changed'"))
        await session.execute(text("UPDATE role_fields SET label = 'Changed'"))
        await session.commit()

        assert (await taxonomy_client.get(roles_url, headers=headers)).json() == roles_first.json()
        assert (await taxonomy_client.get(fields_url, headers=headers)).json() == fields_first.json()

    @pytest.mark.parametrize("garbage", ["{not json", '[{"id": 1}]', "null", '"text"'])
    async def test_unreadable_cache_values_are_replaced_by_fresh_ones(
        self, taxonomy_client, taxonomy_data, user_a, redis_client, garbage
    ):
        t = taxonomy_data
        _, headers = user_a
        cases = [
            ("taxonomy:categories", f"{BASE}/categories"),
            (f"taxonomy:roles:{t.tech_category_id}", f"{BASE}/categories/{t.tech_category_id}/roles"),
            (f"taxonomy:role_fields:{t.engineering_role_id}", f"{BASE}/roles/{t.engineering_role_id}/fields"),
        ]
        for key, _ in cases:
            await redis_client.set(key, garbage)

        for key, url in cases:
            response = await taxonomy_client.get(url, headers=headers)

            assert response.status_code == 200, url
            assert await redis_client.get(key) != garbage, key  # healed in place
            assert (await taxonomy_client.get(url, headers=headers)).json() == response.json(), url

    async def test_outdated_cache_shape_is_replaced_by_the_current_one(
        self, taxonomy_client, taxonomy_data, user_a, redis_client
    ):
        t = taxonomy_data
        _, headers = user_a
        key = f"taxonomy:role_fields:{t.engineering_role_id}"
        # What an older release might have stored: a field that no longer exists and an enum value that was renamed.
        await redis_client.set(key, '[{"id": 1, "role_id": 1, "key": "grade", "legacy": true, "field_type": "dropdown"}]')

        response = await taxonomy_client.get(f"{BASE}/roles/{t.engineering_role_id}/fields", headers=headers)

        assert response.status_code == 200
        assert [f["key"] for f in response.json()] == ["grade"]
        assert response.json()[0]["field_type"] == "select"

    async def test_endpoints_still_work_when_redis_is_down(self, taxonomy_client, taxonomy_data, user_a):
        t = taxonomy_data
        _, headers = user_a
        dead_redis = Redis.from_url(
            "redis://localhost:1/0", decode_responses=True, socket_connect_timeout=0.5, socket_timeout=0.5
        )

        async def _container_without_redis(
            session: AsyncSession = Depends(db_helper.session_getter),
        ) -> Container:
            return Container(session=session, redis_client=dead_redis, analytics_tracker=NoopAnalyticsTracker())

        main_app.dependency_overrides[get_container] = _container_without_redis
        try:
            categories = await taxonomy_client.get(f"{BASE}/categories", headers=headers)
            roles = await taxonomy_client.get(f"{BASE}/categories/{t.tech_category_id}/roles", headers=headers)
            fields = await taxonomy_client.get(f"{BASE}/roles/{t.engineering_role_id}/fields", headers=headers)
        finally:
            await dead_redis.aclose()

        assert categories.status_code == 200 and len(categories.json()) == 2
        assert roles.status_code == 200 and len(roles.json()) == 2
        assert fields.status_code == 200 and len(fields.json()) == 1

    async def test_roles_by_category(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(
            f"{BASE}/categories/{taxonomy_data.tech_category_id}/roles", headers=headers
        )

        assert response.status_code == 200
        assert [r["slug"] for r in response.json()] == ["engineering", "design"]

    async def test_roles_for_unknown_category_returns_404(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(f"{BASE}/categories/999999/roles", headers=headers)

        assert response.status_code == 404

    async def test_role_fields(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(
            f"{BASE}/roles/{taxonomy_data.engineering_role_id}/fields", headers=headers
        )

        assert response.status_code == 200
        body = response.json()
        assert [f["key"] for f in body] == ["grade"]
        assert body[0]["field_type"] == "select"
        assert body[0]["options"][0] == {"value": "junior", "label": "Junior"}

    async def test_role_fields_for_unknown_role_returns_404(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(f"{BASE}/roles/999999/fields", headers=headers)

        assert response.status_code == 404


class TestSuggest:
    async def test_returns_matching_tags_in_scope(self, taxonomy_client, taxonomy_data, session, user_a):
        t = taxonomy_data
        _, headers = user_a
        await add_tag(session, "Python", scopes=[(t.tech_category_id, t.engineering_role_id)])

        response = await taxonomy_client.get(
            f"{BASE}/tags/suggest",
            params={"q": "pyth", "category_id": t.tech_category_id, "role_id": t.engineering_role_id},
            headers=headers,
        )

        assert response.status_code == 200
        assert [x["title"] for x in response.json()] == ["Python"]

    async def test_other_users_pending_tag_is_hidden_but_own_is_shown(
        self, taxonomy_client, taxonomy_data, session, user_a, user_b
    ):
        t = taxonomy_data
        author, author_headers = user_a
        _, other_headers = user_b
        await add_tag(
            session,
            "Zig",
            status=TagStatus.PENDING,
            created_by=author.id,
            scopes=[(t.tech_category_id, None)],
        )
        params = {"q": "zig", "category_id": t.tech_category_id}

        own = await taxonomy_client.get(f"{BASE}/tags/suggest", params=params, headers=author_headers)
        other = await taxonomy_client.get(f"{BASE}/tags/suggest", params=params, headers=other_headers)

        assert [x["title"] for x in own.json()] == ["Zig"]
        assert other.json() == []

    async def test_unknown_category_returns_404(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(
            f"{BASE}/tags/suggest", params={"q": "py", "category_id": 999999}, headers=headers
        )

        assert response.status_code == 404

    async def test_role_from_another_category_returns_404(self, taxonomy_client, taxonomy_data, user_a):
        t = taxonomy_data
        _, headers = user_a

        response = await taxonomy_client.get(
            f"{BASE}/tags/suggest",
            params={"q": "py", "category_id": t.tech_category_id, "role_id": t.study_mate_role_id},
            headers=headers,
        )

        assert response.status_code == 404

    async def test_without_role_returns_only_category_wide_tags(
        self, taxonomy_client, taxonomy_data, session, user_a
    ):
        t = taxonomy_data
        _, headers = user_a
        await add_tag(session, "Teamwork", scopes=[(t.tech_category_id, None)])
        await add_tag(session, "Terraform", scopes=[(t.tech_category_id, t.engineering_role_id)])

        response = await taxonomy_client.get(
            f"{BASE}/tags/suggest", params={"q": "te", "category_id": t.tech_category_id}, headers=headers
        )

        assert [x["title"] for x in response.json()] == ["Teamwork"]

    async def test_result_count_is_capped_by_the_configured_suggest_limit(
        self, taxonomy_client, taxonomy_data, session, user_a
    ):
        t = taxonomy_data
        _, headers = user_a
        limit = settings.taxonomy.suggest_limit
        for i in range(limit + 5):
            await add_tag(session, f"Lib {i}", scopes=[(t.tech_category_id, None)])

        response = await taxonomy_client.get(
            f"{BASE}/tags/suggest", params={"q": "lib", "category_id": t.tech_category_id}, headers=headers
        )

        assert len(response.json()) == limit

    async def test_missing_q_returns_422(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(
            f"{BASE}/tags/suggest", params={"category_id": taxonomy_data.tech_category_id}, headers=headers
        )

        assert response.status_code == 422


class TestCreateCustomTag:
    async def test_new_tag_is_created_pending(self, taxonomy_client, taxonomy_data, session, user_a):
        t = taxonomy_data
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "Node.js", t.tech_category_id, t.engineering_role_id)

        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Node.js"
        assert body["status"] == "pending"
        assert body["usage_count"] == 0
        assert await count_tags(session) == 1
        assert await count_tag_scopes(session) == 1

    async def test_response_does_not_expose_slug_or_normalized_title(
        self, taxonomy_client, taxonomy_data, user_a
    ):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "Node.js", taxonomy_data.tech_category_id)

        assert set(response.json()) == {"id", "title", "status", "usage_count"}

    async def test_same_title_in_other_case_from_another_user_returns_the_same_tag(
        self, taxonomy_client, taxonomy_data, session, user_a, user_b
    ):
        t = taxonomy_data
        _, headers_a = user_a
        _, headers_b = user_b
        first = await _post_tag(taxonomy_client, headers_a, "Node.js", t.tech_category_id)

        second = await _post_tag(taxonomy_client, headers_b, "NODE.JS", t.tech_category_id)

        assert second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["title"] == "Node.js"  # canonical title replaces what B typed
        assert await count_tags(session) == 1

    async def test_existing_seeded_tag_is_returned_for_padded_lowercase_input(
        self, taxonomy_client, taxonomy_data, session, user_a
    ):
        t = taxonomy_data
        _, headers = user_a
        seeded = await add_tag(session, "Python", scopes=[(t.tech_category_id, t.engineering_role_id)])

        response = await _post_tag(
            taxonomy_client, headers, "  python  ", t.tech_category_id, t.engineering_role_id
        )

        assert response.json()["id"] == seeded.id
        assert response.json()["title"] == "Python"
        assert response.json()["status"] == "approved"

    async def test_c_family_and_nodejs_variants_are_distinct_tags(
        self, taxonomy_client, taxonomy_data, session, user_a
    ):
        _, headers = user_a
        titles = ["C", "C#", "C++", "Node.js", "NodeJS", "Node Js"]

        ids = []
        for title in titles:
            response = await _post_tag(taxonomy_client, headers, title, taxonomy_data.tech_category_id)
            assert response.status_code == 200, title
            ids.append(response.json()["id"])

        assert len(set(ids)) == len(titles)
        assert await count_tags(session) == len(titles)

    @pytest.mark.parametrize("title", ["<script>", "a@b", "x" * 65, "   "])
    async def test_invalid_title_returns_400(self, taxonomy_client, taxonomy_data, user_a, title):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, title, taxonomy_data.tech_category_id)

        assert response.status_code == 400

    async def test_empty_title_is_rejected_by_request_validation(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "", taxonomy_data.tech_category_id)

        assert response.status_code == 422

    async def test_role_id_may_be_omitted_for_a_category_wide_tag(
        self, taxonomy_client, taxonomy_data, session, user_a
    ):
        _, headers = user_a

        response = await taxonomy_client.post(
            f"{BASE}/tags/custom",
            json={"title": "Teamwork", "category_id": taxonomy_data.tech_category_id},
            headers=headers,
        )

        assert response.status_code == 200
        role_id = (await session.execute(text("SELECT role_id FROM tag_scopes"))).scalar_one()
        assert role_id is None

    async def test_missing_category_id_returns_422(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.post(f"{BASE}/tags/custom", json={"title": "Python"}, headers=headers)

        assert response.status_code == 422

    async def test_unknown_category_returns_404(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "Python", 999999)

        assert response.status_code == 404

    async def test_unknown_role_returns_404(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "Python", taxonomy_data.tech_category_id, 999999)

        assert response.status_code == 404

    async def test_role_from_another_category_returns_404(self, taxonomy_client, taxonomy_data, session, user_a):
        t = taxonomy_data
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "Python", t.tech_category_id, t.study_mate_role_id)

        assert response.status_code == 404
        assert await count_tags(session) == 0

    async def test_rejected_tag_returns_409_and_adds_no_scope(
        self, taxonomy_client, taxonomy_data, session, user_a
    ):
        t = taxonomy_data
        _, headers = user_a
        await add_tag(session, "Spam", status=TagStatus.REJECTED, scopes=[(t.edu_category_id, None)])

        response = await _post_tag(taxonomy_client, headers, "spam", t.tech_category_id)

        assert response.status_code == 409
        assert await count_tag_scopes(session) == 1  # only the pre-existing one

    async def test_repeating_the_same_request_creates_one_tag_and_one_scope(
        self, taxonomy_client, taxonomy_data, session, user_a
    ):
        t = taxonomy_data
        _, headers = user_a

        first = await _post_tag(taxonomy_client, headers, "Rust", t.tech_category_id, t.engineering_role_id)
        second = await _post_tag(taxonomy_client, headers, "Rust", t.tech_category_id, t.engineering_role_id)

        assert first.json() == second.json()
        assert await count_tags(session) == 1
        assert await count_tag_scopes(session) == 1

    async def test_existing_tag_in_another_category_gets_a_second_scope(
        self, taxonomy_client, taxonomy_data, session, user_a, user_b
    ):
        t = taxonomy_data
        _, headers_a = user_a
        _, headers_b = user_b
        first = await _post_tag(taxonomy_client, headers_a, "English", t.tech_category_id, t.design_role_id)

        second = await _post_tag(taxonomy_client, headers_b, "english", t.edu_category_id, t.study_mate_role_id)

        assert second.json()["id"] == first.json()["id"]
        assert await count_tags(session) == 1
        assert await count_tag_scopes(session) == 2

    async def test_parallel_requests_for_the_same_new_title_produce_one_tag(
        self, taxonomy_client, taxonomy_data, session, user_a, user_b
    ):
        t = taxonomy_data
        _, headers_a = user_a
        _, headers_b = user_b

        responses = await asyncio.gather(
            *[
                _post_tag(taxonomy_client, headers, title, t.tech_category_id, t.engineering_role_id)
                for headers, title in [(headers_a, "Elixir"), (headers_b, "ELIXIR"), (headers_a, "elixir")]
            ]
        )

        assert [r.status_code for r in responses] == [200, 200, 200]
        assert len({r.json()["id"] for r in responses}) == 1
        assert await count_tags(session) == 1
        assert await count_tag_scopes(session) == 1


class TestInputBounds:
    @pytest.mark.parametrize("bad_id", [2**63, 0, -1])
    @pytest.mark.parametrize(
        "path",
        [
            "/categories/{id}/roles",
            "/roles/{id}/fields",
            "/tags/suggest?q=py&category_id={id}",
            "/tags/suggest?q=py&category_id=1&role_id={id}",
        ],
    )
    async def test_out_of_range_id_in_get_requests_returns_422(
        self, taxonomy_client, taxonomy_data, user_a, path, bad_id
    ):
        _, headers = user_a

        response = await taxonomy_client.get(f"{BASE}{path.format(id=bad_id)}", headers=headers)

        assert response.status_code == 422

    @pytest.mark.parametrize("bad_id", [2**63, 0, -1])
    @pytest.mark.parametrize("field", ["category_id", "role_id"])
    async def test_out_of_range_id_in_tag_body_returns_422(self, taxonomy_client, taxonomy_data, user_a, field, bad_id):
        _, headers = user_a
        body = {"title": "Python", "category_id": taxonomy_data.tech_category_id}
        body[field] = bad_id

        response = await taxonomy_client.post(f"{BASE}/tags/custom", json=body, headers=headers)

        assert response.status_code == 422

    async def test_largest_valid_id_is_a_404_not_a_server_error(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(f"{BASE}/categories/{2**63 - 1}/roles", headers=headers)

        assert response.status_code == 404

    async def test_title_of_exactly_the_max_length_is_accepted(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "a" * 64, taxonomy_data.tech_category_id)

        assert response.status_code == 200

    async def test_title_one_character_over_the_max_length_returns_400(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "a" * 65, taxonomy_data.tech_category_id)

        assert response.status_code == 400

    async def test_huge_title_is_rejected_by_request_validation(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "a" * 10_000, taxonomy_data.tech_category_id)

        assert response.status_code == 422

    async def test_huge_search_query_is_rejected_by_request_validation(self, taxonomy_client, taxonomy_data, user_a):
        _, headers = user_a

        response = await taxonomy_client.get(
            f"{BASE}/tags/suggest",
            params={"q": "a" * 10_000, "category_id": taxonomy_data.tech_category_id},
            headers=headers,
        )

        assert response.status_code == 422

    @pytest.mark.parametrize("title", ["...", "+++", "-", "_", "#", "./"])
    async def test_punctuation_only_title_returns_400(self, taxonomy_client, taxonomy_data, session, user_a, title):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, title, taxonomy_data.tech_category_id)

        assert response.status_code == 400
        assert await count_tags(session) == 0

    @pytest.mark.parametrize("title", ["\t", "\n", " \t\n ", " "])
    async def test_whitespace_only_title_returns_400(self, taxonomy_client, taxonomy_data, session, user_a, title):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, title, taxonomy_data.tech_category_id)

        assert response.status_code == 400
        assert await count_tags(session) == 0

    async def test_newline_inside_a_title_is_collapsed_to_a_single_space(
        self, taxonomy_client, taxonomy_data, user_a
    ):
        _, headers = user_a

        response = await _post_tag(taxonomy_client, headers, "a\nb", taxonomy_data.tech_category_id)

        assert response.status_code == 200
        assert response.json()["title"] == "a b"
