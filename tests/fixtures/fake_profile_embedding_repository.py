from dataclasses import replace
from datetime import datetime, timezone

from app.domain.entities import ProfileEmbeddingEntity
from app.domain.enums import EmbeddingStatus
from app.domain.interfaces import IProfileEmbeddingRepository


class FakeProfileEmbeddingRepository(IProfileEmbeddingRepository):
    """In-memory stand-in for SqlAlchemyProfileEmbeddingRepository. Implements the real ABC.

    Writes are conditional on `source_updated_at` exactly like the real UPDATE ... WHERE:
    they return False for a missing row or a profile edited after it was read.
    Vectors are kept apart from the entities, as in the real table, and read through `vector_of`.
    """

    def __init__(self, rows: list[ProfileEmbeddingEntity] | None = None) -> None:
        self._rows: dict[int, ProfileEmbeddingEntity] = {r.profile_id: replace(r) for r in (rows or [])}
        self._vectors: dict[int, list[float]] = {}

    @property
    def rows(self) -> list[ProfileEmbeddingEntity]:
        return [replace(r) for r in self._rows.values()]

    def vector_of(self, profile_id: int) -> list[float] | None:
        vector = self._vectors.get(profile_id)
        return list(vector) if vector is not None else None

    async def get(self, profile_id: int) -> ProfileEmbeddingEntity | None:
        row = self._rows.get(profile_id)
        return replace(row) if row else None

    async def mark_pending(self, profile_id: int, source_updated_at: datetime) -> None:
        now = datetime.now(timezone.utc)
        row = self._rows.get(profile_id)
        if row is None:
            self._rows[profile_id] = ProfileEmbeddingEntity(
                profile_id=profile_id,
                input_hash=None,
                model=None,
                status=EmbeddingStatus.PENDING,
                source_updated_at=source_updated_at,
                updated_at=now,
            )
            return
        # The stored vector, hash and model stay: the old vector is usable until a new one replaces it.
        row.status = EmbeddingStatus.PENDING
        row.source_updated_at = source_updated_at
        row.updated_at = now

    async def mark_pending_many(self, sources: dict[int, datetime]) -> None:
        for profile_id in sorted(sources):
            await self.mark_pending(profile_id, sources[profile_id])

    async def save(
        self,
        profile_id: int,
        embedding: list[float],
        input_hash: str,
        model: str,
        expected_source_updated_at: datetime,
    ) -> bool:
        row = self._rows.get(profile_id)
        if row is None or row.source_updated_at != expected_source_updated_at:
            return False
        row.input_hash = input_hash
        row.model = model
        row.status = EmbeddingStatus.READY
        row.updated_at = datetime.now(timezone.utc)
        self._vectors[profile_id] = list(embedding)
        return True

    async def set_outcome(
        self,
        profile_id: int,
        status: EmbeddingStatus,
        expected_source_updated_at: datetime,
    ) -> bool:
        row = self._rows.get(profile_id)
        if row is None or row.source_updated_at != expected_source_updated_at:
            return False
        row.status = status
        row.updated_at = datetime.now(timezone.utc)
        return True

    async def claim_stale(
        self,
        pending_before: datetime,
        failed_before: datetime,
        model: str,
        limit: int,
    ) -> list[int]:
        stale = [
            r
            for r in self._rows.values()
            if (r.status == EmbeddingStatus.PENDING and r.updated_at < pending_before)
            or (r.status == EmbeddingStatus.FAILED and r.updated_at < failed_before)
            or (r.status == EmbeddingStatus.READY and r.model != model and r.updated_at < pending_before)
        ]
        claimed = sorted(stale, key=lambda r: (r.updated_at, r.profile_id))[:limit]
        now = datetime.now(timezone.utc)
        for row in claimed:
            row.updated_at = now
        return [r.profile_id for r in claimed]
