from dataclasses import replace

from app.domain.entities import NewUserEntity, UserEntity
from app.domain.interfaces import IUserRepository


class FakeUserRepository(IUserRepository):
    """In-memory stand-in for SqlAlchemyUserRepository, used to unit-test use
    cases without a database. Implements the real IUserRepository ABC so a
    signature drift there fails these tests too.

    Like the real `create` (`INSERT ... ON CONFLICT DO NOTHING`), it returns None
    when the telegram_id already exists. `lose_race_once` simulates a concurrent
    first login winning the INSERT: the next `create` stores the user as if
    another request had inserted it first and returns None.
    """

    def __init__(self, users: list[UserEntity] | None = None) -> None:
        self._users: dict[int, UserEntity] = {u.id: replace(u) for u in (users or [])}
        self._next_id = max(self._users, default=0) + 1
        self.lose_race_once = False
        self.locked_user_ids: list[int] = []

    @property
    def users(self) -> list[UserEntity]:
        return [replace(u) for u in self._users.values()]

    async def get_by_telegram_id(self, telegram_id: int) -> UserEntity | None:
        for user in self._users.values():
            if user.telegram_id == telegram_id:
                return replace(user)
        return None

    async def get_by_id(self, user_id: int) -> UserEntity | None:
        user = self._users.get(user_id)
        return replace(user) if user else None

    async def create(self, user: NewUserEntity) -> UserEntity | None:
        exists = await self.get_by_telegram_id(user.telegram_id) is not None
        if self.lose_race_once and not exists:
            self.lose_race_once = False
            self._store(user)  # the other request's insert
            return None
        if exists:
            return None
        return self._store(user)

    async def update(self, user: UserEntity) -> UserEntity:
        # Same columns as the real repository: ban/admin/token_version/profile fields are never written here.
        stored = self._users[user.id]
        stored.first_name = user.first_name
        stored.last_name = user.last_name
        stored.username = user.username
        stored.photo_url = user.photo_url
        stored.language_code = user.language_code
        stored.last_active_at = user.last_active_at
        return replace(stored)

    async def set_active_profile(self, user_id: int, profile_id: int | None) -> None:
        self._users[user_id].active_profile_id = profile_id

    async def set_ban(self, user_id: int, reason: str) -> UserEntity | None:
        # Same condition as the real conditional UPDATE: never bans an admin, an already banned or a deleted user.
        user = self._users.get(user_id)
        if user is None or user.is_banned or user.is_admin or user.deleted_at is not None:
            return None
        user.is_banned = True
        user.ban_reason = reason
        user.token_version += 1
        return replace(user)

    async def clear_ban(self, user_id: int) -> UserEntity | None:
        user = self._users.get(user_id)
        if user is None or not user.is_banned:
            return None
        user.is_banned = False
        user.ban_reason = None
        return replace(user)

    async def set_admin(self, user_id: int, is_admin: bool) -> UserEntity | None:
        user = self._users.get(user_id)
        if user is None or user.is_admin == is_admin or (is_admin and user.is_banned):
            return None
        user.is_admin = is_admin
        return replace(user)

    async def lock_for_update(self, user_id: int) -> None:
        self.locked_user_ids.append(user_id)

    def _store(self, user: NewUserEntity) -> UserEntity:
        entity = UserEntity(
            id=self._next_id,
            telegram_id=user.telegram_id,
            first_name=user.first_name,
            created_at=user.created_at,
            last_active_at=user.last_active_at,
            last_name=user.last_name,
            username=user.username,
            photo_url=user.photo_url,
            language_code=user.language_code,
        )
        self._users[entity.id] = entity
        self._next_id += 1
        return entity
