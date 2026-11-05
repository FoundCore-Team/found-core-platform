"""POST /profiles/{id}/contact over real HTTP, Postgres and Redis, including the races the plan promises to close."""

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from celery import Celery
from sqlalchemy import delete, select, text

import app.core.composition.container as container_module
from app.core.config import settings
from app.domain.enums import NotificationStatus, ProfileStatus
from app.infrastructure.models import ContactOpenModel, ProfileModel
from tests.fixtures.auth import create_user_with_headers
from tests.fixtures.db_ops import set_user_banned
from app.worker.celery_app import SEND_CONTACT_NOTIFICATION_TASK
from tests.fixtures.feed_data import add_profile, add_user
from tests.integration.test_feed_endpoints import Viewer, add_candidate, fetch, make_viewer

FEED = "/api/v1/feed"


def url(profile_id) -> str:
    return f"/api/v1/profiles/{profile_id}/contact"


async def open_contact(client, viewer: Viewer, profile_id):
    return await client.post(url(profile_id), headers=viewer.headers)


async def contact_rows(session) -> list[ContactOpenModel]:
    session.expire_all()
    return list((await session.execute(select(ContactOpenModel))).scalars().all())


@pytest.fixture
async def viewer(session, taxonomy_data) -> Viewer:
    return await make_viewer(session, taxonomy_data)


@pytest.fixture
async def anna(session, taxonomy_data) -> int:
    return await add_candidate(session, taxonomy_data, 2001, user={"username": "anna"})


# ---------- gates ----------


async def test_a_missing_token_is_401(taxonomy_client):
    assert (await taxonomy_client.post(url(1))).status_code == 401


async def test_a_user_without_a_username_gets_409_username_required_first(session, taxonomy_client):
    _, headers = await create_user_with_headers(session, 9100, username=None)

    response = await taxonomy_client.post(url(1), headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "username_required"


async def test_a_user_without_an_active_profile_gets_409_no_active_profile(session, taxonomy_client):
    _, headers = await create_user_with_headers(session, 9102, username="someone")

    response = await taxonomy_client.post(url(1), headers=headers)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_active_profile"


# ---------- the open ----------


async def test_the_first_open_is_201_with_the_username_and_a_pending_row(session, taxonomy_client, viewer, anna):
    response = await open_contact(taxonomy_client, viewer, anna)

    assert response.status_code == 201
    assert response.json() == {
        "username": "anna",
        "is_new_open": True,
        "remaining_today": settings.contact.daily_limit - 1,
    }
    [row] = await contact_rows(session)
    assert (row.opener_profile_id, row.opened_profile_id) == (viewer.profile_id, anna)
    assert row.notification_status is NotificationStatus.PENDING


async def test_a_repeat_is_200_with_the_same_username_and_no_new_row(session, taxonomy_client, viewer, anna):
    await open_contact(taxonomy_client, viewer, anna)

    response = await open_contact(taxonomy_client, viewer, anna)

    assert response.status_code == 200
    assert response.json() == {
        "username": "anna",
        "is_new_open": False,
        "remaining_today": settings.contact.daily_limit - 1,
    }
    assert len(await contact_rows(session)) == 1


async def test_the_response_has_no_last_name_and_no_other_fields(taxonomy_client, viewer, anna):
    response = await open_contact(taxonomy_client, viewer, anna)

    assert set(response.json()) == {"username", "is_new_open", "remaining_today"}


async def test_a_profile_of_another_category_can_be_opened(session, taxonomy_client, taxonomy_data, viewer):
    study_mate = await add_candidate(session, taxonomy_data, 2002, role="study_mate", user={"username": "sam"})

    response = await open_contact(taxonomy_client, viewer, study_mate)

    assert (response.status_code, response.json()["username"]) == (201, "sam")


async def test_opening_does_not_write_a_profile_view(session, taxonomy_client, viewer, anna):
    await open_contact(taxonomy_client, viewer, anna)

    count = (await session.execute(text("SELECT count(*) FROM profile_views"))).scalar_one()
    assert count == 0


# ---------- who cannot be opened ----------


async def test_an_own_profile_is_404(taxonomy_client, viewer):
    assert (await open_contact(taxonomy_client, viewer, viewer.profile_id)).status_code == 404


async def test_a_second_own_profile_is_404(session, taxonomy_client, taxonomy_data, viewer):
    other_own = await add_profile(session, viewer.user_id, taxonomy_data.edu_category_id, taxonomy_data.study_mate_role_id)

    assert (await open_contact(taxonomy_client, viewer, other_own)).status_code == 404


async def test_a_missing_profile_is_404(taxonomy_client, viewer):
    assert (await open_contact(taxonomy_client, viewer, 987654)).status_code == 404


async def test_a_paused_profile_is_404(session, taxonomy_client, taxonomy_data, viewer):
    paused = await add_candidate(session, taxonomy_data, 2003, status=ProfileStatus.PAUSED)

    assert (await open_contact(taxonomy_client, viewer, paused)).status_code == 404


async def test_a_banned_owner_is_404(session, taxonomy_client, taxonomy_data, viewer):
    banned = await add_candidate(session, taxonomy_data, 2004, user={"is_banned": True})

    assert (await open_contact(taxonomy_client, viewer, banned)).status_code == 404


@pytest.mark.parametrize("username", [None, ""])
async def test_an_owner_without_a_username_is_404(session, taxonomy_client, taxonomy_data, viewer, username):
    nameless = await add_candidate(session, taxonomy_data, 2005, user={"username": username})

    assert (await open_contact(taxonomy_client, viewer, nameless)).status_code == 404


async def test_a_deleted_owner_is_404(session, taxonomy_client, taxonomy_data, viewer):
    deleted = await add_candidate(session, taxonomy_data, 2006, user={"deleted": True})

    assert (await open_contact(taxonomy_client, viewer, deleted)).status_code == 404


async def test_an_already_opened_profile_that_became_invisible_is_404_on_repeat(
    session, taxonomy_client, viewer, anna
):
    await open_contact(taxonomy_client, viewer, anna)
    await session.execute(text("UPDATE profiles SET status = 'paused' WHERE id = :id"), {"id": anna})
    await session.commit()

    assert (await open_contact(taxonomy_client, viewer, anna)).status_code == 404


@pytest.mark.parametrize("bad_id", ["0", "-1", str(2**63), "abc", "1.5"])
async def test_a_hostile_profile_id_is_422_never_500(taxonomy_client, viewer, bad_id):
    assert (await taxonomy_client.post(f"/api/v1/profiles/{bad_id}/contact", headers=viewer.headers)).status_code == 422


# ---------- the limit ----------


@pytest.fixture
def limit_of_two(monkeypatch):
    monkeypatch.setattr(settings.contact, "daily_limit", 2)


async def test_over_the_limit_is_429_with_the_reset_time(session, taxonomy_client, taxonomy_data, viewer, limit_of_two):
    ids = [await add_candidate(session, taxonomy_data, 2100 + n) for n in range(3)]
    for profile_id in ids[:2]:
        assert (await open_contact(taxonomy_client, viewer, profile_id)).status_code == 201
    oldest = min(row.created_at for row in await contact_rows(session))

    response = await open_contact(taxonomy_client, viewer, ids[2])

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "contact_limit_reached"
    assert detail["limit"] == 2
    assert datetime.fromisoformat(detail["resets_at"]) == oldest + timedelta(hours=settings.contact.window_hours)
    assert len(await contact_rows(session)) == 2


async def test_a_repeat_works_with_the_limit_exhausted(session, taxonomy_client, taxonomy_data, viewer, limit_of_two):
    ids = [await add_candidate(session, taxonomy_data, 2200 + n) for n in range(2)]
    for profile_id in ids:
        await open_contact(taxonomy_client, viewer, profile_id)

    response = await open_contact(taxonomy_client, viewer, ids[0])

    assert response.status_code == 200
    assert response.json()["remaining_today"] == 0


async def test_an_open_older_than_the_window_frees_a_slot(session, taxonomy_client, taxonomy_data, viewer, limit_of_two):
    ids = [await add_candidate(session, taxonomy_data, 2300 + n) for n in range(3)]
    await open_contact(taxonomy_client, viewer, ids[0])
    await open_contact(taxonomy_client, viewer, ids[1])
    await session.execute(
        text("UPDATE contact_opens SET created_at = :old WHERE opened_profile_id = :id"),
        {"old": datetime.now(timezone.utc) - timedelta(hours=25), "id": ids[0]},
    )
    await session.commit()

    response = await open_contact(taxonomy_client, viewer, ids[2])

    assert (response.status_code, response.json()["remaining_today"]) == (201, 0)


async def test_the_limit_is_shared_by_all_profiles_of_the_person(
    session, taxonomy_client, taxonomy_data, viewer, limit_of_two
):
    second_profile = await add_profile(
        session, viewer.user_id, taxonomy_data.edu_category_id, taxonomy_data.study_mate_role_id
    )
    ids = [await add_candidate(session, taxonomy_data, 2400 + n) for n in range(3)]
    await open_contact(taxonomy_client, viewer, ids[0])
    await session.execute(
        text("UPDATE users SET active_profile_id = :p WHERE id = :u"), {"p": second_profile, "u": viewer.user_id}
    )
    await session.commit()
    await open_contact(taxonomy_client, viewer, ids[1])

    response = await open_contact(taxonomy_client, viewer, ids[2])

    assert response.status_code == 429


async def test_the_pair_belongs_to_the_active_profile_so_the_other_profile_opens_again(
    session, taxonomy_client, taxonomy_data, viewer, anna
):
    second_profile = await add_profile(
        session, viewer.user_id, taxonomy_data.edu_category_id, taxonomy_data.study_mate_role_id
    )
    await open_contact(taxonomy_client, viewer, anna)
    await session.execute(
        text("UPDATE users SET active_profile_id = :p WHERE id = :u"), {"p": second_profile, "u": viewer.user_id}
    )
    await session.commit()

    response = await open_contact(taxonomy_client, viewer, anna)

    assert (response.status_code, response.json()["is_new_open"]) == (201, True)
    assert len(await contact_rows(session)) == 2


# ---------- races ----------


async def test_a_double_click_makes_one_row_one_201_and_the_rest_200(session, taxonomy_client, viewer, anna):
    responses = await asyncio.gather(*(open_contact(taxonomy_client, viewer, anna) for _ in range(6)))

    assert sorted(r.status_code for r in responses) == [200] * 5 + [201]
    assert len(await contact_rows(session)) == 1


async def test_a_double_click_at_the_limit_is_not_a_429(session, taxonomy_client, taxonomy_data, viewer, monkeypatch):
    monkeypatch.setattr(settings.contact, "daily_limit", 1)
    anna_id = await add_candidate(session, taxonomy_data, 2500)

    responses = await asyncio.gather(*(open_contact(taxonomy_client, viewer, anna_id) for _ in range(4)))

    assert sorted(r.status_code for r in responses) == [200, 200, 200, 201]


async def test_parallel_opens_of_different_profiles_at_the_limit_make_exactly_limit_rows(
    session, taxonomy_client, taxonomy_data, viewer, monkeypatch
):
    monkeypatch.setattr(settings.contact, "daily_limit", 3)
    ids = [await add_candidate(session, taxonomy_data, 2600 + n) for n in range(8)]

    responses = await asyncio.gather(*(open_contact(taxonomy_client, viewer, profile_id) for profile_id in ids))

    codes = sorted(r.status_code for r in responses)
    assert codes == [201] * 3 + [429] * 5
    assert len(await contact_rows(session)) == 3


# ---------- the notification job ----------


@pytest.fixture
def patient_enqueue(monkeypatch) -> None:
    # The production enqueue timeout is short on purpose; a slow first connection on a developer machine must not make
    # the tests that look into the broker flaky.
    monkeypatch.setattr(settings.notification, "enqueue_timeout_seconds", 5.0)


async def test_the_first_open_puts_one_job_on_the_notifications_queue(
    taxonomy_client, viewer, anna, broker_client, patient_enqueue
):
    await open_contact(taxonomy_client, viewer, anna)

    messages = await broker_client.lrange(settings.notification.queue_name, 0, -1)
    assert len(messages) == 1
    message = json.loads(messages[0])
    assert message["headers"]["task"] == SEND_CONTACT_NOTIFICATION_TASK
    args, kwargs, _ = json.loads(base64.b64decode(message["body"]))
    assert (args, kwargs) == ([viewer.profile_id, anna], {})


async def test_a_repeat_puts_no_second_job(taxonomy_client, viewer, anna, broker_client, patient_enqueue):
    await open_contact(taxonomy_client, viewer, anna)
    await open_contact(taxonomy_client, viewer, anna)

    assert await broker_client.llen(settings.notification.queue_name) == 1


async def test_a_refused_open_puts_no_job(session, taxonomy_client, viewer, broker_client, patient_enqueue):
    await open_contact(taxonomy_client, viewer, 987654)

    assert await broker_client.llen(settings.notification.queue_name) == 0


async def test_the_open_is_201_and_the_row_pending_while_the_broker_is_down(
    session, taxonomy_client, viewer, anna, monkeypatch
):
    dead_app = Celery("dead", broker="redis://127.0.0.1:1/0")
    dead_app.conf.update(
        broker_connection_timeout=0.2,
        broker_transport_options={"socket_connect_timeout": 0.2},
    )
    monkeypatch.setattr(container_module, "celery_app", dead_app)

    response = await open_contact(taxonomy_client, viewer, anna)

    assert response.status_code == 201
    [row] = await contact_rows(session)
    assert row.notification_status is NotificationStatus.PENDING


# ---------- the cascade ----------


async def test_deleting_either_profile_removes_the_open(session, taxonomy_client, taxonomy_data, viewer):
    first = await add_candidate(session, taxonomy_data, 2700)
    second = await add_candidate(session, taxonomy_data, 2701)
    await open_contact(taxonomy_client, viewer, first)
    await open_contact(taxonomy_client, viewer, second)

    await session.execute(delete(ProfileModel).where(ProfileModel.id == first))
    await session.commit()
    assert [row.opened_profile_id for row in await contact_rows(session)] == [second]

    await session.execute(delete(ProfileModel).where(ProfileModel.id == viewer.profile_id))
    await session.commit()
    assert await contact_rows(session) == []


# ---------- is_contact_opened in the feed ----------


async def test_is_contact_opened_follows_the_open_in_the_feed_and_in_the_card(
    session, taxonomy_client, viewer, anna
):
    before = (await fetch(taxonomy_client, viewer, mode="discovery")).json()["items"]
    card_before = (await taxonomy_client.get(f"{FEED}/cards/{anna}", headers=viewer.headers)).json()
    assert [i["is_contact_opened"] for i in before] == [False]
    assert card_before["is_contact_opened"] is False

    await open_contact(taxonomy_client, viewer, anna)

    after = (await fetch(taxonomy_client, viewer, mode="discovery")).json()["items"]
    card_after = (await taxonomy_client.get(f"{FEED}/cards/{anna}", headers=viewer.headers)).json()
    assert [i["is_contact_opened"] for i in after] == [True]
    assert card_after["is_contact_opened"] is True
    # Not the same thing as a view: opening a contact does not mark the card as viewed.
    assert after[0]["is_viewed"] is False


async def test_is_contact_opened_is_false_for_another_viewer(session, taxonomy_client, taxonomy_data, viewer, anna):
    other = await make_viewer(session, taxonomy_data, telegram_id=9001, username="other")
    await open_contact(taxonomy_client, viewer, anna)

    items = (await fetch(taxonomy_client, other, mode="discovery")).json()["items"]

    anna_item = next(i for i in items if i["profile"]["id"] == anna)
    assert anna_item["is_contact_opened"] is False


async def test_a_banned_viewer_is_403(session, taxonomy_client, viewer, anna):
    await set_user_banned(session, viewer.user_id, "spam")

    assert (await open_contact(taxonomy_client, viewer, anna)).status_code == 403
