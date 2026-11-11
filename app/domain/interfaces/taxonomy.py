from abc import ABC, abstractmethod

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
        The rows stay locked `FOR KEY SHARE` until the transaction ends: a moderator's merge or reject has to wait
        for the save that is using these tags, and a save that started after the moderation sees its result.
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

    @abstractmethod
    async def lock_tags_for_update(self, tag_ids: list[int]) -> list[TagEntity]:
        """Locks the tags (`FOR UPDATE`, in id order) and returns their current state, ordered by id.

        Waits for a concurrent profile save that holds `FOR KEY SHARE` on them. Ids that do not exist are absent.
        """

    @abstractmethod
    async def set_tag_status(self, tag_id: int, status: TagStatus) -> None:
        """Plain status change (approve/reject). Merging goes through `merge_tag`."""

    @abstractmethod
    async def merge_tag(self, source_id: int, target_id: int) -> None:
        """Turns `source_id` into an alias of `target_id`.

        Sets the source to MERGED with `merged_into_tag_id`, adds the source's `usage_count` to the target and
        redirects aliases that pointed at the source to the target, so alias chains never form.
        """

    @abstractmethod
    async def copy_tag_scopes(self, source_id: int, target_id: int) -> None:
        """Gives the target every scope of the source it does not have yet."""

    @abstractmethod
    async def list_pending_tags(self, limit: int, offset: int) -> list[PendingTagEntity]:
        """Pending tags, oldest first (`created_at, id`)."""

    @abstractmethod
    async def count_pending_tags(self) -> int: ...
