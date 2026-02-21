"""add task notification columns

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-02-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('notify_at', JSONB(), nullable=True, server_default='[]'))
    op.add_column('tasks', sa.Column('notifications_sent', JSONB(), nullable=True, server_default='[]'))
    op.add_column('tasks', sa.Column('snoozed_until', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('tasks', 'snoozed_until')
    op.drop_column('tasks', 'notifications_sent')
    op.drop_column('tasks', 'notify_at')
