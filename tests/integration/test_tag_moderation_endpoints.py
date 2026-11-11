"""Tag moderation over real HTTP, Postgres and Redis: the queue, approve / reject / merge, what each does to profiles,
embeddings, the custom-tag endpoint, suggest and the feed filter, the journal, and hostile input (always a 4xx, never a
5xx)."""

import pytest
from sqlalchemy import text

from app.application.use_cases import MergeTagUseCase, RejectTagUseCase
from app.domain.constants import ADMIN_REASON_MAX_LENGTH, MAX_INT64
from app.domain.enums import TagStatus
from app.infrastructure.repositories import SqlAlchemyProfileEmbeddingRepository
from app.worker.tasks import run_embed_profile
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.feed_data import add_profile
from tests.fixtures.taxonomy_data import add_tag
from tests.integration.test_admin_endpoints import journal_rows, make_admin
from tests.integration.test_admin_races import BrokenJournal
from tests.integration.test_feed_endpoints import fetch, make_viewer

TAGS = "/api/v1/admin/tags"
PROFILES = "/api/v1/profiles"


@pytest.fixture
async def admin(taxonomy_client, session):
    _, headers = await make_admin(taxonomy_client, session, 7000)
    return headers


async def pending(client, headers, **params):
    return await client.get(f"{TAGS}/pending", params=params, headers=headers)


async def approve(client, headers, tag_id, reason="looks fine"):
    return await client.post(f"{TAGS}/{tag_id}/approve", json={"reason": reason}, headers=headers)


async def reject(client, headers, tag_id, reason="spam"):
    return await client.post(f"{TAGS}/{tag_id}/reject", json={"reason": reason}, headers=headers)


async def merge(client, headers, tag_id, target_id, reason="same thing"):
    return await client.post(
        f"{TAGS}/{tag_id}/merge", json={"target_tag_id": target_id, "reason": reason}, headers=headers
    )


async def engineering_tags(session, t, count: int, prefix: str = "filler") -> list[int]:
    scope = [(t.tech_category_id, t.engineering_role_id)]
    return [(await add_tag(session, f"{prefix}-{i}", scopes=scope)).id for i in range(count)]


async def create_profile(client, headers, t, tag_ids: list[int]) -> dict:
    response = await client.post(
        PROFILES,
        json={
            "category_id": t.tech_category_id,
            "role_id": t.engineering_role_id,
            "country_code": "DE",
            "timezone": "Europe/Berlin",
            "bio": "b" * 200,
            "goals_description": "g" * 200,
            "tag_ids": tag_ids,
            "extra_attributes": {"grade": "junior"},
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def update_profile(client, headers, profile_id: int, t, tag_ids: list[int]):
    return await client.put(
        f"{PROFILES}/{profile_id}",
        json={
            "country_code": "DE",
            "timezone": "Europe/Berlin",
            "bio": "b" * 200,
            "goals_description": "g" * 200,
            "tag_ids": tag_ids,
            "extra_attributes": {"grade": "junior"},
        },
        headers=headers,
    )


async def profile_tag_ids(session, profile_id: int) -> list[int]:
    rows = await session.execute(
        text("SELECT tag_id FROM profile_tags WHERE profile_id = :id ORDER BY tag_id"), {"id": profile_id}
    )
    return [r[0] for r in rows]


async def tag_row(session, tag_id: int):
    return (
        await session.execute(
            text("SELECT status, merged_into_tag_id, usage_count FROM tags WHERE id = :id"), {"id": tag_id}
        )
    ).one()


async def scopes_of(session, tag_id: int) -> set[tuple[int, int | None]]:
    rows = await session.execute(
        text("SELECT category_id, role_id FROM tag_scopes WHERE tag_id = :id"), {"id": tag_id}
    )
    return {(r[0], r[1]) for r in rows}


async def embedding_row(session, profile_id: int):
    return (
        await session.execute(
            text("SELECT status, source_updated_at FROM profile_embeddings WHERE profile_id = :id"),
            {"id": profile_id},
        )
    ).one_or_none()


async def profile_updated_at(session, profile_id: int):
    return (
        await session.execute(text("SELECT updated_at FROM profiles WHERE id = :id"), {"id": profile_id})
    ).scalar_one()


# ---------- the queue ----------


class TestQueue:
    async def test_lists_pending_tags_oldest_first_with_author_scopes_and_profile_count(
        self, taxonomy_client, session, taxonomy_data, admin
    ):
        t = taxonomy_data
        author, author_headers = await create_user_with_headers(session, 7100, username="author")
        first = await add_tag(
            session,
            "NodeJS",
            status=TagStatus.PENDING,
            created_by=author.id,
            scopes=[(t.tech_category_id, t.engineering_role_id), (t.tech_category_id, None)],
        )
        second = await add_tag(session, "Zig", status=TagStatus.PENDING, scopes=[(t.edu_category_id, None)])
        await add_tag(session, "Approved")
        await add_tag(session, "Rejected", status=TagStatus.REJECTED)
        # The first tag is older (same created_at would fall back to the id).
        await session.execute(text("UPDATE tags SET created_at = now() - interval '1 hour' WHERE id = :id"), {"id": first.id})
        await session.commit()
        fillers = await engineering_tags(session, t, 4)
        await create_profile(taxonomy_client, author_headers, t, [first.id, *fillers])

        response = await pending(taxonomy_client, admin)

        assert response.status_code == 200
        body = response.json()
        assert (body["total"], body["limit"], body["offset"]) == (2, 50, 0)
        assert [item["id"] for item in body["items"]] == [first.id, second.id]
        item = body["items"][0]
        assert set(item) == {
            "id",
            "title",
            "normalized_title",
            "created_at",
            "usage_count",
            "profiles_count",
            "creator",
            "scopes",
        }
        assert (item["title"], item["normalized_title"], item["usage_count"], item["profiles_count"]) == (
            "NodeJS",
            "nodejs",
            1,
            1,
        )
        assert item["creator"] == {"user_id": author.id, "first_name": author.first_name, "username": "author"}
        assert item["scopes"] == [
            {"category_id": t.tech_category_id, "role_id": None},
            {"category_id": t.tech_category_id, "role_id": t.engineering_role_id},
        ]
        assert body["items"][1]["creator"] is None

    async def test_pages_do_not_overlap_and_total_is_the_whole_queue(self, taxonomy_client, session, taxonomy_data, admin):
        ids = [(await add_tag(session, f"T{i}", status=TagStatus.PENDING)).id for i in range(5)]

        pages = [await pending(taxonomy_client, admin, limit=2, offset=offset) for offset in (0, 2, 4)]

        assert all(p.status_code == 200 for p in pages)
        assert [i["id"] for p in pages for i in p.json()["items"]] == ids
        assert {p.json()["total"] for p in pages} == {5}

    async def test_an_empty_queue(self, taxonomy_client, admin):
        response = await pending(taxonomy_client, admin)

        assert response.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}


# ---------- approve ----------


class TestApprove:
    async def test_approves_a_pending_tag_and_writes_one_journal_row(self, taxonomy_client, session, taxonomy_data, admin):
        tag = await add_tag(session, "Rust", status=TagStatus.PENDING)

        response = await approve(taxonomy_client, admin, tag.id, reason="  fine  ")

        assert response.status_code == 200
        assert response.json() == {
            "tag": {
                "id": tag.id,
                "title": "Rust",
                "normalized_title": "rust",
                "status": "approved",
                "merged_into_tag_id": None,
                "usage_count": 0,
            },
            "affected_profiles": 0,
        }
        (row,) = await journal_rows(session)
        assert (row.action, row.target_type, row.target_id, row.reason) == ("approve_tag", "tag", tag.id, "fine")
        assert row.details == {"previous_status": "pending"}
        assert (await tag_row(session, tag.id)).status == "approved"

    async def test_approving_twice_is_200_with_one_journal_row(self, taxonomy_client, session, taxonomy_data, admin):
        tag = await add_tag(session, "Rust", status=TagStatus.PENDING)
        await approve(taxonomy_client, admin, tag.id)

        again = await approve(taxonomy_client, admin, tag.id)

        assert again.status_code == 200
        assert len(await journal_rows(session)) == 1

    @pytest.mark.parametrize("status", [TagStatus.REJECTED, TagStatus.MERGED])
    async def test_rejected_and_merged_tags_are_a_conflict(self, taxonomy_client, session, taxonomy_data, admin, status):
        target = await add_tag(session, "Target")
        tag = await add_tag(session, "Other", status=TagStatus.PENDING)
        if status is TagStatus.MERGED:
            await session.execute(
                text("UPDATE tags SET status = 'merged', merged_into_tag_id = :t WHERE id = :id"),
                {"t": target.id, "id": tag.id},
            )
        else:
            await session.execute(text("UPDATE tags SET status = 'rejected' WHERE id = :id"), {"id": tag.id})
        await session.commit()

        response = await approve(taxonomy_client, admin, tag.id)

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "tag_status_conflict"
        assert response.json()["detail"]["status"] == status.value
        assert await journal_rows(session) == []

    async def test_unknown_tag_is_404(self, taxonomy_client, admin):
        assert (await approve(taxonomy_client, admin, 999999)).status_code == 404


# ---------- reject ----------


class TestReject:
    async def test_removes_the_tag_from_profiles_bumps_them_and_queues_the_embedding(
        self, taxonomy_client, session, taxonomy_data, admin
    ):
        t = taxonomy_data
        _, owner_headers = await create_user_with_headers(session, 7200)
        spam = await add_tag(
            session, "Spam", status=TagStatus.PENDING, scopes=[(t.tech_category_id, t.engineering_role_id)]
        )
        fillers = await engineering_tags(session, t, 5)
        profile = await create_profile(taxonomy_client, owner_headers, t, [spam.id, *fillers[:4]])
        untouched_owner, untouched_headers = await create_user_with_headers(session, 7201)
        untouched = await create_profile(taxonomy_client, untouched_headers, t, fillers)
        before = await profile_updated_at(session, profile["id"])
        untouched_before = await profile_updated_at(session, untouched["id"])

        response = await reject(taxonomy_client, admin, spam.id)

        assert response.status_code == 200
        assert response.json()["tag"]["status"] == "rejected"
        assert response.json()["affected_profiles"] == 1
        assert await profile_tag_ids(session, profile["id"]) == sorted(fillers[:4])
        after = await profile_updated_at(session, profile["id"])
        assert after > before
        row = await embedding_row(session, profile["id"])
        assert (row.status, row.source_updated_at) == ("pending", after)
        assert await profile_updated_at(session, untouched["id"]) == untouched_before
        (journal,) = await journal_rows(session)
        assert (journal.action, journal.target_id) == ("reject_tag", spam.id)
        assert journal.details == {"previous_status": "pending", "detached_profiles": 1}
        # The owner now sees four tags: the minimum is only enforced on save.
        mine = await taxonomy_client.get(f"{PROFILES}/me", headers=owner_headers)
        assert len(mine.json()[0]["tags"]) == 4

    async def test_the_vector_is_recomputed_and_a_worker_that_started_before_loses_its_write(
        self, taxonomy_client, session, taxonomy_data, admin
    ):
        t = taxonomy_data
        _, owner_headers = await create_user_with_headers(session, 7210)
        spam = await add_tag(
            session, "Spam", status=TagStatus.PENDING, scopes=[(t.tech_category_id, t.engineering_role_id)]
        )
        fillers = await engineering_tags(session, t, 4)
        profile = await create_profile(taxonomy_client, owner_headers, t, [spam.id, *fillers])
        await run_embed_profile(profile["id"], is_last_attempt=False)
        old_source = (await embedding_row(session, profile["id"])).source_updated_at
        old_hash = (await session.execute(text("SELECT input_hash FROM profile_embeddings WHERE profile_id = :id"), {"id": profile["id"]})).scalar_one()

        await reject(taxonomy_client, admin, spam.id)

        # A worker that read the profile before the moderation holds the old source value: its write is refused.
        stale = await SqlAlchemyProfileEmbeddingRepository(session).save(
            profile["id"], [0.0] * 1536, "stale-hash", "stub", old_source
        )
        assert stale is False
        await session.rollback()
        # A fresh worker sees the text without the tag and finishes the job.
        await run_embed_profile(profile["id"], is_last_attempt=False)
        row = await embedding_row(session, profile["id"])
        assert row.status == "ready"
        new_hash = (await session.execute(text("SELECT input_hash FROM profile_embeddings WHERE profile_id = :id"), {"id": profile["id"]})).scalar_one()
        assert new_hash != old_hash

    async def test_entering_a_rejected_title_again_or_saving_it_is_409(self, taxonomy_client, session, taxonomy_data, admin):
        t = taxonomy_data
        _, owner_headers = await create_user_with_headers(session, 7220)
        spam = await add_tag(session, "Spam", scopes=[(t.tech_category_id, t.engineering_role_id)])
        fillers = await engineering_tags(session, t, 4)
        profile = await create_profile(taxonomy_client, owner_headers, t, [spam.id, *fillers])
        await reject(taxonomy_client, admin, spam.id)

        custom = await taxonomy_client.post(
            "/api/v1/taxonomy/tags/custom",
            json={"title": "spam", "category_id": t.tech_category_id, "role_id": t.engineering_role_id},
            headers=owner_headers,
        )
        saved = await update_profile(taxonomy_client, owner_headers, profile["id"], t, [spam.id, *fillers])

        assert custom.status_code == 409
        assert saved.status_code == 409

    async def test_an_approved_tag_can_be_rejected_and_a_repeat_is_a_no_op(self, taxonomy_client, session, taxonomy_data, admin):
        tag = await add_tag(session, "Spam")
        first = await reject(taxonomy_client, admin, tag.id)

        again = await reject(taxonomy_client, admin, tag.id)

        assert (first.status_code, again.status_code) == (200, 200)
        rows = await journal_rows(session)
        assert len(rows) == 1 and rows[0].details["previous_status"] == "approved"

    async def test_a_merged_tag_cannot_be_rejected(self, taxonomy_client, session, taxonomy_data, admin):
        source = await add_tag(session, "NodeJS", status=TagStatus.PENDING)
        target = await add_tag(session, "Node.js")
        assert (await merge(taxonomy_client, admin, source.id, target.id)).status_code == 200

        response = await reject(taxonomy_client, admin, source.id)

        assert response.status_code == 409
        assert response.json()["detail"]["status"] == "merged"

    async def test_unknown_tag_is_404(self, taxonomy_client, admin):
        assert (await reject(taxonomy_client, admin, 999999)).status_code == 404


# ---------- merge ----------


class TestMerge:
    async def _setup(self, session, t):
        """NodeJS (pending, also scoped to the whole edu category) is on profile 1 and 2; Node.js (approved) is on
        profile 2 as well and on profile 3."""
        engineering = (t.tech_category_id, t.engineering_role_id)
        alias = await add_tag(
            session, "NodeJS", status=TagStatus.PENDING, usage_count=2, scopes=[engineering, (t.edu_category_id, None)]
        )
        target = await add_tag(session, "Node.js", usage_count=5, scopes=[engineering])
        fillers = await engineering_tags(session, t, 8)
        return alias, target, fillers

    async def test_moves_profiles_scopes_and_usage_and_leaves_an_alias(self, taxonomy_client, session, taxonomy_data, admin):
        t = taxonomy_data
        alias, target, f = await self._setup(session, t)
        owners = [await create_user_with_headers(session, 7300 + i) for i in range(3)]
        p1 = await create_profile(taxonomy_client, owners[0][1], t, [alias.id, *f[:4]])
        p2 = await create_profile(taxonomy_client, owners[1][1], t, [alias.id, target.id, *f[:3]])
        p3 = await create_profile(taxonomy_client, owners[2][1], t, [target.id, *f[3:7]])
        usage_before = (await tag_row(session, target.id)).usage_count
        alias_usage = (await tag_row(session, alias.id)).usage_count
        p3_before = await profile_updated_at(session, p3["id"])

        response = await merge(taxonomy_client, admin, alias.id, target.id)

        assert response.status_code == 200
        body = response.json()
        assert body["affected_profiles"] == 2
        assert body["tag"]["status"] == "merged" and body["tag"]["merged_into_tag_id"] == target.id
        assert await profile_tag_ids(session, p1["id"]) == sorted([target.id, *f[:4]])
        assert await profile_tag_ids(session, p2["id"]) == sorted([target.id, *f[:3]])  # had both: one link
        assert await profile_tag_ids(session, p3["id"]) == sorted([target.id, *f[3:7]])
        assert (await tag_row(session, target.id)).usage_count == usage_before + alias_usage
        assert (await tag_row(session, alias.id)).usage_count == alias_usage
        assert await scopes_of(session, target.id) == {
            (t.tech_category_id, t.engineering_role_id),
            (t.edu_category_id, None),
        }
        assert {(await embedding_row(session, p["id"])).status for p in (p1, p2)} == {"pending"}
        assert await profile_updated_at(session, p3["id"]) == p3_before
        (row,) = await journal_rows(session)
        assert (row.action, row.target_type, row.target_id) == ("merge_tag", "tag", alias.id)
        assert row.details == {"target_tag_id": target.id, "moved_profiles": 2}
        # The invariant everything else relies on: no profile keeps a link to an alias or a rejected tag.
        leftovers = await session.execute(
            text(
                "SELECT count(*) FROM profile_tags pt JOIN tags t ON t.id = pt.tag_id "
                "WHERE t.status IN ('merged', 'rejected')"
            )
        )
        assert leftovers.scalar_one() == 0

    async def test_a_moved_profile_can_still_be_saved(self, taxonomy_client, session, taxonomy_data, admin):
        t = taxonomy_data
        alias, target, f = await self._setup(session, t)
        _, headers = await create_user_with_headers(session, 7310)
        profile = await create_profile(taxonomy_client, headers, t, [alias.id, *f[:4]])
        await merge(taxonomy_client, admin, alias.id, target.id)

        saved = await update_profile(taxonomy_client, headers, profile["id"], t, [target.id, *f[:4]])

        assert saved.status_code == 200, saved.text

    async def test_a_save_with_the_alias_id_is_400(self, taxonomy_client, session, taxonomy_data, admin):
        t = taxonomy_data
        alias, target, f = await self._setup(session, t)
        _, headers = await create_user_with_headers(session, 7320)
        profile = await create_profile(taxonomy_client, headers, t, [target.id, *f[:4]])
        await merge(taxonomy_client, admin, alias.id, target.id)

        update = await update_profile(taxonomy_client, headers, profile["id"], t, [alias.id, *f[:4]])
        create = await taxonomy_client.post(
            PROFILES,
            json={
                "category_id": t.tech_category_id,
                "role_id": t.design_role_id,
                "country_code": "DE",
                "timezone": "Europe/Berlin",
                "bio": "b" * 200,
                "goals_description": "g" * 200,
                "tag_ids": [alias.id, *f[:4]],
                "extra_attributes": {},
            },
            headers=headers,
        )

        assert update.status_code == 400
        assert create.status_code in (400, 409)  # out of the design role's scope as well; never a 5xx

    async def test_custom_tag_with_the_alias_title_returns_the_target_and_adds_the_scope_to_it(
        self, taxonomy_client, session, taxonomy_data, admin
    ):
        t = taxonomy_data
        alias, target, _ = await self._setup(session, t)
        await merge(taxonomy_client, admin, alias.id, target.id)
        _, headers = await create_user_with_headers(session, 7330)
        tags_before = (await session.execute(text("SELECT count(*) FROM tags"))).scalar_one()

        response = await taxonomy_client.post(
            "/api/v1/taxonomy/tags/custom",
            json={"title": "  NODEJS ", "category_id": t.tech_category_id, "role_id": t.design_role_id},
            headers=headers,
        )

        assert response.status_code == 200 or response.status_code == 201
        assert response.json()["id"] == target.id and response.json()["title"] == "Node.js"
        assert (t.tech_category_id, t.design_role_id) in await scopes_of(session, target.id)
        assert (await session.execute(text("SELECT count(*) FROM tags"))).scalar_one() == tags_before

    async def test_suggest_never_offers_the_alias(self, taxonomy_client, session, taxonomy_data, admin):
        t = taxonomy_data
        alias, target, _ = await self._setup(session, t)
        author, author_headers = await create_user_with_headers(session, 7340)
        await session.execute(text("UPDATE tags SET created_by_user_id = :u WHERE id = :id"), {"u": author.id, "id": alias.id})
        await session.commit()
        await merge(taxonomy_client, admin, alias.id, target.id)

        response = await taxonomy_client.get(
            "/api/v1/taxonomy/tags/suggest",
            params={"q": "node", "category_id": t.tech_category_id, "role_id": t.engineering_role_id},
            headers=author_headers,
        )

        assert [tag["id"] for tag in response.json()] == [target.id]

    async def test_a_feed_filter_with_the_alias_is_invalid(self, taxonomy_client, session, taxonomy_data, admin):
        t = taxonomy_data
        alias, target, _ = await self._setup(session, t)
        viewer = await make_viewer(session, t, 7350)
        await merge(taxonomy_client, admin, alias.id, target.id)

        response = await fetch(taxonomy_client, viewer, mode="search", tag_ids=alias.id)

        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid_filter"

    async def test_older_aliases_follow_the_new_target(self, taxonomy_client, session, taxonomy_data, admin):
        first = await add_tag(session, "Node Js", status=TagStatus.PENDING)
        middle = await add_tag(session, "NodeJS")
        final = await add_tag(session, "Node.js")
        assert (await merge(taxonomy_client, admin, first.id, middle.id)).status_code == 200

        assert (await merge(taxonomy_client, admin, middle.id, final.id)).status_code == 200

        assert (await tag_row(session, first.id)).merged_into_tag_id == final.id
        assert (await tag_row(session, middle.id)).merged_into_tag_id == final.id

    async def test_repeating_a_merge_is_200_with_one_journal_row(self, taxonomy_client, session, taxonomy_data, admin):
        alias = await add_tag(session, "NodeJS", status=TagStatus.PENDING)
        target = await add_tag(session, "Node.js")
        await merge(taxonomy_client, admin, alias.id, target.id)

        again = await merge(taxonomy_client, admin, alias.id, target.id)

        assert again.status_code == 200 and again.json()["affected_profiles"] == 0
        assert len(await journal_rows(session)) == 1

    async def test_merge_target_problems_are_409_with_a_reason(self, taxonomy_client, session, taxonomy_data, admin):
        source = await add_tag(session, "NodeJS", status=TagStatus.PENDING)
        approved = await add_tag(session, "Node.js")
        pending_target = await add_tag(session, "Node", status=TagStatus.PENDING)
        alias = await add_tag(session, "Nodejs2", status=TagStatus.PENDING)
        await merge(taxonomy_client, admin, alias.id, approved.id)

        cases = {
            "same_tag": source.id,
            "target_not_found": 999999,
            "target_not_approved": pending_target.id,
            "target_is_alias": alias.id,
        }
        for reason, target_id in cases.items():
            response = await merge(taxonomy_client, admin, source.id, target_id)
            assert response.status_code == 409, reason
            assert response.json()["detail"]["code"] == "merge_target_invalid"
            assert response.json()["detail"]["reason"] == reason
        assert (await tag_row(session, source.id)).status == "pending"
        assert len(await journal_rows(session)) == 1  # only the setup merge

    async def test_a_source_merged_elsewhere_or_rejected_is_tag_status_conflict(
        self, taxonomy_client, session, taxonomy_data, admin
    ):
        merged = await add_tag(session, "NodeJS", status=TagStatus.PENDING)
        rejected = await add_tag(session, "Spam", status=TagStatus.PENDING)
        first_target = await add_tag(session, "Node.js")
        second_target = await add_tag(session, "Node")
        await merge(taxonomy_client, admin, merged.id, first_target.id)
        await reject(taxonomy_client, admin, rejected.id)

        elsewhere = await merge(taxonomy_client, admin, merged.id, second_target.id)
        of_rejected = await merge(taxonomy_client, admin, rejected.id, first_target.id)

        assert elsewhere.status_code == of_rejected.status_code == 409
        assert elsewhere.json()["detail"] == {
            "code": "tag_status_conflict",
            "message": "The tag status does not allow this action.",
            "status": "merged",
        }
        assert of_rejected.json()["detail"]["status"] == "rejected"

    async def test_unknown_source_is_404(self, taxonomy_client, session, taxonomy_data, admin):
        target = await add_tag(session, "Node.js")

        assert (await merge(taxonomy_client, admin, 999999, target.id)).status_code == 404


# ---------- the journal stays in step with the change ----------


async def test_a_failing_journal_write_leaves_a_reject_unchanged(session, container, taxonomy_data):
    t = taxonomy_data
    admin_user, _ = await create_user_with_headers(session, 7400)
    spam = await add_tag(session, "Spam", scopes=[(t.tech_category_id, t.engineering_role_id)])
    owner, _ = await create_user_with_headers(session, 7401)
    fillers = await engineering_tags(session, t, 4)
    profile_id = await add_profile(
        session, owner.id, t.tech_category_id, t.engineering_role_id, tag_ids=(spam.id, *fillers)
    )
    use_case = RejectTagUseCase(
        container.taxonomy_repo(),
        container.profile_repo(),
        container.profile_embedding_repo(),
        BrokenJournal(session),
        container.unit_of_work(),
    )

    spam_id = spam.id  # the rollback below expires the ORM object

    with pytest.raises(RuntimeError):
        await use_case.execute(admin_user, spam_id, "x")
    await session.rollback()

    assert (await tag_row(session, spam_id)).status == "approved"
    assert spam_id in await profile_tag_ids(session, profile_id)
    assert await embedding_row(session, profile_id) is None


async def test_a_failing_journal_write_leaves_a_merge_unchanged(session, container, taxonomy_data):
    t = taxonomy_data
    admin_user, _ = await create_user_with_headers(session, 7410)
    scope = [(t.tech_category_id, t.engineering_role_id)]
    alias = await add_tag(session, "NodeJS", status=TagStatus.PENDING, scopes=[*scope, (t.edu_category_id, None)])
    target = await add_tag(session, "Node.js", scopes=scope)
    owner, _ = await create_user_with_headers(session, 7411)
    profile_id = await add_profile(
        session, owner.id, t.tech_category_id, t.engineering_role_id, tag_ids=(alias.id,)
    )
    use_case = MergeTagUseCase(
        container.taxonomy_repo(),
        container.profile_repo(),
        container.profile_embedding_repo(),
        BrokenJournal(session),
        container.unit_of_work(),
    )

    alias_id, target_id = alias.id, target.id  # the rollback below expires the ORM objects

    with pytest.raises(RuntimeError):
        await use_case.execute(admin_user, alias_id, target_id, "x")
    await session.rollback()

    assert (await tag_row(session, alias_id)).status == "pending"
    assert await profile_tag_ids(session, profile_id) == [alias_id]
    assert await scopes_of(session, target_id) == {(t.tech_category_id, t.engineering_role_id)}
    assert (await tag_row(session, target_id)).usage_count == 0


# ---------- hostile input ----------

BAD_IDS = ["0", "-1", str(MAX_INT64 + 1), "abc", "1.5", "%E2%82%AC"]
BAD_REASON_BODIES = [
    {},
    {"reason": ""},
    {"reason": "   "},
    {"reason": "x" * (ADMIN_REASON_MAX_LENGTH + 1)},
    {"reason": "bad\x00reason"},
    {"reason": 123},
    {"reason": None},
    {"reason": ["spam"]},
    {"reason": "ok", "extra": 1},
    {"reason": "ok", "status": "approved"},
    [],
    "reason",
]


@pytest.mark.parametrize("action", ["approve", "reject"])
@pytest.mark.parametrize("bad_id", BAD_IDS)
async def test_a_bad_tag_id_is_422(taxonomy_client, admin, action, bad_id):
    response = await taxonomy_client.post(f"{TAGS}/{bad_id}/{action}", json={"reason": "x"}, headers=admin)

    assert response.status_code == 422


@pytest.mark.parametrize("payload", BAD_REASON_BODIES)
@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_a_bad_reason_is_422_and_changes_nothing(taxonomy_client, session, taxonomy_data, admin, action, payload):
    tag = await add_tag(session, "Rust", status=TagStatus.PENDING)

    response = await taxonomy_client.post(f"{TAGS}/{tag.id}/{action}", json=payload, headers=admin)

    assert response.status_code == 422
    assert (await tag_row(session, tag.id)).status == "pending"
    assert await journal_rows(session) == []


@pytest.mark.parametrize("payload", BAD_REASON_BODIES)
async def test_a_merge_with_a_bad_reason_is_422(taxonomy_client, session, taxonomy_data, admin, payload):
    source = await add_tag(session, "NodeJS", status=TagStatus.PENDING)
    target = await add_tag(session, "Node.js")
    body = {**payload, "target_tag_id": target.id} if isinstance(payload, dict) else payload

    response = await taxonomy_client.post(f"{TAGS}/{source.id}/merge", json=body, headers=admin)

    assert response.status_code == 422
    assert (await tag_row(session, source.id)).status == "pending"


@pytest.mark.parametrize(
    "body",
    [
        {"reason": "x"},
        {"reason": "x", "target_tag_id": 0},
        {"reason": "x", "target_tag_id": -1},
        {"reason": "x", "target_tag_id": MAX_INT64 + 1},
        {"reason": "x", "target_tag_id": "abc"},
        {"reason": "x", "target_tag_id": None},
        {"reason": "x", "target_tag_id": 1.5},
        {"reason": "x", "target_tag_id": [1]},
        {"reason": "x", "target_tag_id": 1, "extra": True},
    ],
)
async def test_a_bad_merge_target_is_422(taxonomy_client, admin, body):
    response = await taxonomy_client.post(f"{TAGS}/1/merge", json=body, headers=admin)

    assert response.status_code == 422


@pytest.mark.parametrize("bad_id", BAD_IDS)
async def test_a_bad_source_id_in_a_merge_is_422(taxonomy_client, admin, bad_id):
    response = await taxonomy_client.post(f"{TAGS}/{bad_id}/merge", json={"target_tag_id": 1, "reason": "x"}, headers=admin)

    assert response.status_code == 422


async def test_merging_a_tag_into_itself_is_409_not_a_server_error(taxonomy_client, session, taxonomy_data, admin):
    tag = await add_tag(session, "Node.js")

    response = await merge(taxonomy_client, admin, tag.id, tag.id)

    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "same_tag"


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": -1},
        {"limit": "abc"},
        {"offset": -1},
        {"offset": 100_001},
        {"offset": "abc"},
        {"limit": "€"},
    ],
)
async def test_bad_paging_is_422(taxonomy_client, admin, params):
    assert (await pending(taxonomy_client, admin, **params)).status_code == 422


async def test_repeated_paging_parameters_are_never_a_server_error(taxonomy_client, admin):
    response = await taxonomy_client.get(f"{TAGS}/pending?limit=1&limit=2&offset=0&offset=1", headers=admin)

    assert response.status_code < 500
