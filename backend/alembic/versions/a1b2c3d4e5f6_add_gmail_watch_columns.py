"""add gmail watch columns

Revision ID: a1b2c3d4e5f6
Revises: 00047dc09945
Create Date: 2026-02-18 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '00047dc09945'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('gmail_history_id', sa.String(length=50), nullable=True))
    op.add_column('users', sa.Column('gmail_watch_expiry', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'gmail_watch_expiry')
    op.drop_column('users', 'gmail_history_id')
