from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.v1.dependencies.auth import require_admin
from app.api.v1.mappers.moderation import map_profile_entity_to_admin_profile_schema
from app.api.v1.schemas.moderation import AdminProfileSchema, AdminReasonRequest
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.constants import MAX_INT64
from app.domain.entities import UserEntity
from app.domain.exceptions import ProfileNotFoundError

router = APIRouter(prefix="/admin/profiles", tags=["Admin"], dependencies=[Depends(require_admin)])


@router.post("/{profile_id}/hide")
async def hide_profile(
    profile_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: AdminReasonRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> AdminProfileSchema:
    try:
        profile = await container.hide_profile_use_case().execute(admin, profile_id, body.reason)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return map_profile_entity_to_admin_profile_schema(profile)


@router.post("/{profile_id}/unhide")
async def unhide_profile(
    profile_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: AdminReasonRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> AdminProfileSchema:
    try:
        profile = await container.unhide_profile_use_case().execute(admin, profile_id, body.reason)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return map_profile_entity_to_admin_profile_schema(profile)
