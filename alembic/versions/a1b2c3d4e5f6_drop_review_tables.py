"""drop review tables (inbox deep refactor phase 1).

Revision ID: a1b2c3d4e5f6
Revises: b7b97976f5da
"""

import sqlalchemy as sa
from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "b7b97976f5da"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("reviewhistoryrecord")
    op.drop_table("reviewrecord")


def downgrade() -> None:
    # 重建最小 schema（尽力而为，不含历史数据）
    op.create_table(
        "reviewrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("draft_id", sa.String(), index=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "reviewhistoryrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("draft_id", sa.String(), index=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
