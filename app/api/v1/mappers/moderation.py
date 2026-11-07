from app.api.v1.schemas.moderation import AdminActionPageSchema, AdminActionSchema, AdminUserSchema
from app.domain.entities import AdminActionEntity, UserEntity


def map_user_entity_to_admin_user_schema(user: UserEntity) -> AdminUserSchema:
    return AdminUserSchema(
        id=user.id,
        telegram_id=user.telegram_id,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        is_admin=user.is_admin,
        is_banned=user.is_banned,
        ban_reason=user.ban_reason,
        created_at=user.created_at,
        last_active_at=user.last_active_at,
    )


def map_admin_action_entity_to_admin_action_schema(action: AdminActionEntity) -> AdminActionSchema:
    return AdminActionSchema(
        id=action.id,
        actor_user_id=action.actor_user_id,
        action=action.action,
        target_type=action.target_type,
        target_id=action.target_id,
        reason=action.reason,
        details=action.details,
        created_at=action.created_at,
    )


def map_admin_actions_to_admin_action_page_schema(
    actions: list[AdminActionEntity], total: int, limit: int, offset: int
) -> AdminActionPageSchema:
    return AdminActionPageSchema(
        items=[map_admin_action_entity_to_admin_action_schema(action) for action in actions],
        total=total,
        limit=limit,
        offset=offset,
    )
