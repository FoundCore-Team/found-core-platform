from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import AdminActionEntity, NewAdminActionEntity
from app.domain.enums import AdminTargetType
from app.domain.interfaces import IAdminActionRepository
from app.infrastructure.mappers.admin_action_mapper import map_admin_action_model_to_admin_action_entity
from app.infrastructure.models import AdminActionModel


class SqlAlchemyAdminActionRepository(IAdminActionRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _target_filters(target_type: AdminTargetType | None, target_id: int | None) -> list[ColumnElement[bool]]:
        filters: list[ColumnElement[bool]] = []
        if target_type is not None:
            filters.append(AdminActionModel.target_type == target_type)
        if target_id is not None:
            filters.append(AdminActionModel.target_id == target_id)
        return filters

    async def add(self, action: NewAdminActionEntity) -> AdminActionEntity:
        model = AdminActionModel(
            actor_user_id=action.actor_user_id,
            action=action.action,
            target_type=action.target_type,
            target_id=action.target_id,
            reason=action.reason,
            details=action.details,
            created_at=action.created_at,
        )
        self._session.add(model)
        # Flush assigns the id; the commit stays with the use case.
        await self._session.flush()
        return map_admin_action_model_to_admin_action_entity(model)

    async def list_actions(
        self,
        limit: int,
        offset: int,
        target_type: AdminTargetType | None,
        target_id: int | None,
    ) -> list[AdminActionEntity]:
        result = await self._session.execute(
            select(AdminActionModel)
            .where(*self._target_filters(target_type, target_id))
            # `id` is the unique tie-breaker: rows created in the same instant keep one order on every call.
            .order_by(AdminActionModel.created_at.desc(), AdminActionModel.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return [map_admin_action_model_to_admin_action_entity(model) for model in result.scalars()]

    async def count(self, target_type: AdminTargetType | None, target_id: int | None) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(AdminActionModel).where(*self._target_filters(target_type, target_id))
        )
        return result.scalar_one()
