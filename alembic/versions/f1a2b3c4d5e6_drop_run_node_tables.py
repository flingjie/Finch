"""drop runrecord + noderecord tables (remove generic graph runtime storage).

Revision ID: f1a2b3c4d5e6
Revises: d0e1f2a3b4c5
"""

import sqlalchemy as sa
from alembic import op

revision = "f1a2b3c4d5e6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # runrecord / noderecord 由 ``Store.init`` 的 ``create_all`` 建表（基线链内，
    # 但新库可能因历史 create_all 路径而存在差异），故用 IF EXISTS 幂等删除。
    op.execute("DROP TABLE IF EXISTS noderecord")
    op.execute("DROP TABLE IF EXISTS runrecord")


def downgrade() -> None:
    # 尽力而为：重建最小 schema（不含历史数据）。
    op.create_table(
        "runrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "noderecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("node_name", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("output_json", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
    )
