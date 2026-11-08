from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import ProfileStatus, ReportReason, ReportStatus


@dataclass
class NewReportEntity:
    # A new report is always `open`, so there is no status here; the id and `resolved_at` appear after the insert.
    reporter_user_id: int
    profile_id: int
    reason: ReportReason
    comment: str | None
    created_at: datetime


@dataclass
class ReportEntity:
    id: int
    reporter_user_id: int
    profile_id: int
    reason: ReportReason
    comment: str | None
    status: ReportStatus
    created_at: datetime
    # None while the report is open.
    resolved_at: datetime | None


@dataclass
class ReportGroupEntity:
    """One row of the admin queue: all open reports on one profile, with the owner's data the admin needs at a glance."""

    profile_id: int
    profile_status: ProfileStatus
    category_id: int
    role_id: int
    owner_user_id: int
    owner_first_name: str
    owner_username: str | None
    owner_is_banned: bool
    open_count: int
    oldest_at: datetime
    newest_at: datetime
    # Every reason is present, with 0 for those nobody used.
    reasons: dict[ReportReason, int]
