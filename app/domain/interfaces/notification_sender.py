from abc import ABC, abstractmethod

from app.domain.entities import ContactNotification


class INotificationSender(ABC):
    @abstractmethod
    async def send_contact_notification(self, notification: ContactNotification, text: str) -> None:
        """Delivers `text` to the owner's chat with a button that opens the opener's profile.

        Raises NotificationUnavailableError for a temporary failure (worth retrying) and NotificationRejectedError
        for a permanent one (retrying cannot help).
        """
