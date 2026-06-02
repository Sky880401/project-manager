"""add workspace to bmo_jobs

Revision ID: f7a8b9c0d1e2
Revises: e5f6a7b8c9d0
Create Date: 2026-06-02 16:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f7a8b9c0d1e2'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bmo_jobs', sa.Column('workspace', sa.String(length=50),
                                        nullable=False, server_default='project-manager'))
    op.create_index('ix_bmo_jobs_workspace', 'bmo_jobs', ['workspace'])


def downgrade() -> None:
    op.drop_index('ix_bmo_jobs_workspace', table_name='bmo_jobs')
    op.drop_column('bmo_jobs', 'workspace')
