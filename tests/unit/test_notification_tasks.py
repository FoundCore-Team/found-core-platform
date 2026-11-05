from app.core.config import settings
from app.domain.exceptions import NotificationUnavailableError
from app.worker import tasks
from app.worker.celery_app import (
    SEND_CONTACT_NOTIFICATION_TASK,
    SWEEP_STALE_NOTIFICATIONS_TASK,
    celery_app,
)

def _task_class() -> type:
    # `tasks.send_contact_notification` is a lazy proxy, and `type()` of it is the proxy class, not the task's. The
    # registry holds the real task instance, whose class has `retry`.
    return type(celery_app.tasks[SEND_CONTACT_NOTIFICATION_TASK])


class TestSendContactNotificationTask:
    def test_retries_until_the_last_attempt_and_flags_only_that_one(self, monkeypatch):
        calls: list[tuple[int, int, bool]] = []

        async def fake(opener: int, opened: int, is_last_attempt: bool) -> None:
            calls.append((opener, opened, is_last_attempt))
            # What the real use case does: an unavailable channel is retried until the last attempt.
            if not is_last_attempt:
                raise NotificationUnavailableError()

        monkeypatch.setattr(tasks, "run_send_contact_notification", fake)

        result = tasks.send_contact_notification.apply(args=[3, 42])

        assert result.successful()
        assert calls == [(3, 42, False)] * settings.notification.task_max_retries + [(3, 42, True)]

    def test_success_on_the_first_attempt_is_not_retried(self, monkeypatch):
        calls: list[bool] = []

        async def fake(opener: int, opened: int, is_last_attempt: bool) -> None:
            calls.append(is_last_attempt)

        monkeypatch.setattr(tasks, "run_send_contact_notification", fake)

        assert tasks.send_contact_notification.apply(args=[3, 42]).successful()
        assert calls == [False]

    def test_the_delay_is_exponential_but_never_shorter_than_retry_after(self, monkeypatch):
        monkeypatch.setattr(settings.notification, "retry_base_delay_seconds", 5)
        countdowns: list[float] = []

        async def fake(opener: int, opened: int, is_last_attempt: bool) -> None:
            raise NotificationUnavailableError(retry_after=100 if not countdowns else None)

        def record_retry(self, *args, **kwargs):
            countdowns.append(kwargs["countdown"])
            raise RuntimeError("stop after recording")

        monkeypatch.setattr(tasks, "run_send_contact_notification", fake)
        monkeypatch.setattr(_task_class(), "retry", record_retry)

        tasks.send_contact_notification.apply(args=[3, 42])

        # First attempt: 5 * 2**0 = 5 s, but Telegram asked for 100 s.
        assert countdowns == [100]

    def test_the_plain_exponential_delay_when_there_is_no_retry_after(self, monkeypatch):
        monkeypatch.setattr(settings.notification, "retry_base_delay_seconds", 5)
        countdowns: list[float] = []

        async def fake(opener: int, opened: int, is_last_attempt: bool) -> None:
            raise NotificationUnavailableError()

        def record_retry(self, *args, **kwargs):
            countdowns.append(kwargs["countdown"])
            raise RuntimeError("stop after recording")

        monkeypatch.setattr(tasks, "run_send_contact_notification", fake)
        monkeypatch.setattr(_task_class(), "retry", record_retry)

        tasks.send_contact_notification.apply(args=[3, 42])

        assert countdowns == [5]

    def test_other_errors_are_not_retried(self, monkeypatch):
        calls: list[bool] = []

        async def fake(opener: int, opened: int, is_last_attempt: bool) -> None:
            calls.append(is_last_attempt)
            raise RuntimeError("boom")

        monkeypatch.setattr(tasks, "run_send_contact_notification", fake)

        result = tasks.send_contact_notification.apply(args=[3, 42])

        assert result.failed()
        assert calls == [False]


class TestSweepTask:
    def test_runs_the_sweep(self, monkeypatch):
        calls: list[str] = []

        async def fake() -> int:
            calls.append("sweep")
            return 3

        monkeypatch.setattr(tasks, "run_sweep_stale_notifications", fake)

        assert tasks.sweep_stale_notifications.apply().successful()
        assert calls == ["sweep"]


class TestSchedule:
    def test_the_notification_sweep_runs_in_the_notification_queue(self):
        entry = celery_app.conf.beat_schedule["sweep-stale-notifications"]

        assert entry["task"] == SWEEP_STALE_NOTIFICATIONS_TASK
        assert entry["options"] == {"queue": settings.notification.queue_name}
        assert entry["schedule"] == float(settings.notification.sweep_interval_seconds)

    def test_the_embedding_sweep_stays_in_the_default_queue(self):
        entry = celery_app.conf.beat_schedule["sweep-stale-embeddings"]

        assert "options" not in entry

    def test_the_tasks_are_registered_under_the_names_the_queue_sends(self):
        assert SEND_CONTACT_NOTIFICATION_TASK in celery_app.tasks
        assert SWEEP_STALE_NOTIFICATIONS_TASK in celery_app.tasks
