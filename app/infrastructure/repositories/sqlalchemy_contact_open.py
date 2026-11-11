from datetime import datetime

from sqlalchemy import exists, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domain.entities import ContactNotificationContext, ContactOpenEntity, ContactWindowUsage, FeedViewer
from app.domain.enums import NotificationStatus, ProfileStatus
from app.domain.interfaces import IContactOpenRepository
from app.infrastructure.mappers.contact_open_mapper import (
    map_contact_notification_row_to_contact_notification_context,
    map_contact_open_model_to_contact_open_entity,
)
from app.infrastructure.models import CategoryModel, ContactOpenModel, ProfileModel, RoleModel, UserModel
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
            .where(ProfileModel.id == profile_id, *visible_to(viewer.user_id))
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

    async def get_notification_context(
        self, opener_profile_id: int, opened_profile_id: int
    ) -> ContactNotificationContext | None:
        opener_profile = aliased(ProfileModel)
        owner_profile = aliased(ProfileModel)
        opener_user = aliased(UserModel)
        owner_user = aliased(UserModel)
        opener_category = aliased(CategoryModel)
        owner_category = aliased(CategoryModel)
        opener_role = aliased(RoleModel)
        owner_role = aliased(RoleModel)

        # One read of the open and of both sides, as they are now. Inner joins are safe: the profiles, the users and
        # the taxonomy rows cannot disappear without the open (profiles cascade, taxonomy rows cannot be deleted
        # while profiles use them).
        stmt = (
            select(
                ContactOpenModel.notification_status,
                ContactOpenModel.created_at,
                opener_user.is_banned.label("opener_is_banned"),
                (opener_user.deleted_at.is_not(None)).label("opener_is_deleted"),
                (opener_profile.status == ProfileStatus.HIDDEN_BY_ADMIN).label("opener_profile_is_hidden"),
                opener_user.first_name.label("opener_first_name"),
                opener_user.last_name.label("opener_last_name"),
                opener_user.username.label("opener_username"),
                owner_user.is_banned.label("owner_is_banned"),
                (owner_user.deleted_at.is_not(None)).label("owner_is_deleted"),
                owner_user.telegram_id.label("owner_telegram_id"),
                opener_category.title.label("opener_category_title"),
                opener_role.title.label("opener_role_title"),
                owner_category.title.label("owner_category_title"),
                owner_role.title.label("owner_role_title"),
            )
            .select_from(ContactOpenModel)
            .join(opener_profile, opener_profile.id == ContactOpenModel.opener_profile_id)
            .join(opener_user, opener_user.id == opener_profile.user_id)
            .join(opener_category, opener_category.id == opener_profile.category_id)
            .join(opener_role, opener_role.id == opener_profile.role_id)
            .join(owner_profile, owner_profile.id == ContactOpenModel.opened_profile_id)
            .join(owner_user, owner_user.id == owner_profile.user_id)
            .join(owner_category, owner_category.id == owner_profile.category_id)
            .join(owner_role, owner_role.id == owner_profile.role_id)
            .where(
                ContactOpenModel.opener_profile_id == opener_profile_id,
                ContactOpenModel.opened_profile_id == opened_profile_id,
            )
        )
        row = (await self._session.execute(stmt)).one_or_none()
        if row is None:
            return None
        return map_contact_notification_row_to_contact_notification_context(row, opener_profile_id)

    async def settle_notification(
        self, opener_profile_id: int, opened_profile_id: int, status: NotificationStatus, now: datetime
    ) -> bool:
        # The `pending` condition is re-checked by Postgres, so two jobs for one pair cannot both settle it.
        stmt = (
            update(ContactOpenModel)
            .where(
                ContactOpenModel.opener_profile_id == opener_profile_id,
                ContactOpenModel.opened_profile_id == opened_profile_id,
                ContactOpenModel.notification_status == NotificationStatus.PENDING,
            )
            .values(notification_status=status, notification_updated_at=now)
            .execution_options(synchronize_session=False)
        )
        return (await self._session.execute(stmt)).rowcount == 1

    async def claim_stale_pending(self, pending_before: datetime, now: datetime, limit: int) -> list[tuple[int, int]]:
        stale = (
            select(ContactOpenModel.opener_profile_id, ContactOpenModel.opened_profile_id)
            .where(
                ContactOpenModel.notification_status == NotificationStatus.PENDING,
                ContactOpenModel.notification_updated_at < pending_before,
            )
            .order_by(ContactOpenModel.notification_updated_at, ContactOpenModel.opener_profile_id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        stmt = (
            update(ContactOpenModel)
            .where(tuple_(ContactOpenModel.opener_profile_id, ContactOpenModel.opened_profile_id).in_(stale))
            .values(notification_updated_at=now)
            .returning(ContactOpenModel.opener_profile_id, ContactOpenModel.opened_profile_id)
            .execution_options(synchronize_session=False)
        )
        return sorted((row[0], row[1]) for row in (await self._session.execute(stmt)).all())
