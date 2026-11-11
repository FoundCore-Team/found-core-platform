from abc import ABC, abstractmethod

from app.domain.entities import NewUserEntity, UserEntity


class IUserRepository(ABC):
    @abstractmethod
    async def get_by_telegram_id(self, telegram_id: int) -> UserEntity | None: ...

    @abstractmethod
    async def get_by_id(self, user_id: int) -> UserEntity | None: ...

    @abstractmethod
    async def create(self, user: NewUserEntity) -> UserEntity | None: ...

    @abstractmethod
    async def update(self, user: UserEntity) -> UserEntity: ...

    @abstractmethod
    async def set_active_profile(self, user_id: int, profile_id: int | None) -> None: ...

    @abstractmethod
    async def set_ban(self, user_id: int, reason: str) -> UserEntity | None:
        """Bans a user: `is_banned`, `ban_reason` and `token_version + 1` in one statement, so every token issued
        before the ban stops working. Applies only to a user who is not banned, not an admin and not deleted;
        returns None when that does not hold (the use case re-reads and decides what it means)."""

    @abstractmethod
    async def clear_ban(self, user_id: int) -> UserEntity | None:
        """Lifts a ban: `is_banned = false`, `ban_reason = NULL`; `token_version` is left alone. Applies only to a
        banned user; returns None otherwise."""

    @abstractmethod
    async def set_admin(self, user_id: int, is_admin: bool) -> UserEntity | None:
        """Grants (only to a user who is not an admin and not banned) or revokes (only from an admin) the admin role.
        Returns None when the user is not in the state the change expects."""

    @abstractmethod
    async def lock_for_update(self, user_id: int) -> None:
        """Takes a row lock on the user until the end of the transaction, so that scenarios of one person that must
        not overlap (counting and spending a limit) run one after another."""
