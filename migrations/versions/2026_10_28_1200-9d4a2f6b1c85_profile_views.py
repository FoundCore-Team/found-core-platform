"""profile_views

Revision ID: 9d4a2f6b1c85
Revises: 7c1e5a9b3d42
Create Date: 2026-10-28 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4a2f6b1c85'
down_revision: Union[str, Sequence[str], None] = '7c1e5a9b3d42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('profile_views',
    sa.Column('viewer_profile_id', sa.BigInteger(), nullable=False),
    sa.Column('viewed_profile_id', sa.BigInteger(), nullable=False),
    sa.Column('viewed_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['viewed_profile_id'], ['profiles.id'], name=op.f('fk_profile_views_viewed_profile_id_profiles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['viewer_profile_id'], ['profiles.id'], name=op.f('fk_profile_views_viewer_profile_id_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('viewer_profile_id', 'viewed_profile_id', name=op.f('pk_profile_views'))
    )
    op.create_index(op.f('ix_profile_views_viewed_profile_id'), 'profile_views', ['viewed_profile_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_profile_views_viewed_profile_id'), table_name='profile_views')
    op.drop_table('profile_views')
