from app.domain.entities import ContactOpenEntity
from app.infrastructure.models import ContactOpenModel


def map_contact_open_model_to_contact_open_entity(model: ContactOpenModel) -> ContactOpenEntity:
    return ContactOpenEntity(
        opener_profile_id=model.opener_profile_id,
        opened_profile_id=model.opened_profile_id,
        created_at=model.created_at,
        notification_status=model.notification_status,
        notification_updated_at=model.notification_updated_at,
    )
