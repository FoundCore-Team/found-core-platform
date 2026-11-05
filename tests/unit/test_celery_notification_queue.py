import logging
import threading
import time

from app.infrastructure.queues import CeleryNotificationJobQueue
from app.worker.celery_app import SEND_CONTACT_NOTIFICATION_TASK
from tests.unit.test_celery_embedding_queue import FakeCeleryApp


def _queue(app: FakeCeleryApp, timeout_seconds: float = 1.0) -> CeleryNotificationJobQueue:
    return CeleryNotificationJobQueue(app, queue_name="notifications", timeout_seconds=timeout_seconds)


class TestCeleryNotificationJobQueue:
    async def test_sends_the_task_to_the_notifications_queue_without_retries(self):
        app = FakeCeleryApp()

        await _queue(app).enqueue(3, 42)

        assert app.calls == [
            (SEND_CONTACT_NOTIFICATION_TASK, {"args": [3, 42], "queue": "notifications", "retry": False})
        ]

    async def test_send_runs_outside_the_event_loop_thread(self):
        threads: list[int] = []

        class ThreadRecordingApp(FakeCeleryApp):
            def send_task(self, name: str, **kwargs) -> None:
                threads.append(threading.get_ident())

        await _queue(ThreadRecordingApp()).enqueue(3, 42)

        assert threads != [threading.get_ident()]

    async def test_broker_error_is_logged_and_not_raised(self, caplog):
        app = FakeCeleryApp(error=ConnectionError("broker is down"))

        with caplog.at_level(logging.WARNING):
            await _queue(app).enqueue(3, 42)

        assert "Could not enqueue the contact notification for profiles 3 -> 42" in caplog.text

    async def test_hanging_broker_does_not_hold_the_caller_past_the_timeout(self, caplog):
        app = FakeCeleryApp(block_seconds=1.0)

        started = time.monotonic()
        with caplog.at_level(logging.WARNING):
            await _queue(app, timeout_seconds=0.05).enqueue(3, 42)

        assert time.monotonic() - started < 0.5
        assert "Could not enqueue the contact notification for profiles 3 -> 42" in caplog.text
