from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.models.base import Base


class ProfileViewModel(Base):
    __tablename__ = "profile_views"

    # Both sides are profiles: a view belongs to the active profile the viewer was using at the time.
    viewer_profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    viewed_profile_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    viewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
