from abc import ABC, abstractmethod

from app.domain.entities import (
    CategoryEntity,
    NewTagEntity,
    NewTagScopeEntity,
    RoleEntity,
    RoleFieldEntity,
    TagEntity,
)


class ITaxonomyRepository(ABC):
    @abstractmethod
    async def get_categories(self) -> list[CategoryEntity]: ...

    @abstractmethod
    async def get_category_by_id(self, category_id: int) -> CategoryEntity | None: ...

    @abstractmethod
    async def get_roles_by_category(self, category_id: int) -> list[RoleEntity]: ...

    @abstractmethod
    async def get_role_by_id(self, role_id: int) -> RoleEntity | None: ...

    @abstractmethod
    async def get_role_fields_by_role(self, role_id: int) -> list[RoleFieldEntity]: ...

    @abstractmethod
    async def suggest_tags(
        self, query: str, category_id: int, role_id: int | None, user_id: int
    ) -> list[TagEntity]: ...

    @abstractmethod
    async def get_tag_by_normalized_title(self, normalized_title: str) -> TagEntity | None: ...

    @abstractmethod
    async def get_tags_in_scope_by_ids(
        self, tag_ids: list[int], category_id: int, role_id: int
    ) -> list[TagEntity]:
        """Tags of any status among `tag_ids` scoped to the role or to its whole category.

        Ids that do not exist or are out of scope are simply absent from the result.
        """

    @abstractmethod
    async def get_tags_by_ids(self, tag_ids: list[int]) -> list[TagEntity]:
        """Tags of any status among `tag_ids`, ordered by id. Ids that do not exist are simply absent."""

    @abstractmethod
    async def create_tag(self, tag: NewTagEntity) -> TagEntity | None:
        """Returns None if a tag with the same normalized_title already exists."""

    @abstractmethod
    async def increment_tags_usage(self, tag_ids: list[int]) -> None:
        """Adds 1 to `usage_count` of every listed tag. The counter is never decreased."""

    @abstractmethod
    async def create_tag_scope(self, scope: NewTagScopeEntity) -> None:
        """Idempotent: does nothing if the scope already exists."""
