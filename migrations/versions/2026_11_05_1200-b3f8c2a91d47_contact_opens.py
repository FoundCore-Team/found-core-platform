"""contact_opens

Revision ID: b3f8c2a91d47
Revises: 9d4a2f6b1c85
Create Date: 2026-11-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f8c2a91d47'
down_revision: Union[str, Sequence[str], None] = '9d4a2f6b1c85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('contact_opens',
    sa.Column('opener_profile_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('opened_profile_id', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('notification_status', sa.Enum('pending', 'sent', 'failed', 'skipped', name='notification_status'), nullable=False),
    sa.Column('notification_updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['opened_profile_id'], ['profiles.id'], name=op.f('fk_contact_opens_opened_profile_id_profiles'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['opener_profile_id'], ['profiles.id'], name=op.f('fk_contact_opens_opener_profile_id_profiles'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('opener_profile_id', 'opened_profile_id', name=op.f('pk_contact_opens'))
    )
    op.create_index(op.f('ix_contact_opens_opened_profile_id'), 'contact_opens', ['opened_profile_id'], unique=False)
    op.create_index('ix_contact_opens_pending_updated_at', 'contact_opens', ['notification_updated_at'], unique=False, postgresql_where=sa.text("notification_status = 'pending'"))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_contact_opens_pending_updated_at', table_name='contact_opens', postgresql_where=sa.text("notification_status = 'pending'"))
    op.drop_index(op.f('ix_contact_opens_opened_profile_id'), table_name='contact_opens')
    op.drop_table('contact_opens')

    # drop_table leaves PG enum types behind, which would make the next upgrade fail.
    op.execute("DROP TYPE notification_status")
