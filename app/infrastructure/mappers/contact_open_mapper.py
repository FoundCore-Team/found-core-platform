from sqlalchemy import Row

from app.domain.entities import ContactNotification, ContactNotificationContext, ContactOpenEntity
from app.infrastructure.models import ContactOpenModel


def map_contact_open_model_to_contact_open_entity(model: ContactOpenModel) -> ContactOpenEntity:
    return ContactOpenEntity(
        opener_profile_id=model.opener_profile_id,
        opened_profile_id=model.opened_profile_id,
        created_at=model.created_at,
        notification_status=model.notification_status,
        notification_updated_at=model.notification_updated_at,
    )


def map_contact_notification_row_to_contact_notification_context(
    row: Row, opener_profile_id: int
) -> ContactNotificationContext:
    return ContactNotificationContext(
        notification_status=row.notification_status,
        created_at=row.created_at,
        opener_is_banned=row.opener_is_banned,
        opener_is_deleted=row.opener_is_deleted,
        owner_is_banned=row.owner_is_banned,
        owner_is_deleted=row.owner_is_deleted,
        notification=ContactNotification(
            owner_telegram_id=row.owner_telegram_id,
            opener_profile_id=opener_profile_id,
            opener_first_name=row.opener_first_name,
            opener_last_name=row.opener_last_name,
            # A user without a username is skipped before anything is sent, so "" stands in for None here.
            opener_username=row.opener_username or "",
            opener_category_title=row.opener_category_title,
            opener_role_title=row.opener_role_title,
            owner_category_title=row.owner_category_title,
            owner_role_title=row.owner_role_title,
        ),
    )
