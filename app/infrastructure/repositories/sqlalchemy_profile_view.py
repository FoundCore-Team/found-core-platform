from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces import IProfileViewRepository
from app.infrastructure.models import ProfileViewModel


class SqlAlchemyProfileViewRepository(IProfileViewRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, viewer_profile_id: int, viewed_profile_id: int, viewed_at: datetime) -> bool:
        result = await self._session.execute(
            pg_insert(ProfileViewModel)
            .values(
                viewer_profile_id=viewer_profile_id,
                viewed_profile_id=viewed_profile_id,
                viewed_at=viewed_at,
            )
            .on_conflict_do_nothing(index_elements=["viewer_profile_id", "viewed_profile_id"])
            .returning(ProfileViewModel.viewer_profile_id)
        )
        # A conflict returns no row: the view had been stored before, by an earlier or a parallel request.
        return result.first() is not None
