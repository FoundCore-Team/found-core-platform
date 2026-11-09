from datetime import datetime, timezone

from sqlalchemy import BigInteger, delete, literal, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import NewProfileEntity, ProfileEntity, TagEntity
from app.domain.enums import ProfileStatus
from app.domain.interfaces import IProfileRepository
from app.infrastructure.mappers.profile_mapper import map_profile_model_to_profile_entity
from app.infrastructure.mappers.taxonomy_mapper import map_tag_model_to_tag_entity
from app.infrastructure.models import ProfileModel, ProfileTagModel, TagModel


class SqlAlchemyProfileRepository(IProfileRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _load_tags(self, profile_ids: list[int]) -> dict[int, list[TagEntity]]:
        """One query for all profiles; every id gets a list, empty if it has no tags."""
        
        tags: dict[int, list[TagEntity]] = {profile_id: [] for profile_id in profile_ids}
        result = await self._session.execute(
            select(ProfileTagModel.profile_id, TagModel)
            .join(TagModel, TagModel.id == ProfileTagModel.tag_id)
            .where(ProfileTagModel.profile_id.in_(profile_ids))
            .order_by(ProfileTagModel.profile_id, TagModel.id)
        )
        for profile_id, tag_model in result.all():
            tags[profile_id].append(map_tag_model_to_tag_entity(tag_model))
        return tags

    async def get_by_id(self, profile_id: int) -> ProfileEntity | None:
        result = await self._session.execute(select(ProfileModel).where(ProfileModel.id == profile_id))
        model = result.scalar_one_or_none()
        if model is None:
            return None
        tags = await self._load_tags([model.id])
        return map_profile_model_to_profile_entity(model, tags[model.id])

    async def get_by_user_category_role(
        self, user_id: int, category_id: int, role_id: int
    ) -> ProfileEntity | None:
        result = await self._session.execute(
            select(ProfileModel).where(
                ProfileModel.user_id == user_id,
                ProfileModel.category_id == category_id,
                ProfileModel.role_id == role_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        tags = await self._load_tags([model.id])
        return map_profile_model_to_profile_entity(model, tags[model.id])

    async def list_by_user_id(self, user_id: int) -> list[ProfileEntity]:
        result = await self._session.execute(
            select(ProfileModel)
            .where(ProfileModel.user_id == user_id)
            .order_by(ProfileModel.created_at, ProfileModel.id)
        )
        models = list(result.scalars().all())
        tags = await self._load_tags([m.id for m in models])
        return [map_profile_model_to_profile_entity(m, tags[m.id]) for m in models]

    async def get_newest_active_by_user_id(self, user_id: int) -> ProfileEntity | None:
        result = await self._session.execute(
            select(ProfileModel)
            .where(ProfileModel.user_id == user_id, ProfileModel.status == ProfileStatus.ACTIVE)
            .order_by(ProfileModel.created_at.desc(), ProfileModel.id.desc())
            .limit(1)
        )
        model = result.scalar_one_or_none()
        if model is None:
            return None
        tags = await self._load_tags([model.id])
        return map_profile_model_to_profile_entity(model, tags[model.id])

    async def create(self, profile: NewProfileEntity) -> ProfileEntity | None:
        stmt = (
            pg_insert(ProfileModel)
            .values(
                user_id=profile.user_id,
                category_id=profile.category_id,
                role_id=profile.role_id,
                country_code=profile.country_code,
                timezone=profile.timezone,
                bio=profile.bio,
                goals_description=profile.goals_description,
                extra_attributes=profile.extra_attributes,
                status=profile.status,
                created_at=profile.created_at,
                updated_at=profile.updated_at,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "category_id", "role_id"])
            .returning(ProfileModel)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        if model is None:
            return None
        await self._session.execute(
            pg_insert(ProfileTagModel).values(
                [{"profile_id": model.id, "tag_id": tag_id} for tag_id in profile.tag_ids]
            )
        )
        tags = await self._load_tags([model.id])
        return map_profile_model_to_profile_entity(model, tags[model.id])

    async def update(self, profile: ProfileEntity, tag_ids: list[int]) -> ProfileEntity:
        # The row lock serializes concurrent PUTs: otherwise both would re-insert the same
        # (profile_id, tag_id) pairs and one would fail on the profile_tags primary key.
        result = await self._session.execute(
            select(ProfileModel)
            .where(ProfileModel.id == profile.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        model = result.scalar_one()
        model.country_code = profile.country_code
        model.timezone = profile.timezone
        model.bio = profile.bio
        model.goals_description = profile.goals_description
        model.extra_attributes = profile.extra_attributes
        model.updated_at = profile.updated_at
        await self._session.execute(delete(ProfileTagModel).where(ProfileTagModel.profile_id == model.id))
        await self._session.execute(
            pg_insert(ProfileTagModel).values(
                [{"profile_id": model.id, "tag_id": tag_id} for tag_id in tag_ids]
            )
        )
        await self._session.flush()
        await self._session.refresh(model)
        tags = await self._load_tags([model.id])
        return map_profile_model_to_profile_entity(model, tags[model.id])

    async def set_status(self, profile_id: int, status: ProfileStatus) -> bool:
        result = await self._session.execute(
            update(ProfileModel)
            .where(ProfileModel.id == profile_id, ProfileModel.status != ProfileStatus.HIDDEN_BY_ADMIN)
            .values(status=status)
            .returning(ProfileModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def hide(self, profile_id: int) -> bool:
        result = await self._session.execute(
            update(ProfileModel)
            .where(
                ProfileModel.id == profile_id,
                ProfileModel.status.in_([ProfileStatus.ACTIVE, ProfileStatus.PAUSED]),
            )
            .values(status=ProfileStatus.HIDDEN_BY_ADMIN)
            .returning(ProfileModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def unhide(self, profile_id: int) -> bool:
        result = await self._session.execute(
            update(ProfileModel)
            .where(ProfileModel.id == profile_id, ProfileModel.status == ProfileStatus.HIDDEN_BY_ADMIN)
            .values(status=ProfileStatus.PAUSED)
            .returning(ProfileModel.id)
        )
        return result.scalar_one_or_none() is not None

    async def delete(self, profile_id: int) -> None:
        await self._session.execute(delete(ProfileModel).where(ProfileModel.id == profile_id))

    async def detach_tag(self, tag_id: int) -> list[int]:
        result = await self._session.execute(
            delete(ProfileTagModel).where(ProfileTagModel.tag_id == tag_id).returning(ProfileTagModel.profile_id)
        )
        return sorted(result.scalars())

    async def replace_tag(self, source_id: int, target_id: int) -> list[int]:
        affected = list(
            (
                await self._session.execute(
                    select(ProfileTagModel.profile_id)
                    .where(ProfileTagModel.tag_id == source_id)
                    .order_by(ProfileTagModel.profile_id)
                )
            ).scalars()
        )
        if not affected:
            return []
        # A profile that already carries the target keeps its single link: the conflicting insert is skipped.
        await self._session.execute(
            pg_insert(ProfileTagModel)
            .from_select(
                ["profile_id", "tag_id"],
                select(ProfileTagModel.profile_id, literal(target_id, BigInteger)).where(
                    ProfileTagModel.tag_id == source_id
                ),
            )
            .on_conflict_do_nothing(index_elements=["profile_id", "tag_id"])
        )
        await self._session.execute(delete(ProfileTagModel).where(ProfileTagModel.tag_id == source_id))
        return affected

    async def touch(self, profile_ids: list[int]) -> dict[int, datetime]:
        if not profile_ids:
            return {}
        now = datetime.now(timezone.utc)
        # Rows are locked in id order first (lock order is tags, then profiles, then profile_embeddings).
        await self._session.execute(
            select(ProfileModel.id).where(ProfileModel.id.in_(profile_ids)).order_by(ProfileModel.id).with_for_update()
        )
        result = await self._session.execute(
            update(ProfileModel)
            .where(ProfileModel.id.in_(profile_ids))
            .values(updated_at=now)
            .returning(ProfileModel.id)
            .execution_options(synchronize_session=False)
        )
        return {profile_id: now for profile_id in result.scalars()}
