from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.v1.dependencies.auth import get_current_user
from app.api.v1.mappers.profile import (
    map_profile_entity_to_profile_schema,
    map_profile_form_config_to_profile_form_config_schema,
    map_profile_update_request_to_profile_form_entity,
)
from app.api.v1.schemas import (
    ProfileCreateRequest,
    ProfileFormConfigSchema,
    ProfileSchema,
    ProfileUpdateRequest,
)
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.domain.constants import MAX_INT64
from app.domain.entities import UserEntity
from app.domain.exceptions import (
    CategoryNotFoundError,
    InvalidCountryError,
    InvalidExtraAttributesError,
    InvalidProfileTextError,
    InvalidTimezoneError,
    ProfileAlreadyExistsError,
    ProfileHiddenError,
    ProfileNotActivatableError,
    ProfileNotFoundError,
    ProfileTagInvalidError,
    RoleNotFoundError,
    TagRejectedError,
    TooFewTagsError,
    TooManyTagsError,
)

router = APIRouter(prefix="/profiles", tags=["Profiles"])

# Everything a client can get wrong in the profile content; shared by create and update.
INVALID_PROFILE_CONTENT_ERRORS = (
    InvalidProfileTextError,
    InvalidCountryError,
    InvalidTimezoneError,
    TooFewTagsError,
    TooManyTagsError,
    ProfileTagInvalidError,
    InvalidExtraAttributesError,
)

# Routes with fixed paths (/form-config, /me) are declared before /{profile_id}, otherwise
# "me" would be captured as a profile id.


@router.get("/form-config")
async def get_form_config(
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> ProfileFormConfigSchema:
    config, countries = await container.get_profile_form_config_use_case().execute()
    return map_profile_form_config_to_profile_form_config_schema(config, countries)


@router.get("/me")
async def get_my_profiles(
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> list[ProfileSchema]:
    profiles = await container.get_my_profiles_use_case().execute(current_user.id)
    return [map_profile_entity_to_profile_schema(p, current_user.active_profile_id) for p in profiles]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_profile(
    payload: ProfileCreateRequest,
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> ProfileSchema:
    try:
        profile = await container.create_profile_use_case().execute(
            user=current_user,
            category_id=payload.category_id,
            role_id=payload.role_id,
            form=map_profile_update_request_to_profile_form_entity(payload),
            extra_attributes=payload.extra_attributes,
        )
    except (CategoryNotFoundError, RoleNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except INVALID_PROFILE_CONTENT_ERRORS as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TagRejectedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ProfileAlreadyExistsError as exc:
        existing = map_profile_entity_to_profile_schema(exc.profile, current_user.active_profile_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": str(exc), "profile": existing.model_dump(mode="json")},
        ) from exc
    return map_profile_entity_to_profile_schema(profile, current_user.active_profile_id)


@router.get("/{profile_id}")
async def get_profile(
    profile_id: int = Path(..., ge=1, le=MAX_INT64),
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> ProfileSchema:
    try:
        profile = await container.get_profile_use_case().execute(current_user.id, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return map_profile_entity_to_profile_schema(profile, current_user.active_profile_id)


@router.put("/{profile_id}")
async def update_profile(
    payload: ProfileUpdateRequest,
    profile_id: int = Path(..., ge=1, le=MAX_INT64),
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> ProfileSchema:
    try:
        profile = await container.update_profile_use_case().execute(
            user_id=current_user.id,
            profile_id=profile_id,
            form=map_profile_update_request_to_profile_form_entity(payload),
            extra_attributes=payload.extra_attributes,
        )
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except INVALID_PROFILE_CONTENT_ERRORS as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except (ProfileHiddenError, TagRejectedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return map_profile_entity_to_profile_schema(profile, current_user.active_profile_id)


@router.post("/{profile_id}/activate")
async def activate_profile(
    profile_id: int = Path(..., ge=1, le=MAX_INT64),
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> ProfileSchema:
    try:
        profile = await container.activate_profile_use_case().execute(current_user, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ProfileHiddenError, ProfileNotActivatableError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return map_profile_entity_to_profile_schema(profile, current_user.active_profile_id)


@router.post("/{profile_id}/pause")
async def pause_profile(
    profile_id: int = Path(..., ge=1, le=MAX_INT64),
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> ProfileSchema:
    try:
        profile = await container.pause_profile_use_case().execute(current_user, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProfileHiddenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return map_profile_entity_to_profile_schema(profile, current_user.active_profile_id)


@router.post("/{profile_id}/resume")
async def resume_profile(
    profile_id: int = Path(..., ge=1, le=MAX_INT64),
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> ProfileSchema:
    try:
        profile = await container.resume_profile_use_case().execute(current_user, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProfileHiddenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return map_profile_entity_to_profile_schema(profile, current_user.active_profile_id)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(
    profile_id: int = Path(..., ge=1, le=MAX_INT64),
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(get_current_user),
) -> None:
    try:
        await container.delete_profile_use_case().execute(current_user, profile_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ProfileHiddenError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
