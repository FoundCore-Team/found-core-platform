from datetime import datetime, timezone

from app.domain.entities import NewAdminActionEntity, UserEntity
from app.domain.enums import AdminActionType, AdminTargetType
from app.domain.exceptions import CannotBanAdminError, UserBannedError, UserNotFoundError
from app.domain.interfaces import IAdminActionRepository, IReportRepository, IUnitOfWork, IUserRepository


class SetAdminUseCase:
    """Grants or revokes the admin role. Called by `scripts/make_admin.py`, never by an HTTP endpoint: there is no
    "become an admin" route to protect. The journal row has no actor (the script is the system)."""

    def __init__(
        self,
        user_repository: IUserRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, telegram_id: int, make_admin: bool, reason: str) -> tuple[UserEntity, bool]:
        """Returns the user and whether anything changed (False when the user already was in the wanted state)."""
        user = await self._user_repository.get_by_telegram_id(telegram_id)
        if user is None or user.deleted_at is not None:
            raise UserNotFoundError()
        if make_admin and user.is_banned:
            raise UserBannedError(user.ban_reason)
        if user.is_admin == make_admin:
            return user, False

        updated = await self._user_repository.set_admin(user.id, make_admin)
        if updated is None:
            # The state changed between the read and the write (a parallel script run, or a ban). Re-read and answer
            # for the state that is there now.
            current = await self._user_repository.get_by_id(user.id)
            if current is None or current.deleted_at is not None:
                raise UserNotFoundError()
            if make_admin and current.is_banned:
                raise UserBannedError(current.ban_reason)
            return current, False

        await self._admin_action_repository.add(
            NewAdminActionEntity(
                actor_user_id=None,
                action=AdminActionType.GRANT_ADMIN if make_admin else AdminActionType.REVOKE_ADMIN,
                target_type=AdminTargetType.USER,
                target_id=user.id,
                reason=reason,
                details={"telegram_id": telegram_id},
                created_at=datetime.now(timezone.utc),
            )
        )
        await self._unit_of_work.commit()
        return updated, True


class BanUserUseCase:
    """Bans a user until an admin lifts it. `token_version` goes up in the same statement, so every token issued before
    the ban stops working at once. Statuses of the user's profiles are not touched: they drop out of the feed by
    the visibility rule. Open reports on any of the user's profiles are closed as `resolved`: the question they raised
    is answered."""

    def __init__(
        self,
        user_repository: IUserRepository,
        report_repository: IReportRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._report_repository = report_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, user_id: int, reason: str) -> UserEntity:
        # Bans and unbans of one user run one after another: the state read below cannot change until our commit.
        await self._user_repository.lock_for_update(user_id)
        user = await self._user_repository.get_by_id(user_id)
        if user is None or user.deleted_at is not None:
            raise UserNotFoundError()
        # Also covers an admin banning themselves.
        if user.is_admin:
            raise CannotBanAdminError()
        if user.is_banned:
            return user

        banned = await self._user_repository.set_ban(user.id, reason)
        if banned is None:
            # Lost a race: the user was banned, made an admin or deleted after the read above.
            current = await self._user_repository.get_by_id(user.id)
            if current is None or current.deleted_at is not None:
                raise UserNotFoundError()
            if current.is_admin:
                raise CannotBanAdminError()
            if current.is_banned:
                return current
            # Cannot happen while the row lock is held; kept so a broken lock fails loudly instead of silently.
            raise UserNotFoundError()

        now = datetime.now(timezone.utc)
        resolved_reports = await self._report_repository.resolve_open_for_user(user.id, now)
        await self._admin_action_repository.add(
            NewAdminActionEntity(
                actor_user_id=admin.id,
                action=AdminActionType.BAN_USER,
                target_type=AdminTargetType.USER,
                target_id=user.id,
                reason=reason,
                details={"resolved_reports": resolved_reports},
                created_at=now,
            )
        )
        await self._unit_of_work.commit()
        return banned


class UnbanUserUseCase:
    """Lifts a ban. `token_version` is not touched: a token issued to the user while banned works again."""

    def __init__(
        self,
        user_repository: IUserRepository,
        admin_action_repository: IAdminActionRepository,
        unit_of_work: IUnitOfWork,
    ) -> None:
        self._user_repository = user_repository
        self._admin_action_repository = admin_action_repository
        self._unit_of_work = unit_of_work

    async def execute(self, admin: UserEntity, user_id: int, reason: str) -> UserEntity:
        # Same lock as the ban: the two never interleave on one user.
        await self._user_repository.lock_for_update(user_id)
        user = await self._user_repository.get_by_id(user_id)
        if user is None or user.deleted_at is not None:
            raise UserNotFoundError()
        if not user.is_banned:
            return user

        unbanned = await self._user_repository.clear_ban(user.id)
        if unbanned is None:
            # Someone else lifted the ban between the read and the write: the wanted state is already there.
            current = await self._user_repository.get_by_id(user.id)
            if current is None or current.deleted_at is not None:
                raise UserNotFoundError()
            return current

        await self._admin_action_repository.add(
            NewAdminActionEntity(
                actor_user_id=admin.id,
                action=AdminActionType.UNBAN_USER,
                target_type=AdminTargetType.USER,
                target_id=user.id,
                reason=reason,
                details={},
                created_at=datetime.now(timezone.utc),
            )
        )
        await self._unit_of_work.commit()
        return unbanned
