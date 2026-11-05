from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import NotificationStatus
from app.infrastructure.models.base import Base


class ContactOpenModel(Base):
    __tablename__ = "contact_opens"
    __table_args__ = (
        # Only rows still waiting for their notification: the sweep scans this index, and almost every row is sent.
        Index(
            "ix_contact_opens_pending_updated_at",
            "notification_updated_at",
            postgresql_where=text("notification_status = 'pending'"),
        ),
    )

    # Both sides are profiles. The primary key `(opener_profile_id, opened_profile_id)` is "one open per pair" and its
    # prefix also serves lookups by opener.
    opener_profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True, autoincrement=False
    )
    # Indexed for the cascade of a deleted profile and for `is_contact_opened`.
    opened_profile_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("profiles.id", ondelete="CASCADE"),
        primary_key=True,
        autoincrement=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notification_status: Mapped[NotificationStatus] = mapped_column(
        Enum(NotificationStatus, name="notification_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    notification_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
