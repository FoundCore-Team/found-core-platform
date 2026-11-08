from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.dependencies.auth import require_admin
from app.api.v1.mappers.moderation import (
    map_profile_reports_to_profile_reports_schema,
    map_report_groups_to_report_group_page_schema,
)
from app.api.v1.schemas.moderation import (
    AdminReasonRequest,
    DismissedReportsSchema,
    ProfileReportsSchema,
    ReportGroupPageSchema,
)
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.core.config import settings
from app.domain.constants import MAX_INT64
from app.domain.entities import UserEntity
from app.domain.exceptions import ProfileNotFoundError

router = APIRouter(prefix="/admin/reports", tags=["Admin"], dependencies=[Depends(require_admin)])


@router.get("")
async def list_report_groups(
    limit: Annotated[
        int, Query(ge=1, le=settings.moderation.page_size_max)
    ] = settings.moderation.page_size_default,
    offset: Annotated[int, Query(ge=0, le=settings.moderation.max_offset)] = 0,
    container: Container = Depends(get_container),
) -> ReportGroupPageSchema:
    groups, total = await container.list_report_groups_use_case().execute(limit, offset)
    return map_report_groups_to_report_group_page_schema(groups, total, limit, offset)


@router.get("/profiles/{profile_id}")
async def get_profile_reports(
    profile_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    container: Container = Depends(get_container),
) -> ProfileReportsSchema:
    try:
        profile, owner, reports = await container.get_profile_reports_use_case().execute(profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return map_profile_reports_to_profile_reports_schema(profile, owner, reports)


@router.post("/profiles/{profile_id}/dismiss")
async def dismiss_profile_reports(
    profile_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: AdminReasonRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> DismissedReportsSchema:
    try:
        dismissed = await container.dismiss_profile_reports_use_case().execute(admin, profile_id, body.reason)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DismissedReportsSchema(profile_id=profile_id, dismissed_count=dismissed)
