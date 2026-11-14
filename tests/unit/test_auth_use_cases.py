import time

import pytest
from freezegun import freeze_time

from app.application.services.jwt_service import JWTService
from app.application.services.telegram_init_data import TelegramInitDataValidator
from app.application.use_cases.auth import (
    AuthenticateTelegramUserUseCase,
    VerifyAccessTokenUseCase,
)
from app.domain.exceptions import (
    InitDataSignatureInvalidError,
    TokenExpiredError,
    TokenInvalidError,
    UserBannedError,
)
from app.domain.enums import AnalyticsEvent
from scripts.dev_gen_init_data import build_init_data
from tests.fixtures.factories import make_user_entity
from tests.fixtures.fake_analytics import FakeAnalyticsTracker
from tests.fixtures.fake_profile_repository import FakeProfileRepository
from tests.fixtures.fake_unit_of_work import FakeUnitOfWork
from tests.fixtures.fake_user_repository import FakeUserRepository

BOT_TOKEN = "123"


@pytest.fixture
def validator() -> TelegramInitDataValidator:
    return TelegramInitDataValidator(bot_token=BOT_TOKEN, max_age_seconds=300, max_future_skew_seconds=60)


@pytest.fixture
def jwt_service() -> JWTService:
    return JWTService(secret_key="test-secret", algorithm="HS256", expires_minutes=60)


class TestAuthenticateTelegramUserUseCase:
    async def test_new_user_is_created(self, validator, jwt_service):
        repo = FakeUserRepository()
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        result = await use_case.execute(raw)

        assert result.is_new_user is True
        assert result.user.telegram_id == 555
        assert await repo.get_by_telegram_id(555) is not None

    async def test_existing_user_is_updated_not_recreated(self, validator, jwt_service):
        existing = make_user_entity(id=1, telegram_id=555, username="old_name")
        repo = FakeUserRepository([existing])
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        result = await use_case.execute(raw)

        assert result.is_new_user is False
        assert result.user.id == 1
        assert result.user.username == "testuser"  # build_init_data's fixed payload username

    async def test_lost_race_reuses_the_winners_user(self, validator, jwt_service):
        uow = FakeUnitOfWork()
        repo = FakeUserRepository()
        repo.lose_race_once = True
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, uow, FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        result = await use_case.execute(raw)

        assert result.is_new_user is False  # the other request created the user
        assert len(repo.users) == 1
        assert result.user.id == repo.users[0].id
        assert result.user.username == "testuser"  # refreshed from this request's initData
        assert uow.commits == 1

    async def test_banned_user_still_authenticates_successfully(self, validator, jwt_service):
        existing = make_user_entity(id=1, telegram_id=555, is_banned=True, ban_reason="spam")
        repo = FakeUserRepository([existing])
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        result = await use_case.execute(raw)  # must not raise

        assert result.access_token

    async def test_relogin_refreshes_profile_fields_but_leaves_protected_ones_in_storage(
        self, validator, jwt_service
    ):
        existing = make_user_entity(
            id=1,
            telegram_id=555,
            username="old_name",
            is_banned=True,
            ban_reason="spam",
            is_admin=True,
            token_version=4,
        )
        repo = FakeUserRepository([existing])
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        result = await use_case.execute(raw)

        stored = repo.users[0]
        assert stored.username == "testuser"
        assert (stored.is_banned, stored.ban_reason, stored.is_admin, stored.token_version) == (
            True,
            "spam",
            True,
            4,
        )
        assert result.user.is_banned is True

    async def test_issued_token_reflects_current_token_version(self, validator, jwt_service):
        existing = make_user_entity(id=1, telegram_id=555, token_version=9)
        repo = FakeUserRepository([existing])
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        result = await use_case.execute(raw)
        payload = jwt_service.decode_access_token(result.access_token)

        assert payload.token_version == 9

    async def test_admin_flag_is_reported_in_the_response_but_not_in_the_token(self, validator, jwt_service):
        repo = FakeUserRepository([make_user_entity(id=1, telegram_id=555, is_admin=True)])
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        result = await use_case.execute(raw)

        assert result.user.is_admin is True
        assert not hasattr(jwt_service.decode_access_token(result.access_token), "is_admin")

    async def test_validator_errors_propagate_unchanged(self, validator, jwt_service):
        repo = FakeUserRepository()
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=True)

        with pytest.raises(InitDataSignatureInvalidError):
            await use_case.execute(raw)

    async def test_new_user_commits_exactly_once(self, validator, jwt_service):
        uow = FakeUnitOfWork()
        use_case = AuthenticateTelegramUserUseCase(FakeUserRepository(), validator, jwt_service, uow, FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        await use_case.execute(raw)

        assert uow.commits == 1

    async def test_existing_user_commits_exactly_once(self, validator, jwt_service):
        uow = FakeUnitOfWork()
        repo = FakeUserRepository([make_user_entity(id=1, telegram_id=555)])
        use_case = AuthenticateTelegramUserUseCase(repo, validator, jwt_service, uow, FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False)

        await use_case.execute(raw)

        assert uow.commits == 1

    async def test_invalid_init_data_does_not_commit(self, validator, jwt_service):
        uow = FakeUnitOfWork()
        use_case = AuthenticateTelegramUserUseCase(FakeUserRepository(), validator, jwt_service, uow, FakeProfileRepository(), FakeAnalyticsTracker())
        raw = build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=True)

        with pytest.raises(InitDataSignatureInvalidError):
            await use_case.execute(raw)

        assert uow.commits == 0


class TestAuthenticationEvents:
    async def test_a_first_login_is_a_sign_up_with_the_admin_flag_set_on_the_person(self, validator, jwt_service):
        tracker = FakeAnalyticsTracker()
        use_case = AuthenticateTelegramUserUseCase(
            FakeUserRepository(), validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), tracker
        )

        result = await use_case.execute(build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False))

        [event] = tracker.events
        assert (event.event, event.user_id) == (AnalyticsEvent.USER_SIGNED_UP, result.user.id)
        assert event.properties == {"has_username": True}
        assert event.person_properties == {"is_admin": False}

    async def test_a_returning_admin_logs_in_with_is_admin_true(self, validator, jwt_service):
        tracker = FakeAnalyticsTracker()
        repo = FakeUserRepository([make_user_entity(id=1, telegram_id=555, is_admin=True)])
        use_case = AuthenticateTelegramUserUseCase(
            repo, validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), tracker
        )

        await use_case.execute(build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False))

        [event] = tracker.events
        assert (event.event, event.user_id) == (AnalyticsEvent.USER_LOGGED_IN, 1)
        assert event.person_properties == {"is_admin": True}

    async def test_every_login_is_an_event(self, validator, jwt_service):
        tracker = FakeAnalyticsTracker()
        use_case = AuthenticateTelegramUserUseCase(
            FakeUserRepository(), validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), tracker
        )

        for _ in range(3):
            await use_case.execute(build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False))

        assert [event.event for event in tracker.events] == [
            AnalyticsEvent.USER_SIGNED_UP,
            AnalyticsEvent.USER_LOGGED_IN,
            AnalyticsEvent.USER_LOGGED_IN,
        ]

    async def test_a_user_without_a_username_is_reported_without_it(self, validator, jwt_service):
        tracker = FakeAnalyticsTracker()
        use_case = AuthenticateTelegramUserUseCase(
            FakeUserRepository(), validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), tracker
        )

        await use_case.execute(build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False, username=None))

        assert tracker.events[0].properties == {"has_username": False}

    async def test_no_personal_data_from_telegram_reaches_the_event(self, validator, jwt_service):
        tracker = FakeAnalyticsTracker()
        use_case = AuthenticateTelegramUserUseCase(
            FakeUserRepository(), validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), tracker
        )

        await use_case.execute(
            build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False, first_name="Анна", username="anna_k")
        )

        [event] = tracker.events
        sent = {**event.properties, **event.person_properties}
        assert "Анна" not in sent.values() and "anna_k" not in sent.values() and 555 not in sent.values()

    async def test_a_failed_commit_sends_nothing(self, validator, jwt_service):
        tracker = FakeAnalyticsTracker()
        uow = FakeUnitOfWork()
        uow.fail_on_commit = True
        use_case = AuthenticateTelegramUserUseCase(
            FakeUserRepository(), validator, jwt_service, uow, FakeProfileRepository(), tracker
        )

        with pytest.raises(RuntimeError):
            await use_case.execute(build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=False))

        assert tracker.events == []

    async def test_a_rejected_init_data_sends_nothing(self, validator, jwt_service):
        tracker = FakeAnalyticsTracker()
        use_case = AuthenticateTelegramUserUseCase(
            FakeUserRepository(), validator, jwt_service, FakeUnitOfWork(), FakeProfileRepository(), tracker
        )

        with pytest.raises(InitDataSignatureInvalidError):
            await use_case.execute(build_init_data(BOT_TOKEN, 555, int(time.time()), bad_hash=True))

        assert tracker.events == []


class TestVerifyAccessTokenUseCase:
    async def test_valid_token_returns_user(self, jwt_service):
        user = make_user_entity(id=1, telegram_id=555, token_version=0)
        repo = FakeUserRepository([user])
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        token = jwt_service.create_access_token(user_id=1, telegram_id=555, token_version=0)

        result = await use_case.execute(token)

        assert result.id == 1

    async def test_admin_status_comes_from_the_database_not_from_the_token(self, jwt_service):
        # The token was issued while the user was an admin; the row now says otherwise.
        repo = FakeUserRepository([make_user_entity(id=1, telegram_id=555, is_admin=False)])
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        token = jwt_service.create_access_token(user_id=1, telegram_id=555, token_version=0)

        result = await use_case.execute(token)

        assert result.is_admin is False

    async def test_user_not_found_raises_token_invalid(self, jwt_service):
        repo = FakeUserRepository()
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        token = jwt_service.create_access_token(user_id=999, telegram_id=1, token_version=0)

        with pytest.raises(TokenInvalidError):
            await use_case.execute(token)

    async def test_token_version_mismatch_raises_token_invalid(self, jwt_service):
        user = make_user_entity(id=1, telegram_id=555, token_version=2)
        repo = FakeUserRepository([user])
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        token = jwt_service.create_access_token(user_id=1, telegram_id=555, token_version=1)

        with pytest.raises(TokenInvalidError):
            await use_case.execute(token)

    async def test_banned_user_raises_user_banned_error(self, jwt_service):
        user = make_user_entity(id=1, telegram_id=555, token_version=0, is_banned=True, ban_reason="rules violation")
        repo = FakeUserRepository([user])
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        token = jwt_service.create_access_token(user_id=1, telegram_id=555, token_version=0)

        with pytest.raises(UserBannedError) as exc_info:
            await use_case.execute(token)
        assert exc_info.value.ban_reason == "rules violation"

    async def test_banned_user_with_no_reason(self, jwt_service):
        user = make_user_entity(id=1, telegram_id=555, token_version=0, is_banned=True, ban_reason=None)
        repo = FakeUserRepository([user])
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        token = jwt_service.create_access_token(user_id=1, telegram_id=555, token_version=0)

        with pytest.raises(UserBannedError) as exc_info:
            await use_case.execute(token)
        assert exc_info.value.ban_reason is None

    async def test_expired_token_propagates_from_jwt_service(self, jwt_service):
        repo = FakeUserRepository()
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        with freeze_time("2024-01-01T00:00:00Z"):
            token = jwt_service.create_access_token(user_id=1, telegram_id=1, token_version=0)
        with freeze_time("2024-01-01T02:00:00Z"):
            with pytest.raises(TokenExpiredError):
                await use_case.execute(token)

    async def test_get_by_id_called_once_per_execute(self, jwt_service, monkeypatch):
        user = make_user_entity(id=1, telegram_id=555, token_version=0)
        repo = FakeUserRepository([user])
        calls: list[int] = []
        original = repo.get_by_id

        async def counting_get_by_id(user_id):
            calls.append(user_id)
            return await original(user_id)

        monkeypatch.setattr(repo, "get_by_id", counting_get_by_id)
        use_case = VerifyAccessTokenUseCase(repo, jwt_service)
        token = jwt_service.create_access_token(user_id=1, telegram_id=555, token_version=0)

        await use_case.execute(token)

        assert calls == [1]
