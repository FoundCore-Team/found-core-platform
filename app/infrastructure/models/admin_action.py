from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.constants import ADMIN_REASON_MAX_LENGTH
from app.domain.enums import AdminActionType, AdminTargetType
from app.infrastructure.models.base import Base


class AdminActionModel(Base):
    __tablename__ = "admin_actions"
    __table_args__ = (
        # The journal is read newest first (`created_at DESC, id DESC`); Postgres scans this index backwards.
        Index("ix_admin_actions_created_at_id", "created_at", "id"),
        # "What was done to this user / profile / tag".
        Index("ix_admin_actions_target", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # NULL is the system (the `make_admin` script). SET NULL keeps the row when the admin's account is deleted.
    actor_user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[AdminActionType] = mapped_column(
        Enum(AdminActionType, name="admin_action_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    target_type: Mapped[AdminTargetType] = mapped_column(
        Enum(AdminTargetType, name="admin_target_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    # No foreign key on purpose: the journal is an audit trail and must outlive a deleted profile or user.
    target_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(ADMIN_REASON_MAX_LENGTH), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
