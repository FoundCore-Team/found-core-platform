from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.constants import REPORT_COMMENT_MAX_LENGTH
from app.domain.enums import ReportReason, ReportStatus
from app.infrastructure.models.base import Base


class ReportModel(Base):
    __tablename__ = "reports"
    __table_args__ = (
        # One OPEN report per (reporter, profile): a double click or a retry cannot create a second one. A closed
        # report does not count, so after `dismiss`/`resolve` the same user may report the profile again. The insert
        # uses `ON CONFLICT (reporter_user_id, profile_id) WHERE status = 'open' DO NOTHING`, which needs this exact
        # predicate.
        Index(
            "uq_reports_open_per_reporter_profile",
            "reporter_user_id",
            "profile_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        # "Open reports of this profile" for the queue and for closing them. Also serves the cascade of a deleted
        # profile.
        Index("ix_reports_profile_id_status", "profile_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # CASCADE: a report goes away with its author's account (a user decision, see the stage 8 plan, section 9).
    reporter_user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # CASCADE: reports on a deleted profile are pointless; the admin's actions stay in `admin_actions`.
    profile_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[ReportReason] = mapped_column(
        Enum(ReportReason, name="report_reason", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(String(REPORT_COMMENT_MAX_LENGTH))
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        server_default=ReportStatus.OPEN.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
