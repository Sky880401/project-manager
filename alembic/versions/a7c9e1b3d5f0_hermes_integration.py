"""hermes integration: source col + bmo_job_suggestions

Revision ID: a7c9e1b3d5f0
Revises: d1e2f3a4b5c6
Create Date: 2026-06-06 13:00:00.000000

讓 Hermes agent 能派寫碼 job 並提交「待採用建議」：
- bmo_jobs.source：派工來源標記（NULL=人類，"hermes"=Hermes agent）
- bmo_job_suggestions：Hermes 提交、真人 adopt/reject 的建議表

一個 revision 同時做兩件事，downgrade -1 可一次回滾整個 PR。
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a7c9e1b3d5f0'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # nullable，無 server_default：既有資料 source 維持 NULL（= 人類，行為不變）
    op.add_column('bmo_jobs', sa.Column('source', sa.Text(), nullable=True))

    op.create_table(
        'bmo_job_suggestions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('suggestion', sa.Text(), nullable=False),
        sa.Column('rationale', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='pending'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['bmo_jobs.id']),
    )
    op.create_index('ix_bmo_job_suggestions_job_id', 'bmo_job_suggestions', ['job_id'])


def downgrade() -> None:
    op.drop_index('ix_bmo_job_suggestions_job_id', table_name='bmo_job_suggestions')
    op.drop_table('bmo_job_suggestions')
    op.drop_column('bmo_jobs', 'source')
