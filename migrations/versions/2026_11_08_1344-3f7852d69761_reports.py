"""reports

Revision ID: 3f7852d69761
Revises: e5a1c7d93b28
Create Date: 2026-11-08 13:44:09.139407

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f7852d69761'
down_revision: Union[str, Sequence[str], None] = 'e5a1c7d93b28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('reports',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('reporter_user_id', sa.BigInteger(), nullable=False),
    sa.Column('profile_id', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.Enum('spam', 'fake', 'offensive', 'inappropriate_contact', 'other', name='report_reason'), nullable=False),
    sa.Column('comment', sa.String(length=1000), nullable=True),
    sa.Column('status', sa.Enum('open', 'resolved', 'dismissed', name='report_status'), server_default='open', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['profile_id'], ['profiles.id'], name=op.f('fk_reports_profile_id_profiles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['reporter_user_id'], ['users.id'], name=op.f('fk_reports_reporter_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_reports'))
    )
    op.create_index('ix_reports_profile_id_status', 'reports', ['profile_id', 'status'], unique=False)
    op.create_index('uq_reports_open_per_reporter_profile', 'reports', ['reporter_user_id', 'profile_id'], unique=True, postgresql_where=sa.text("status = 'open'"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_reports_open_per_reporter_profile', table_name='reports', postgresql_where=sa.text("status = 'open'"))
    op.drop_index('ix_reports_profile_id_status', table_name='reports')
    op.drop_table('reports')

    # drop_table leaves PG enum types behind, which would make the next upgrade fail.
    op.execute("DROP TYPE report_reason")
    op.execute("DROP TYPE report_status")
