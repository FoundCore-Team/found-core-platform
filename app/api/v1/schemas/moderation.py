from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.common import AdminReason, Int64Id
from app.api.v1.schemas.taxonomy import TagSchema
from app.domain.enums import AdminActionType, AdminTargetType, ProfileStatus, ReportReason, TagStatus


class AdminReasonRequest(BaseModel):
    """The body of every admin action that only needs a reason."""

    model_config = ConfigDict(extra="forbid")

    reason: AdminReason


class BanUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: AdminReason = Field(
        description=(
            "Written to the journal AND shown to the banned user (the 403 they get and `ban_reason` in the login "
            "response). Write it as text for that user."
        )
    )


class AdminUserSchema(BaseModel):
    id: int
    telegram_id: int
    first_name: str
    last_name: str | None
    username: str | None
    is_admin: bool
    is_banned: bool
    ban_reason: str | None
    created_at: datetime
    last_active_at: datetime


class AdminProfileSchema(BaseModel):
    """A profile as an admin sees it: any status, with the owner's id. No `is_active` (that is the owner's view)."""

    id: int
    user_id: int
    category_id: int
    role_id: int
    status: ProfileStatus
    country_code: str
    timezone: str
    bio: str
    goals_description: str
    extra_attributes: dict[str, str]
    tags: list[TagSchema]
    created_at: datetime
    updated_at: datetime


class ReportOwnerSchema(BaseModel):
    user_id: int
    first_name: str
    username: str | None
    is_banned: bool


class ReportGroupSchema(BaseModel):
    """One row of the report queue: a profile and its open reports."""

    profile_id: int
    profile_status: ProfileStatus
    category_id: int
    role_id: int
    owner: ReportOwnerSchema
    open_count: int
    oldest_at: datetime
    newest_at: datetime
    # Every reason is present, with 0 for the ones nobody used.
    reasons: dict[ReportReason, int]


class ReportGroupPageSchema(BaseModel):
    items: list[ReportGroupSchema]
    total: int
    limit: int
    offset: int


class AdminReportSchema(BaseModel):
    id: int
    reporter_user_id: int
    reason: ReportReason
    comment: str | None
    created_at: datetime


class ProfileReportsSchema(BaseModel):
    profile: AdminProfileSchema
    owner: AdminUserSchema
    reports: list[AdminReportSchema]


class DismissedReportsSchema(BaseModel):
    profile_id: int
    dismissed_count: int


class MergeTagRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_tag_id: Int64Id
    reason: AdminReason


class TagScopeSchema(BaseModel):
    category_id: int
    # None: the tag is scoped to the whole category.
    role_id: int | None


class TagCreatorSchema(BaseModel):
    user_id: int
    first_name: str
    username: str | None


class PendingTagSchema(BaseModel):
    """One row of the tag moderation queue."""

    id: int
    title: str
    normalized_title: str
    created_at: datetime
    usage_count: int
    # Profiles that carry the tag right now.
    profiles_count: int
    # None if the author's account is gone.
    creator: TagCreatorSchema | None
    scopes: list[TagScopeSchema]


class PendingTagPageSchema(BaseModel):
    items: list[PendingTagSchema]
    total: int
    limit: int
    offset: int


class AdminTagSchema(BaseModel):
    id: int
    title: str
    normalized_title: str
    status: TagStatus
    # Set exactly when the status is `merged`.
    merged_into_tag_id: int | None
    usage_count: int


class TagModerationResultSchema(BaseModel):
    tag: AdminTagSchema
    # Profiles whose tags changed; 0 for a repeated action.
    affected_profiles: int


class AdminActionSchema(BaseModel):
    id: int
    # None: the system did it (the `make_admin` script).
    actor_user_id: int | None
    action: AdminActionType
    target_type: AdminTargetType
    target_id: int
    reason: str
    details: dict[str, Any]
    created_at: datetime


class AdminActionPageSchema(BaseModel):
    items: list[AdminActionSchema]
    total: int
    limit: int
    offset: int
