"""Dev-only: fills the database with ~100 handwritten profiles (scripts/data/seed_profiles/*.json) so the feed can
be judged by eye. Every profile goes through the same ProfileContentValidator as a real one, then its embedding is
computed synchronously with the provider from .env (no Celery worker needed).

Idempotent: a user is found by telegram_id and a profile by (user, category, role); existing rows are left alone.
Refuses to run with APP_CONFIG__RUN__ENV=prod. Every seed username starts with `seed_`, so a future "open contact"
can never point at a real Telegram account.

Requires `alembic upgrade head` and `python -m scripts.dev_seed_taxonomy` first.

Usage:
    python -m scripts.dev_seed_profiles
    python -m scripts.dev_seed_profiles --reset      # deletes the seed users (telegram_id 10**12 + key) first
    python -m scripts.dev_seed_profiles --reembed    # recompute embeddings of all seed profiles (after switching provider)

Log in as a seed user: python scripts/dev_gen_init_data.py --telegram-id 1000000000001 --username seed_anna_backend --first-name Анна
"""

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.composition.container import Container
from app.core.config import settings
from app.domain.entities import NewProfileEntity, NewTagEntity, NewTagScopeEntity, ProfileFormEntity
from app.domain.enums import ProfileStatus, TagStatus
from app.domain.services import clean_tag_title, normalize_tag_title
from app.infrastructure.helpers import db_helper
from app.infrastructure.models import CategoryModel, ProfileEmbeddingModel, ProfileModel, RoleModel, UserModel
from app.infrastructure.providers import NoopAnalyticsTracker
from app.worker.tasks import run_embed_profile

DATA_DIR = Path(__file__).parent / "data" / "seed_profiles"
# Real Telegram ids stay far below this, so the range can be wiped by --reset without touching a real account.
TELEGRAM_ID_BASE = 10**12
TELEGRAM_ID_RANGE = 10**6
USERNAME_PREFIX = "seed_"


class SeedError(Exception):
    pass


def load_fixture() -> list[dict]:
    users: list[dict] = []
    for path in sorted(DATA_DIR.glob("*.json")):
        users.extend(json.loads(path.read_text(encoding="utf-8")))

    keys = [user["key"] for user in users]
    if len(keys) != len(set(keys)):
        raise SeedError("The fixture has duplicate user keys.")
    usernames = [user["username"] for user in users if user["username"] is not None]
    if len(usernames) != len(set(usernames)):
        raise SeedError("The fixture has duplicate usernames.")
    for user in users:
        if not 0 < user["key"] < TELEGRAM_ID_RANGE:
            raise SeedError(f"User key {user['key']} is outside 1..{TELEGRAM_ID_RANGE - 1}.")
        if user["username"] is not None and not user["username"].startswith(USERNAME_PREFIX):
            raise SeedError(f"User key {user['key']}: username must start with '{USERNAME_PREFIX}'.")
    return users


def _seed_users_filter():
    return UserModel.telegram_id.between(TELEGRAM_ID_BASE, TELEGRAM_ID_BASE + TELEGRAM_ID_RANGE - 1)


async def _lookup_roles(session: AsyncSession) -> dict[tuple[str, str], tuple[int, int]]:
    rows = (
        await session.execute(
            select(CategoryModel.slug, RoleModel.slug, CategoryModel.id, RoleModel.id).join(
                CategoryModel, CategoryModel.id == RoleModel.category_id
            )
        )
    ).all()
    if not rows:
        raise SeedError("The taxonomy is empty: run `python -m scripts.dev_seed_taxonomy` first.")
    return {(category_slug, role_slug): (category_id, role_id) for category_slug, role_slug, category_id, role_id in rows}


async def _ensure_user(session: AsyncSession, user: dict, now: datetime) -> tuple[int, bool]:
    """Returns the user id and whether the user already has an active profile."""
    last_active_at = now - timedelta(days=user["last_active_days_ago"])
    is_banned = user.get("is_banned", False)
    created_id = (
        await session.execute(
            pg_insert(UserModel)
            .values(
                telegram_id=TELEGRAM_ID_BASE + user["key"],
                first_name=user["first_name"],
                last_name=user.get("last_name"),
                username=user["username"],
                photo_url=None,
                language_code="ru",
                is_banned=is_banned,
                ban_reason="Seed user banned to test the feed" if is_banned else None,
                created_at=last_active_at - timedelta(days=30),
                last_active_at=last_active_at,
            )
            .on_conflict_do_nothing(index_elements=["telegram_id"])
            .returning(UserModel.id)
        )
    ).scalar_one_or_none()
    if created_id is not None:
        return created_id, False

    existing = (
        await session.execute(
            select(UserModel.id, UserModel.active_profile_id).where(
                UserModel.telegram_id == TELEGRAM_ID_BASE + user["key"]
            )
        )
    ).one()
    return existing.id, existing.active_profile_id is not None


async def _ensure_tag_id(
    container: Container, title: str, status: TagStatus, author_id: int | None, category_id: int, role_id: int
) -> int:
    taxonomy_repository = container.taxonomy_repo()
    cleaned_title = clean_tag_title(title)
    normalized_title = normalize_tag_title(cleaned_title)

    tag = await taxonomy_repository.get_tag_by_normalized_title(normalized_title)
    if tag is None:
        tag = await taxonomy_repository.create_tag(
            NewTagEntity(
                title=cleaned_title,
                normalized_title=normalized_title,
                status=status,
                created_by_user_id=author_id,
            )
        )
    if tag is None:
        tag = await taxonomy_repository.get_tag_by_normalized_title(normalized_title)
    assert tag is not None
    await taxonomy_repository.create_tag_scope(NewTagScopeEntity(tag_id=tag.id, category_id=category_id, role_id=role_id))
    return tag.id


async def _seed_profiles(session: AsyncSession, container: Container, users: list[dict]) -> list[int]:
    """Creates users and profiles in the caller's transaction. Returns ids of profiles that need an embedding."""
    roles = await _lookup_roles(session)
    profile_repository = container.profile_repo()
    user_repository = container.user_repo()
    embedding_repository = container.profile_embedding_repo()
    taxonomy_repository = container.taxonomy_repo()
    validator = container.profile_content_validator()
    now = datetime.now(timezone.utc)
    to_embed: list[int] = []

    for user in users:
        user_id, has_active_profile = await _ensure_user(session, user, now)

        for profile in user["profiles"]:
            where = f"user key {user['key']}, {profile['category']}/{profile['role']}"
            if (profile["category"], profile["role"]) not in roles:
                raise SeedError(f"{where}: unknown category/role.")
            category_id, role_id = roles[(profile["category"], profile["role"])]
            if await profile_repository.get_by_user_category_role(user_id, category_id, role_id) is not None:
                continue

            tag_ids = [
                await _ensure_tag_id(container, title, TagStatus.APPROVED, None, category_id, role_id)
                for title in profile["tags"]
            ]
            tag_ids += [
                await _ensure_tag_id(container, title, TagStatus.PENDING, user_id, category_id, role_id)
                for title in profile.get("pending_tags", [])
            ]

            form = ProfileFormEntity(
                country_code=profile["country_code"],
                timezone=profile["timezone"],
                bio=profile["bio"],
                goals_description=profile["goals"],
                tag_ids=tag_ids,
            )
            try:
                form, extra_attributes = await validator.validate(
                    category_id, role_id, form, profile["extra_attributes"]
                )
            except Exception as error:
                raise SeedError(f"{where}: {type(error).__name__}: {error}") from error

            status = ProfileStatus(profile.get("status", "active"))
            created = await profile_repository.create(
                NewProfileEntity(
                    user_id=user_id,
                    category_id=category_id,
                    role_id=role_id,
                    country_code=form.country_code,
                    timezone=form.timezone,
                    bio=form.bio,
                    goals_description=form.goals_description,
                    extra_attributes=extra_attributes,
                    tag_ids=form.tag_ids,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )
            assert created is not None
            await taxonomy_repository.increment_tags_usage(form.tag_ids)

            if status is ProfileStatus.ACTIVE and not has_active_profile:
                await user_repository.set_active_profile(user_id, created.id)
                has_active_profile = True

            # A profile marked skip_embedding gets no embeddings row at all, so neither the worker nor the sweep ever
            # computes a vector for it: it stays a permanent "no vector yet" case for the feed.
            if not profile.get("skip_embedding", False):
                await embedding_repository.mark_pending(created.id, created.updated_at)
                to_embed.append(created.id)

    return to_embed


async def _seed_profile_ids_with_embedding_row(session: AsyncSession) -> list[int]:
    result = await session.execute(
        select(ProfileModel.id)
        .join(UserModel, UserModel.id == ProfileModel.user_id)
        .join(ProfileEmbeddingModel, ProfileEmbeddingModel.profile_id == ProfileModel.id)
        .where(_seed_users_filter())
        .order_by(ProfileModel.id)
    )
    return list(result.scalars().all())


async def _print_summary(session: AsyncSession) -> None:
    total = (
        await session.execute(
            select(func.count()).select_from(ProfileModel).join(UserModel, UserModel.id == ProfileModel.user_id).where(_seed_users_filter())
        )
    ).scalar_one()
    by_status = dict(
        (
            await session.execute(
                select(ProfileEmbeddingModel.status, func.count())
                .join(ProfileModel, ProfileModel.id == ProfileEmbeddingModel.profile_id)
                .join(UserModel, UserModel.id == ProfileModel.user_id)
                .where(_seed_users_filter())
                .group_by(ProfileEmbeddingModel.status)
            )
        ).all()
    )
    ready = by_status.get("ready", 0)
    failed = by_status.get("failed", 0)
    pending = by_status.get("pending", 0)
    print(
        f"Seed profiles: {total}. Embeddings: ready={ready}, failed={failed}, pending={pending}, "
        f"skipped={total - ready - failed - pending}."
    )


async def main(reset: bool = False, reembed: bool = False) -> None:
    if settings.run.env == "prod":
        raise SystemExit("Refusing to seed fake profiles with APP_CONFIG__RUN__ENV=prod.")
    users = load_fixture()

    # Never used for the cache here; Container just needs one. The client connects lazily.
    redis_client = Redis.from_url(str(settings.redis.url))
    try:
        async with db_helper.session_factory() as session:
            # The seed uses only repositories and the validator, which send no analytics events.
            container = Container(session=session, redis_client=redis_client, analytics_tracker=NoopAnalyticsTracker())
            if reset:
                await session.execute(delete(UserModel).where(_seed_users_filter()))
            to_embed = await _seed_profiles(session, container, users)
            await session.commit()

        if reembed:
            async with db_helper.session_factory() as session:
                to_embed = await _seed_profile_ids_with_embedding_row(session)
        for profile_id in to_embed:
            await run_embed_profile(profile_id, is_last_attempt=True)

        async with db_helper.session_factory() as session:
            await _print_summary(session)
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete the seed users (and their profiles) first")
    parser.add_argument("--reembed", action="store_true", help="recompute the embeddings of every seed profile")
    arguments = parser.parse_args()
    asyncio.run(main(reset=arguments.reset, reembed=arguments.reembed))
