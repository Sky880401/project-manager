"""add real usage pct from /usage to code usage

Revision ID: c9d8e7f60001
Revises: a9c1d2e3f4a5
Create Date: 2026-06-04 12:40:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c9d8e7f60001'
down_revision: Union[str, None] = 'a9c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 來自 Claude Code /usage 的真實額度百分比（5 小時 session 與當周）
    op.add_column('code_usage_reports', sa.Column('session_pct', sa.Integer(), nullable=True))
    op.add_column('code_usage_reports', sa.Column('weekly_pct', sa.Integer(), nullable=True))
    op.add_column('code_usage_reports', sa.Column('usage_reported_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('code_usage_reports', 'usage_reported_at')
    op.drop_column('code_usage_reports', 'weekly_pct')
    op.drop_column('code_usage_reports', 'session_pct')
