"""add order to projects

Revision ID: a1b2c3d4e5f6
Revises: 806867173ed3
Create Date: 2026-06-01 22:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '806867173ed3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('order', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('projects', 'order')
