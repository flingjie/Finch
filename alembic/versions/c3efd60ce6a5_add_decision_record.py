"""add decision record

Revision ID: c3efd60ce6a5
Revises: f0e9d8c7b6a5
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = 'c3efd60ce6a5'
down_revision: Union[str, None] = 'f0e9d8c7b6a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('decisionrecordrecord',
        sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('job_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('draft_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('payload_json', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('decisionrecordrecord', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_decisionrecordrecord_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_decisionrecordrecord_draft_id'), ['draft_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('decisionrecordrecord', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_decisionrecordrecord_draft_id'))
        batch_op.drop_index(batch_op.f('ix_decisionrecordrecord_job_id'))
    op.drop_table('decisionrecordrecord')
