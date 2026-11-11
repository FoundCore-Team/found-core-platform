"""tag_moderation

Revision ID: a7c2e94f1b36
Revises: 3f7852d69761
Create Date: 2026-11-09 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c2e94f1b36'
down_revision: Union[str, Sequence[str], None] = '3f7852d69761'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # A new enum value cannot be used in the transaction that adds it, and `alembic upgrade head` runs in one
    # transaction, so the value is added outside of it before anything refers to it (the CHECK below does).
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE tag_status ADD VALUE IF NOT EXISTS 'merged'")

    # Existing rows get the time of this migration.
    op.add_column('tags', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('tags', sa.Column('merged_into_tag_id', sa.BigInteger(), nullable=True))
    op.create_index(op.f('ix_tags_merged_into_tag_id'), 'tags', ['merged_into_tag_id'], unique=False)
    op.create_foreign_key(op.f('fk_tags_merged_into_tag_id_tags'), 'tags', 'tags', ['merged_into_tag_id'], ['id'])
    op.create_index('ix_tags_pending_created_at', 'tags', ['created_at', 'id'], unique=False, postgresql_where=sa.text("status = 'pending'"))
    op.create_check_constraint(op.f('ck_tags_merged_target_matches_status'), 'tags', "(status = 'merged') = (merged_into_tag_id IS NOT NULL)")


def downgrade() -> None:
    """Downgrade schema."""
    # The partial index and the CHECK mention `status`, so they go before the column type is replaced.
    op.drop_constraint(op.f('ck_tags_merged_target_matches_status'), 'tags', type_='check')
    op.drop_index('ix_tags_pending_created_at', table_name='tags', postgresql_where=sa.text("status = 'pending'"))
    op.drop_constraint(op.f('fk_tags_merged_into_tag_id_tags'), 'tags', type_='foreignkey')
    op.drop_index(op.f('ix_tags_merged_into_tag_id'), table_name='tags')
    op.drop_column('tags', 'merged_into_tag_id')
    op.drop_column('tags', 'created_at')

    # An alias is gone with its link, so it falls back to the closest old status. Postgres cannot drop one value from
    # an enum: the type is recreated without 'merged'.
    op.execute("UPDATE tags SET status = 'rejected' WHERE status = 'merged'")
    op.execute("ALTER TYPE tag_status RENAME TO tag_status_old")
    op.execute("CREATE TYPE tag_status AS ENUM ('approved', 'pending', 'rejected')")
    op.execute("ALTER TABLE tags ALTER COLUMN status TYPE tag_status USING status::text::tag_status")
    op.execute("DROP TYPE tag_status_old")
