from datetime import datetime, timezone

from app.domain.entities import NewAdminActionEntity, ProfileEntity, ReportEntity, ReportGroupEntity, UserEntity
from app.domain.enums import AdminActionType, AdminTargetType, ReportStatus
from app.domain.exceptions import ProfileNotFoundError
from app.domain.interfaces import (
    IAdminActionRepository,
    IProfileRepository,
    IReportRepository,
    IUnitOfWork,
    IUserRepository,
)


class ListReportGroupsUseCase:
    """The admin queue: profiles with open reports. Read-only."""

    def __init__(self, report_repository: IReportRepository) -> None:
        self._report_repository = report_repository

    async def execute(self, limit: int, offset: int) -> tuple[list[ReportGroupEntity], int]:
        groups = await self._report_repository.list_open_groups(limit, offset)
        total = await self._report_repository.count_open_groups()
        return groups, total


class GetProfileReportsUseCase:
    """One reported profile with its owner and open reports. An admin sees the profile in any status (`paused` and
    `hidden_by_admin` too), so this does not use the user-facing visibility rule. Read-only."""

    def __init__(
        self,
        profile_repository: IProfileRepository,
        user_repository: IUserRepository,
        report_repository: IReportRepository,
    ) -> None:
        self._profile_repository = profile_repository
        self._user_repository = user_repository
        self._report_repository = report_repository

    async def execute(self, profile_id: int) -> tuple[ProfileEntity, UserEntity, list[ReportEntity]]:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None:
            raise ProfileNotFoundError()
        owner = await self._user_repository.get_by_id(profile.user_id)
        if owner is None:
            raise ProfileNotFoundError()
        reports = await self._report_repository.list_open_by_profile(profile.id)
        return profile, owner, reports


class DismissProfileReportsUseCase:
    """The admin found no violation: every open report on the profile becomes `dismissed`. With no open reports
    nothing is written, no journal row is added and nothing is committed."""

    def __init__(
        self,
        profile_repository: IProfileRepository,
        report_repository: IReportRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._report_repository = report_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, profile_id: int, reason: str) -> int:
        """Returns how many reports were dismissed."""
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None:
            raise ProfileNotFoundError()

        now = datetime.now(timezone.utc)
        dismissed = await self._report_repository.close_open_for_profile(profile.id, ReportStatus.DISMISSED, now)
        if dismissed == 0:
            return 0

        await self._admin_action_repository.add(
            NewAdminActionEntity(
                actor_user_id=admin.id,
                action=AdminActionType.DISMISS_REPORTS,
                target_type=AdminTargetType.PROFILE,
                target_id=profile.id,
                reason=reason,
                details={"owner_user_id": profile.user_id, "dismissed_reports": dismissed},
                created_at=now,
            )
        )
        await self._unit_of_work.commit()
        return dismissed
