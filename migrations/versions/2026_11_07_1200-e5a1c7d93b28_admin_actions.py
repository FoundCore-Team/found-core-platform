"""admin_actions

Revision ID: e5a1c7d93b28
Revises: b3f8c2a91d47
Create Date: 2026-11-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e5a1c7d93b28'
down_revision: Union[str, Sequence[str], None] = 'b3f8c2a91d47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('admin_actions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('actor_user_id', sa.BigInteger(), nullable=True),
    sa.Column('action', sa.Enum('ban_user', 'unban_user', 'hide_profile', 'unhide_profile', 'dismiss_reports', 'approve_tag', 'reject_tag', 'merge_tag', 'grant_admin', 'revoke_admin', name='admin_action_type'), nullable=False),
    sa.Column('target_type', sa.Enum('user', 'profile', 'tag', name='admin_target_type'), nullable=False),
    sa.Column('target_id', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.String(length=1000), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_admin_actions_actor_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_admin_actions'))
    )
    op.create_index('ix_admin_actions_created_at_id', 'admin_actions', ['created_at', 'id'], unique=False)
    op.create_index('ix_admin_actions_target', 'admin_actions', ['target_type', 'target_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_admin_actions_target', table_name='admin_actions')
    op.drop_index('ix_admin_actions_created_at_id', table_name='admin_actions')
    op.drop_table('admin_actions')

    # drop_table leaves PG enum types behind, which would make the next upgrade fail.
    op.execute("DROP TYPE admin_action_type")
    op.execute("DROP TYPE admin_target_type")
