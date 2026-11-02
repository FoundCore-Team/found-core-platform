from enum import StrEnum


class FeedMode(StrEnum):
    DISCOVERY = "discovery"
    SEARCH = "search"


class FeedTier(StrEnum):
    DISCOVERY = "DISCOVERY"
    EXACT_MATCH = "EXACT_MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    RECOMMENDED_BY_BIO = "RECOMMENDED_BY_BIO"


class FeedEndReason(StrEnum):
    NO_CANDIDATES_YET = "NO_CANDIDATES_YET"
    END_OF_FEED = "END_OF_FEED"


class FeedReasonCode(StrEnum):
    MATCHED_FILTERS = "matched_filters"
    SHARED_TAGS = "shared_tags"
    SAME_ROLE = "same_role"
    SAME_COUNTRY = "same_country"
    SAME_TIMEZONE = "same_timezone"
    SIMILAR_BIO = "similar_bio"
    RECENTLY_ACTIVE = "recently_active"
