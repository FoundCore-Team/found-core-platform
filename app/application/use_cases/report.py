from datetime import datetime, timezone

from app.domain.entities import NewReportEntity, ReportEntity, UserEntity
from app.domain.enums import ReportReason
from app.domain.exceptions import ReportProfileNotFoundError
from app.domain.interfaces import IReportRepository, IUnitOfWork


class CreateReportUseCase:
    """A user reports a profile they could see. One OPEN report per (reporter, profile): a repeat while it is open
    returns the existing report and writes nothing; after it is closed a new one may be filed."""

    def __init__(self, report_repository: IReportRepository, unit_of_work: IUnitOfWork) -> None:
        self._report_repository = report_repository
        self._unit_of_work = unit_of_work

    async def execute(
        self, user: UserEntity, profile_id: int, reason: ReportReason, comment: str | None
    ) -> tuple[ReportEntity, bool]:
        """Returns the report and whether it is new (True: `201`, False: an open one already existed, `200`)."""
        if not await self._report_repository.is_profile_visible_to(user.id, profile_id):
            raise ReportProfileNotFoundError()

        new_report = NewReportEntity(
            reporter_user_id=user.id,
            profile_id=profile_id,
            reason=reason,
            comment=comment,
            created_at=datetime.now(timezone.utc),
        )
        # Two attempts: the insert is refused by an open report, but that report may be closed by an admin before we
        # read it, and then the second insert goes through.
        for _ in range(2):
            created = await self._report_repository.create(new_report)
            if created is not None:
                await self._unit_of_work.commit()
                return created, True
            existing = await self._report_repository.get_open_by_reporter_and_profile(user.id, profile_id)
            if existing is not None:
                return existing, False
        # Needs the open report to be closed twice in the gap between two of our statements.
        raise RuntimeError("Could not create or find the open report")
