from enum import StrEnum


class RateLimitAction(StrEnum):
    """What a per-user rate limit is counted for. A value is also the name of its field in `RateLimitConfig` and the
    middle part of the Redis key, so renaming one changes the config and orphans live counters."""

    CONTACT_OPEN = "contact_open"
    TAG_SUGGEST = "tag_suggest"
    TAG_CREATE = "tag_create"
    TAG_CREATE_DAILY = "tag_create_daily"
    REPORT_CREATE = "report_create"
    FEED_REQUEST = "feed_request"
    FEED_SESSION_BUILD = "feed_session_build"
    FEED_CARD = "feed_card"
    FEED_CARD_DAILY = "feed_card_daily"
    FEED_VIEW = "feed_view"
    PROFILE_WRITE = "profile_write"
