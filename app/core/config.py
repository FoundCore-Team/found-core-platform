import hashlib
import json
import math
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, PostgresDsn, RedisDsn, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Public placeholders from .env.template / .env.test; a production deployment must never run with them.
_PLACEHOLDER_SECRET_KEYS = frozenset({"", "dev-only-insecure-secret-change-me", "change-me", "secret"})
_PLACEHOLDER_BOT_TOKENS = frozenset({"", "key", "123"})
_MIN_PROD_SECRET_KEY_LENGTH = 32


class RunConfig(BaseModel):
    # Plain BaseModel, not BaseSettings: the default `RunConfig()` instance would otherwise read unprefixed
    # environment variables, and a generic name like ENV is often already set in the shell.
    env: Literal["dev", "test", "prod"] = "dev"
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True


class DetailsConfig(BaseModel):
    title: str = "FoundCore API"
    description: str = "API"


class ApiConfig(BaseModel):
    prefix: str = "/api"


class DatabaseConfig(BaseSettings):
    url: PostgresDsn
    echo: bool = False
    echo_pool: bool = False
    pool_size: int = 50
    max_overflow: int = 10

    naming_convention: dict[str, str] = {
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }


class RedisConfig(BaseSettings):
    url: RedisDsn
    socket_timeout: float = 0.3


class BotConfig(BaseSettings):
    token: str


class AuthConfig(BaseSettings):
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    init_data_ttl_seconds: int = 300
    init_data_max_future_skew_seconds: int = 60


class TaxonomyConfig(BaseSettings):
    tag_title_min_length: int = 1
    tag_title_max_length: int = 64
    tag_title_allowed_pattern: str = r"[\w\s+#./-]+"
    cache_ttl_seconds: int = 21600
    suggest_limit: int = 20


class ProfileConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig: the default instance must not read unprefixed env variables.
    bio_min_length: int = 200
    bio_max_length: int = 2000
    goals_min_length: int = 200
    goals_max_length: int = 2000
    tags_min_count: int = 5
    tags_max_count: int = 15
    # Empty means every ISO 3166 country that has at least one timezone is allowed.
    allowed_country_codes: list[str] = []


class WorkerConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig. `broker_url` has no default and no fallback to `redis.url`,
    # so the Celery broker can never end up in the logical Redis DB that holds the cache by accident. It is shared by
    # every queue (embeddings, notifications): there is one `celery_app`.
    broker_url: str


class ContactConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig.
    # A sliding window per user (all of their profiles); repeated opens of one pair are free and not counted.
    daily_limit: int = 20
    window_hours: int = 24

    @model_validator(mode="after")
    def _check_consistency(self) -> "ContactConfig":
        if self.daily_limit < 1:
            raise ValueError("contact daily_limit must be positive")
        if self.window_hours < 1:
            raise ValueError("contact window_hours must be positive")
        return self


class ModerationConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig.
    # Paging of the admin lists (reports, pending tags, the action journal).
    page_size_default: int = 50
    page_size_max: int = 100
    # Upper bound of `offset`, so a huge value is a 422 instead of a slow sequential scan.
    max_offset: int = 100_000

    @model_validator(mode="after")
    def _check_consistency(self) -> "ModerationConfig":
        if not 1 <= self.page_size_default <= self.page_size_max:
            raise ValueError("moderation page_size_default must be between 1 and page_size_max")
        if self.max_offset < 0:
            raise ValueError("moderation max_offset must not be negative")
        return self


class NotificationConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig.
    # stub writes the text to the log; telegram calls the Bot API with `BotConfig.token`.
    provider: Literal["stub", "telegram"] = "stub"
    # Base of the "Open profile" button: `{mini_app_url}?profile_id=<opener profile id>`. Required in prod, https only.
    mini_app_url: str | None = None
    telegram_base_url: str = "https://api.telegram.org"
    request_timeout_seconds: float = 10.0
    enqueue_timeout_seconds: float = 0.5
    queue_name: str = "notifications"
    task_max_retries: int = 5
    task_time_limit_seconds: int = 60
    # Backoff is `retry_base_delay_seconds * 2**n`, or Telegram's `retry_after` if that is longer.
    retry_base_delay_seconds: int = 5
    sweep_interval_seconds: int = 60
    sweep_pending_after_seconds: int = 120
    sweep_batch_size: int = 100
    # A notification that is still pending after this long is dropped (`skipped`): late news looks like spam.
    stale_after_seconds: int = 86400

    @model_validator(mode="after")
    def _check_consistency(self) -> "NotificationConfig":
        if self.task_max_retries < 0:
            raise ValueError("notification task_max_retries must not be negative")
        for name in ("sweep_interval_seconds", "sweep_pending_after_seconds", "sweep_batch_size", "stale_after_seconds"):
            if getattr(self, name) < 1:
                raise ValueError(f"notification {name} must be positive")
        return self


class EmbeddingConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig.
    provider: Literal["stub", "openai"] = "stub"
    openai_api_key: SecretStr | None = None
    model: str = "text-embedding-3-small"
    openai_base_url: str = "https://api.openai.com/v1"
    request_timeout_seconds: float = 10.0
    enqueue_timeout_seconds: float = 0.5
    queue_name: str = "embeddings"
    task_max_retries: int = 5
    task_time_limit_seconds: int = 60
    retry_base_delay_seconds: int = 5
    sweep_interval_seconds: int = 600
    sweep_pending_after_seconds: int = 300
    sweep_failed_after_seconds: int = 3600
    sweep_batch_size: int = 100


class FeedConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig.
    weight_semantic: float = 0.4
    weight_tags: float = 0.4
    weight_role: float = 0.2
    role_affinity_same_role: float = 1.0
    role_affinity_same_category: float = 0.5
    activity_recent_days: int = 7
    activity_stale_days: int = 30
    activity_factor_recent: float = 1.0
    activity_factor_stale: float = 0.8
    activity_factor_old: float = 0.5
    session_ttl_seconds: int = 1800
    session_cap: int = 200
    page_size_default: int = 20
    page_size_max: int = 50
    bio_tier_min_cos_sim: float = 0.35
    similar_bio_reason_min_cos_sim: float = 0.5
    max_reason_tags: int = 5
    # Sanity bound for the offset inside a cursor; far above session_cap, so only forged cursors reach it.
    max_cursor_offset: int = 10_000

    @model_validator(mode="after")
    def _check_consistency(self) -> "FeedConfig":
        # Float sums are inexact (0.1 + 0.2 + 0.7 != 1.0), so compare with a tolerance.
        weights_sum = self.weight_semantic + self.weight_tags + self.weight_role
        if not math.isclose(weights_sum, 1.0):
            raise ValueError("feed weights must sum to 1")
        if not 0 < self.activity_recent_days < self.activity_stale_days:
            raise ValueError("feed activity thresholds must satisfy 0 < recent < stale")
        for name in ("bio_tier_min_cos_sim", "similar_bio_reason_min_cos_sim"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if not 1 <= self.page_size_default <= self.page_size_max:
            raise ValueError("feed page_size_default must be between 1 and page_size_max")
        if self.session_cap < 1:
            raise ValueError("feed session_cap must be positive")
        if self.max_cursor_offset < self.session_cap:
            raise ValueError("feed max_cursor_offset must not be below session_cap")
        return self

    def scoring_fingerprint(self) -> str:
        """Hash of every value that changes who is in a feed session or in what order. Part of the session key."""
        values = self.model_dump(
            include={
                "weight_semantic",
                "weight_tags",
                "weight_role",
                "role_affinity_same_role",
                "role_affinity_same_category",
                "activity_recent_days",
                "activity_stale_days",
                "activity_factor_recent",
                "activity_factor_stale",
                "activity_factor_old",
                "bio_tier_min_cos_sim",
                "similar_bio_reason_min_cos_sim",
                "max_reason_tags",
            }
        )
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode("ascii")).hexdigest()


class RateLimitRule(BaseModel):
    # At most `limit` requests per user in one fixed window of `window_seconds`.
    limit: int = Field(gt=0)
    window_seconds: int = Field(gt=0)


class RateLimitConfig(BaseModel):
    # Plain BaseModel for the same reason as RunConfig. One field per `RateLimitAction` member, named like its value
    # (a unit test keeps the two in step). Override one number with
    # APP_CONFIG__RATE_LIMIT__<ACTION>__LIMIT or __WINDOW_SECONDS.
    contact_open: RateLimitRule = RateLimitRule(limit=10, window_seconds=60)
    tag_suggest: RateLimitRule = RateLimitRule(limit=60, window_seconds=60)
    tag_create: RateLimitRule = RateLimitRule(limit=5, window_seconds=60)
    tag_create_daily: RateLimitRule = RateLimitRule(limit=30, window_seconds=86400)
    report_create: RateLimitRule = RateLimitRule(limit=10, window_seconds=86400)
    feed_request: RateLimitRule = RateLimitRule(limit=60, window_seconds=60)
    feed_session_build: RateLimitRule = RateLimitRule(limit=10, window_seconds=60)
    feed_card: RateLimitRule = RateLimitRule(limit=30, window_seconds=60)
    feed_card_daily: RateLimitRule = RateLimitRule(limit=300, window_seconds=86400)
    feed_view: RateLimitRule = RateLimitRule(limit=60, window_seconds=60)
    profile_write: RateLimitRule = RateLimitRule(limit=5, window_seconds=600)

    @model_validator(mode="before")
    @classmethod
    def _complete_partial_rules(cls, data):
        # pydantic-settings does not merge a nested env value into the field's default, so setting only
        # ..._CONTACT_OPEN__LIMIT would arrive without `window_seconds`. Fill the missing number from the default.
        if not isinstance(data, dict):
            return data
        completed = dict(data)
        for name, field in cls.model_fields.items():
            given = data.get(name)
            if isinstance(given, dict):
                completed[name] = {**field.default.model_dump(), **given}
        return completed


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env",),
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_nested_delimiter="__",
        env_prefix="APP_CONFIG__",
        extra="ignore",
    )
    run: RunConfig = RunConfig()
    api: ApiConfig = ApiConfig()
    db: DatabaseConfig
    redis: RedisConfig
    details: DetailsConfig = DetailsConfig()
    bot: BotConfig
    auth: AuthConfig
    taxonomy: TaxonomyConfig = TaxonomyConfig()
    profile: ProfileConfig = ProfileConfig()
    feed: FeedConfig = FeedConfig()
    contact: ContactConfig = ContactConfig()
    moderation: ModerationConfig = ModerationConfig()
    rate_limit: RateLimitConfig = RateLimitConfig()
    worker: WorkerConfig
    embedding: EmbeddingConfig = EmbeddingConfig()
    notification: NotificationConfig = NotificationConfig()

    @staticmethod
    def _redis_location(url: str) -> tuple[str | None, int, str]:
        parts = urlsplit(url)
        return parts.hostname, parts.port or 6379, parts.path.lstrip("/") or "0"

    @model_validator(mode="after")
    def _refuse_insecure_production_settings(self) -> "Settings":
        if self.run.env != "prod":
            return self

        problems = []
        if self.auth.secret_key in _PLACEHOLDER_SECRET_KEYS:
            problems.append("APP_CONFIG__AUTH__SECRET_KEY is a known placeholder")
        elif len(self.auth.secret_key) < _MIN_PROD_SECRET_KEY_LENGTH:
            problems.append(f"APP_CONFIG__AUTH__SECRET_KEY is shorter than {_MIN_PROD_SECRET_KEY_LENGTH} characters")
        if self.bot.token in _PLACEHOLDER_BOT_TOKENS:
            problems.append("APP_CONFIG__BOT__TOKEN is a known placeholder")
        if self.run.reload:
            problems.append("APP_CONFIG__RUN__RELOAD must be false")
        if self.embedding.provider == "stub":
            problems.append("APP_CONFIG__EMBEDDING__PROVIDER must not be stub")
        elif self.embedding.openai_api_key is None or not self.embedding.openai_api_key.get_secret_value():
            problems.append("APP_CONFIG__EMBEDDING__OPENAI_API_KEY is required for the openai provider")
        if self.notification.provider == "stub":
            problems.append("APP_CONFIG__NOTIFICATION__PROVIDER must not be stub")
        if not self.notification.mini_app_url:
            problems.append("APP_CONFIG__NOTIFICATION__MINI_APP_URL is required")
        elif urlsplit(self.notification.mini_app_url).scheme != "https":
            # Telegram refuses a web_app button that is not https, which would fail every notification.
            problems.append("APP_CONFIG__NOTIFICATION__MINI_APP_URL must be an https URL")
        if self._redis_location(self.worker.broker_url) == self._redis_location(str(self.redis.url)):
            problems.append("APP_CONFIG__WORKER__BROKER_URL must use a different Redis DB than APP_CONFIG__REDIS__URL")
        if problems:
            raise ValueError("Refusing to start with env=prod: " + "; ".join(problems))
        return self


settings = Settings()  # type: ignore