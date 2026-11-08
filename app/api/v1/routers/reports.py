from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.api.v1.dependencies.auth import get_current_user
from app.api.v1.mappers.report import map_report_entity_to_report_schema
from app.api.v1.schemas.report import ReportCreateRequest, ReportSchema
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.entities import UserEntity
from app.domain.exceptions import ReportProfileNotFoundError

router = APIRouter(prefix="/reports", tags=["Reports"])


@router.post("")
async def create_report(
    body: ReportCreateRequest,
    response: Response,
    container: Container = Depends(get_container),
    # Not `require_username`: a person with no username or no active profile may still have seen spam and must be able
    # to report it.
    current_user: UserEntity = Depends(get_current_user),
) -> ReportSchema:
    try:
        report, is_new = await container.create_report_use_case().execute(
            current_user,
            body.profile_id,
            body.reason,
            # An empty comment (a blank text field) means no comment.
            body.comment or None,
        )
    except ReportProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # 201: a new report. 200: the user already has an open report on this profile, returned unchanged.
    response.status_code = status.HTTP_201_CREATED if is_new else status.HTTP_200_OK
    return map_report_entity_to_report_schema(report)
