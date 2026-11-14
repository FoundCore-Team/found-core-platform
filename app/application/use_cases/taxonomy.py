from app.domain.entities import (
    CategoryEntity,
    NewTagEntity,
    NewTagScopeEntity,
    RoleEntity,
    RoleFieldEntity,
    TagEntity,
)
from app.domain.enums import AnalyticsEvent, TagStatus
from app.domain.exceptions import CategoryNotFoundError, RoleNotFoundError, TagRejectedError
from app.domain.interfaces import IAnalyticsTracker, ITaxonomyCacheRepository, ITaxonomyRepository, IUnitOfWork
from app.domain.services import TagTitleValidator, clean_tag_title, normalize_tag_title


class GetCategoriesUseCase:
    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        taxonomy_cache_repository: ITaxonomyCacheRepository,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._taxonomy_cache_repository = taxonomy_cache_repository

    async def execute(self) -> list[CategoryEntity]:
        cached = await self._taxonomy_cache_repository.get_categories()
        if cached is not None:
            return cached

        categories = await self._taxonomy_repository.get_categories()
        await self._taxonomy_cache_repository.set_categories(categories)
        return categories


class GetRolesByCategoryUseCase:
    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        taxonomy_cache_repository: ITaxonomyCacheRepository,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._taxonomy_cache_repository = taxonomy_cache_repository

    async def execute(self, category_id: int) -> list[RoleEntity]:
        cached = await self._taxonomy_cache_repository.get_roles_by_category(category_id)
        if cached is not None:
            return cached

        category = await self._taxonomy_repository.get_category_by_id(category_id)
        if category is None:
            raise CategoryNotFoundError()

        roles = await self._taxonomy_repository.get_roles_by_category(category_id)
        await self._taxonomy_cache_repository.set_roles_by_category(category_id, roles)
        return roles


class GetRoleFieldsByRoleUseCase:
    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        taxonomy_cache_repository: ITaxonomyCacheRepository,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._taxonomy_cache_repository = taxonomy_cache_repository

    async def execute(self, role_id: int) -> list[RoleFieldEntity]:
        cached = await self._taxonomy_cache_repository.get_role_fields_by_role(role_id)
        if cached is not None:
            return cached

        role = await self._taxonomy_repository.get_role_by_id(role_id)
        if role is None:
            raise RoleNotFoundError()

        fields = await self._taxonomy_repository.get_role_fields_by_role(role_id)
        await self._taxonomy_cache_repository.set_role_fields_by_role(role_id, fields)
        return fields


class SuggestTagsUseCase:
    def __init__(self, taxonomy_repository: ITaxonomyRepository) -> None:
        self._taxonomy_repository = taxonomy_repository

    async def execute(
        self, query: str, category_id: int, role_id: int | None, user_id: int
    ) -> list[TagEntity]:
        category = await self._taxonomy_repository.get_category_by_id(category_id)
        if category is None:
            raise CategoryNotFoundError()
        if role_id is not None:
            role = await self._taxonomy_repository.get_role_by_id(role_id)
            if role is None or role.category_id != category_id:
                raise RoleNotFoundError()
        return await self._taxonomy_repository.suggest_tags(query, category_id, role_id, user_id)


class CreateCustomTagUseCase:
    def __init__(
        self,
        taxonomy_repository: ITaxonomyRepository,
        tag_title_validator: TagTitleValidator,
        unit_of_work: IUnitOfWork,
        analytics_tracker: IAnalyticsTracker,
    ) -> None:
        self._taxonomy_repository = taxonomy_repository
        self._tag_title_validator = tag_title_validator
        self._unit_of_work = unit_of_work
        self._analytics_tracker = analytics_tracker

    async def execute(self, title: str, category_id: int, role_id: int | None, user_id: int) -> TagEntity:
        title = clean_tag_title(title)
        self._tag_title_validator.validate(title)

        category = await self._taxonomy_repository.get_category_by_id(category_id)
        if category is None:
            raise CategoryNotFoundError()
        if role_id is not None:
            role = await self._taxonomy_repository.get_role_by_id(role_id)
            if role is None or role.category_id != category_id:
                raise RoleNotFoundError()

        normalized_title = normalize_tag_title(title)
        tag = await self._taxonomy_repository.get_tag_by_normalized_title(normalized_title)
        is_new_tag = False
        if tag is None:
            tag = await self._taxonomy_repository.create_tag(
                NewTagEntity(
                    title=title,
                    normalized_title=normalized_title,
                    status=TagStatus.PENDING,
                    created_by_user_id=user_id,
                )
            )
            # None means a concurrent request won the insert: the tag exists, but this request did not create it.
            is_new_tag = tag is not None
        if tag is None:
            # A concurrent request inserted the same normalized_title between our SELECT and INSERT.
            tag = await self._taxonomy_repository.get_tag_by_normalized_title(normalized_title)
        assert tag is not None

        if tag.status == TagStatus.MERGED:
            # The title belongs to an alias: continue with the tag it was merged into, so no new duplicate appears
            # and the caller gets the canonical title ("NodeJS" -> "Node.js").
            (tag,) = await self._taxonomy_repository.get_tags_by_ids([tag.merged_into_tag_id])

        if tag.status == TagStatus.REJECTED:
            raise TagRejectedError()

        await self._taxonomy_repository.create_tag_scope(
            NewTagScopeEntity(tag_id=tag.id, category_id=category_id, role_id=role_id)
        )
        await self._unit_of_work.commit()
        if is_new_tag:
            # Reusing an existing tag (or following an alias) adds only a scope, which is not a new tag. The title
            # stays out: the id leads to it in Postgres.
            self._analytics_tracker.track(
                AnalyticsEvent.TAG_CREATED,
                user_id,
                {"tag_id": tag.id, "category_id": category_id, "role_id": role_id},
            )
        return tag
