from app.domain.entities import ContactNotification
from app.domain.interfaces import INotificationJobQueue, INotificationSender


class FakeNotificationQueue(INotificationJobQueue):
    """Records the scheduled (opener, opened) pairs. Like the real queue, it never raises."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[int, int]] = []

    async def enqueue(self, opener_profile_id: int, opened_profile_id: int) -> None:
        self.enqueued.append((opener_profile_id, opened_profile_id))


class FakeNotificationSender(INotificationSender):
    """Records what would be sent; `error` is raised instead when set."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.sent: list[tuple[ContactNotification, str]] = []
        self.attempts = 0

    async def send_contact_notification(self, notification: ContactNotification, text: str) -> None:
        self.attempts += 1
        if self.error is not None:
            raise self.error
        self.sent.append((notification, text))
