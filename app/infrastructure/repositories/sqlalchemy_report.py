from datetime import datetime

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.entities import NewReportEntity, ReportEntity, ReportGroupEntity
from app.domain.enums import ReportReason, ReportStatus
from app.domain.interfaces import IReportRepository
from app.infrastructure.mappers.report_mapper import (
    map_report_group_row_to_report_group_entity,
    map_report_model_to_report_entity,
)
from app.infrastructure.models import ProfileModel, ReportModel, UserModel
from app.infrastructure.repositories.profile_visibility import visible_to


class SqlAlchemyReportRepository(IReportRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_profile_visible_to(self, viewer_user_id: int, profile_id: int) -> bool:
        result = await self._session.execute(
            select(ProfileModel.id)
            .join(UserModel, UserModel.id == ProfileModel.user_id)
            .where(ProfileModel.id == profile_id, *visible_to(viewer_user_id))
        )
        return result.scalar_one_or_none() is not None

    async def create(self, report: NewReportEntity) -> ReportEntity | None:
        stmt = (
            pg_insert(ReportModel)
            .values(
                reporter_user_id=report.reporter_user_id,
                profile_id=report.profile_id,
                reason=report.reason,
                comment=report.comment,
                created_at=report.created_at,
            )
            # The predicate must match the partial unique index exactly, otherwise Postgres cannot infer it.
            .on_conflict_do_nothing(
                index_elements=["reporter_user_id", "profile_id"],
                index_where=text("status = 'open'"),
            )
            .returning(ReportModel)
        )
        model = (await self._session.execute(stmt)).scalar_one_or_none()
        return map_report_model_to_report_entity(model) if model is not None else None

    async def get_open_by_reporter_and_profile(self, reporter_user_id: int, profile_id: int) -> ReportEntity | None:
        result = await self._session.execute(
            select(ReportModel).where(
                ReportModel.reporter_user_id == reporter_user_id,
                ReportModel.profile_id == profile_id,
                ReportModel.status == ReportStatus.OPEN,
            )
        )
        model = result.scalar_one_or_none()
        return map_report_model_to_report_entity(model) if model is not None else None

    async def list_open_groups(self, limit: int, offset: int) -> list[ReportGroupEntity]:
        open_count = func.count(ReportModel.id)
        oldest_at = func.min(ReportModel.created_at)
        rows = (
            await self._session.execute(
                select(
                    ProfileModel.id.label("profile_id"),
                    ProfileModel.status.label("profile_status"),
                    ProfileModel.category_id,
                    ProfileModel.role_id,
                    UserModel.id.label("owner_user_id"),
                    UserModel.first_name.label("owner_first_name"),
                    UserModel.username.label("owner_username"),
                    UserModel.is_banned.label("owner_is_banned"),
                    open_count.label("open_count"),
                    oldest_at.label("oldest_at"),
                    func.max(ReportModel.created_at).label("newest_at"),
                )
                .select_from(ReportModel)
                .join(ProfileModel, ProfileModel.id == ReportModel.profile_id)
                .join(UserModel, UserModel.id == ProfileModel.user_id)
                .where(ReportModel.status == ReportStatus.OPEN)
                # Both primary keys: Postgres then allows the other columns of these two tables in the SELECT.
                .group_by(ProfileModel.id, UserModel.id)
                # `profile_id` is the unique tie-breaker: equal groups keep one order on every call.
                .order_by(open_count.desc(), oldest_at.asc(), ProfileModel.id.asc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
        if not rows:
            return []

        # The breakdown by reason is a second query, only for the profiles of this page.
        reason_rows = (
            await self._session.execute(
                select(ReportModel.profile_id, ReportModel.reason, func.count())
                .where(
                    ReportModel.status == ReportStatus.OPEN,
                    ReportModel.profile_id.in_([row.profile_id for row in rows]),
                )
                .group_by(ReportModel.profile_id, ReportModel.reason)
            )
        ).all()
        reasons_by_profile: dict[int, dict[ReportReason, int]] = {}
        for profile_id, reason, count in reason_rows:
            reasons_by_profile.setdefault(profile_id, {})[reason] = count

        return [
            map_report_group_row_to_report_group_entity(row, reasons_by_profile.get(row.profile_id, {}))
            for row in rows
        ]

    async def count_open_groups(self) -> int:
        result = await self._session.execute(
            select(func.count(func.distinct(ReportModel.profile_id))).where(ReportModel.status == ReportStatus.OPEN)
        )
        return result.scalar_one()

    async def list_open_by_profile(self, profile_id: int) -> list[ReportEntity]:
        result = await self._session.execute(
            select(ReportModel)
            .where(ReportModel.profile_id == profile_id, ReportModel.status == ReportStatus.OPEN)
            .order_by(ReportModel.created_at, ReportModel.id)
        )
        return [map_report_model_to_report_entity(model) for model in result.scalars()]

    async def close_open_for_profile(self, profile_id: int, status: ReportStatus, now: datetime) -> int:
        # The status condition is in the UPDATE itself, so two admins closing at once close each report only once.
        result = await self._session.execute(
            update(ReportModel)
            .where(ReportModel.profile_id == profile_id, ReportModel.status == ReportStatus.OPEN)
            .values(status=status, resolved_at=now)
            .returning(ReportModel.id)
        )
        return len(result.all())

    async def resolve_open_for_user(self, user_id: int, now: datetime) -> int:
        result = await self._session.execute(
            update(ReportModel)
            .where(
                ReportModel.profile_id.in_(select(ProfileModel.id).where(ProfileModel.user_id == user_id)),
                ReportModel.status == ReportStatus.OPEN,
            )
            .values(status=ReportStatus.RESOLVED, resolved_at=now)
            .returning(ReportModel.id)
        )
        return len(result.all())
