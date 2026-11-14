from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.enums import AnalyticsEvent
from app.domain.interfaces import IAnalyticsTracker
from app.domain.interfaces.analytics import AnalyticsValue

_PRIMITIVE_TYPES = (str, int, float, bool, type(None))


@dataclass(frozen=True)
class TrackedEvent:
    event: AnalyticsEvent
    user_id: int
    properties: dict[str, AnalyticsValue]
    person_properties: dict[str, AnalyticsValue] | None


class FakeAnalyticsTracker(IAnalyticsTracker):
    """Records copies of the events. Stricter than the real tracker on one point: a non-primitive value (a list, a
    dict, an entity) fails the test at once. PostHog would accept a list, but the port allows only primitives."""

    def __init__(self) -> None:
        self.events: list[TrackedEvent] = []
        self.flush_calls = 0

    def track(
        self,
        event: AnalyticsEvent,
        user_id: int,
        properties: Mapping[str, AnalyticsValue],
        *,
        person_properties: Mapping[str, AnalyticsValue] | None = None,
    ) -> None:
        for name, value in [*properties.items(), *(person_properties or {}).items()]:
            if type(value) not in _PRIMITIVE_TYPES:
                raise AssertionError(f"analytics property {name!r} is not a primitive: {value!r}")
        self.events.append(
            TrackedEvent(
                event=event,
                user_id=user_id,
                properties=dict(properties),
                person_properties=dict(person_properties) if person_properties is not None else None,
            )
        )

    def flush(self) -> None:
        self.flush_calls += 1
