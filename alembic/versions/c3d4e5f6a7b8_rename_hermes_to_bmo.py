"""rename hermes_jobs to bmo_jobs and add review columns

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-01 23:55:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table('hermes_jobs', 'bmo_jobs')
    op.add_column('bmo_jobs', sa.Column('parent_id', sa.Integer(), nullable=True))
    op.add_column('bmo_jobs', sa.Column('branch', sa.String(length=200), nullable=True))
    op.add_column('bmo_jobs', sa.Column('diff', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('bmo_jobs', 'diff')
    op.drop_column('bmo_jobs', 'branch')
    op.drop_column('bmo_jobs', 'parent_id')
    op.rename_table('bmo_jobs', 'hermes_jobs')
