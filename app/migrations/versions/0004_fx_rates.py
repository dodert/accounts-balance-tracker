"""Exchange rates (fx_rates)

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new table: nothing existing changes
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("base", sa.String(3), nullable=False),
        sa.Column("quote", sa.String(3), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("base", "quote", "day", name="uq_fx_rate_pair_day"),
    )


def downgrade() -> None:
    op.drop_table("fx_rates")
