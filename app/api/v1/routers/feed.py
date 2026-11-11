from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from pydantic import Field

from app.api.v1.dependencies.auth import require_username
from app.api.v1.dependencies.rate_limit import rate_limited, too_many_requests
from app.api.v1.mappers.feed import (
    decode_feed_cursor,
    map_feed_page_item_to_feed_item_schema,
    map_feed_page_to_feed_page_schema,
    map_feed_query_to_feed_filters,
)
from app.api.v1.schemas import FeedItemSchema, FeedPageSchema, FeedViewRequest
from app.api.v1.schemas.common import Int64Id
from app.core.composition.container import Container
from app.core.composition.di import get_container
from app.core.config import settings
from app.domain.constants import MAX_INT64
from app.domain.entities import UserEntity
from app.domain.enums import FeedMode, RateLimitAction
from app.domain.exceptions import (
    FeedProfileNotFoundError,
    InvalidFeedCursorError,
    InvalidFeedFilterError,
    NoActiveProfileError,
    RateLimitExceededError,
)

router = APIRouter(prefix="/feed", tags=["Feed"])

# Bounds of a request, set above the business limits (those live in the domain) so a hostile query is rejected early.
MAX_ATTR_ITEMS = 10
MAX_ATTR_LENGTH = 200
MAX_TAG_FILTER_IDS = 15


def _no_active_profile(exc: NoActiveProfileError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "no_active_profile", "message": str(exc)},
    )


@router.get("", dependencies=[Depends(rate_limited(RateLimitAction.FEED_REQUEST))])
async def get_feed(
    mode: FeedMode,
    role_id: Annotated[int | None, Query(ge=1, le=MAX_INT64)] = None,
    attr: Annotated[list[Annotated[str, Field(max_length=MAX_ATTR_LENGTH)]], Query(max_length=MAX_ATTR_ITEMS)] = [],
    tag_ids: Annotated[list[Int64Id], Query(max_length=MAX_TAG_FILTER_IDS)] = [],
    country_code: Annotated[str | None, Query(max_length=16)] = None,
    same_timezone: bool = False,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=settings.feed.page_size_max)] = settings.feed.page_size_default,
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(require_username),
) -> FeedPageSchema:
    has_filters = role_id is not None or bool(attr) or bool(tag_ids) or country_code is not None or same_timezone
    # Search without a filter would be Discovery under another name, and Discovery with filters would silently ignore
    # them: either way the client is told to pick the mode it means.
    if mode is FeedMode.SEARCH and not has_filters:
        raise HTTPException(status_code=422, detail="mode=search needs a filter.")
    if mode is FeedMode.DISCOVERY and has_filters:
        raise HTTPException(status_code=422, detail="Filters need mode=search.")

    try:
        feed_cursor = decode_feed_cursor(cursor, settings.feed.max_cursor_offset) if cursor is not None else None
        filters = map_feed_query_to_feed_filters(role_id, attr, tag_ids, country_code, same_timezone)
        page = await container.get_feed_use_case().execute(current_user, mode, filters, feed_cursor, limit)
    except NoActiveProfileError as exc:
        raise _no_active_profile(exc) from exc
    except InvalidFeedFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_filter", "message": str(exc), "reason": exc.reason},
        ) from exc
    except InvalidFeedCursorError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_cursor", "message": str(exc)},
        ) from exc
    except RateLimitExceededError as exc:
        # Raised when the feed session has to be built; the per-request limit is the route dependency.
        raise too_many_requests(exc) from exc
    return map_feed_page_to_feed_page_schema(page)


@router.get(
    "/cards/{profile_id}",
    dependencies=[
        Depends(rate_limited(RateLimitAction.FEED_CARD)),
        Depends(rate_limited(RateLimitAction.FEED_CARD_DAILY)),
    ],
)
async def get_feed_card(
    profile_id: Annotated[int, Path(ge=1, le=MAX_INT64)],
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(require_username),
) -> FeedItemSchema:
    try:
        item = await container.get_feed_card_use_case().execute(current_user, profile_id)
    except NoActiveProfileError as exc:
        raise _no_active_profile(exc) from exc
    except FeedProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return map_feed_page_item_to_feed_item_schema(item)


@router.post(
    "/views",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limited(RateLimitAction.FEED_VIEW))],
)
async def record_profile_view(
    payload: FeedViewRequest,
    container: Container = Depends(get_container),
    current_user: UserEntity = Depends(require_username),
) -> Response:
    try:
        await container.record_profile_view_use_case().execute(current_user, payload.profile_id)
    except NoActiveProfileError as exc:
        raise _no_active_profile(exc) from exc
    except FeedProfileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
