from app.api.v1.schemas.contact import OpenedContactSchema
from app.domain.entities import OpenedContact


def map_opened_contact_to_opened_contact_schema(contact: OpenedContact) -> OpenedContactSchema:
    return OpenedContactSchema(
        username=contact.username,
        is_new_open=contact.is_new_open,
        remaining_today=contact.remaining_today,
    )
