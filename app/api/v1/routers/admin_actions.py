from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.v1.dependencies.auth import require_admin
from app.api.v1.mappers.moderation import map_admin_actions_to_admin_action_page_schema
from app.api.v1.schemas.moderation import AdminActionPageSchema
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.core.config import settings
from app.domain.constants import MAX_INT64
from app.domain.enums import AdminTargetType

router = APIRouter(prefix="/admin/actions", tags=["Admin"], dependencies=[Depends(require_admin)])


@router.get("")
async def list_admin_actions(
    target_type: AdminTargetType | None = None,
    target_id: Annotated[int | None, Query(ge=1, le=MAX_INT64)] = None,
    limit: Annotated[
        int, Query(ge=1, le=settings.moderation.page_size_max)
    ] = settings.moderation.page_size_default,
    offset: Annotated[int, Query(ge=0, le=settings.moderation.max_offset)] = 0,
    container: Container = Depends(get_container),
) -> AdminActionPageSchema:
    # An id means nothing without the kind of row it belongs to.
    if target_id is not None and target_type is None:
        raise HTTPException(status_code=422, detail="target_id needs target_type.")

    actions, total = await container.list_admin_actions_use_case().execute(limit, offset, target_type, target_id)
    return map_admin_actions_to_admin_action_page_schema(actions, total, limit, offset)
