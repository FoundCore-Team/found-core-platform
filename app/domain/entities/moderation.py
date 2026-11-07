from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.enums import AdminActionType, AdminTargetType


@dataclass
class NewAdminActionEntity:
    # None means the system did it (the `make_admin` script), not a person with an account.
    actor_user_id: int | None
    action: AdminActionType
    target_type: AdminTargetType
    target_id: int
    reason: str
    # Context of the action (owner of a hidden profile, number of closed reports, ...); `{}` when there is none.
    details: dict[str, Any]
    created_at: datetime


@dataclass
class AdminActionEntity:
    id: int
    actor_user_id: int | None
    action: AdminActionType
    target_type: AdminTargetType
    target_id: int
    reason: str
    details: dict[str, Any]
    created_at: datetime
