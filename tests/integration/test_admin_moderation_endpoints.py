"""Hiding profiles and the report queue over real HTTP, Postgres and Redis: what hiding does to the owner, the feed,
the card and the contact; the queue, the details and `dismiss`; a ban closing reports; hostile input (always a 4xx,
never a 5xx)."""

from dataclasses import dataclass

import pytest
from sqlalchemy import text

from app.domain.constants import ADMIN_REASON_MAX_LENGTH, MAX_INT64
from app.domain.enums import ProfileStatus
from app.core.config import settings
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.feed_data import add_profile
from tests.integration.test_admin_endpoints import ban, journal_rows, make_admin
from tests.integration.test_feed_endpoints import add_candidate, fetch, make_viewer
from tests.integration.test_report_endpoints import report, report_rows

PROFILES = "/api/v1/admin/profiles"
REPORTS = "/api/v1/admin/reports"
ADMIN_PROFILE_KEYS = {
    "id",
    "user_id",
    "category_id",
    "role_id",
    "status",
    "country_code",
    "timezone",
    "bio",
    "goals_description",
    "extra_attributes",
    "tags",
    "created_at",
    "updated_at",
}
REASONS = {"spam", "fake", "offensive", "inappropriate_contact", "other"}


@dataclass
class Owner:
    user_id: int
    profile_ids: list[int]
    headers: dict[str, str]


async def make_owner(session, taxonomy, telegram_id: int, *, roles=("engineering",), statuses=()) -> Owner:
    """A user with a username and one profile per role; the first active one becomes the active profile."""
    user, headers = await create_user_with_headers(session, telegram_id, username=f"owner{telegram_id}")
    role_ids = {"engineering": taxonomy.engineering_role_id, "design": taxonomy.design_role_id}
    statuses = [*statuses, *[ProfileStatus.ACTIVE] * (len(roles) - len(statuses))]
    profile_ids = [
        await add_profile(session, user.id, taxonomy.tech_category_id, role_ids[role], status=status)
        for role, status in zip(roles, statuses)
    ]
    first_active = next((p for p, status in zip(profile_ids, statuses) if status is ProfileStatus.ACTIVE), None)
    await session.execute(
        text("UPDATE users SET active_profile_id = :p WHERE id = :u"), {"p": first_active, "u": user.id}
    )
    await session.commit()
    return Owner(user.id, profile_ids, headers)


async def hide(client, headers, profile_id, reason="spam in bio"):
    return await client.post(f"{PROFILES}/{profile_id}/hide", json={"reason": reason}, headers=headers)


async def unhide(client, headers, profile_id, reason="appeal accepted"):
    return await client.post(f"{PROFILES}/{profile_id}/unhide", json={"reason": reason}, headers=headers)


async def dismiss(client, headers, profile_id, reason="no violation"):
    return await client.post(f"{REPORTS}/profiles/{profile_id}/dismiss", json={"reason": reason}, headers=headers)


async def queue(client, headers, **params):
    return await client.get(REPORTS, params=params, headers=headers)


async def pointer(session, user_id: int) -> int | None:
    return (await session.execute(text("SELECT active_profile_id FROM users WHERE id = :id"), {"id": user_id})).scalar_one()


async def status_of(session, profile_id: int) -> str:
    return (await session.execute(text("SELECT status FROM profiles WHERE id = :id"), {"id": profile_id})).scalar_one()


async def report_from_new_users(client, session, profile_id: int, reasons: list[str], first_telegram_id: int) -> None:
    for i, reason in enumerate(reasons):
        _, headers = await create_user_with_headers(session, first_telegram_id + i)
        assert (await report(client, headers, profile_id, reason)).status_code == 201


@pytest.fixture
async def admin(taxonomy_client, session):
    _, headers = await make_admin(taxonomy_client, session, 7001)
    return headers


# ---------- hide ----------


async def test_hiding_returns_the_profile_and_writes_one_journal_row(taxonomy_client, session, taxonomy_data):
    admin_body, admin_headers = await make_admin(taxonomy_client, session, 7010)
    owner = await make_owner(session, taxonomy_data, 7011)
    [profile_id] = owner.profile_ids

    response = await hide(taxonomy_client, admin_headers, profile_id, "spam in bio")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == ADMIN_PROFILE_KEYS
    assert (body["id"], body["user_id"], body["status"]) == (profile_id, owner.user_id, "hidden_by_admin")
    assert await status_of(session, profile_id) == "hidden_by_admin"
    [row] = await journal_rows(session)
    assert row.actor_user_id == admin_body["user"]["id"]
    assert (row.action, row.target_type, row.target_id, row.reason) == ("hide_profile", "profile", profile_id, "spam in bio")
    assert row.details == {"owner_user_id": owner.user_id, "previous_status": "active", "resolved_reports": 0}


async def test_hiding_a_paused_profile_records_it_as_the_previous_status(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7012, statuses=[ProfileStatus.PAUSED])

    response = await hide(taxonomy_client, admin, owner.profile_ids[0])

    assert response.status_code == 200
    assert (await journal_rows(session))[0].details["previous_status"] == "paused"


async def test_hiding_again_is_200_and_adds_no_second_journal_row(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7013)

    first = await hide(taxonomy_client, admin, owner.profile_ids[0], "first reason")
    second = await hide(taxonomy_client, admin, owner.profile_ids[0], "second reason")

    assert (first.status_code, second.status_code) == (200, 200)
    [row] = await journal_rows(session)
    assert row.reason == "first reason"


async def test_hiding_an_unknown_profile_is_404(taxonomy_client, admin):
    response = await hide(taxonomy_client, admin, 987_654)

    assert response.status_code == 404
    assert response.json()["detail"] == "Profile not found."


async def test_hiding_the_active_profile_moves_the_pointer_to_the_other_active_one(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7014, roles=("engineering", "design"))
    first, second = owner.profile_ids
    assert await pointer(session, owner.user_id) == first

    await hide(taxonomy_client, admin, first)

    assert await pointer(session, owner.user_id) == second


async def test_hiding_the_only_active_profile_clears_the_pointer(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7015)

    await hide(taxonomy_client, admin, owner.profile_ids[0])

    assert await pointer(session, owner.user_id) is None


async def test_hiding_does_not_ban_the_owner_or_touch_their_other_profiles(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7016, roles=("engineering", "design"))

    await hide(taxonomy_client, admin, owner.profile_ids[0])

    assert await status_of(session, owner.profile_ids[1]) == "active"
    is_banned = (await session.execute(text("SELECT is_banned FROM users WHERE id = :id"), {"id": owner.user_id})).scalar_one()
    assert is_banned is False


async def test_hiding_resolves_the_open_reports_on_that_profile_and_counts_them(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7017, roles=("engineering", "design"))
    hidden_id, other_id = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, hidden_id, ["spam", "fake"], 7100)
    await report_from_new_users(taxonomy_client, session, other_id, ["spam"], 7110)

    await hide(taxonomy_client, admin, hidden_id)

    rows = await report_rows(session)
    assert [(r.profile_id == hidden_id, r.status) for r in rows] == [(True, "resolved"), (True, "resolved"), (False, "open")]
    assert all(r.resolved_at is not None for r in rows if r.status == "resolved")
    assert (await journal_rows(session))[0].details["resolved_reports"] == 2


# ---------- what the owner can still do with a hidden profile: nothing ----------


async def test_the_owner_sees_the_status_but_can_neither_undo_nor_delete_it(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7020)
    [profile_id] = owner.profile_ids
    await hide(taxonomy_client, admin, profile_id)
    url = f"/api/v1/profiles/{profile_id}"

    assert (await taxonomy_client.get(url, headers=owner.headers)).json()["status"] == "hidden_by_admin"
    assert (await taxonomy_client.post(f"{url}/resume", headers=owner.headers)).status_code == 409
    assert (await taxonomy_client.post(f"{url}/pause", headers=owner.headers)).status_code == 409
    assert (await taxonomy_client.post(f"{url}/activate", headers=owner.headers)).status_code == 409
    assert (await taxonomy_client.delete(url, headers=owner.headers)).status_code == 409
    assert await status_of(session, profile_id) == "hidden_by_admin"  # the row is still there and still hidden


async def test_unhiding_gives_the_profile_back_as_paused_and_only_the_owner_makes_it_active(
    taxonomy_client, session, taxonomy_data, admin
):
    owner = await make_owner(session, taxonomy_data, 7021)
    [profile_id] = owner.profile_ids
    await hide(taxonomy_client, admin, profile_id)

    response = await unhide(taxonomy_client, admin, profile_id, "appeal accepted")

    assert response.status_code == 200
    assert response.json()["status"] == "paused"
    assert await pointer(session, owner.user_id) is None  # still not the active profile
    resumed = await taxonomy_client.post(f"/api/v1/profiles/{profile_id}/resume", headers=owner.headers)
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "active"
    assert await pointer(session, owner.user_id) == profile_id
    assert [r.action for r in await journal_rows(session)] == ["hide_profile", "unhide_profile"]


async def test_unhiding_a_profile_that_is_not_hidden_is_200_and_writes_nothing(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7022)

    response = await unhide(taxonomy_client, admin, owner.profile_ids[0])

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert await journal_rows(session) == []


async def test_unhiding_does_not_reopen_the_reports_that_hiding_closed(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7023)
    [profile_id] = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam"], 7120)
    await hide(taxonomy_client, admin, profile_id)

    await unhide(taxonomy_client, admin, profile_id)

    assert [r.status for r in await report_rows(session)] == ["resolved"]


async def test_unhiding_an_unknown_profile_is_404(taxonomy_client, admin):
    assert (await unhide(taxonomy_client, admin, 987_654)).status_code == 404


# ---------- what other people see ----------


async def test_a_hidden_profile_leaves_the_feed_the_card_and_the_contact_even_for_an_opened_pair(
    taxonomy_client, session, taxonomy_data, admin
):
    viewer = await make_viewer(session, taxonomy_data, telegram_id=7030)
    candidate = await add_candidate(session, taxonomy_data, 7031, user={"username": "anna"})
    contact_url = f"/api/v1/profiles/{candidate}/contact"
    card_url = f"/api/v1/feed/cards/{candidate}"

    feed_before = await fetch(taxonomy_client, viewer, mode="discovery")
    assert candidate in [item["profile"]["id"] for item in feed_before.json()["items"]]
    assert (await taxonomy_client.post(contact_url, headers=viewer.headers)).status_code == 201

    await hide(taxonomy_client, admin, candidate)

    feed_after = await fetch(taxonomy_client, viewer, mode="discovery")
    assert feed_after.status_code == 200
    assert candidate not in [item["profile"]["id"] for item in feed_after.json()["items"]]
    assert (await taxonomy_client.get(card_url, headers=viewer.headers)).status_code == 404
    assert (await taxonomy_client.post(contact_url, headers=viewer.headers)).status_code == 404  # the opened pair too


async def test_after_the_unhide_and_a_resume_the_profile_is_visible_again(taxonomy_client, session, taxonomy_data, admin):
    viewer = await make_viewer(session, taxonomy_data, telegram_id=7032)
    candidate_owner = await make_owner(session, taxonomy_data, 7033)
    [candidate] = candidate_owner.profile_ids
    await hide(taxonomy_client, admin, candidate)
    await unhide(taxonomy_client, admin, candidate)
    # Unhidden means paused: still invisible until the owner resumes.
    assert (await taxonomy_client.get(f"/api/v1/feed/cards/{candidate}", headers=viewer.headers)).status_code == 404

    await taxonomy_client.post(f"/api/v1/profiles/{candidate}/resume", headers=candidate_owner.headers)

    assert (await taxonomy_client.get(f"/api/v1/feed/cards/{candidate}", headers=viewer.headers)).status_code == 200


async def test_a_hidden_profile_cannot_be_reported(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7034)
    [profile_id] = owner.profile_ids
    _, reporter_headers = await create_user_with_headers(session, 7035)
    await hide(taxonomy_client, admin, profile_id)

    assert (await report(taxonomy_client, reporter_headers, profile_id)).status_code == 404


async def test_the_notification_context_knows_when_the_openers_profile_is_hidden_but_not_the_owners(
    taxonomy_client, session, container, taxonomy_data, admin
):
    viewer = await make_viewer(session, taxonomy_data, telegram_id=7036)
    anna = await add_candidate(session, taxonomy_data, 7037, user={"username": "anna"})
    assert (await taxonomy_client.post(f"/api/v1/profiles/{anna}/contact", headers=viewer.headers)).status_code == 201
    repository = container.contact_open_repo()

    context = await repository.get_notification_context(viewer.profile_id, anna)
    assert context.opener_profile_is_hidden is False

    await hide(taxonomy_client, admin, anna)  # the owner's profile hidden: the news is still welcome
    context = await repository.get_notification_context(viewer.profile_id, anna)
    assert context.opener_profile_is_hidden is False

    await hide(taxonomy_client, admin, viewer.profile_id)  # the opener's profile hidden: do not send
    context = await repository.get_notification_context(viewer.profile_id, anna)
    assert context.opener_profile_is_hidden is True


# ---------- the report queue ----------


async def test_the_queue_groups_open_reports_by_profile_with_all_five_reasons(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7040)
    [profile_id] = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam", "spam", "fake"], 7200)

    response = await queue(taxonomy_client, admin)

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["limit"], body["offset"]) == (1, settings.moderation.page_size_default, 0)
    [item] = body["items"]
    assert set(item) == {
        "profile_id",
        "profile_status",
        "category_id",
        "role_id",
        "owner",
        "open_count",
        "oldest_at",
        "newest_at",
        "reasons",
    }
    assert item["profile_id"] == profile_id and item["open_count"] == 3 and item["profile_status"] == "active"
    assert set(item["reasons"]) == REASONS
    assert item["reasons"] == {"spam": 2, "fake": 1, "offensive": 0, "inappropriate_contact": 0, "other": 0}
    assert item["owner"] == {
        "user_id": owner.user_id,
        "first_name": "Test",
        "username": "owner7040",
        "is_banned": False,
    }
    assert item["oldest_at"] <= item["newest_at"]


async def test_the_queue_puts_the_most_reported_profile_first_and_pages_without_overlap(
    taxonomy_client, session, taxonomy_data, admin
):
    few, many, middle = [await make_owner(session, taxonomy_data, 7050 + i) for i in range(3)]
    await report_from_new_users(taxonomy_client, session, few.profile_ids[0], ["spam"], 7300)
    await report_from_new_users(taxonomy_client, session, many.profile_ids[0], ["spam", "fake", "other"], 7310)
    await report_from_new_users(taxonomy_client, session, middle.profile_ids[0], ["fake", "fake"], 7320)

    whole = (await queue(taxonomy_client, admin)).json()
    paged = []
    for offset in (0, 2):
        page = (await queue(taxonomy_client, admin, limit=2, offset=offset)).json()
        assert page["total"] == 3
        paged += [item["profile_id"] for item in page["items"]]

    assert [i["profile_id"] for i in whole["items"]] == [many.profile_ids[0], middle.profile_ids[0], few.profile_ids[0]]
    assert paged == [i["profile_id"] for i in whole["items"]]


async def test_the_queue_shows_paused_and_hidden_profiles_to_the_admin(taxonomy_client, session, taxonomy_data, admin):
    # A report is only possible on a visible profile, so the status changes after the report was filed.
    to_pause = await make_owner(session, taxonomy_data, 7060)
    to_hide = await make_owner(session, taxonomy_data, 7061)
    await report_from_new_users(taxonomy_client, session, to_pause.profile_ids[0], ["spam"], 7400)
    await report_from_new_users(taxonomy_client, session, to_hide.profile_ids[0], ["spam"], 7410)
    await session.execute(text("UPDATE profiles SET status = 'paused' WHERE id = :id"), {"id": to_pause.profile_ids[0]})
    await session.execute(
        text("UPDATE profiles SET status = 'hidden_by_admin' WHERE id = :id"), {"id": to_hide.profile_ids[0]}
    )
    await session.commit()

    statuses = {i["profile_id"]: i["profile_status"] for i in (await queue(taxonomy_client, admin)).json()["items"]}

    assert statuses == {to_pause.profile_ids[0]: "paused", to_hide.profile_ids[0]: "hidden_by_admin"}


async def test_a_banned_owner_is_marked_in_the_queue(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7062)
    await report_from_new_users(taxonomy_client, session, owner.profile_ids[0], ["spam"], 7420)
    await session.execute(text("UPDATE users SET is_banned = true WHERE id = :id"), {"id": owner.user_id})
    await session.commit()

    [item] = (await queue(taxonomy_client, admin)).json()["items"]

    assert item["owner"]["is_banned"] is True


async def test_an_empty_queue_is_an_empty_page(taxonomy_client, admin):
    response = await queue(taxonomy_client, admin)

    assert response.json() == {"items": [], "total": 0, "limit": settings.moderation.page_size_default, "offset": 0}


# ---------- the details of one profile ----------


async def test_the_details_hold_the_profile_the_owner_and_the_open_reports(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7070)
    [profile_id] = owner.profile_ids
    reporter, reporter_headers = await create_user_with_headers(session, 7071)
    await report(taxonomy_client, reporter_headers, profile_id, "offensive", comment="rude")
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam"], 7500)

    response = await taxonomy_client.get(f"{REPORTS}/profiles/{profile_id}", headers=admin)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"profile", "owner", "reports"}
    assert set(body["profile"]) == ADMIN_PROFILE_KEYS and body["profile"]["id"] == profile_id
    assert body["owner"]["id"] == owner.user_id and "ban_reason" in body["owner"]
    assert [(r["reporter_user_id"], r["reason"], r["comment"]) for r in body["reports"]][0] == (reporter.id, "offensive", "rude")
    assert len(body["reports"]) == 2
    assert set(body["reports"][0]) == {"id", "reporter_user_id", "reason", "comment", "created_at"}


async def test_the_details_work_for_a_hidden_profile_and_list_no_closed_reports(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7072)
    [profile_id] = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam"], 7510)
    await hide(taxonomy_client, admin, profile_id)  # closes the report

    body = (await taxonomy_client.get(f"{REPORTS}/profiles/{profile_id}", headers=admin)).json()

    assert body["profile"]["status"] == "hidden_by_admin"
    assert body["reports"] == []


async def test_the_details_of_an_unknown_profile_are_404(taxonomy_client, admin):
    assert (await taxonomy_client.get(f"{REPORTS}/profiles/987654", headers=admin)).status_code == 404


# ---------- dismiss ----------


async def test_dismissing_closes_only_the_open_reports_of_that_profile_and_writes_the_journal(
    taxonomy_client, session, taxonomy_data
):
    admin_body, admin_headers = await make_admin(taxonomy_client, session, 7080)
    admin_id = admin_body["user"]["id"]
    owner = await make_owner(session, taxonomy_data, 7081, roles=("engineering", "design"))
    target, other = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, target, ["spam", "fake"], 7600)
    await report_from_new_users(taxonomy_client, session, other, ["spam"], 7610)

    response = await dismiss(taxonomy_client, admin_headers, target, "no violation")

    assert response.status_code == 200
    assert response.json() == {"profile_id": target, "dismissed_count": 2}
    assert [(r.profile_id == target, r.status) for r in await report_rows(session)] == [
        (True, "dismissed"),
        (True, "dismissed"),
        (False, "open"),
    ]
    [row] = await journal_rows(session)
    assert row.actor_user_id == admin_id
    assert (row.action, row.target_type, row.target_id, row.reason) == ("dismiss_reports", "profile", target, "no violation")
    assert row.details == {"owner_user_id": owner.user_id, "dismissed_reports": 2}
    assert await status_of(session, target) == "active"  # dismissing does not change the profile


async def test_dismissing_again_is_200_with_zero_and_adds_no_journal_row(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7082)
    [profile_id] = owner.profile_ids
    await report_from_new_users(taxonomy_client, session, profile_id, ["spam"], 7620)
    await dismiss(taxonomy_client, admin, profile_id)

    again = await dismiss(taxonomy_client, admin, profile_id)

    assert again.status_code == 200
    assert again.json() == {"profile_id": profile_id, "dismissed_count": 0}
    assert len(await journal_rows(session)) == 1


async def test_dismissing_a_profile_that_was_never_reported_is_200_with_zero(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7083)

    response = await dismiss(taxonomy_client, admin, owner.profile_ids[0])

    assert response.json()["dismissed_count"] == 0
    assert await journal_rows(session) == []


async def test_dismissing_an_unknown_profile_is_404(taxonomy_client, admin):
    assert (await dismiss(taxonomy_client, admin, 987_654)).status_code == 404


async def test_after_a_dismiss_the_same_user_may_report_again_and_the_old_report_stays(
    taxonomy_client, session, taxonomy_data, admin
):
    owner = await make_owner(session, taxonomy_data, 7084)
    [profile_id] = owner.profile_ids
    _, reporter_headers = await create_user_with_headers(session, 7085)
    await report(taxonomy_client, reporter_headers, profile_id)
    await dismiss(taxonomy_client, admin, profile_id)

    again = await report(taxonomy_client, reporter_headers, profile_id)

    assert again.status_code == 201
    assert [r.status for r in await report_rows(session)] == ["dismissed", "open"]
    assert (await queue(taxonomy_client, admin)).json()["total"] == 1


# ---------- a ban closes the reports ----------


async def test_a_ban_resolves_the_open_reports_on_all_the_owners_profiles_and_nobodys_else(
    taxonomy_client, session, taxonomy_data, admin
):
    owner = await make_owner(session, taxonomy_data, 7090, roles=("engineering", "design"))
    bystander = await make_owner(session, taxonomy_data, 7091)
    await report_from_new_users(taxonomy_client, session, owner.profile_ids[0], ["spam"], 7700)
    await report_from_new_users(taxonomy_client, session, owner.profile_ids[1], ["fake", "other"], 7710)
    await report_from_new_users(taxonomy_client, session, bystander.profile_ids[0], ["spam"], 7720)

    response = await ban(taxonomy_client, admin, owner.user_id, "harassment")

    assert response.status_code == 200
    rows = await report_rows(session)
    assert sorted(r.status for r in rows) == ["open", "resolved", "resolved", "resolved"]
    assert [r.status for r in rows if r.profile_id == bystander.profile_ids[0]] == ["open"]
    [row] = await journal_rows(session)
    assert (row.action, row.details) == ("ban_user", {"resolved_reports": 3})
    assert [i["profile_id"] for i in (await queue(taxonomy_client, admin)).json()["items"]] == [bystander.profile_ids[0]]


async def test_banning_a_second_time_does_not_touch_reports_filed_in_between(taxonomy_client, session, taxonomy_data, admin):
    owner = await make_owner(session, taxonomy_data, 7092)
    await report_from_new_users(taxonomy_client, session, owner.profile_ids[0], ["spam"], 7730)
    await ban(taxonomy_client, admin, owner.user_id, "first")
    # The owner is banned now, so nobody can file a new report; put one in directly to see that a repeat ban ignores it.
    await session.execute(
        text(
            "INSERT INTO reports (reporter_user_id, profile_id, reason, created_at) "
            f"VALUES ({owner.user_id}, {owner.profile_ids[0]}, 'spam', now())"
        )
    )
    await session.commit()

    again = await ban(taxonomy_client, admin, owner.user_id, "second")

    assert again.status_code == 200
    assert [r.status for r in await report_rows(session)] == ["resolved", "open"]
    assert len(await journal_rows(session)) == 1


# ---------- hostile input: 4xx, never 5xx ----------

BAD_IDS = ["0", "-1", str(MAX_INT64 + 1), "abc", "1.5", "привет", "%00", "1e3", "9" * 40]
ID_ROUTES = [
    ("POST", PROFILES + "/{}/hide", True),
    ("POST", PROFILES + "/{}/unhide", True),
    ("GET", REPORTS + "/profiles/{}", False),
    ("POST", REPORTS + "/profiles/{}/dismiss", True),
]


@pytest.mark.parametrize("bad_id", BAD_IDS)
@pytest.mark.parametrize("method, template, has_body", ID_ROUTES)
async def test_a_bad_id_is_422(taxonomy_client, admin, method, template, has_body, bad_id):
    kwargs = {"json": {"reason": "x"}} if has_body else {}

    response = await taxonomy_client.request(method, template.format(bad_id), headers=admin, **kwargs)

    assert response.status_code == 422


BAD_REASON_BODIES = [
    {},
    {"reason": ""},
    {"reason": "   "},
    {"reason": "x" * (ADMIN_REASON_MAX_LENGTH + 1)},
    {"reason": "bad\x00reason"},
    {"reason": 123},
    {"reason": None},
    {"reason": ["spam"]},
    {"reason": {"a": 1}},
    {"reason": "ok", "extra": 1},
    {"reason": "ok", "status": "hidden_by_admin"},
    [],
    "reason",
]


@pytest.mark.parametrize("payload", BAD_REASON_BODIES)
@pytest.mark.parametrize("action", ["hide", "unhide", "dismiss"])
async def test_a_bad_reason_is_422_and_changes_nothing(taxonomy_client, session, taxonomy_data, admin, action, payload):
    owner = await make_owner(session, taxonomy_data, 7800)
    [profile_id] = owner.profile_ids
    path = {
        "hide": f"{PROFILES}/{profile_id}/hide",
        "unhide": f"{PROFILES}/{profile_id}/unhide",
        "dismiss": f"{REPORTS}/profiles/{profile_id}/dismiss",
    }[action]

    response = await taxonomy_client.post(path, json=payload, headers=admin)

    assert response.status_code == 422
    assert await status_of(session, profile_id) == "active"
    assert await journal_rows(session) == []


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": settings.moderation.page_size_max + 1},
        {"limit": -1},
        {"limit": "abc"},
        {"limit": "1.5"},
        {"offset": -1},
        {"offset": settings.moderation.max_offset + 1},
        {"offset": 10**30},
        {"offset": "abc"},
    ],
)
async def test_bad_paging_of_the_queue_is_422(taxonomy_client, admin, params):
    assert (await queue(taxonomy_client, admin, **params)).status_code == 422


async def test_the_largest_allowed_page_and_offset_are_accepted(taxonomy_client, admin):
    response = await queue(
        taxonomy_client, admin, limit=settings.moderation.page_size_max, offset=settings.moderation.max_offset
    )

    assert response.status_code == 200


async def test_repeated_query_parameters_never_cause_a_server_error(taxonomy_client, admin):
    response = await taxonomy_client.get(REPORTS + "?limit=1&limit=2&offset=0&offset=1", headers=admin)

    assert response.status_code < 500


async def test_unknown_query_parameters_are_ignored_not_an_error(taxonomy_client, admin):
    assert (await queue(taxonomy_client, admin, nonsense="1")).status_code == 200
