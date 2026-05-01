"""request_line.qty -> integer

Revision ID: c4a1f0b9e7d2
Revises: 50ff17a00757
Create Date: 2026-05-01 13:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c4a1f0b9e7d2"
down_revision: Union[str, Sequence[str], None] = "50ff17a00757"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Round any pre-existing fractional quantities to the nearest whole
    # number before tightening the column type so the cast can't lose
    # data silently.
    op.execute(
        "UPDATE request_line "
        "SET qty = CAST(ROUND(qty) AS INTEGER) "
        "WHERE qty IS NOT NULL"
    )
    with op.batch_alter_table("request_line", schema=None) as batch_op:
        batch_op.alter_column(
            "qty",
            existing_type=sa.Float(),
            type_=sa.Integer(),
            existing_nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("request_line", schema=None) as batch_op:
        batch_op.alter_column(
            "qty",
            existing_type=sa.Integer(),
            type_=sa.Float(),
            existing_nullable=True,
        )
