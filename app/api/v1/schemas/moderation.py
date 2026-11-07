from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.schemas.common import AdminReason
from app.domain.enums import AdminActionType, AdminTargetType


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
