from app.domain.entities import (
    CategoryEntity,
    PendingTagEntity,
    RoleEntity,
    RoleFieldEntity,
    TagCreatorEntity,
    TagEntity,
    TagScopeEntity,
)
from app.infrastructure.models import CategoryModel, RoleFieldModel, RoleModel, TagModel, TagScopeModel


def map_category_model_to_category_entity(model: CategoryModel) -> CategoryEntity:
    return CategoryEntity(
        id=model.id,
        slug=model.slug,
        title=model.title,
        sort_order=model.sort_order,
    )


def map_role_model_to_role_entity(model: RoleModel) -> RoleEntity:
    return RoleEntity(
        id=model.id,
        category_id=model.category_id,
        slug=model.slug,
        title=model.title,
        sort_order=model.sort_order,
    )


def map_role_field_model_to_role_field_entity(model: RoleFieldModel) -> RoleFieldEntity:
    return RoleFieldEntity(
        id=model.id,
        role_id=model.role_id,
        key=model.key,
        label=model.label,
        field_type=model.field_type,
        options=model.options,
        is_required=model.is_required,
        is_filterable=model.is_filterable,
        sort_order=model.sort_order,
    )


def map_tag_model_to_tag_entity(model: TagModel) -> TagEntity:
    return TagEntity(
        id=model.id,
        title=model.title,
        normalized_title=model.normalized_title,
        status=model.status,
        created_by_user_id=model.created_by_user_id,
        usage_count=model.usage_count,
        merged_into_tag_id=model.merged_into_tag_id,
    )


def map_pending_tag_row_to_pending_tag_entity(
    tag: TagModel,
    profiles_count: int,
    creator_first_name: str | None,
    creator_username: str | None,
    scopes: list[TagScopeModel],
) -> PendingTagEntity:
    # The creator columns come from an outer join: they are NULL when the tag has no author (the account is gone).
    creator = (
        TagCreatorEntity(user_id=tag.created_by_user_id, first_name=creator_first_name, username=creator_username)
        if tag.created_by_user_id is not None and creator_first_name is not None
        else None
    )
    return PendingTagEntity(
        tag=map_tag_model_to_tag_entity(tag),
        created_at=tag.created_at,
        profiles_count=profiles_count,
        creator=creator,
        scopes=[TagScopeEntity(category_id=s.category_id, role_id=s.role_id) for s in scopes],
    )
