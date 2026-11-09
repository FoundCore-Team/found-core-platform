from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.v1.dependencies.auth import require_admin
from app.api.v1.mappers.moderation import (
    map_pending_tags_to_pending_tag_page_schema,
    map_tag_moderation_result_to_tag_moderation_result_schema,
)
from app.api.v1.schemas.moderation import (
    AdminReasonRequest,
    MergeTagRequest,
    PendingTagPageSchema,
    TagModerationResultSchema,
)
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.core.config import settings
from app.domain.constants import MAX_INT64
from app.domain.entities import UserEntity
from app.domain.exceptions import TagMergeTargetInvalidError, TagNotFoundError, TagStatusConflictError

router = APIRouter(prefix="/admin/tags", tags=["Admin"], dependencies=[Depends(require_admin)])


@router.get("/pending")
async def list_pending_tags(
    limit: Annotated[
        int, Query(ge=1, le=settings.moderation.page_size_max)
    ] = settings.moderation.page_size_default,
    offset: Annotated[int, Query(ge=0, le=settings.moderation.max_offset)] = 0,
    container: Container = Depends(get_container),
) -> PendingTagPageSchema:
    tags, total = await container.list_pending_tags_use_case().execute(limit, offset)
    return map_pending_tags_to_pending_tag_page_schema(tags, total, limit, offset)


@router.post("/{tag_id}/approve")
async def approve_tag(
    tag_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: AdminReasonRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> TagModerationResultSchema:
    try:
        result = await container.approve_tag_use_case().execute(admin, tag_id, body.reason)
    except TagNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TagStatusConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "tag_status_conflict", "message": str(exc), "status": exc.status},
        ) from exc
    return map_tag_moderation_result_to_tag_moderation_result_schema(result)


@router.post("/{tag_id}/reject")
async def reject_tag(
    tag_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: AdminReasonRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> TagModerationResultSchema:
    try:
        result = await container.reject_tag_use_case().execute(admin, tag_id, body.reason)
    except TagNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TagStatusConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "tag_status_conflict", "message": str(exc), "status": exc.status},
        ) from exc
    return map_tag_moderation_result_to_tag_moderation_result_schema(result)


@router.post("/{tag_id}/merge")
async def merge_tag(
    tag_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: MergeTagRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> TagModerationResultSchema:
    try:
        result = await container.merge_tag_use_case().execute(admin, tag_id, body.target_tag_id, body.reason)
    except TagNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except TagStatusConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "tag_status_conflict", "message": str(exc), "status": exc.status},
        ) from exc
    except TagMergeTargetInvalidError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "merge_target_invalid", "message": str(exc), "reason": exc.problem},
        ) from exc
    return map_tag_moderation_result_to_tag_moderation_result_schema(result)
