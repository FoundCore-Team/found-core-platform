"""Analytics providers and the per-process helper. The PostHog provider is checked against the real SDK client: with
`send=False` it builds the final message but neither queues it for a thread nor sends it, and `before_send` hands that
message to the test. So these tests see what PostHog would receive, without a network or a background thread."""

import importlib
import inspect
import logging

import pytest
from posthog import Posthog

from app.core.config import AnalyticsConfig
from app.domain.enums import AnalyticsEvent
from app.infrastructure.helpers.analytics_helper import AnalyticsHelper
from app.infrastructure.providers import NoopAnalyticsTracker, PostHogAnalyticsTracker, StubAnalyticsTracker

# `app.infrastructure.helpers.analytics_helper` as an attribute is the singleton re-exported by the package, not the
# module, so the module whose `Posthog` the tests replace is taken from the import system.
analytics_helper_module = importlib.import_module("app.infrastructure.helpers.analytics_helper")

POSTHOG_KEY = "phc_test_project_key"


def _sdk_client(sent: list[dict], **options) -> Posthog:
    def before_send(message: dict) -> None:
        sent.append(message)
        return None  # drop it: nothing is queued or sent

    return Posthog(POSTHOG_KEY, host="https://eu.i.posthog.com", send=False, before_send=before_send, **options)


def _posthog_tracker(sent: list[dict], **options) -> PostHogAnalyticsTracker:
    return PostHogAnalyticsTracker(client=_sdk_client(sent, **options), env="test", flush_timeout_seconds=3.0)


class _RecordingClient:
    """Stands in for the SDK only where the real one cannot be made to fail or to show its flush timeout."""

    def __init__(self, capture_error: Exception | None = None, flush_error: Exception | None = None) -> None:
        self.capture_error = capture_error
        self.flush_error = flush_error
        self.flush_timeouts: list[float | None] = []

    def capture(self, event: str, **kwargs) -> None:
        if self.capture_error is not None:
            raise self.capture_error

    def flush(self, timeout_seconds: float | None = 10) -> None:
        self.flush_timeouts.append(timeout_seconds)
        if self.flush_error is not None:
            raise self.flush_error


# ---------- PostHog provider, through the real SDK ----------


def test_posthog_event_carries_the_user_id_as_distinct_id_and_the_properties():
    sent: list[dict] = []

    _posthog_tracker(sent).track(AnalyticsEvent.PROFILE_CREATED, 42, {"actor_profile_id": 7, "is_first_profile": True})

    [message] = sent
    assert message["event"] == "profile_created"
    assert message["distinct_id"] == "42"
    assert message["properties"]["actor_profile_id"] == 7
    assert message["properties"]["is_first_profile"] is True


def test_posthog_adds_env_to_every_event_and_a_caller_cannot_override_it():
    sent: list[dict] = []
    tracker = _posthog_tracker(sent)

    tracker.track(AnalyticsEvent.FEED_PAGE_SERVED, 1, {"offset": 0})
    tracker.track(AnalyticsEvent.USER_LOGGED_IN, 1, {"env": "prod"})

    assert [message["properties"]["env"] for message in sent] == ["test", "test"]


def test_posthog_sends_person_properties_as_set_of_the_same_event():
    sent: list[dict] = []

    _posthog_tracker(sent).track(
        AnalyticsEvent.USER_LOGGED_IN, 5, {"has_username": True}, person_properties={"is_admin": False}
    )

    [message] = sent
    assert message["event"] == "user_logged_in"
    assert message["properties"]["$set"] == {"is_admin": False}


def test_posthog_event_without_person_properties_sets_nothing_on_the_person():
    sent: list[dict] = []

    _posthog_tracker(sent).track(AnalyticsEvent.CONTACT_OPENED, 5, {"remaining_today": 19})

    assert "$set" not in sent[0]["properties"]


def test_posthog_disables_geoip_even_on_a_client_built_with_geoip_on():
    sent: list[dict] = []

    _posthog_tracker(sent, disable_geoip=False).track(AnalyticsEvent.TAG_CREATED, 5, {"tag_id": 3})

    assert sent[0]["properties"]["$geoip_disable"] is True


# ---------- PostHog provider never raises ----------


def test_posthog_swallows_an_sdk_error_on_track_and_logs_a_warning(caplog):
    tracker = PostHogAnalyticsTracker(
        client=_RecordingClient(capture_error=RuntimeError("queue broken")), env="test", flush_timeout_seconds=3.0
    )

    with caplog.at_level(logging.WARNING):
        tracker.track(AnalyticsEvent.REPORT_CREATED, 5, {"reason": "spam"})

    assert "report_created was not queued" in caplog.text


def test_posthog_flush_is_bounded_by_the_configured_timeout():
    client = _RecordingClient()

    PostHogAnalyticsTracker(client=client, env="test", flush_timeout_seconds=3.0).flush()

    assert client.flush_timeouts == [3.0]


def test_posthog_swallows_an_sdk_error_on_flush(caplog):
    client = _RecordingClient(flush_error=RuntimeError("thread gone"))

    with caplog.at_level(logging.WARNING):
        PostHogAnalyticsTracker(client=client, env="test", flush_timeout_seconds=3.0).flush()

    assert "Analytics flush failed" in caplog.text


# ---------- stub ----------


def test_stub_writes_the_event_to_the_log(caplog):
    with caplog.at_level(logging.INFO, logger="app.infrastructure.providers.stub_analytics"):
        StubAnalyticsTracker().track(
            AnalyticsEvent.PROFILE_VIEWED, 9, {"target_profile_id": 4}, person_properties={"is_admin": True}
        )

    assert "profile_viewed" in caplog.text
    assert "user_id=9" in caplog.text
    assert "'target_profile_id': 4" in caplog.text
    assert "'is_admin': True" in caplog.text


# ---------- the per-process helper ----------


@pytest.mark.parametrize(
    "provider, tracker_type", [("disabled", NoopAnalyticsTracker), ("stub", StubAnalyticsTracker)]
)
def test_helper_builds_the_configured_provider(provider, tracker_type):
    helper = AnalyticsHelper(config=AnalyticsConfig(provider=provider), env="dev")

    assert type(helper.tracker) is tracker_type


def test_helper_creates_one_tracker_per_process():
    helper = AnalyticsHelper(config=AnalyticsConfig(provider="stub"), env="dev")

    assert helper.tracker is helper.tracker


class _FakePosthog:
    """Records how the helper builds the SDK client, so no real client and no thread is started."""

    instances: list["_FakePosthog"] = []

    def __init__(self, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs
        self.flush_timeouts: list[float | None] = []
        _FakePosthog.instances.append(self)

    def flush(self, timeout_seconds: float | None = 10) -> None:
        self.flush_timeouts.append(timeout_seconds)


@pytest.fixture
def fake_posthog(monkeypatch) -> type[_FakePosthog]:
    _FakePosthog.instances = []
    monkeypatch.setattr(analytics_helper_module, "Posthog", _FakePosthog)
    return _FakePosthog


def test_helper_builds_the_sdk_client_from_the_config(fake_posthog):
    config = AnalyticsConfig(provider="posthog", project_api_key=POSTHOG_KEY, host="https://eu.i.posthog.com")

    tracker = AnalyticsHelper(config=config, env="prod").tracker

    assert type(tracker) is PostHogAnalyticsTracker
    [client] = fake_posthog.instances
    # The names must exist in the real SDK: a misspelt keyword would raise TypeError only at the first real start.
    arguments = inspect.signature(Posthog).bind(*client.args, **client.kwargs).arguments
    assert arguments["project_api_key"] == POSTHOG_KEY
    assert arguments["host"] == "https://eu.i.posthog.com"
    assert (arguments["max_queue_size"], arguments["flush_at"], arguments["flush_interval"]) == (10_000, 100, 5.0)
    assert arguments["timeout"] == 5.0
    assert arguments.get("sync_mode", False) is False  # a blocking send per event would stall the request


async def test_helper_dispose_flushes_the_tracker_it_created(fake_posthog):
    helper = AnalyticsHelper(config=AnalyticsConfig(provider="posthog", project_api_key=POSTHOG_KEY), env="dev")
    _ = helper.tracker  # the first request creates it

    await helper.dispose()

    assert fake_posthog.instances[0].flush_timeouts == [3.0]


async def test_helper_dispose_without_a_tracker_creates_no_client(fake_posthog):
    helper = AnalyticsHelper(config=AnalyticsConfig(provider="posthog", project_api_key=POSTHOG_KEY), env="dev")

    await helper.dispose()

    assert fake_posthog.instances == []
