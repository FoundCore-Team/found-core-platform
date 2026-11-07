from app.domain.entities import AdminActionEntity
from app.domain.enums import AdminTargetType
from app.domain.interfaces import IAdminActionRepository


class ListAdminActionsUseCase:
    """A page of the admin journal, newest first, optionally for one target. Read-only: it never commits."""

    def __init__(self, admin_action_repository: IAdminActionRepository) -> None:
        self._admin_action_repository = admin_action_repository

    async def execute(
        self,
        limit: int,
        offset: int,
        target_type: AdminTargetType | None,
        target_id: int | None,
    ) -> tuple[list[AdminActionEntity], int]:
        """Returns the page and the total number of rows matching the same filter."""
        actions = await self._admin_action_repository.list_actions(limit, offset, target_type, target_id)
        total = await self._admin_action_repository.count(target_type, target_id)
        return actions, total
