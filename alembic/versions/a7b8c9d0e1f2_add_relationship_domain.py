"""add relationship domain tables (peers / interaction records / conversation threads).

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
"""

import sqlalchemy as sa
from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "peerrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("author_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("platform", "author_id", name="uq_peer_platform_author"),
    )
    op.create_table(
        "interactionrecordrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("proposal_id", sa.String(), nullable=False),
        sa.Column("peer_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "conversationthreadrecord",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("peer_id", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("interactionrecordrecord") as batch_op:
        batch_op.create_index("ix_interactionrecordrecord_proposal_id", ["proposal_id"])
        batch_op.create_index("ix_interactionrecordrecord_peer_id", ["peer_id"])
    with op.batch_alter_table("conversationthreadrecord") as batch_op:
        batch_op.create_index("ix_conversationthreadrecord_peer_id", ["peer_id"])


def downgrade() -> None:
    op.drop_table("conversationthreadrecord")
    op.drop_table("interactionrecordrecord")
    op.drop_table("peerrecord")
