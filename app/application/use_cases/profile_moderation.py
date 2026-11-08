from datetime import datetime, timezone

from app.domain.entities import NewAdminActionEntity, ProfileEntity, UserEntity
from app.domain.enums import AdminActionType, AdminTargetType, ProfileStatus, ReportStatus
from app.domain.exceptions import ProfileNotFoundError
from app.domain.interfaces import (
    IAdminActionRepository,
    IProfileRepository,
    IReportRepository,
    IUnitOfWork,
    IUserRepository,
)


class HideProfileUseCase:
    """Takes a profile out of circulation: `active`/`paused` -> `hidden_by_admin`. The owner cannot change it back.
    Open reports on the profile are closed as `resolved`. The reason lives only in the journal."""

    def __init__(
        self,
        profile_repository: IProfileRepository,
        user_repository: IUserRepository,
        report_repository: IReportRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._user_repository = user_repository
        self._report_repository = report_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, profile_id: int, reason: str) -> ProfileEntity:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None:
            raise ProfileNotFoundError()
        if profile.status == ProfileStatus.HIDDEN_BY_ADMIN:
            return profile

        previous_status = profile.status
        if not await self._profile_repository.hide(profile.id):
            # Another admin hid it (or the owner deleted it) after the read above: answer for the state that is there.
            current = await self._profile_repository.get_by_id(profile.id)
            if current is None:
                raise ProfileNotFoundError()
            return current

        # A hidden profile cannot be the active one (the feed would answer `no_active_profile`): move the pointer the
        # same way `PauseProfileUseCase` does. The hide above is already in this transaction, so the lookup skips it.
        owner = await self._user_repository.get_by_id(profile.user_id)
        if owner is None:
            raise ProfileNotFoundError()
        if owner.active_profile_id == profile.id:
            newest_active = await self._profile_repository.get_newest_active_by_user_id(owner.id)
            await self._user_repository.set_active_profile(owner.id, newest_active.id if newest_active else None)

        now = datetime.now(timezone.utc)
        resolved_reports = await self._report_repository.close_open_for_profile(profile.id, ReportStatus.RESOLVED, now)
        await self._admin_action_repository.add(
            NewAdminActionEntity(
                actor_user_id=admin.id,
                action=AdminActionType.HIDE_PROFILE,
                target_type=AdminTargetType.PROFILE,
                target_id=profile.id,
                reason=reason,
                details={
                    "owner_user_id": profile.user_id,
                    "previous_status": previous_status.value,
                    "resolved_reports": resolved_reports,
                },
                created_at=now,
            )
        )
        await self._unit_of_work.commit()
        profile.status = ProfileStatus.HIDDEN_BY_ADMIN
        return profile


class UnhideProfileUseCase:
    """Returns a hidden profile to the owner as `paused`, never straight to `active`: it does not reappear in the feed
    without the owner knowing. The owner resumes it themselves. Closed reports are not reopened."""

    def __init__(
        self,
        profile_repository: IProfileRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._profile_repository = profile_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, profile_id: int, reason: str) -> ProfileEntity:
        profile = await self._profile_repository.get_by_id(profile_id)
        if profile is None:
            raise ProfileNotFoundError()
        if profile.status != ProfileStatus.HIDDEN_BY_ADMIN:
            return profile

        if not await self._profile_repository.unhide(profile.id):
            current = await self._profile_repository.get_by_id(profile.id)
            if current is None:
                raise ProfileNotFoundError()
            return current

        await self._admin_action_repository.add(
            NewAdminActionEntity(
                actor_user_id=admin.id,
                action=AdminActionType.UNHIDE_PROFILE,
                target_type=AdminTargetType.PROFILE,
                target_id=profile.id,
                reason=reason,
                details={"owner_user_id": profile.user_id},
                created_at=datetime.now(timezone.utc),
            )
        )
        await self._unit_of_work.commit()
        profile.status = ProfileStatus.PAUSED
        return profile
