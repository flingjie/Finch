"""add node duration_ms

Revision ID: f0e9d8c7b6a5
Revises: 07a1d0830293
Create Date: 2026-09-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f0e9d8c7b6a5'
down_revision: Union[str, None] = '07a1d0830293'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('noderecord', schema=None) as batch_op:
        batch_op.add_column(sa.Column('duration_ms', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('noderecord', schema=None) as batch_op:
        batch_op.drop_column('duration_ms')
