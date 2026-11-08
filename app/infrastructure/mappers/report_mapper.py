from collections.abc import Mapping
from typing import Any

from app.domain.entities import ReportEntity, ReportGroupEntity
from app.domain.enums import ReportReason
from app.infrastructure.models import ReportModel


def map_report_model_to_report_entity(model: ReportModel) -> ReportEntity:
    return ReportEntity(
        id=model.id,
        reporter_user_id=model.reporter_user_id,
        profile_id=model.profile_id,
        reason=model.reason,
        comment=model.comment,
        status=model.status,
        created_at=model.created_at,
        resolved_at=model.resolved_at,
    )


def map_report_group_row_to_report_group_entity(
    row: Any, reason_counts: Mapping[ReportReason, int]
) -> ReportGroupEntity:
    return ReportGroupEntity(
        profile_id=row.profile_id,
        profile_status=row.profile_status,
        category_id=row.category_id,
        role_id=row.role_id,
        owner_user_id=row.owner_user_id,
        owner_first_name=row.owner_first_name,
        owner_username=row.owner_username,
        owner_is_banned=row.owner_is_banned,
        open_count=row.open_count,
        oldest_at=row.oldest_at,
        newest_at=row.newest_at,
        # All five reasons are always present; the ones nobody used are 0.
        reasons={reason: reason_counts.get(reason, 0) for reason in ReportReason},
    )
