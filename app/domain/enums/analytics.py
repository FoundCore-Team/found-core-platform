from enum import StrEnum


class AnalyticsEvent(StrEnum):
    """Names of the product analytics events (`object_action`, past tense). A name cannot change once events are
    collected under it: old and new events would end up on different charts. The frontend never reuses these."""

    USER_SIGNED_UP = "user_signed_up"
    USER_LOGGED_IN = "user_logged_in"
    PROFILE_CREATED = "profile_created"
    PROFILE_UPDATED = "profile_updated"
    PROFILE_ACTIVATED = "profile_activated"
    PROFILE_PAUSED = "profile_paused"
    PROFILE_RESUMED = "profile_resumed"
    PROFILE_DELETED = "profile_deleted"
    FEED_PAGE_SERVED = "feed_page_served"
    FEED_RANKED = "feed_ranked"
    FEED_EMPTY = "feed_empty"
    PROFILE_VIEWED = "profile_viewed"
    CONTACT_OPENED = "contact_opened"
    TAG_CREATED = "tag_created"
    REPORT_CREATED = "report_created"
