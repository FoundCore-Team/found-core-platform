from abc import ABC, abstractmethod
from datetime import datetime

from app.domain.entities import NewReportEntity, ReportEntity, ReportGroupEntity
from app.domain.enums import ReportStatus


class IReportRepository(ABC):
    @abstractmethod
    async def is_profile_visible_to(self, viewer_user_id: int, profile_id: int) -> bool:
        """Whether this user may see (and so report) the profile: the same rule as the feed and contact opening."""

    @abstractmethod
    async def create(self, report: NewReportEntity) -> ReportEntity | None:
        """Inserts an `open` report. `None` means the reporter already has an open report on this profile (another
        request got there first); the use case then reads that one. Does not commit."""

    @abstractmethod
    async def get_open_by_reporter_and_profile(self, reporter_user_id: int, profile_id: int) -> ReportEntity | None:
        """The reporter's open report on the profile, if there is one."""

    @abstractmethod
    async def list_open_groups(self, limit: int, offset: int) -> list[ReportGroupEntity]:
        """A page of the admin queue: profiles with open reports, one row each. Ordered by `open_count DESC`, oldest
        report first, then `profile_id` as the unique tie-breaker."""

    @abstractmethod
    async def count_open_groups(self) -> int:
        """How many profiles have at least one open report."""

    @abstractmethod
    async def list_open_by_profile(self, profile_id: int) -> list[ReportEntity]:
        """Open reports on one profile, oldest first (`created_at, id`)."""

    @abstractmethod
    async def close_open_for_profile(self, profile_id: int, status: ReportStatus, now: datetime) -> int:
        """Moves every open report on the profile to `status` (`resolved` or `dismissed`) and returns how many were
        closed. Does not commit."""

    @abstractmethod
    async def resolve_open_for_user(self, user_id: int, now: datetime) -> int:
        """Moves every open report on any profile of this user to `resolved` and returns how many. Does not commit."""
