from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import RoleFieldType, TagStatus
from app.infrastructure.models.base import Base


class CategoryModel(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class RoleModel(Base):
    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("category_id", "slug"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class RoleFieldModel(Base):
    __tablename__ = "role_fields"
    __table_args__ = (UniqueConstraint("role_id", "key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    role_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    field_type: Mapped[RoleFieldType] = mapped_column(
        Enum(RoleFieldType, name="role_field_type", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    options: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB, nullable=True)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class TagModel(Base):
    __tablename__ = "tags"
    __table_args__ = (
        Index("ix_tags_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
        # The moderation queue reads pending tags oldest first; pending is a small part of the table, so the index is tiny.
        Index(
            "ix_tags_pending_created_at",
            "created_at",
            "id",
            postgresql_where=text("status = 'pending'"),
        ),
        # An alias points at its target and only an alias does.
        CheckConstraint(
            "(status = 'merged') = (merged_into_tag_id IS NOT NULL)",
            name="merged_target_matches_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_title: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[TagStatus] = mapped_column(
        Enum(TagStatus, name="tag_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    # No ondelete: a tag that other tags are merged into must not be deleted from under its aliases.
    merged_into_tag_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("tags.id"), nullable=True, index=True
    )


class TagScopeModel(Base):
    __tablename__ = "tag_scopes"
    __table_args__ = (
        UniqueConstraint("tag_id", "category_id", "role_id", postgresql_nulls_not_distinct=True),
        Index("ix_tag_scopes_category_id_role_id", "category_id", "role_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tag_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("categories.id", ondelete="CASCADE"), nullable=False
    )
    role_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("roles.id", ondelete="CASCADE"), nullable=True
    )
