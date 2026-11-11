from abc import ABC, abstractmethod

from app.domain.entities import AdminActionEntity, NewAdminActionEntity
from app.domain.enums import AdminTargetType


class IAdminActionRepository(ABC):
    @abstractmethod
    async def add(self, action: NewAdminActionEntity) -> AdminActionEntity:
        """Adds a journal row to the current transaction. Does not commit: the use case commits it together with the
        change it records, so the journal and the state can never disagree."""

    @abstractmethod
    async def list_actions(
        self,
        limit: int,
        offset: int,
        target_type: AdminTargetType | None,
        target_id: int | None,
    ) -> list[AdminActionEntity]:
        """A page of the journal, newest first (`created_at DESC, id DESC`), optionally for one target."""

    @abstractmethod
    async def count(self, target_type: AdminTargetType | None, target_id: int | None) -> int:
        """How many journal rows match the same filter as `list_actions`."""
