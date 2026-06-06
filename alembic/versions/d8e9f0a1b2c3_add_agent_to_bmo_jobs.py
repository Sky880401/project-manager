"""add agent column to bmo_jobs (Hermes 多代理路由)

Revision ID: d8e9f0a1b2c3
Revises: a7c9e1b3d5f0
Create Date: 2026-06-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8e9f0a1b2c3'
down_revision: Union[str, None] = 'a7c9e1b3d5f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('bmo_jobs', sa.Column('agent', sa.String(length=30), nullable=True))
    op.create_index('ix_bmo_jobs_agent', 'bmo_jobs', ['agent'])


def downgrade() -> None:
    op.drop_index('ix_bmo_jobs_agent', table_name='bmo_jobs')
    op.drop_column('bmo_jobs', 'agent')
