import logging

from app.domain.entities import ContactNotification
from app.domain.interfaces import INotificationSender

logger = logging.getLogger(__name__)


class StubNotificationSender(INotificationSender):
    """Writes the message to the log instead of sending it: the stage works without a bot (CLAUDE.md, "Embeddings"
    has the same idea for vectors). The default outside prod."""

    async def send_contact_notification(self, notification: ContactNotification, text: str) -> None:
        logger.info(
            "[stub notification] to telegram_id=%s (button: profile %s)\n%s",
            notification.owner_telegram_id,
            notification.opener_profile_id,
            text,
        )
