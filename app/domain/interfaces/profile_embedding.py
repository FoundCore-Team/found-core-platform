from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.entities import ProfileEmbeddingEntity
from app.domain.enums import EmbeddingStatus


class IProfileEmbeddingRepository(ABC):
    """`save` and `set_outcome` return False if the row is missing or its `source_updated_at` differs from the expected one."""

    @abstractmethod
    async def get(self, profile_id: int) -> ProfileEmbeddingEntity | None: ...

    @abstractmethod
    async def mark_pending(self, profile_id: int, source_updated_at: datetime) -> None:
        """Creates the row or resets an existing one to pending with a new `source_updated_at`.

        A stored vector, hash and model are kept: the old vector stays usable until a new one replaces it.
        """

    @abstractmethod
    async def mark_pending_many(self, sources: dict[int, datetime]) -> None:
        """`mark_pending` for many profiles at once; `sources` maps profile id to its `source_updated_at`."""

    @abstractmethod
    async def save(
        self,
        profile_id: int,
        embedding: list[float],
        input_hash: str,
        model: str,
        expected_source_updated_at: datetime,
    ) -> bool:
        """Stores the vector and sets the status to ready."""

    @abstractmethod
    async def set_outcome(
        self,
        profile_id: int,
        status: EmbeddingStatus,
        expected_source_updated_at: datetime,
    ) -> bool:
        """Sets READY (the stored vector is still up to date) or FAILED without touching the vector."""

    @abstractmethod
    async def claim_stale(
        self,
        pending_before: datetime,
        failed_before: datetime,
        model: str,
        limit: int,
    ) -> list[int]:
        """Ids of rows that need a new job; their `updated_at` is refreshed so concurrent callers never get the same row.

        Selects pending rows older than `pending_before`, failed rows older than `failed_before`
        and ready rows of another `model` older than `pending_before`.
        """
