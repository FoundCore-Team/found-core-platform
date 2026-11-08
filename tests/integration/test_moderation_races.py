"""Races around reports and hiding, fired in parallel with asyncio.gather against real Postgres, and the promise that a
hide or a ban is committed together with its journal row and the closed reports, or not at all."""

import asyncio

import pytest
from sqlalchemy import text

from app.application.use_cases import BanUserUseCase, HideProfileUseCase
from app.domain.enums import ProfileStatus
from app.infrastructure.helpers import db_helper
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.db_ops import set_user_admin
from tests.integration.test_admin_endpoints import ban, journal_rows, make_admin
from tests.integration.test_admin_moderation_endpoints import (
    dismiss,
    hide,
    make_owner,
    pointer,
    report_from_new_users,
    status_of,
)
from tests.integration.test_admin_races import BrokenJournal
from tests.integration.test_report_endpoints import report, report_rows

ROUNDS = 10


async def open_report_count(session, profile_id: int) -> int:
    return (
        await session.execute(
            text("SELECT count(*) FROM reports WHERE profile_id = :id AND status = 'open'"), {"id": profile_id}
        )
    ).scalar_one()


# ---------- reports ----------


async def test_parallel_duplicate_reports_make_one_open_report_one_201_and_the_rest_200(
    taxonomy_client, session, taxonomy_data
):
    owner = await make_owner(session, taxonomy_data, 9500)
    [profile_id] = owner.profile_ids
    _, headers = await create_user_with_headers(session, 9501)

    responses = await asyncio.gather(*(report(taxonomy_client, headers, profile_id, "spam") for _ in range(10)))

    assert sorted(r.status_code for r in responses) == [200] * 9 + [201]
    assert len(await report_rows(session)) == 1
    assert len({r.json()["id"] for r in responses}) == 1  # everybody was handed the same report


async def test_parallel_reports_from_different_users_all_succeed(taxonomy_client, session, taxonomy_data):
    owner = await make_owner(session, taxonomy_data, 9502)
    [profile_id] = owner.profile_ids
    headers = [(await create_user_with_headers(session, 9510 + i))[1] for i in range(8)]

    responses = await asyncio.gather(*(report(taxonomy_client, h, profile_id, "fake") for h in headers))

    assert {r.status_code for r in responses} == {201}
    assert await open_report_count(session, profile_id) == 8


async def test_a_report_racing_a_dismiss_never_errors_and_leaves_at_most_one_open_report(
    taxonomy_client, session, taxonomy_data
):
    _, admin_headers = await make_admin(taxonomy_client, session, 9503)

    for round_number in range(ROUNDS):
        owner = await make_owner(session, taxonomy_data, 9600 + round_number)
        [profile_id] = owner.profile_ids
        _, headers = await create_user_with_headers(session, 9700 + round_number)
        assert (await report(taxonomy_client, headers, profile_id)).status_code == 201

        again, dismissed = await asyncio.gather(
            report(taxonomy_client, headers, profile_id), dismiss(taxonomy_client, admin_headers, profile_id)
        )

        assert dismissed.status_code == 200, f"round {round_number}"
        # Either the repeat saw the open report (200), or the dismiss closed it first and a new one was filed (201).
        assert again.status_code in (200, 201), f"round {round_number}"
        assert await open_report_count(session, profile_id) <= 1, f"round {round_number}"


async def test_a_report_racing_a_ban_of_the_owner_never_errors(taxonomy_client, session, taxonomy_data):
    _, admin_headers = await make_admin(taxonomy_client, session, 9504)

    for round_number in range(ROUNDS):
        owner = await make_owner(session, taxonomy_data, 9800 + round_number)
        [profile_id] = owner.profile_ids
        _, headers = await create_user_with_headers(session, 9900 + round_number)

        reported, banned = await asyncio.gather(
            report(taxonomy_client, headers, profile_id), ban(taxonomy_client, admin_headers, owner.user_id)
        )

        assert banned.status_code == 200, f"round {round_number}"
        # Before the ban the profile is visible (201); after it the owner is banned and the profile looks missing.
        assert reported.status_code in (201, 404), f"round {round_number}"


# ---------- hiding ----------


async def test_parallel_hides_of_one_profile_write_one_journal_row(taxonomy_client, session, taxonomy_data):
    _, admin_headers = await make_admin(taxonomy_client, session, 9505)
    owner = await make_owner(session, taxonomy_data, 9506)
    [profile_id] = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam", "fake"], 9520)

    responses = await asyncio.gather(*(hide(taxonomy_client, admin_headers, profile_id, f"reason {i}") for i in range(8)))

    assert {r.status_code for r in responses} == {200}
    [row] = await journal_rows(session)
    assert row.action == "hide_profile"
    assert row.details["resolved_reports"] == 2  # the one request that won closed them all
    assert await status_of(session, profile_id) == "hidden_by_admin"


@pytest.mark.parametrize("owner_action", ["pause", "resume"])
async def test_a_hide_racing_the_owners_pause_or_resume_always_ends_hidden_with_no_pointer_to_it(
    taxonomy_client, session, taxonomy_data, owner_action
):
    _, admin_headers = await make_admin(taxonomy_client, session, 9507)
    start_status = ProfileStatus.PAUSED if owner_action == "resume" else ProfileStatus.ACTIVE

    for round_number in range(ROUNDS):
        owner = await make_owner(
            session, taxonomy_data, 9300 + round_number + (100 if owner_action == "resume" else 0), statuses=[start_status]
        )
        [profile_id] = owner.profile_ids

        hidden, owned = await asyncio.gather(
            hide(taxonomy_client, admin_headers, profile_id),
            taxonomy_client.post(f"/api/v1/profiles/{profile_id}/{owner_action}", headers=owner.headers),
        )

        assert hidden.status_code == 200, f"round {round_number}"
        assert owned.status_code in (200, 409), f"round {round_number}: {owned.text}"
        assert await status_of(session, profile_id) == "hidden_by_admin", f"round {round_number}"
        assert await pointer(session, owner.user_id) is None, f"round {round_number}: the pointer names a hidden profile"
        assert [r.action for r in await journal_rows(session) if r.target_id == profile_id] == ["hide_profile"]


async def test_a_hide_racing_a_delete_by_the_owner_ends_in_a_409_or_a_missing_profile_never_a_5xx(
    taxonomy_client, session, taxonomy_data
):
    _, admin_headers = await make_admin(taxonomy_client, session, 9508)

    for round_number in range(ROUNDS):
        owner = await make_owner(session, taxonomy_data, 9400 + round_number)
        [profile_id] = owner.profile_ids

        hidden, deleted = await asyncio.gather(
            hide(taxonomy_client, admin_headers, profile_id),
            taxonomy_client.delete(f"/api/v1/profiles/{profile_id}", headers=owner.headers),
        )

        assert hidden.status_code in (200, 404), f"round {round_number}"
        assert deleted.status_code in (204, 409), f"round {round_number}: {deleted.text}"


# ---------- the journal row and the change are one transaction ----------


async def fresh_state(profile_id: int, user_id: int):
    async with db_helper.session_factory() as fresh:  # another connection: only committed data is visible
        profile_status = (
            await fresh.execute(text("SELECT status FROM profiles WHERE id = :id"), {"id": profile_id})
        ).scalar_one()
        user = (
            await fresh.execute(
                text("SELECT active_profile_id, is_banned, token_version FROM users WHERE id = :id"), {"id": user_id}
            )
        ).one()
        statuses = [
            row.status
            for row in (await fresh.execute(text("SELECT status FROM reports ORDER BY id"))).all()
        ]
        journal = (await fresh.execute(text("SELECT count(*) FROM admin_actions"))).scalar_one()
    return profile_status, user, statuses, journal


async def test_a_journal_failure_leaves_the_profile_visible_the_pointer_and_the_reports_untouched(
    taxonomy_client, session, container, taxonomy_data
):
    admin, _ = await create_user_with_headers(session, 9530)
    await set_user_admin(session, admin.id)
    admin = await container.user_repo().get_by_id(admin.id)
    owner = await make_owner(session, taxonomy_data, 9531)
    [profile_id] = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam"], 9540)
    use_case = HideProfileUseCase(
        profile_repository=container.profile_repo(),
        user_repository=container.user_repo(),
        report_repository=container.report_repo(),
        admin_action_repository=BrokenJournal(session),
        unit_of_work=container.unit_of_work(),
    )

    with pytest.raises(RuntimeError):
        await use_case.execute(admin, profile_id, "spam")

    profile_status, user, report_statuses, journal = await fresh_state(profile_id, owner.user_id)
    assert profile_status == "active"
    assert user.active_profile_id == profile_id
    assert report_statuses == ["open"]
    assert journal == 0


async def test_a_journal_failure_during_a_ban_leaves_no_ban_and_the_reports_open(
    taxonomy_client, session, container, taxonomy_data
):
    admin, _ = await create_user_with_headers(session, 9532)
    await set_user_admin(session, admin.id)
    admin = await container.user_repo().get_by_id(admin.id)
    owner = await make_owner(session, taxonomy_data, 9533)
    [profile_id] = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam", "fake"], 9550)
    use_case = BanUserUseCase(
        user_repository=container.user_repo(),
        report_repository=container.report_repo(),
        admin_action_repository=BrokenJournal(session),
        unit_of_work=container.unit_of_work(),
    )

    with pytest.raises(RuntimeError):
        await use_case.execute(admin, owner.user_id, "spam")

    _, user, report_statuses, journal = await fresh_state(profile_id, owner.user_id)
    assert (user.is_banned, user.token_version) == (False, 0)
    assert report_statuses == ["open", "open"]
    assert journal == 0
