"""add user_source_settings table and task.source column

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-02-22 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e6f7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create user_source_settings table
    op.create_table(
        'user_source_settings',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('source', sa.String(50), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('sync_cursor', sa.Text(), nullable=True),
        sa.Column('watch_expiry', sa.DateTime(timezone=True), nullable=True),
        sa.Column('watch_resource_id', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('user_id', 'source', name='uq_user_source_settings_user_source'),
    )

    # Backfill a gmail source setting row for every existing user
    op.execute("""
        INSERT INTO user_source_settings (id, user_id, source, enabled, sync_cursor, watch_expiry, created_at, updated_at)
        SELECT
            gen_random_uuid()::text,
            u.id,
            'gmail',
            true,
            CASE WHEN u.gmail_history_id IS NOT NULL
                 THEN '{"history_id": "' || u.gmail_history_id || '"}'
                 ELSE NULL
            END,
            u.gmail_watch_expiry,
            NOW(),
            NOW()
        FROM users u
    """)

    # Add source column to tasks table with default 'gmail'
    op.add_column('tasks', sa.Column('source', sa.String(50), nullable=False, server_default='gmail'))
    op.create_index('ix_tasks_source', 'tasks', ['source'])


def downgrade() -> None:
    op.drop_index('ix_tasks_source', table_name='tasks')
    op.drop_column('tasks', 'source')
    op.drop_table('user_source_settings')
