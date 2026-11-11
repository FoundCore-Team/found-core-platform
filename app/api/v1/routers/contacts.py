from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Response, status

from app.api.v1.dependencies.auth import require_username
from app.api.v1.dependencies.rate_limit import rate_limited
from app.api.v1.mappers.contact import map_opened_contact_to_opened_contact_schema
from app.api.v1.schemas.contact import OpenedContactSchema
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.constants import MAX_INT64
from app.domain.entities import UserEntity
from app.domain.enums import RateLimitAction
from app.domain.exceptions import ContactLimitReachedError, ContactProfileNotFoundError, NoActiveProfileError

router = APIRouter(prefix="/profiles", tags=["Contacts"])


@router.post("/{profile_id}/contact", dependencies=[Depends(rate_limited(RateLimitAction.CONTACT_OPEN))])
async def open_contact(
    profile_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    response: Response,
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(require_username),
) -> OpenedContactSchema:
    try:
        contact = await container.open_contact_use_case().execute(current_user, profile_id)
    except NoActiveProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_profile", "message": str(exc)},
        ) from exc
    except ContactProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ContactLimitReachedError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "contact_limit_reached",
                "message": str(exc),
                "limit": exc.limit,
                "resets_at": exc.resets_at.isoformat(),
            },
        ) from exc
    # 201: the open created a record. 200: a repeat of an already opened pair.
    response.status_code = status.HTTP_201_CREATED if contact.is_new_open else status.HTTP_200_OK
    return map_opened_contact_to_opened_contact_schema(contact)
