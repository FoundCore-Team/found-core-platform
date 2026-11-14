import pytest
from pydantic import ValidationError

from app.core.config import AnalyticsConfig, RateLimitConfig, Settings
from app.domain.enums import RateLimitAction

STRONG_SECRET = "k3J9vQ2mXw7LpR5tYb8NcA4dFg6HsZ1u"
REAL_BOT_TOKEN = "7012345678:AAH-real-looking-token-value"


def _settings(**overrides) -> Settings:
    values = {
        "db": {"url": "postgresql+asyncpg://u:p@localhost:5432/db"},
        "redis": {"url": "redis://localhost:6379/0"},
        "auth": {"secret_key": STRONG_SECRET},
        "bot": {"token": REAL_BOT_TOKEN},
        "run": {"env": "prod", "reload": False},
        "worker": {"broker_url": "redis://localhost:6379/2"},
        "embedding": {"provider": "openai", "openai_api_key": "sk-test-key"},
        "notification": {"provider": "telegram", "mini_app_url": "https://app.example.com"},
        "analytics": {"provider": "disabled"},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


POSTHOG_KEY = "phc_test_project_key"


def test_production_with_real_secrets_starts():
    settings = _settings()

    assert settings.run.env == "prod"


@pytest.mark.parametrize("secret", ["dev-only-insecure-secret-change-me", "change-me", "secret", ""])
def test_production_refuses_a_placeholder_secret_key(secret):
    with pytest.raises(ValidationError, match="SECRET_KEY is a known placeholder"):
        _settings(auth={"secret_key": secret})


def test_production_refuses_a_short_secret_key():
    with pytest.raises(ValidationError, match="shorter than 32"):
        _settings(auth={"secret_key": "x" * 31})


def test_production_accepts_a_secret_key_of_exactly_the_minimum_length():
    _settings(auth={"secret_key": "x" * 32})


@pytest.mark.parametrize("token", ["123", "key", ""])
def test_production_refuses_a_placeholder_bot_token(token):
    with pytest.raises(ValidationError, match="BOT__TOKEN is a known placeholder"):
        _settings(bot={"token": token})


def test_production_refuses_auto_reload():
    with pytest.raises(ValidationError, match="RELOAD must be false"):
        _settings(run={"env": "prod", "reload": True})


def test_production_reports_every_problem_at_once():
    with pytest.raises(ValidationError) as error:
        _settings(
            auth={"secret_key": "change-me"},
            bot={"token": "123"},
            run={"env": "prod", "reload": True},
        )

    message = str(error.value)
    assert "SECRET_KEY" in message and "BOT__TOKEN" in message and "RELOAD" in message
    assert "NOTIFICATION" not in message  # the default fixture values are valid, so only the three above fail


def test_production_refuses_the_stub_embedding_provider():
    with pytest.raises(ValidationError, match="PROVIDER must not be stub"):
        _settings(embedding={"provider": "stub"})


@pytest.mark.parametrize("api_key", [None, ""])
def test_production_refuses_openai_without_an_api_key(api_key):
    with pytest.raises(ValidationError, match="OPENAI_API_KEY is required"):
        _settings(embedding={"provider": "openai", "openai_api_key": api_key})


@pytest.mark.parametrize(
    "broker_url",
    ["redis://localhost:6379/0", "redis://localhost:6379", "redis://localhost/0"],
)
def test_production_refuses_a_broker_in_the_same_redis_db_as_the_cache(broker_url):
    with pytest.raises(ValidationError, match="different Redis DB"):
        _settings(worker={"broker_url": broker_url})


def test_production_accepts_a_broker_on_another_host_with_the_same_db_number():
    _settings(worker={"broker_url": "redis://broker:6379/0"})


@pytest.mark.parametrize("env", ["dev", "test"])
def test_placeholders_are_fine_outside_production(env):
    settings = _settings(
        auth={"secret_key": "dev-only-insecure-secret-change-me"},
        bot={"token": "123"},
        run={"env": env, "reload": True},
        embedding={"provider": "stub"},
    )

    assert settings.run.env == env


def test_broker_url_is_required(monkeypatch):
    # The old name must not satisfy the new requirement: a forgotten rename in `.env` has to fail at startup.
    monkeypatch.delenv("APP_CONFIG__WORKER__BROKER_URL", raising=False)
    monkeypatch.setenv("APP_CONFIG__EMBEDDING__BROKER_URL", "redis://localhost:6379/2")

    with pytest.raises(ValidationError, match="worker"):
        Settings(
            _env_file=None,
            db={"url": "postgresql+asyncpg://u:p@localhost:5432/db"},
            redis={"url": "redis://localhost:6379/0"},
            auth={"secret_key": STRONG_SECRET},
            bot={"token": REAL_BOT_TOKEN},
            embedding={"provider": "stub"},
        )


def test_production_refuses_the_stub_notification_provider():
    with pytest.raises(ValidationError, match="NOTIFICATION__PROVIDER must not be stub"):
        _settings(notification={"provider": "stub", "mini_app_url": "https://app.example.com"})


@pytest.mark.parametrize("mini_app_url", [None, ""])
def test_production_requires_a_mini_app_url(mini_app_url):
    with pytest.raises(ValidationError, match="MINI_APP_URL is required"):
        _settings(notification={"provider": "telegram", "mini_app_url": mini_app_url})


@pytest.mark.parametrize("mini_app_url", ["http://app.example.com", "app.example.com", "ftp://app.example.com"])
def test_production_requires_an_https_mini_app_url(mini_app_url):
    with pytest.raises(ValidationError, match="MINI_APP_URL must be an https URL"):
        _settings(notification={"provider": "telegram", "mini_app_url": mini_app_url})


def test_stub_notifications_and_no_mini_app_url_are_fine_outside_production():
    settings = _settings(run={"env": "dev", "reload": True}, notification={"provider": "stub"})

    assert settings.notification.provider == "stub"
    assert settings.notification.mini_app_url is None


def test_notification_defaults():
    config = _settings().notification

    assert (config.task_max_retries, config.retry_base_delay_seconds, config.request_timeout_seconds) == (5, 5, 10.0)
    assert (config.sweep_interval_seconds, config.sweep_pending_after_seconds, config.sweep_batch_size) == (60, 120, 100)
    assert (config.stale_after_seconds, config.queue_name) == (86400, "notifications")


@pytest.mark.parametrize(
    "override",
    [
        {"sweep_interval_seconds": 0},
        {"sweep_pending_after_seconds": 0},
        {"sweep_batch_size": 0},
        {"stale_after_seconds": 0},
        {"task_max_retries": -1},
    ],
)
def test_notification_config_rejects_invalid_values(override):
    with pytest.raises(ValidationError):
        _settings(notification={"provider": "telegram", "mini_app_url": "https://app.example.com", **override})


def test_production_allows_disabled_analytics():
    # The prod setting until the terms of use and the legal review are done (plan of stage 9, decision 55).
    assert _settings().analytics.provider == "disabled"


def test_production_refuses_the_stub_analytics_provider():
    with pytest.raises(ValidationError, match="ANALYTICS__PROVIDER must not be stub"):
        _settings(analytics={"provider": "stub"})


def test_production_accepts_posthog_with_a_key_and_an_https_host():
    config = _settings(analytics={"provider": "posthog", "project_api_key": POSTHOG_KEY}).analytics

    assert config.host == "https://eu.i.posthog.com"
    assert config.project_api_key.get_secret_value() == POSTHOG_KEY


@pytest.mark.parametrize("host", ["http://eu.i.posthog.com", "eu.i.posthog.com"])
def test_production_refuses_posthog_with_a_host_that_is_not_https(host):
    with pytest.raises(ValidationError, match="ANALYTICS__HOST must be an https URL"):
        _settings(analytics={"provider": "posthog", "project_api_key": POSTHOG_KEY, "host": host})


@pytest.mark.parametrize("env", ["dev", "prod"])
@pytest.mark.parametrize("api_key", [None, "", "   "])
def test_posthog_without_a_project_key_is_refused_in_every_environment(env, api_key):
    # The SDK would silently drop every event with an empty key, so this is not only a prod check.
    with pytest.raises(ValidationError, match="PROJECT_API_KEY is required"):
        _settings(
            run={"env": env, "reload": env != "prod"},
            analytics={"provider": "posthog", "project_api_key": api_key},
        )


def test_posthog_with_a_plain_http_host_is_fine_outside_production():
    config = _settings(
        run={"env": "dev", "reload": True},
        analytics={"provider": "posthog", "project_api_key": POSTHOG_KEY, "host": "http://localhost:8010"},
    ).analytics

    assert config.host == "http://localhost:8010"


def test_analytics_defaults():
    # Built directly: through Settings, the APP_CONFIG__ANALYTICS__PROVIDER=disabled of .env.test would be merged in.
    config = AnalyticsConfig()

    assert (config.provider, config.host, config.project_api_key) == ("stub", "https://eu.i.posthog.com", None)
    # The SDK's own defaults in posthog 7.81.1, and the timeouts of decision 66.
    assert (config.max_queue_size, config.flush_at, config.flush_interval_seconds) == (10_000, 100, 5.0)
    assert (config.request_timeout_seconds, config.flush_timeout_seconds) == (5.0, 3.0)


@pytest.mark.parametrize(
    "override",
    [
        {"max_queue_size": 0},
        {"flush_at": 0},
        {"flush_interval_seconds": 0},
        {"request_timeout_seconds": -1},
        {"flush_timeout_seconds": 0},
    ],
)
def test_analytics_config_rejects_non_positive_values(override):
    with pytest.raises(ValidationError):
        _settings(analytics={"provider": "disabled", **override})


def test_contact_limit_defaults():
    settings = _settings()

    assert settings.contact.daily_limit == 20
    assert settings.contact.window_hours == 24


@pytest.mark.parametrize("override", [{"daily_limit": 0}, {"window_hours": 0}, {"daily_limit": -1}])
def test_contact_config_rejects_non_positive_values(override):
    with pytest.raises(ValidationError):
        _settings(contact=override)


@pytest.mark.parametrize(
    "action, limit, window_seconds",
    [
        ("contact_open", 10, 60),
        ("tag_suggest", 60, 60),
        ("tag_create", 5, 60),
        ("tag_create_daily", 30, 86400),
        ("report_create", 10, 86400),
        ("feed_request", 60, 60),
        ("feed_session_build", 10, 60),
        ("feed_card", 30, 60),
        ("feed_card_daily", 300, 86400),
        ("feed_view", 60, 60),
        ("profile_write", 5, 600),
    ],
)
def test_rate_limit_defaults_match_the_plan(action, limit, window_seconds):
    rule = getattr(_settings().rate_limit, action)

    assert (rule.limit, rule.window_seconds) == (limit, window_seconds)


def test_rate_limit_config_has_exactly_one_rule_per_action():
    # A new RateLimitAction without a config field would fail at the first request; a field without an action is dead.
    assert set(RateLimitConfig.model_fields) == {action.value for action in RateLimitAction}


@pytest.mark.parametrize("override", [{"limit": 0}, {"limit": -1}, {"window_seconds": 0}, {"window_seconds": -60}])
def test_rate_limit_rule_rejects_non_positive_values(override):
    with pytest.raises(ValidationError):
        _settings(rate_limit={"contact_open": {"limit": 10, "window_seconds": 60, **override}})


def test_overriding_one_number_of_a_rate_limit_keeps_the_other_default():
    rule = _settings(rate_limit={"contact_open": {"limit": 3}}).rate_limit.contact_open

    assert (rule.limit, rule.window_seconds) == (3, 60)


def test_rate_limit_override_from_the_environment_keeps_the_other_default(monkeypatch):
    monkeypatch.setenv("APP_CONFIG__RATE_LIMIT__PROFILE_WRITE__WINDOW_SECONDS", "120")

    settings = _settings()

    assert (settings.rate_limit.profile_write.limit, settings.rate_limit.profile_write.window_seconds) == (5, 120)
    assert settings.rate_limit.contact_open.limit == 10


def test_unknown_environment_name_is_rejected():
    with pytest.raises(ValidationError):
        _settings(run={"env": "production"})


def test_bot_token_is_required(monkeypatch):
    monkeypatch.delenv("APP_CONFIG__BOT__TOKEN", raising=False)

    with pytest.raises(ValidationError, match="bot"):
        Settings(
            _env_file=None,
            db={"url": "postgresql+asyncpg://u:p@localhost:5432/db"},
            redis={"url": "redis://localhost:6379/0"},
            auth={"secret_key": STRONG_SECRET},
            worker={"broker_url": "redis://localhost:6379/2"},
        )


def test_unprefixed_env_variable_does_not_leak_into_the_run_config(monkeypatch):
    # A generic ENV is often set by the shell; only APP_CONFIG__RUN__ENV may select the environment.
    monkeypatch.setenv("ENV", "/etc/profile")
    monkeypatch.delenv("APP_CONFIG__RUN__ENV", raising=False)

    settings = Settings(
        _env_file=None,
        db={"url": "postgresql+asyncpg://u:p@localhost:5432/db"},
        redis={"url": "redis://localhost:6379/0"},
        auth={"secret_key": STRONG_SECRET},
        bot={"token": REAL_BOT_TOKEN},
        worker={"broker_url": "redis://localhost:6379/2"},
    )

    assert settings.run.env == "dev"


def test_env_template_is_not_a_settings_source():
    assert ".env.template" not in Settings.model_config["env_file"]
