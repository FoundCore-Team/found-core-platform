import base64
import json
import re

from app.api.v1.mappers.taxonomy import map_tag_entity_to_tag_schema
from app.api.v1.schemas import FeedItemSchema, FeedPageSchema, FeedProfileSchema, FeedReasonSchema
from app.api.v1.schemas.feed import (
    MatchedFiltersReasonSchema,
    ReasonTagSchema,
    SharedTagsReasonSchema,
    SimpleReasonSchema,
)
from app.domain.entities import FeedCursor, FeedFilters, FeedPage, FeedPageItem, FeedReason, TagEntity
from app.domain.enums import FeedReasonCode
from app.domain.exceptions import InvalidFeedCursorError, InvalidFeedFilterError

_BASE64_URL_ALPHABET = re.compile(r"[A-Za-z0-9_-]*")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# Bounds of one `attr=key:value` pair; they match the role_fields columns with room to spare.
_MAX_ATTRIBUTE_KEY_LENGTH = 64
_MAX_ATTRIBUTE_VALUE_LENGTH = 128


def encode_feed_cursor(cursor: FeedCursor) -> str:
    payload = json.dumps(
        {"o": cursor.offset, "p": cursor.params_hash, "s": cursor.session_key}, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def decode_feed_cursor(raw: str, max_offset: int) -> FeedCursor:
    """Parses a cursor the client sent back. Anything that is not exactly what `encode_feed_cursor` produced is invalid."""
    # b64decode silently skips characters outside the alphabet, so the alphabet is checked first.
    if not _BASE64_URL_ALPHABET.fullmatch(raw):
        raise InvalidFeedCursorError()
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (ValueError, RecursionError) as exc:
        raise InvalidFeedCursorError() from exc

    if type(data) is not dict or set(data) != {"o", "p", "s"}:
        raise InvalidFeedCursorError()
    offset, params_hash, session_key = data["o"], data["p"], data["s"]
    # `type(...) is int`: a bool is an int subclass and must not pass as an offset.
    if type(offset) is not int or not 0 <= offset <= max_offset:
        raise InvalidFeedCursorError()
    for value in (params_hash, session_key):
        if type(value) is not str or not _SHA256_HEX.fullmatch(value):
            raise InvalidFeedCursorError()
    return FeedCursor(offset=offset, params_hash=params_hash, session_key=session_key)


def map_feed_query_to_feed_filters(
    role_id: int | None,
    attr: list[str],
    tag_ids: list[int],
    country_code: str | None,
    same_timezone: bool,
) -> FeedFilters:
    """`attr` items look like `key:value`; the pair is split at the first colon."""
    attributes: dict[str, str] = {}
    for raw in attr:
        key, separator, value = raw.partition(":")
        key, value = key.strip(), value.strip()
        malformed = (
            not separator
            or not key
            or not value
            or len(key) > _MAX_ATTRIBUTE_KEY_LENGTH
            or len(value) > _MAX_ATTRIBUTE_VALUE_LENGTH
            or key in attributes
        )
        if malformed:
            raise InvalidFeedFilterError("attribute_malformed")
        attributes[key] = value
    return FeedFilters(
        role_id=role_id,
        attributes=attributes,
        tag_ids=tag_ids,
        country_code=country_code,
        same_timezone=same_timezone,
    )


def _map_reason(reason: FeedReason, tags_by_id: dict[int, TagEntity]) -> FeedReasonSchema | None:
    if reason.code is FeedReasonCode.SHARED_TAGS:
        # Titles come from the card, so the session only stores ids. A tag that was removed since is skipped.
        tags = [ReasonTagSchema(id=i, title=tags_by_id[i].title) for i in reason.tag_ids if i in tags_by_id]
        return SharedTagsReasonSchema(tags=tags) if tags else None
    if reason.code is FeedReasonCode.MATCHED_FILTERS:
        return MatchedFiltersReasonSchema(matched=reason.matched, missed=reason.missed)
    return SimpleReasonSchema(code=reason.code)  # type: ignore[arg-type]


def map_feed_page_item_to_feed_item_schema(item: FeedPageItem) -> FeedItemSchema:
    profile = item.card.profile
    tags_by_id = {tag.id: tag for tag in profile.tags}
    reasons = [_map_reason(reason, tags_by_id) for reason in item.reasons]
    return FeedItemSchema(
        profile=FeedProfileSchema(
            id=profile.id,
            first_name=item.card.first_name,
            photo_url=item.card.photo_url,
            category_id=profile.category_id,
            role_id=profile.role_id,
            country_code=profile.country_code,
            timezone=profile.timezone,
            bio=profile.bio,
            goals_description=profile.goals_description,
            extra_attributes=profile.extra_attributes,
            tags=[map_tag_entity_to_tag_schema(tag) for tag in profile.tags],
        ),
        match_type=item.tier,
        match_score=item.match_score,
        is_viewed=item.card.is_viewed,
        is_contact_opened=item.card.is_contact_opened,
        reasons=[reason for reason in reasons if reason is not None],
    )


def map_feed_page_to_feed_page_schema(page: FeedPage) -> FeedPageSchema:
    return FeedPageSchema(
        items=[map_feed_page_item_to_feed_item_schema(item) for item in page.items],
        next_cursor=encode_feed_cursor(page.next_cursor) if page.next_cursor is not None else None,
        has_more=page.has_more,
        end_reason=page.end_reason,
    )
