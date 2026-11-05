import json
import logging

import httpx
import pytest

from app.domain.exceptions import NotificationRejectedError, NotificationUnavailableError
from app.infrastructure.providers import StubNotificationSender, TelegramNotificationSender
from tests.unit.test_contact_notification_text import make_notification

TOKEN = "7012345678:AAH-secret-token"
TEXT = "Анна открыл(а) ваш контакт"
OK = {"ok": True, "result": {"message_id": 1}}


def _sender(handler, mini_app_url: str = "https://app.example.com") -> TelegramNotificationSender:
    return TelegramNotificationSender(
        token=TOKEN,
        base_url="https://api.telegram.test",
        mini_app_url=mini_app_url,
        timeout_seconds=1.0,
        transport=httpx.MockTransport(handler),
    )


def _respond(status: int, body=None, content: bytes | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if content is not None:
            return httpx.Response(status, content=content)
        return httpx.Response(status, json=body if body is not None else {})

    return handler


async def _send(handler, **kwargs) -> None:
    await _sender(handler, **kwargs).send_contact_notification(make_notification(), TEXT)


# ---------- the request ----------


async def test_posts_the_text_to_the_owner_chat_with_a_web_app_button():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["method"], sent["url"] = request.method, str(request.url)
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK)

    await _send(handler)

    assert sent["method"] == "POST"
    assert sent["url"] == f"https://api.telegram.test/bot{TOKEN}/sendMessage"
    assert sent["body"] == {
        "chat_id": 555,
        "text": TEXT,
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "Открыть анкету", "web_app": {"url": "https://app.example.com?profile_id=7"}}]
            ]
        },
    }


async def test_a_mini_app_url_that_already_has_a_query_gets_an_ampersand():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK)

    await _send(handler, mini_app_url="https://app.example.com/?lang=ru")

    button = sent["body"]["reply_markup"]["inline_keyboard"][0][0]
    assert button["web_app"]["url"] == "https://app.example.com/?lang=ru&profile_id=7"


async def test_no_parse_mode_is_sent():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json=OK)

    await _send(handler)

    assert "parse_mode" not in sent["body"]


# ---------- 200 ----------


async def test_a_200_with_ok_true_is_success():
    await _send(_respond(200, OK))


async def test_a_200_with_ok_false_is_a_rejection():
    with pytest.raises(NotificationRejectedError):
        await _send(_respond(200, {"ok": False}))


@pytest.mark.parametrize("body", [[], "ok", 1, None])
async def test_a_200_whose_json_is_not_an_object_with_ok_true_is_a_rejection(body):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(body).encode())

    with pytest.raises(NotificationRejectedError):
        await _send(handler)


async def test_a_200_that_is_not_json_is_retried():
    with pytest.raises(NotificationUnavailableError):
        await _send(_respond(200, content=b"<html>captive portal</html>"))


# ---------- permanent failures ----------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
async def test_other_4xx_are_rejections_without_retries(status):
    # 403: blocked the bot or never pressed /start. 400: chat not found. Repeating the request changes nothing.
    body = {"ok": False, "error_code": status, "description": "Forbidden: bot was blocked by the user"}

    with pytest.raises(NotificationRejectedError):
        await _send(_respond(status, body))


# ---------- temporary failures ----------


@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_is_retried(status):
    with pytest.raises(NotificationUnavailableError) as error:
        await _send(_respond(status, content=b"upstream trouble"))

    assert error.value.retry_after is None


async def test_429_is_retried_with_the_retry_after_from_telegram():
    body = {"ok": False, "error_code": 429, "parameters": {"retry_after": 17}}

    with pytest.raises(NotificationUnavailableError) as error:
        await _send(_respond(429, body))

    assert error.value.retry_after == 17.0


@pytest.mark.parametrize(
    "body",
    [
        {"ok": False},
        {"parameters": {}},
        {"parameters": {"retry_after": "soon"}},
        {"parameters": {"retry_after": True}},
        {"parameters": {"retry_after": -5}},
        {"parameters": []},
    ],
)
async def test_429_with_a_missing_or_nonsense_retry_after_is_still_retried(body):
    with pytest.raises(NotificationUnavailableError) as error:
        await _send(_respond(429, body))

    assert error.value.retry_after is None


async def test_429_that_is_not_json_is_still_retried():
    with pytest.raises(NotificationUnavailableError) as error:
        await _send(_respond(429, content=b"slow down"))

    assert error.value.retry_after is None


async def test_a_timeout_is_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(NotificationUnavailableError):
        await _send(handler)


async def test_a_connection_error_is_retried():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(NotificationUnavailableError):
        await _send(handler)


# ---------- the token stays out of errors ----------


async def test_a_network_error_does_not_carry_the_original_exception_with_the_token_url():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    with pytest.raises(NotificationUnavailableError) as error:
        await _send(handler)

    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True
    assert TOKEN not in str(error.value)


# ---------- the stub ----------


async def test_the_stub_logs_the_text_and_never_fails(caplog):
    with caplog.at_level(logging.INFO):
        await StubNotificationSender().send_contact_notification(make_notification(), TEXT)

    assert TEXT in caplog.text
    assert "telegram_id=555" in caplog.text
