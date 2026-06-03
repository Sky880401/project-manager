"""add owner_id to projects

Revision ID: a9c1d2e3f4a5
Revises: f7a8b9c0d1e2
Create Date: 2026-06-03

每個專案歸屬於一位 LINE 使用者（owner_id）。既有資料保留 NULL，
視為「舊資料」，只有管理者（BMO_ADMIN_USER 或桌機 dashboard）看得到。
"""
from alembic import op
import sqlalchemy as sa


revision = "a9c1d2e3f4a5"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_projects_owner_id", ["owner_id"])


def downgrade():
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_index("ix_projects_owner_id")
        batch_op.drop_column("owner_id")
