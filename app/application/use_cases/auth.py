from datetime import datetime, timezone

from app.application.services.jwt_service import JWTService
from app.application.services.telegram_init_data import TelegramInitDataValidator
from app.domain.entities import TelegramAuthResult, UserEntity
from app.domain.enums import AnalyticsEvent
from app.domain.exceptions import TokenInvalidError, UserBannedError
from app.domain.interfaces import IAnalyticsTracker, IProfileRepository, IUnitOfWork, IUserRepository
from app.domain.mappers.telegram_user import map_telegram_user_payload_to_new_user_entity


class AuthenticateTelegramUserUseCase:
    def __init__(
        self,
        user_repository: IUserRepository,
        init_data_validator: TelegramInitDataValidator,
        jwt_service: JWTService,
        unit_of_work: IUnitOfWork,
        profile_repository: IProfileRepository,
        analytics_tracker: IAnalyticsTracker,
    ) -> None:
        self._user_repository = user_repository
        self._init_data_validator = init_data_validator
        self._jwt_service = jwt_service
        self._unit_of_work = unit_of_work
        self._profile_repository = profile_repository
        self._analytics_tracker = analytics_tracker

    async def execute(self, init_data_raw: str) -> TelegramAuthResult:
        parsed = self._init_data_validator.validate(init_data_raw)
        tg_user = parsed.user
        now = datetime.now(timezone.utc)

        existing = await self._user_repository.get_by_telegram_id(tg_user.id)
        user = None
        if existing is None:
            user = await self._user_repository.create(
                map_telegram_user_payload_to_new_user_entity(tg_user, now)
            )
            if user is None:
                # A concurrent first login inserted the same telegram_id between our SELECT and INSERT.
                existing = await self._user_repository.get_by_telegram_id(tg_user.id)

        is_new_user = user is not None
        if user is None:
            assert existing is not None
            existing.first_name = tg_user.first_name
            existing.last_name = tg_user.last_name
            existing.username = tg_user.username
            existing.photo_url = tg_user.photo_url
            existing.language_code = tg_user.language_code
            existing.last_active_at = now
            user = await self._user_repository.update(existing)

        await self._unit_of_work.commit()
        # Every login, not only the first: the Mini App logs in on each opening. `is_admin` is set on the person from
        # the live row, so PostHog can leave the team out of every event of theirs, not only this one.
        self._analytics_tracker.track(
            AnalyticsEvent.USER_SIGNED_UP if is_new_user else AnalyticsEvent.USER_LOGGED_IN,
            user.id,
            {"has_username": bool(user.username)},
            person_properties={"is_admin": user.is_admin},
        )

        token = self._jwt_service.create_access_token(
            user_id=user.id,
            telegram_id=user.telegram_id,
            token_version=user.token_version,
        )
        profiles = await self._profile_repository.list_by_user_id(user.id)
        return TelegramAuthResult(user=user, access_token=token, is_new_user=is_new_user, profiles=profiles)


class VerifyAccessTokenUseCase:
    def __init__(self, user_repository: IUserRepository, jwt_service: JWTService) -> None:
        self._user_repository = user_repository
        self._jwt_service = jwt_service

    async def execute(self, token: str) -> UserEntity:
        payload = self._jwt_service.decode_access_token(token)

        user = await self._user_repository.get_by_id(payload.user_id)
        if user is None or user.token_version != payload.token_version:
            raise TokenInvalidError()

        if user.is_banned:
            raise UserBannedError(user.ban_reason)

        return user
