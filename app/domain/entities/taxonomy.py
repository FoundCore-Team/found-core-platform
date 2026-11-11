from dataclasses import dataclass
from datetime import datetime

from app.domain.enums import RoleFieldType, TagStatus


@dataclass
class CategoryEntity:
    id: int
    slug: str
    title: str
    sort_order: int = 0


@dataclass
class RoleEntity:
    id: int
    category_id: int
    slug: str
    title: str
    sort_order: int = 0


@dataclass
class RoleFieldEntity:
    id: int
    role_id: int
    key: str
    label: str
    field_type: RoleFieldType
    options: list[dict[str, str]] | None
    is_required: bool
    is_filterable: bool
    sort_order: int = 0


@dataclass
class NewTagEntity:
    title: str
    normalized_title: str
    status: TagStatus
    created_by_user_id: int | None = None


@dataclass
class TagEntity:
    id: int
    title: str
    normalized_title: str
    status: TagStatus
    created_by_user_id: int | None
    usage_count: int = 0
    # Set exactly when status is MERGED: the tag this one is an alias of (never itself an alias).
    merged_into_tag_id: int | None = None


@dataclass
class NewTagScopeEntity:
    tag_id: int
    category_id: int
    role_id: int | None = None


@dataclass
class TagScopeEntity:
    category_id: int
    # None = the tag is scoped to the whole category.
    role_id: int | None


@dataclass
class TagCreatorEntity:
    user_id: int
    first_name: str
    username: str | None


@dataclass
class PendingTagEntity:
    """A row of the moderation queue: the tag plus what a moderator needs to judge it."""

    tag: TagEntity
    created_at: datetime
    # How many profiles carry the tag right now (usage_count only ever grows).
    profiles_count: int
    # None if the author's account is gone.
    creator: TagCreatorEntity | None
    scopes: list[TagScopeEntity]
