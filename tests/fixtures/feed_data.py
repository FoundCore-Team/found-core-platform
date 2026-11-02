"""Direct ORM helpers that put users, profiles and vectors into the test database for the feed tests."""

import math
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.constants import EMBEDDING_DIMENSIONS
from app.domain.enums import EmbeddingStatus, ProfileStatus
from app.infrastructure.models import ProfileEmbeddingModel, ProfileModel, ProfileTagModel, UserModel

CURRENT_MODEL = "stub-v1"


def vector_with_cosine(cosine: float) -> list[float]:
    """A unit vector whose cosine similarity to `basis_vector()` is `cosine`."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = cosine
    vector[1] = math.sqrt(1 - cosine**2)
    return vector


def basis_vector() -> list[float]:
    return vector_with_cosine(1.0)


async def add_user(
    session: AsyncSession,
    telegram_id: int,
    *,
    username: str | None = "someone",
    is_banned: bool = False,
    deleted: bool = False,
    last_active_days_ago: int = 1,
) -> int:
    now = datetime.now(timezone.utc)
    user = UserModel(
        telegram_id=telegram_id,
        first_name=f"User{telegram_id}",
        username=username,
        is_banned=is_banned,
        created_at=now,
        last_active_at=now - timedelta(days=last_active_days_ago),
        deleted_at=now if deleted else None,
    )
    session.add(user)
    await session.commit()
    return user.id


async def add_profile(
    session: AsyncSession,
    user_id: int,
    category_id: int,
    role_id: int,
    *,
    status: ProfileStatus = ProfileStatus.ACTIVE,
    country_code: str = "RU",
    timezone_id: str = "Europe/Moscow",
    extra_attributes: dict[str, str] | None = None,
    tag_ids: tuple[int, ...] = (),
) -> int:
    now = datetime.now(timezone.utc)
    profile = ProfileModel(
        user_id=user_id,
        category_id=category_id,
        role_id=role_id,
        country_code=country_code,
        timezone=timezone_id,
        bio="b" * 200,
        goals_description="g" * 200,
        extra_attributes=extra_attributes or {},
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(profile)
    await session.flush()
    session.add_all([ProfileTagModel(profile_id=profile.id, tag_id=tag_id) for tag_id in tag_ids])
    await session.commit()
    return profile.id


async def add_vector(
    session: AsyncSession, profile_id: int, vector: list[float], model: str = CURRENT_MODEL
) -> None:
    now = datetime.now(timezone.utc)
    session.add(
        ProfileEmbeddingModel(
            profile_id=profile_id,
            embedding=vector,
            input_hash="hash",
            model=model,
            status=EmbeddingStatus.READY,
            source_updated_at=now,
            updated_at=now,
        )
    )
    await session.commit()
