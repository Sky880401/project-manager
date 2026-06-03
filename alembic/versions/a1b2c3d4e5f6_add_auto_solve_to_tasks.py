"""add auto_solve to tasks

Revision ID: d1e2f3a4b5c6
Revises: c9d8e7f60001
Create Date: 2026-06-04 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c9d8e7f60001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('tasks', sa.Column('auto_solve', sa.Boolean(),
                                     nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('tasks', 'auto_solve')
