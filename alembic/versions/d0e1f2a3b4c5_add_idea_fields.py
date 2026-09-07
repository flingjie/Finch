"""add idea fields to contentjobrecord

Revision ID: d0e1f2a3b4c5
Revises: e6f7a8b9c0d1
Create Date: 2026-09-07
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("contentjobrecord", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("origin", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("generation_key", sqlmodel.sql.sqltypes.AutoString(), nullable=True)
        )
    with op.batch_alter_table("contentjobrecord", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_contentjobrecord_origin"), ["origin"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_contentjobrecord_generation_key"),
            ["generation_key"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("contentjobrecord", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_contentjobrecord_generation_key"))
        batch_op.drop_index(batch_op.f("ix_contentjobrecord_origin"))
        batch_op.drop_column("generation_key")
        batch_op.drop_column("origin")
