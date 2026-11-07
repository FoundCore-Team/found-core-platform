from dataclasses import replace

from app.domain.entities import AdminActionEntity, NewAdminActionEntity
from app.domain.enums import AdminTargetType
from app.domain.interfaces import IAdminActionRepository


class FakeAdminActionRepository(IAdminActionRepository):
    """In-memory stand-in for SqlAlchemyAdminActionRepository. Implements the real ABC, assigns ids, hands out copies
    and orders like the real one (`created_at DESC, id DESC`).

    `fail_on_add` makes `add` raise, to check that a use case which cannot write its journal row commits nothing.
    """

    def __init__(self) -> None:
        self._actions: list[AdminActionEntity] = []
        self.fail_on_add: Exception | None = None

    @property
    def actions(self) -> list[AdminActionEntity]:
        return [replace(action, details=dict(action.details)) for action in self._actions]

    def _matching(self, target_type: AdminTargetType | None, target_id: int | None) -> list[AdminActionEntity]:
        return [
            action
            for action in self._actions
            if (target_type is None or action.target_type == target_type)
            and (target_id is None or action.target_id == target_id)
        ]

    async def add(self, action: NewAdminActionEntity) -> AdminActionEntity:
        if self.fail_on_add is not None:
            raise self.fail_on_add
        entity = AdminActionEntity(
            id=len(self._actions) + 1,
            actor_user_id=action.actor_user_id,
            action=action.action,
            target_type=action.target_type,
            target_id=action.target_id,
            reason=action.reason,
            details=dict(action.details),
            created_at=action.created_at,
        )
        self._actions.append(entity)
        return replace(entity, details=dict(entity.details))

    async def list_actions(
        self,
        limit: int,
        offset: int,
        target_type: AdminTargetType | None,
        target_id: int | None,
    ) -> list[AdminActionEntity]:
        ordered = sorted(self._matching(target_type, target_id), key=lambda a: (a.created_at, a.id), reverse=True)
        return [replace(a, details=dict(a.details)) for a in ordered[offset : offset + limit]]

    async def count(self, target_type: AdminTargetType | None, target_id: int | None) -> int:
        return len(self._matching(target_type, target_id))
