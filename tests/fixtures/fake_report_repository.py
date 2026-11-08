from dataclasses import replace
from datetime import datetime

from app.domain.entities import NewReportEntity, ReportEntity, ReportGroupEntity
from app.domain.enums import ReportStatus
from app.domain.interfaces import IReportRepository


class FakeReportRepository(IReportRepository):
    """In-memory stand-in for SqlAlchemyReportRepository. Implements the real ABC and hands out copies.

    Like the real partial unique index, `create` returns None while the reporter has an OPEN report on the profile;
    a closed one does not block a new report.

    The fake has no profiles table, so what it needs from one is given up front: `profile_owners` maps a profile id to
    its owner (for `resolve_open_for_user`) and `visible` maps a viewer to the profile ids they may see (for
    `is_profile_visible_to`). The admin queue (`list_open_groups`, `count_open_groups`) needs joined profile and user
    data and is checked against real Postgres, so the fake refuses to pretend.
    """

    def __init__(
        self,
        reports: list[ReportEntity] | None = None,
        profile_owners: dict[int, int] | None = None,
        visible: dict[int, set[int]] | None = None,
    ) -> None:
        self._reports: dict[int, ReportEntity] = {r.id: replace(r) for r in (reports or [])}
        self._next_id = max(self._reports, default=0) + 1
        self._profile_owners = dict(profile_owners or {})
        self._visible = {viewer: set(ids) for viewer, ids in (visible or {}).items()}

    @property
    def reports(self) -> list[ReportEntity]:
        return [replace(r) for r in self._reports.values()]

    def _open(self) -> list[ReportEntity]:
        return [r for r in self._reports.values() if r.status == ReportStatus.OPEN]

    async def is_profile_visible_to(self, viewer_user_id: int, profile_id: int) -> bool:
        return profile_id in self._visible.get(viewer_user_id, set())

    async def create(self, report: NewReportEntity) -> ReportEntity | None:
        for existing in self._open():
            if (existing.reporter_user_id, existing.profile_id) == (report.reporter_user_id, report.profile_id):
                return None
        entity = ReportEntity(
            id=self._next_id,
            reporter_user_id=report.reporter_user_id,
            profile_id=report.profile_id,
            reason=report.reason,
            comment=report.comment,
            status=ReportStatus.OPEN,
            created_at=report.created_at,
            resolved_at=None,
        )
        self._reports[entity.id] = entity
        self._next_id += 1
        return replace(entity)

    async def get_open_by_reporter_and_profile(self, reporter_user_id: int, profile_id: int) -> ReportEntity | None:
        for report in self._open():
            if (report.reporter_user_id, report.profile_id) == (reporter_user_id, profile_id):
                return replace(report)
        return None

    async def list_open_groups(self, limit: int, offset: int) -> list[ReportGroupEntity]:
        raise NotImplementedError("The admin queue joins profiles and users: test it against Postgres")

    async def count_open_groups(self) -> int:
        raise NotImplementedError("The admin queue joins profiles and users: test it against Postgres")

    async def list_open_by_profile(self, profile_id: int) -> list[ReportEntity]:
        own = [r for r in self._open() if r.profile_id == profile_id]
        return [replace(r) for r in sorted(own, key=lambda r: (r.created_at, r.id))]

    async def close_open_for_profile(self, profile_id: int, status: ReportStatus, now: datetime) -> int:
        return self._close([r for r in self._open() if r.profile_id == profile_id], status, now)

    async def resolve_open_for_user(self, user_id: int, now: datetime) -> int:
        owned = [r for r in self._open() if self._profile_owners.get(r.profile_id) == user_id]
        return self._close(owned, ReportStatus.RESOLVED, now)

    @staticmethod
    def _close(reports: list[ReportEntity], status: ReportStatus, now: datetime) -> int:
        for report in reports:
            report.status = status
            report.resolved_at = now
        return len(reports)
