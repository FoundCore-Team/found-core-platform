from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import NewUserEntity, UserEntity
from app.domain.interfaces import IUserRepository
from app.infrastructure.mappers.user_mapper import (
    apply_user_entity_to_user_model,
    map_user_model_to_user_entity,
)
from app.infrastructure.models import UserModel


class SqlAlchemyUserRepository(IUserRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> UserEntity | None:
        result = await self._session.execute(
            select(UserModel).where(UserModel.telegram_id == telegram_id)
        )
        model = result.scalar_one_or_none()
        return map_user_model_to_user_entity(model) if model else None

    async def get_by_id(self, user_id: int) -> UserEntity | None:
        result = await self._session.execute(select(UserModel).where(UserModel.id == user_id))
        model = result.scalar_one_or_none()
        return map_user_model_to_user_entity(model) if model else None

    async def create(self, user: NewUserEntity) -> UserEntity | None:
        stmt = (
            pg_insert(UserModel)
            .values(
                telegram_id=user.telegram_id,
                first_name=user.first_name,
                created_at=user.created_at,
                last_active_at=user.last_active_at,
                last_name=user.last_name,
                username=user.username,
                photo_url=user.photo_url,
                language_code=user.language_code,
            )
            .on_conflict_do_nothing(index_elements=["telegram_id"])
            .returning(UserModel)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return map_user_model_to_user_entity(model) if model else None

    async def update(self, user: UserEntity) -> UserEntity:
        result = await self._session.execute(select(UserModel).where(UserModel.id == user.id))
        model = result.scalar_one()
        apply_user_entity_to_user_model(user, model)
        await self._session.flush()
        await self._session.refresh(model)
        return map_user_model_to_user_entity(model)

    async def set_active_profile(self, user_id: int, profile_id: int | None) -> None:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(active_profile_id=profile_id)
        )

    async def lock_for_update(self, user_id: int) -> None:
        # Selects only the id column, not the model: nothing is loaded into the session's identity map, so there is no
        # stale object to confuse a later read. The lock is held until the transaction commits or rolls back.
        await self._session.execute(select(UserModel.id).where(UserModel.id == user_id).with_for_update())
