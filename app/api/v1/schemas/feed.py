from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.api.v1.schemas.common import Int64Id
from app.api.v1.schemas.taxonomy import TagSchema
from app.domain.enums import FeedEndReason, FeedReasonCode, FeedTier


class FeedProfileSchema(BaseModel):
    """The public part of a profile. Deliberately without `username` and `last_name`: the contact opens through `POST /profiles/{id}/contact`."""

    id: int
    first_name: str
    photo_url: str | None
    category_id: int
    role_id: int
    country_code: str
    timezone: str
    bio: str
    goals_description: str
    extra_attributes: dict[str, str]
    tags: list[TagSchema]


class ReasonTagSchema(BaseModel):
    id: int
    title: str


class SharedTagsReasonSchema(BaseModel):
    code: Literal[FeedReasonCode.SHARED_TAGS] = FeedReasonCode.SHARED_TAGS
    tags: list[ReasonTagSchema]


class MatchedFiltersReasonSchema(BaseModel):
    code: Literal[FeedReasonCode.MATCHED_FILTERS] = FeedReasonCode.MATCHED_FILTERS
    matched: list[str]
    missed: list[str]


class SimpleReasonSchema(BaseModel):
    code: Literal[
        FeedReasonCode.SAME_ROLE,
        FeedReasonCode.SAME_COUNTRY,
        FeedReasonCode.SAME_TIMEZONE,
        FeedReasonCode.SIMILAR_BIO,
        FeedReasonCode.RECENTLY_ACTIVE,
    ]


FeedReasonSchema = SharedTagsReasonSchema | MatchedFiltersReasonSchema | SimpleReasonSchema


class FeedItemSchema(BaseModel):
    profile: FeedProfileSchema
    # None for a card opened by id: it did not come out of a ranking.
    match_type: FeedTier | None
    match_score: int
    is_viewed: bool
    # The frontend shows "Write" instead of "Open contact"; the username itself comes from a free repeat POST.
    is_contact_opened: bool
    reasons: list[FeedReasonSchema]


class FeedPageSchema(BaseModel):
    items: list[FeedItemSchema]
    next_cursor: str | None
    has_more: bool
    end_reason: FeedEndReason | None


class FeedViewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: Int64Id
