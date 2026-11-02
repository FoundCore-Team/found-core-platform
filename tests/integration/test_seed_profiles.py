"""Runs the real profile seed script against the test database (stub embedding provider from .env.test)."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.core.config import settings
from app.domain.enums import EmbeddingStatus, ProfileStatus, TagStatus
from app.infrastructure.models import (
    ProfileEmbeddingModel,
    ProfileModel,
    ProfileTagModel,
    TagModel,
    UserModel,
)
import scripts.dev_seed_profiles as seed_profiles
from scripts.dev_seed_profiles import SeedError, TELEGRAM_ID_BASE, USERNAME_PREFIX, load_fixture, main
from scripts.dev_seed_taxonomy import main as seed_taxonomy

FIXTURE = load_fixture()
FIXTURE_PROFILES = [profile for user in FIXTURE for profile in user["profiles"]]
SEED_RANGE = UserModel.telegram_id >= TELEGRAM_ID_BASE


async def _count(session, model, *conditions) -> int:
    return (await session.execute(select(func.count()).select_from(model).where(*conditions))).scalar_one()


async def _snapshot(session) -> dict[str, int]:
    return {
        "users": await _count(session, UserModel, SEED_RANGE),
        "profiles": await _count(session, ProfileModel),
        "profile_tags": await _count(session, ProfileTagModel),
        "tags": await _count(session, TagModel),
        "embeddings": await _count(session, ProfileEmbeddingModel),
        "tag_usage": (await session.execute(select(func.coalesce(func.sum(TagModel.usage_count), 0)))).scalar_one(),
    }


@pytest.fixture
async def seeded(session) -> dict[str, int]:
    await seed_taxonomy()
    await main()
    return await _snapshot(session)


async def test_seed_creates_every_user_and_profile_from_the_fixture(seeded):
    assert seeded["users"] == len(FIXTURE)
    assert seeded["profiles"] == len(FIXTURE_PROFILES)
    assert seeded["profile_tags"] > len(FIXTURE_PROFILES) * 4


async def test_every_stored_username_is_marked_as_seed_data(session, seeded):
    usernames = (await session.execute(select(UserModel.username).where(SEED_RANGE))).scalars().all()

    assert all(name is None or name.startswith(USERNAME_PREFIX) for name in usernames)
    assert sum(name is None for name in usernames) == sum(user["username"] is None for user in FIXTURE)


async def test_edge_cases_reach_the_database(session, seeded):
    paused = await _count(session, ProfileModel, ProfileModel.status == ProfileStatus.PAUSED)
    banned = await _count(session, UserModel, SEED_RANGE, UserModel.is_banned.is_(True))
    two_profiles = (
        await session.execute(
            select(func.count()).select_from(
                select(ProfileModel.user_id).group_by(ProfileModel.user_id).having(func.count() > 1).subquery()
            )
        )
    ).scalar_one()

    assert paused == sum(profile.get("status") == "paused" for profile in FIXTURE_PROFILES)
    assert banned == sum(user.get("is_banned", False) for user in FIXTURE)
    assert two_profiles == sum(len(user["profiles"]) > 1 for user in FIXTURE)


async def test_pending_tags_are_pending_and_belong_to_a_seed_author(session, seeded):
    expected = {title for profile in FIXTURE_PROFILES for title in profile.get("pending_tags", [])}
    rows = (
        await session.execute(
            select(TagModel.title, TagModel.created_by_user_id).where(TagModel.status == TagStatus.PENDING)
        )
    ).all()

    assert {title for title, _ in rows} == expected
    assert all(author_id is not None for _, author_id in rows)


async def test_the_seed_creates_only_approved_and_pending_tags(session, seeded):
    statuses = set((await session.execute(select(TagModel.status).distinct())).scalars().all())

    assert statuses == {TagStatus.APPROVED, TagStatus.PENDING}


async def test_active_profile_pointer_is_set_only_to_an_active_profile_of_the_same_user(session, seeded):
    users = (await session.execute(select(UserModel.id, UserModel.active_profile_id).where(SEED_RANGE))).all()

    for user_id, active_profile_id in users:
        profiles = (
            await session.execute(select(ProfileModel.id, ProfileModel.status).where(ProfileModel.user_id == user_id))
        ).all()
        has_active = any(status is ProfileStatus.ACTIVE for _, status in profiles)
        if not has_active:
            assert active_profile_id is None
            continue
        assert active_profile_id in {pid for pid, status in profiles if status is ProfileStatus.ACTIVE}


async def test_with_the_stub_provider_every_profile_but_the_skipped_ones_gets_a_ready_vector(session, seeded):
    skipped = sum(profile.get("skip_embedding", False) for profile in FIXTURE_PROFILES)
    ready = await _count(
        session,
        ProfileEmbeddingModel,
        ProfileEmbeddingModel.status == EmbeddingStatus.READY,
        ProfileEmbeddingModel.embedding.is_not(None),
    )

    assert skipped >= 2
    assert ready == len(FIXTURE_PROFILES) - skipped
    assert seeded["embeddings"] == ready


async def test_rerunning_the_seed_changes_nothing(session, seeded):
    await main()

    assert await _snapshot(session) == seeded


async def test_rerunning_does_not_overwrite_what_a_profile_was_edited_to(session, seeded):
    await session.execute(ProfileModel.__table__.update().where(ProfileModel.id == 1).values(bio="edited by hand"))
    await session.commit()

    await main()

    assert (await session.execute(select(ProfileModel.bio).where(ProfileModel.id == 1))).scalar_one() == "edited by hand"


async def test_reset_recreates_the_seed_range_and_leaves_other_users_alone(session, seeded):
    now = datetime.now(timezone.utc)
    session.add(UserModel(telegram_id=555, first_name="Real", created_at=now, last_active_at=now))
    await session.commit()
    first_seed_user = FIXTURE[0]
    await session.execute(
        UserModel.__table__.update()
        .where(UserModel.telegram_id == TELEGRAM_ID_BASE + first_seed_user["key"])
        .values(first_name="changed")
    )
    await session.commit()

    await main(reset=True)

    session.expire_all()
    names = dict((await session.execute(select(UserModel.telegram_id, UserModel.first_name))).all())
    assert names[555] == "Real"
    assert names[TELEGRAM_ID_BASE + first_seed_user["key"]] == first_seed_user["first_name"]
    assert (await _snapshot(session))["users"] == seeded["users"]
    assert (await _snapshot(session))["profiles"] == seeded["profiles"]


async def test_the_seed_refuses_to_run_in_production_and_writes_nothing(session, monkeypatch):
    await seed_taxonomy()
    monkeypatch.setattr(settings.run, "env", "prod")

    with pytest.raises(SystemExit):
        await main()

    assert await _count(session, UserModel) == 0


async def test_reset_in_production_deletes_nothing(session, seeded, monkeypatch):
    monkeypatch.setattr(settings.run, "env", "prod")

    with pytest.raises(SystemExit):
        await main(reset=True)

    assert await _snapshot(session) == seeded


async def test_an_invalid_profile_stops_the_seed_and_writes_nothing(session, monkeypatch):
    await seed_taxonomy()
    broken = [dict(user, profiles=[dict(p) for p in user["profiles"]]) for user in FIXTURE[:2]]
    broken[1]["profiles"][0]["bio"] = "too short"
    monkeypatch.setattr(seed_profiles, "load_fixture", lambda: broken)

    with pytest.raises(SeedError, match=f"user key {broken[1]['key']}"):
        await main()

    assert await _count(session, UserModel) == 0
    assert await _count(session, ProfileModel) == 0


async def test_the_seed_needs_the_taxonomy_first(session):
    with pytest.raises(SeedError, match="dev_seed_taxonomy"):
        await main()

    assert await _count(session, UserModel) == 0


async def test_reembed_keeps_ready_vectors_ready(session, seeded):
    await main(reembed=True)

    assert (await _snapshot(session)) == seeded

