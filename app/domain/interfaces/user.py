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
    async def lock_for_update(self, user_id: int) -> None:
        """Takes a row lock on the user until the end of the transaction, so that scenarios of one person that must
        not overlap (counting and spending a limit) run one after another."""
