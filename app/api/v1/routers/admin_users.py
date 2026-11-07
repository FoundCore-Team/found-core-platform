from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.v1.dependencies.auth import require_admin
from app.api.v1.mappers.moderation import map_user_entity_to_admin_user_schema
from app.api.v1.schemas.moderation import AdminReasonRequest, AdminUserSchema, BanUserRequest
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.constants import MAX_INT64
from app.domain.entities import UserEntity
from app.domain.exceptions import CannotBanAdminError, UserNotFoundError

# The router-level dependency guards every route added here later; a route that needs the admin's id declares the
# same dependency as a parameter (it runs once per request).
router = APIRouter(prefix="/admin/users", tags=["Admin"], dependencies=[Depends(require_admin)])


@router.post("/{user_id}/ban")
async def ban_user(
    user_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: BanUserRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> AdminUserSchema:
    try:
        user = await container.ban_user_use_case().execute(admin, user_id, body.reason)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except CannotBanAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "cannot_ban_admin", "message": str(exc)},
        ) from exc
    return map_user_entity_to_admin_user_schema(user)


@router.post("/{user_id}/unban")
async def unban_user(
    user_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    body: AdminReasonRequest,
    container: Container = Depends(get_container),
    admin: UserEntity = Depends(require_admin),
) -> AdminUserSchema:
    try:
        user = await container.unban_user_use_case().execute(admin, user_id, body.reason)
    except UserNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return map_user_entity_to_admin_user_schema(user)
