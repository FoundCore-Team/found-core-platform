from datetime import datetime, timedelta, timezone

from app.domain.entities import (
    FeedCandidate,
    FeedCard,
    FeedViewer,
    NewProfileEntity,
    NewUserEntity,
    ProfileEntity,
    UserEntity,
)
from app.domain.enums import ProfileStatus

FEED_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def make_feed_viewer(**overrides) -> FeedViewer:
    defaults = dict(
        profile_id=1,
        user_id=1,
        category_id=1,
        role_id=5,
        country_code="RU",
        timezone="Europe/Moscow",
        tag_ids=[1, 2, 3, 4, 5],
        has_comparable_vector=True,
    )
    defaults.update(overrides)
    return FeedViewer(**defaults)


def make_feed_candidate(**overrides) -> FeedCandidate:
    defaults = dict(
        profile_id=101,
        category_id=1,
        role_id=5,
        country_code="RU",
        timezone="Europe/Moscow",
        tag_ids=[3, 4, 5, 42, 43, 44],
        cos_sim=0.62,
        last_active_at=FEED_NOW - timedelta(days=2),
        matched_filters=[],
    )
    defaults.update(overrides)
    return FeedCandidate(**defaults)


def make_new_user_entity(**overrides) -> NewUserEntity:
    now = datetime.now(timezone.utc)
    defaults = dict(
        telegram_id=123456789,
        first_name="Test",
        created_at=now,
        last_active_at=now,
        last_name=None,
        username="testuser",
        photo_url=None,
        language_code="en",
    )
    defaults.update(overrides)
    return NewUserEntity(**defaults)


def make_user_entity(**overrides) -> UserEntity:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1,
        telegram_id=123456789,
        first_name="Test",
        created_at=now,
        last_active_at=now,
        last_name=None,
        username="testuser",
        photo_url=None,
        language_code="en",
        terms_accepted_at=None,
        is_admin=False,
        is_banned=False,
        ban_reason=None,
        token_version=0,
        active_profile_id=None,
        deleted_at=None,
    )
    defaults.update(overrides)
    return UserEntity(**defaults)


def make_feed_card(profile_id: int, **overrides) -> FeedCard:
    defaults = dict(
        profile=make_profile_entity(id=profile_id, user_id=profile_id + 1000),
        first_name=f"User{profile_id}",
        photo_url=None,
        is_viewed=False,
    )
    defaults.update(overrides)
    return FeedCard(**defaults)


def make_new_profile_entity(**overrides) -> NewProfileEntity:
    now = datetime.now(timezone.utc)
    defaults = dict(
        user_id=1,
        category_id=1,
        role_id=1,
        country_code="DE",
        timezone="Europe/Berlin",
        bio="b" * 200,
        goals_description="g" * 200,
        extra_attributes={},
        tag_ids=[1, 2, 3, 4, 5],
        status=ProfileStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return NewProfileEntity(**defaults)


def make_profile_entity(**overrides) -> ProfileEntity:
    now = datetime.now(timezone.utc)
    defaults = dict(
        id=1,
        user_id=1,
        category_id=1,
        role_id=1,
        country_code="DE",
        timezone="Europe/Berlin",
        bio="b" * 200,
        goals_description="g" * 200,
        extra_attributes={},
        status=ProfileStatus.ACTIVE,
        created_at=now,
        updated_at=now,
        tags=[],
    )
    defaults.update(overrides)
    return ProfileEntity(**defaults)
