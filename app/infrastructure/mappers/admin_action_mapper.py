from app.domain.entities import AdminActionEntity
from app.infrastructure.models import AdminActionModel


def map_admin_action_model_to_admin_action_entity(model: AdminActionModel) -> AdminActionEntity:
    return AdminActionEntity(
        id=model.id,
        actor_user_id=model.actor_user_id,
        action=model.action,
        target_type=model.target_type,
        target_id=model.target_id,
        reason=model.reason,
        details=dict(model.details),
        created_at=model.created_at,
    )
