from sqlalchemy import BigInteger, and_, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import (
    CategoryEntity,
    NewTagEntity,
    NewTagScopeEntity,
    PendingTagEntity,
    RoleEntity,
    RoleFieldEntity,
    TagEntity,
)
from app.domain.enums import TagStatus
from app.domain.interfaces import ITaxonomyRepository
from app.infrastructure.mappers.taxonomy_mapper import (
    map_category_model_to_category_entity,
    map_pending_tag_row_to_pending_tag_entity,
    map_role_field_model_to_role_field_entity,
    map_role_model_to_role_entity,
    map_tag_model_to_tag_entity,
)
from app.infrastructure.models import (
    CategoryModel,
    ProfileTagModel,
    RoleFieldModel,
    RoleModel,
    TagModel,
    TagScopeModel,
    UserModel,
)


class SqlAlchemyTaxonomyRepository(ITaxonomyRepository):
    def __init__(self, session: AsyncSession, suggest_limit: int) -> None:
        self._session = session
        self._suggest_limit = suggest_limit

    async def get_categories(self) -> list[CategoryEntity]:
        result = await self._session.execute(
            select(CategoryModel).order_by(CategoryModel.sort_order, CategoryModel.id)
        )
        return [map_category_model_to_category_entity(m) for m in result.scalars().all()]

    async def get_category_by_id(self, category_id: int) -> CategoryEntity | None:
        result = await self._session.execute(select(CategoryModel).where(CategoryModel.id == category_id))
        model = result.scalar_one_or_none()
        return map_category_model_to_category_entity(model) if model else None

    async def get_roles_by_category(self, category_id: int) -> list[RoleEntity]:
        result = await self._session.execute(
            select(RoleModel)
            .where(RoleModel.category_id == category_id)
            .order_by(RoleModel.sort_order, RoleModel.id)
        )
        return [map_role_model_to_role_entity(m) for m in result.scalars().all()]

    async def get_role_by_id(self, role_id: int) -> RoleEntity | None:
        result = await self._session.execute(select(RoleModel).where(RoleModel.id == role_id))
        model = result.scalar_one_or_none()
        return map_role_model_to_role_entity(model) if model else None

    async def get_role_fields_by_role(self, role_id: int) -> list[RoleFieldEntity]:
        result = await self._session.execute(
            select(RoleFieldModel)
            .where(RoleFieldModel.role_id == role_id)
            .order_by(RoleFieldModel.sort_order, RoleFieldModel.id)
        )
        return [map_role_field_model_to_role_field_entity(m) for m in result.scalars().all()]

    async def suggest_tags(
        self, query: str, category_id: int, role_id: int | None, user_id: int
    ) -> list[TagEntity]:
        if role_id is not None:
            scope_condition = and_(
                TagScopeModel.category_id == category_id,
                or_(TagScopeModel.role_id == role_id, TagScopeModel.role_id.is_(None)),
            )
        else:
            scope_condition = and_(
                TagScopeModel.category_id == category_id,
                TagScopeModel.role_id.is_(None),
            )
        scope_exists = (
            select(TagScopeModel.id)
            .where(TagScopeModel.tag_id == TagModel.id, scope_condition)
            .exists()
        )

        result = await self._session.execute(
            select(TagModel)
            .where(
                # autoescape: user input must match literally, not as LIKE wildcards (% _ \).
                TagModel.title.icontains(query, autoescape=True),
                scope_exists,
                or_(
                    TagModel.status == TagStatus.APPROVED,
                    and_(TagModel.status == TagStatus.PENDING, TagModel.created_by_user_id == user_id),
                ),
            )
            .order_by(TagModel.usage_count.desc(), TagModel.title, TagModel.id)
            .limit(self._suggest_limit)
        )
        return [map_tag_model_to_tag_entity(m) for m in result.scalars().all()]

    async def get_tag_by_normalized_title(self, normalized_title: str) -> TagEntity | None:
        result = await self._session.execute(
            select(TagModel).where(TagModel.normalized_title == normalized_title)
        )
        model = result.scalar_one_or_none()
        return map_tag_model_to_tag_entity(model) if model else None

    async def get_tags_in_scope_by_ids(
        self, tag_ids: list[int], category_id: int, role_id: int
    ) -> list[TagEntity]:
        scope_exists = (
            select(TagScopeModel.id)
            .where(
                TagScopeModel.tag_id == TagModel.id,
                TagScopeModel.category_id == category_id,
                or_(TagScopeModel.role_id == role_id, TagScopeModel.role_id.is_(None)),
            )
            .exists()
        )
        # FOR KEY SHARE: a moderator's merge/reject (FOR UPDATE) waits for this transaction to finish, and a save
        # that starts after the moderation re-reads the tag here and sees the new status. KEY SHARE does not
        # conflict with another KEY SHARE, so two profile saves never wait for each other. populate_existing:
        # the row may already sit in the session from an earlier read and must be refreshed after the wait.
        result = await self._session.execute(
            select(TagModel)
            .where(TagModel.id.in_(tag_ids), scope_exists)
            .order_by(TagModel.id)
            .with_for_update(key_share=True, read=True)
            .execution_options(populate_existing=True)
        )
        return [map_tag_model_to_tag_entity(m) for m in result.scalars().all()]

    async def get_tags_by_ids(self, tag_ids: list[int]) -> list[TagEntity]:
        result = await self._session.execute(
            select(TagModel)
            .where(TagModel.id.in_(tag_ids))
            .order_by(TagModel.id)
            .execution_options(populate_existing=True)
        )
        return [map_tag_model_to_tag_entity(m) for m in result.scalars().all()]

    async def create_tag(self, tag: NewTagEntity) -> TagEntity | None:
        stmt = (
            pg_insert(TagModel)
            .values(
                title=tag.title,
                normalized_title=tag.normalized_title,
                status=tag.status,
                created_by_user_id=tag.created_by_user_id,
            )
            .on_conflict_do_nothing(index_elements=["normalized_title"])
            .returning(TagModel)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return map_tag_model_to_tag_entity(model) if model else None

    async def increment_tags_usage(self, tag_ids: list[int]) -> None:
        if not tag_ids:
            return
        # Rows are locked in id order so two profiles sharing tags cannot deadlock on each other.
        # FOR NO KEY UPDATE (not FOR UPDATE): it does not conflict with the FOR KEY SHARE that the profile validator
        # and the profile_tags foreign key hold on the same rows. With FOR UPDATE two parallel saves of profiles
        # sharing a tag would each hold KEY SHARE and wait for the other's to go: a guaranteed deadlock.
        await self._session.execute(
            select(TagModel.id)
            .where(TagModel.id.in_(tag_ids))
            .order_by(TagModel.id)
            .with_for_update(key_share=True)
        )
        await self._session.execute(
            update(TagModel).where(TagModel.id.in_(tag_ids)).values(usage_count=TagModel.usage_count + 1)
        )

    async def create_tag_scope(self, scope: NewTagScopeEntity) -> None:
        stmt = (
            pg_insert(TagScopeModel)
            .values(tag_id=scope.tag_id, category_id=scope.category_id, role_id=scope.role_id)
            .on_conflict_do_nothing(index_elements=["tag_id", "category_id", "role_id"])
        )
        await self._session.execute(stmt)

    async def lock_tags_for_update(self, tag_ids: list[int]) -> list[TagEntity]:
        # Id order everywhere: two moderators locking [A, B] and [B, A] would otherwise deadlock.
        result = await self._session.execute(
            select(TagModel)
            .where(TagModel.id.in_(tag_ids))
            .order_by(TagModel.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return [map_tag_model_to_tag_entity(m) for m in result.scalars().all()]

    async def set_tag_status(self, tag_id: int, status: TagStatus) -> None:
        await self._session.execute(
            update(TagModel)
            .where(TagModel.id == tag_id)
            .values(status=status)
            .execution_options(synchronize_session=False)
        )

    async def merge_tag(self, source_id: int, target_id: int) -> None:
        # Aliases of the source move first, so no alias ever points at another alias.
        await self._session.execute(
            update(TagModel)
            .where(TagModel.merged_into_tag_id == source_id)
            .values(merged_into_tag_id=target_id)
            .execution_options(synchronize_session=False)
        )
        source_usage = select(TagModel.usage_count).where(TagModel.id == source_id).scalar_subquery()
        await self._session.execute(
            update(TagModel)
            .where(TagModel.id == target_id)
            .values(usage_count=TagModel.usage_count + source_usage)
            .execution_options(synchronize_session=False)
        )
        await self._session.execute(
            update(TagModel)
            .where(TagModel.id == source_id)
            .values(status=TagStatus.MERGED, merged_into_tag_id=target_id)
            .execution_options(synchronize_session=False)
        )

    async def copy_tag_scopes(self, source_id: int, target_id: int) -> None:
        stmt = (
            pg_insert(TagScopeModel)
            .from_select(
                ["tag_id", "category_id", "role_id"],
                select(literal(target_id, BigInteger), TagScopeModel.category_id, TagScopeModel.role_id).where(
                    TagScopeModel.tag_id == source_id
                ),
            )
            .on_conflict_do_nothing(index_elements=["tag_id", "category_id", "role_id"])
        )
        await self._session.execute(stmt)

    async def list_pending_tags(self, limit: int, offset: int) -> list[PendingTagEntity]:
        profiles_count = (
            select(func.count())
            .select_from(ProfileTagModel)
            .where(ProfileTagModel.tag_id == TagModel.id)
            .scalar_subquery()
        )
        rows = (
            await self._session.execute(
                select(TagModel, profiles_count, UserModel.first_name, UserModel.username)
                .outerjoin(UserModel, UserModel.id == TagModel.created_by_user_id)
                .where(TagModel.status == TagStatus.PENDING)
                .order_by(TagModel.created_at, TagModel.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()

        tag_ids = [row[0].id for row in rows]
        scopes_by_tag: dict[int, list[TagScopeModel]] = {tag_id: [] for tag_id in tag_ids}
        if tag_ids:
            scope_rows = await self._session.execute(
                select(TagScopeModel)
                .where(TagScopeModel.tag_id.in_(tag_ids))
                .order_by(TagScopeModel.category_id, TagScopeModel.role_id.asc().nulls_first(), TagScopeModel.id)
            )
            for scope in scope_rows.scalars():
                scopes_by_tag[scope.tag_id].append(scope)

        return [
            map_pending_tag_row_to_pending_tag_entity(tag, count, first_name, username, scopes_by_tag[tag.id])
            for tag, count, first_name, username in rows
        ]

    async def count_pending_tags(self) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(TagModel).where(TagModel.status == TagStatus.PENDING)
        )
        return result.scalar_one()
