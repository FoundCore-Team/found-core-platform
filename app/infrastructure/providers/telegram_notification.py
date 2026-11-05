import httpx

from app.domain.entities import ContactNotification
from app.domain.exceptions import NotificationRejectedError, NotificationUnavailableError
from app.domain.interfaces import INotificationSender
from app.domain.services import CONTACT_NOTIFICATION_BUTTON_TEXT


class TelegramNotificationSender(INotificationSender):
    """Calls the Bot API `sendMessage`. `transport` is None in production; tests pass httpx.MockTransport.

    The token is part of the URL, so no error raised here carries the original exception (`from None`): a traceback or
    a log line must never contain it.
    """

    def __init__(
        self,
        token: str,
        base_url: str,
        mini_app_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._mini_app_url = mini_app_url
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    def _button_url(self, notification: ContactNotification) -> str:
        separator = "&" if "?" in self._mini_app_url else "?"
        return f"{self._mini_app_url}{separator}profile_id={notification.opener_profile_id}"

    async def send_contact_notification(self, notification: ContactNotification, text: str) -> None:
        payload = {
            # A private chat with a user has the same id as the user.
            "chat_id": notification.owner_telegram_id,
            "text": text,
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": CONTACT_NOTIFICATION_BUTTON_TEXT, "web_app": {"url": self._button_url(notification)}}]
                ]
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds, transport=self._transport) as client:
                response = await client.post(f"{self._base_url}/bot{self._token}/sendMessage", json=payload)
        except httpx.RequestError:
            raise NotificationUnavailableError() from None

        if response.status_code == 429:
            raise NotificationUnavailableError(retry_after=self._retry_after(response))
        if response.status_code >= 500:
            raise NotificationUnavailableError()
        if response.status_code != 200:
            # 403 (blocked the bot, never pressed /start), 400 (chat not found, bad request) and the other 4xx:
            # the same request would fail the same way.
            raise NotificationRejectedError()

        try:
            body = response.json()
        except ValueError:
            # A 200 that is not JSON is not from the Bot API (a proxy or a captive page): try again later.
            raise NotificationUnavailableError() from None
        if not isinstance(body, dict) or body.get("ok") is not True:
            raise NotificationRejectedError()

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        try:
            value = response.json()["parameters"]["retry_after"]
        except (ValueError, KeyError, TypeError):
            return None
        # `bool` is an int subclass and must not pass as a number of seconds.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return None
        return float(value)
