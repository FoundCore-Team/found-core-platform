from datetime import datetime

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import ProfileEmbeddingEntity
from app.domain.enums import EmbeddingStatus
from app.domain.interfaces import IProfileEmbeddingRepository
from app.infrastructure.mappers.profile_embedding_mapper import (
    map_profile_embedding_model_to_profile_embedding_entity,
)
from app.infrastructure.models import ProfileEmbeddingModel


class SqlAlchemyProfileEmbeddingRepository(IProfileEmbeddingRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, profile_id: int) -> ProfileEmbeddingEntity | None:
        stmt = (
            select(ProfileEmbeddingModel)
            .where(ProfileEmbeddingModel.profile_id == profile_id)
            .execution_options(populate_existing=True)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return map_profile_embedding_model_to_profile_embedding_entity(model) if model else None

    async def mark_pending(self, profile_id: int, source_updated_at: datetime) -> None:
        stmt = pg_insert(ProfileEmbeddingModel).values(
            profile_id=profile_id,
            status=EmbeddingStatus.PENDING,
            source_updated_at=source_updated_at,
            updated_at=func.now(),
        )
        # The stored vector, hash and model are left alone: the old vector stays usable until a new one replaces it.
        stmt = stmt.on_conflict_do_update(
            index_elements=["profile_id"],
            set_={
                "status": stmt.excluded.status,
                "source_updated_at": stmt.excluded.source_updated_at,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)

    async def mark_pending_many(self, sources: dict[int, datetime]) -> None:
        if not sources:
            return
        # Id order keeps the row locks of concurrent batches in the same order.
        stmt = pg_insert(ProfileEmbeddingModel).values(
            [
                {
                    "profile_id": profile_id,
                    "status": EmbeddingStatus.PENDING,
                    "source_updated_at": sources[profile_id],
                    "updated_at": func.now(),
                }
                for profile_id in sorted(sources)
            ]
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["profile_id"],
            set_={
                "status": stmt.excluded.status,
                "source_updated_at": stmt.excluded.source_updated_at,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self._session.execute(stmt)

    async def save(
        self,
        profile_id: int,
        embedding: list[float],
        input_hash: str,
        model: str,
        expected_source_updated_at: datetime,
    ) -> bool:
        # The condition is re-checked by Postgres after waiting for a concurrent edit's row lock,
        # so a vector computed from stale text cannot overwrite the pending mark that edit left.
        stmt = (
            update(ProfileEmbeddingModel)
            .where(
                ProfileEmbeddingModel.profile_id == profile_id,
                ProfileEmbeddingModel.source_updated_at == expected_source_updated_at,
            )
            .values(
                embedding=embedding,
                input_hash=input_hash,
                model=model,
                status=EmbeddingStatus.READY,
                updated_at=func.now(),
            )
            .execution_options(synchronize_session=False)
        )
        return (await self._session.execute(stmt)).rowcount == 1

    async def set_outcome(
        self,
        profile_id: int,
        status: EmbeddingStatus,
        expected_source_updated_at: datetime,
    ) -> bool:
        stmt = (
            update(ProfileEmbeddingModel)
            .where(
                ProfileEmbeddingModel.profile_id == profile_id,
                ProfileEmbeddingModel.source_updated_at == expected_source_updated_at,
            )
            .values(status=status, updated_at=func.now())
            .execution_options(synchronize_session=False)
        )
        return (await self._session.execute(stmt)).rowcount == 1

    async def claim_stale(
        self,
        pending_before: datetime,
        failed_before: datetime,
        model: str,
        limit: int,
    ) -> list[int]:
        stale = (
            select(ProfileEmbeddingModel.profile_id)
            .where(
                or_(
                    and_(
                        ProfileEmbeddingModel.status == EmbeddingStatus.PENDING,
                        ProfileEmbeddingModel.updated_at < pending_before,
                    ),
                    and_(
                        ProfileEmbeddingModel.status == EmbeddingStatus.FAILED,
                        ProfileEmbeddingModel.updated_at < failed_before,
                    ),
                    and_(
                        ProfileEmbeddingModel.status == EmbeddingStatus.READY,
                        ProfileEmbeddingModel.model != model,
                        ProfileEmbeddingModel.updated_at < pending_before,
                    ),
                )
            )
            .order_by(ProfileEmbeddingModel.updated_at, ProfileEmbeddingModel.profile_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(ProfileEmbeddingModel)
            .where(ProfileEmbeddingModel.profile_id.in_(stale))
            .values(updated_at=func.now())
            .returning(ProfileEmbeddingModel.profile_id)
            .execution_options(synchronize_session=False)
        )
        return sorted((await self._session.execute(stmt)).scalars())
