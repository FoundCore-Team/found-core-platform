from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.entities import NewProfileEntity, ProfileEntity
from app.domain.enums import ProfileStatus


class IProfileRepository(ABC):
    @abstractmethod
    async def get_by_id(self, profile_id: int) -> ProfileEntity | None: ...

    @abstractmethod
    async def get_by_user_category_role(
        self, user_id: int, category_id: int, role_id: int
    ) -> ProfileEntity | None: ...

    @abstractmethod
    async def list_by_user_id(self, user_id: int) -> list[ProfileEntity]:
        """Ordered by created_at, id."""

    @abstractmethod
    async def get_newest_active_by_user_id(self, user_id: int) -> ProfileEntity | None:
        """Newest by created_at DESC, id DESC among the user's profiles with status active."""

    @abstractmethod
    async def create(self, profile: NewProfileEntity) -> ProfileEntity | None:
        """Inserts the profile together with its tag links.

        Returns None if a profile for the same (user, category, role) already exists.
        """

    @abstractmethod
    async def update(self, profile: ProfileEntity, tag_ids: list[int]) -> ProfileEntity:
        """Writes the editable content and replaces the tag links. Never changes status, category or role."""

    @abstractmethod
    async def set_status(self, profile_id: int, status: ProfileStatus) -> bool:
        """The owner's status change (pause/resume). Never touches a `hidden_by_admin` profile: the condition is in
        the UPDATE itself, so an owner who read `paused` cannot overwrite a hide that happened a moment later.
        Returns False if nothing was changed (hidden, or the profile no longer exists)."""

    @abstractmethod
    async def hide(self, profile_id: int) -> bool:
        """`active`/`paused` -> `hidden_by_admin`. Returns False if the profile was already hidden or is gone."""

    @abstractmethod
    async def unhide(self, profile_id: int) -> bool:
        """`hidden_by_admin` -> `paused` (never straight to `active`). Returns False if it was not hidden."""

    @abstractmethod
    async def delete(self, profile_id: int) -> None: ...

    @abstractmethod
    async def detach_tag(self, tag_id: int) -> list[int]:
        """Removes the tag from every profile. Returns the ids of the profiles that had it, ascending."""

    @abstractmethod
    async def replace_tag(self, source_id: int, target_id: int) -> list[int]:
        """Moves every link from `source_id` to `target_id`; a profile that already has both keeps one link.

        Returns the ids of the profiles that had the source, ascending.
        """

    @abstractmethod
    async def touch(self, profile_ids: list[int]) -> dict[int, datetime]:
        """Sets `updated_at` of the profiles to one fresh value and returns it per profile id.

        The new value is what makes a running embedding job lose its conditional write.
        """
