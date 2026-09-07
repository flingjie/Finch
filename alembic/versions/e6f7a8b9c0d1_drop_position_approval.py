"""drop position approval record table (inbox deep refactor phase 1).

Revision ID: e6f7a8b9c0d1
Revises: c1d2e3f4a5b6
"""

import sqlalchemy as sa
from alembic import op

revision = "e6f7a8b9c0d1"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # positionapprovalrecord 由 ``Store.init`` 的 ``create_all`` 建表（不在 alembic
    # 基线链内），全新库可能不存在该表，故用 IF EXISTS 幂等删除。
    op.execute("DROP TABLE IF EXISTS positionapprovalrecord")


def downgrade() -> None:
    # 尽力而为：重建最小 schema（不含历史数据）。
    op.create_table(
        "positionapprovalrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
