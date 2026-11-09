"""Races around tag moderation, fired in parallel with asyncio.gather against real Postgres: moderation against
moderation, moderation against profile saves, and the promise that no profile ever keeps a link to an alias or a
rejected tag."""

import asyncio

import pytest
from sqlalchemy import text

from app.domain.enums import TagStatus
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.feed_data import add_profile
from tests.fixtures.taxonomy_data import add_tag
from tests.integration.test_admin_endpoints import journal_rows, make_admin
from tests.integration.test_tag_moderation_endpoints import (
    approve,
    create_profile,
    engineering_tags,
    merge,
    profile_tag_ids,
    reject,
    tag_row,
    update_profile,
)

ROUNDS = 10
PROFILES = "/api/v1/profiles"


@pytest.fixture
async def admin(taxonomy_client, session):
    _, headers = await make_admin(taxonomy_client, session, 8000)
    return headers


async def links_to_dead_tags(session) -> int:
    result = await session.execute(
        text(
            "SELECT count(*) FROM profile_tags pt JOIN tags t ON t.id = pt.tag_id "
            "WHERE t.status IN ('merged', 'rejected')"
        )
    )
    return result.scalar_one()


async def test_parallel_approves_make_one_change_and_one_journal_row(taxonomy_client, session, taxonomy_data, admin):
    tag = await add_tag(session, "Rust", status=TagStatus.PENDING)

    responses = await asyncio.gather(*(approve(taxonomy_client, admin, tag.id) for _ in range(10)))

    assert {r.status_code for r in responses} == {200}
    assert len(await journal_rows(session)) == 1


async def test_parallel_merges_of_the_same_pair_make_one_change_and_one_journal_row(
    taxonomy_client, session, taxonomy_data, admin
):
    alias = await add_tag(session, "NodeJS", status=TagStatus.PENDING, usage_count=3)
    target = await add_tag(session, "Node.js", usage_count=4)

    responses = await asyncio.gather(*(merge(taxonomy_client, admin, alias.id, target.id) for _ in range(8)))

    assert {r.status_code for r in responses} == {200}
    assert len(await journal_rows(session)) == 1
    assert (await tag_row(session, target.id)).usage_count == 7  # the alias was added exactly once


async def test_approve_racing_reject_ends_rejected_and_the_journal_tells_the_same_story(
    taxonomy_client, session, taxonomy_data, admin
):
    for round_number in range(ROUNDS):
        tag = await add_tag(session, f"Tag{round_number}", status=TagStatus.PENDING)
        tag_id = tag.id
        await session.execute(text("DELETE FROM admin_actions"))
        await session.commit()

        approved, rejected = await asyncio.gather(
            approve(taxonomy_client, admin, tag_id), reject(taxonomy_client, admin, tag_id)
        )

        assert rejected.status_code == 200, f"round {round_number}"
        assert approved.status_code in (200, 409), f"round {round_number}"
        assert (await tag_row(session, tag_id)).status == "rejected"
        actions = [row.action for row in await journal_rows(session)]
        # Approve first, then reject; or reject first and the approve is refused (nothing written for it).
        assert actions in (["approve_tag", "reject_tag"], ["reject_tag"]), f"round {round_number}: {actions}"


async def test_a_merge_racing_a_profile_update_never_leaves_a_link_to_the_alias(
    taxonomy_client, session, taxonomy_data, admin
):
    t = taxonomy_data
    for round_number in range(ROUNDS):
        scope = [(t.tech_category_id, t.engineering_role_id)]
        alias = await add_tag(session, f"NodeJS{round_number}", status=TagStatus.PENDING, scopes=scope)
        target = await add_tag(session, f"Node.js{round_number}", scopes=scope)
        alias_id, target_id = alias.id, target.id
        fillers = await engineering_tags(session, t, 4, prefix=f"f{round_number}")
        owner, headers = await create_user_with_headers(session, 8100 + round_number)
        profile = await create_profile(taxonomy_client, headers, t, [target_id, *fillers])

        merged, saved = await asyncio.gather(
            merge(taxonomy_client, admin, alias_id, target_id),
            update_profile(taxonomy_client, headers, profile["id"], t, [alias_id, *fillers]),
        )

        assert merged.status_code == 200, f"round {round_number}: {merged.text}"
        # Saved before the merge (and then moved by it), or refused because the id was already an alias.
        assert saved.status_code in (200, 400), f"round {round_number}: {saved.text}"
        ids = await profile_tag_ids(session, profile["id"])
        assert alias_id not in ids, f"round {round_number}"
        assert target_id in ids if saved.status_code == 200 else ids == sorted([target_id, *fillers])
        assert await links_to_dead_tags(session) == 0, f"round {round_number}"


async def test_a_merge_racing_a_profile_create_never_leaves_a_link_to_the_alias(
    taxonomy_client, session, taxonomy_data, admin
):
    t = taxonomy_data
    for round_number in range(ROUNDS):
        scope = [(t.tech_category_id, t.engineering_role_id)]
        alias = await add_tag(session, f"NodeJS{round_number}", status=TagStatus.PENDING, scopes=scope)
        target = await add_tag(session, f"Node.js{round_number}", scopes=scope)
        alias_id, target_id = alias.id, target.id
        fillers = await engineering_tags(session, t, 4, prefix=f"f{round_number}")
        _, headers = await create_user_with_headers(session, 8200 + round_number)

        merged, created = await asyncio.gather(
            merge(taxonomy_client, admin, alias_id, target_id),
            taxonomy_client.post(
                PROFILES,
                json={
                    "category_id": t.tech_category_id,
                    "role_id": t.engineering_role_id,
                    "country_code": "DE",
                    "timezone": "Europe/Berlin",
                    "bio": "b" * 200,
                    "goals_description": "g" * 200,
                    "tag_ids": [alias_id, *fillers],
                    "extra_attributes": {"grade": "junior"},
                },
                headers=headers,
            ),
        )

        assert merged.status_code == 200, f"round {round_number}: {merged.text}"
        assert created.status_code in (201, 400), f"round {round_number}: {created.text}"
        assert await links_to_dead_tags(session) == 0, f"round {round_number}"


async def test_a_merge_racing_a_reject_of_its_target_keeps_the_invariant(
    taxonomy_client, session, taxonomy_data, admin
):
    t = taxonomy_data
    for round_number in range(ROUNDS):
        scope = [(t.tech_category_id, t.engineering_role_id)]
        alias = await add_tag(session, f"NodeJS{round_number}", status=TagStatus.PENDING, scopes=scope)
        target = await add_tag(session, f"Node.js{round_number}", scopes=scope)
        alias_id, target_id = alias.id, target.id
        fillers = await engineering_tags(session, t, 4, prefix=f"f{round_number}")
        owner, _ = await create_user_with_headers(session, 8300 + round_number)
        await add_profile(
            session, owner.id, t.tech_category_id, t.engineering_role_id, tag_ids=(alias_id, *fillers)
        )

        merged, rejected = await asyncio.gather(
            merge(taxonomy_client, admin, alias_id, target_id), reject(taxonomy_client, admin, target_id)
        )

        assert rejected.status_code == 200, f"round {round_number}"
        assert merged.status_code in (200, 409), f"round {round_number}: {merged.text}"
        assert (await tag_row(session, target_id)).status == "rejected"
        assert await links_to_dead_tags(session) == 0, f"round {round_number}"
        if merged.status_code == 409:
            assert merged.json()["detail"]["reason"] == "target_not_approved"


async def test_profiles_created_in_parallel_with_the_same_tags_do_not_deadlock(
    taxonomy_client, session, taxonomy_data
):
    t = taxonomy_data
    tag_ids = await engineering_tags(session, t, 5, prefix="shared")
    users = [await create_user_with_headers(session, 8400 + i) for i in range(8)]

    responses = await asyncio.gather(
        *(
            taxonomy_client.post(
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
            for _, headers in users
        )
    )

    assert {r.status_code for r in responses} == {201}, [r.text for r in responses if r.status_code != 201]
    counts = await session.execute(text("SELECT usage_count FROM tags WHERE id = ANY(:ids)"), {"ids": tag_ids})
    assert {row[0] for row in counts} == {8}


async def test_parallel_custom_tag_requests_for_an_alias_title_all_get_the_same_target(
    taxonomy_client, session, taxonomy_data, admin
):
    t = taxonomy_data
    alias = await add_tag(session, "NodeJS", status=TagStatus.PENDING)
    target = await add_tag(session, "Node.js")
    assert (await merge(taxonomy_client, admin, alias.id, target.id)).status_code == 200
    users = [await create_user_with_headers(session, 8500 + i) for i in range(8)]

    responses = await asyncio.gather(
        *(
            taxonomy_client.post(
                "/api/v1/taxonomy/tags/custom",
                json={"title": "nodejs", "category_id": t.tech_category_id, "role_id": t.engineering_role_id},
                headers=headers,
            )
            for _, headers in users
        )
    )

    assert {r.status_code for r in responses} <= {200, 201}
    assert {r.json()["id"] for r in responses} == {target.id}
    scopes = await session.execute(text("SELECT count(*) FROM tag_scopes WHERE tag_id = :id"), {"id": target.id})
    assert scopes.scalar_one() == 1
