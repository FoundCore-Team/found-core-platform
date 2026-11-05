from datetime import datetime

from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import ContactOpenEntity, ContactWindowUsage, FeedViewer
from app.domain.enums import NotificationStatus
from app.domain.interfaces import IContactOpenRepository
from app.infrastructure.mappers.contact_open_mapper import map_contact_open_model_to_contact_open_entity
from app.infrastructure.models import ContactOpenModel, ProfileModel, UserModel
from app.infrastructure.repositories.profile_visibility import visible_to


class SqlAlchemyContactOpenRepository(IContactOpenRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_visible_contact(self, viewer: FeedViewer, profile_id: int) -> str | None:
        # The same conditions as the feed (`visible_to`), in the same query that reads the username: no gap between
        # "may I see it" and "what is it". The feed's own queries never select `username`, this is the one that does.
        result = await self._session.execute(
            select(UserModel.username)
            .select_from(ProfileModel)
            .join(UserModel, UserModel.id == ProfileModel.user_id)
            .where(ProfileModel.id == profile_id, *visible_to(viewer))
        )
        return result.scalar_one_or_none()

    async def exists(self, opener_profile_id: int, opened_profile_id: int) -> bool:
        result = await self._session.execute(
            select(
                exists().where(
                    ContactOpenModel.opener_profile_id == opener_profile_id,
                    ContactOpenModel.opened_profile_id == opened_profile_id,
                )
            )
        )
        return bool(result.scalar())

    async def get_window_usage(self, user_id: int, since: datetime) -> ContactWindowUsage:
        # The window is per person, not per profile, so a second profile does not get a second limit: the opener's
        # profile is joined to its owner.
        result = await self._session.execute(
            select(func.count(), func.min(ContactOpenModel.created_at))
            .select_from(ContactOpenModel)
            .join(ProfileModel, ProfileModel.id == ContactOpenModel.opener_profile_id)
            .where(ProfileModel.user_id == user_id, ContactOpenModel.created_at > since)
        )
        count, oldest = result.one()
        return ContactWindowUsage(count=count, oldest_opened_at=oldest)

    async def add(self, opener_profile_id: int, opened_profile_id: int, now: datetime) -> ContactOpenEntity | None:
        stmt = (
            pg_insert(ContactOpenModel)
            .values(
                opener_profile_id=opener_profile_id,
                opened_profile_id=opened_profile_id,
                created_at=now,
                notification_status=NotificationStatus.PENDING,
                notification_updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["opener_profile_id", "opened_profile_id"])
            .returning(ContactOpenModel)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return map_contact_open_model_to_contact_open_entity(model) if model else None
