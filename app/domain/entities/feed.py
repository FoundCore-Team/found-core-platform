from dataclasses import dataclass, field
from datetime import datetime

from app.domain.entities.profile import ProfileEntity
from app.domain.enums import FeedEndReason, FeedReasonCode, FeedTier


@dataclass
class FeedFilters:
    role_id: int | None
    attributes: dict[str, str]
    tag_ids: list[int]
    country_code: str | None
    same_timezone: bool

    def criteria(self) -> list[str]:
        """Codes of the criteria the viewer actually set. Each one is matched or missed by a candidate on its own."""
        codes = []
        if self.role_id is not None:
            codes.append("role")
        codes.extend(f"attr:{key}" for key in sorted(self.attributes))
        if self.tag_ids:
            codes.append("tags")
        if self.country_code is not None:
            codes.append("country")
        if self.same_timezone:
            codes.append("timezone")
        return codes


@dataclass
class FeedViewer:
    """The viewer's active profile reduced to the signals the feed compares."""

    profile_id: int
    user_id: int
    category_id: int
    role_id: int
    country_code: str
    timezone: str
    # Tags without the rejected ones.
    tag_ids: list[int]
    # True if the profile has a vector of the current embedding model.
    has_comparable_vector: bool


@dataclass
class FeedCandidate:
    """Raw signals of one candidate as selected by SQL. The formula is applied later, in Python."""

    profile_id: int
    category_id: int
    role_id: int
    country_code: str
    timezone: str
    tag_ids: list[int]
    # None if the pair has no comparable vectors; otherwise the cosine similarity, clamped to [0, 1] by the query.
    cos_sim: float | None
    last_active_at: datetime
    # Criteria codes (see FeedFilters.criteria) this candidate matched; empty in discovery and in the bio tier.
    matched_filters: list[str]


@dataclass
class FeedReason:
    """A machine-readable reason; the frontend builds the text. Only the fields of its own code are filled."""

    code: FeedReasonCode
    # shared_tags: ids only, titles are taken from the hydrated card so the session stays small.
    tag_ids: list[int] = field(default_factory=list)
    # matched_filters
    matched: list[str] = field(default_factory=list)
    missed: list[str] = field(default_factory=list)


@dataclass
class FeedRankedItem:
    profile_id: int
    tier: FeedTier
    score: float
    reasons: list[FeedReason]


@dataclass
class FeedSession:
    # The key comes from the client's cursor, so the use case checks these two before trusting the items.
    viewer_profile_id: int
    params_hash: str
    items: list[FeedRankedItem]
    built_at: datetime


@dataclass
class FeedCursor:
    offset: int
    params_hash: str
    session_key: str


@dataclass
class FeedCard:
    profile: ProfileEntity
    first_name: str
    photo_url: str | None
    is_viewed: bool
    # The viewer's active profile has already opened this profile's contact (a free repeat `POST` gives the username).
    is_contact_opened: bool = False


@dataclass
class FeedPageItem:
    card: FeedCard
    # None for a single card opened by id: it did not come from a ranking.
    tier: FeedTier | None
    match_score: int
    reasons: list[FeedReason]


@dataclass
class FeedPage:
    items: list[FeedPageItem]
    next_cursor: FeedCursor | None
    has_more: bool
    end_reason: FeedEndReason | None
