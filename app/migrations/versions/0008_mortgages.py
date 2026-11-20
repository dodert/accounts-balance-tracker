"""Fixed-rate mortgages and their partial prepayments (#128)

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two new tables: nothing existing changes
    op.create_table(
        "mortgages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("institution", sa.String(100), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("principal", sa.Numeric(16, 4), nullable=False),
        sa.Column("rate", sa.Numeric(7, 4), nullable=False),
        sa.Column("discount", sa.Numeric(7, 4), nullable=False),
        sa.Column("term_months", sa.Integer(), nullable=False),
        sa.Column("first_payment", sa.Date(), nullable=False),
        sa.Column("payment_day", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "mortgage_prepayments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("mortgage_id", sa.Integer(), sa.ForeignKey("mortgages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(16, 4), nullable=False),
        sa.Column("mode", sa.Enum("reduce_payment", "reduce_term", name="prepaymentmode", native_enum=False,
                                  length=20), nullable=False),
        sa.Column("fee", sa.Numeric(16, 4), nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_mortgage_prepayments_mortgage_id", "mortgage_prepayments", ["mortgage_id"])


def downgrade() -> None:
    op.drop_index("ix_mortgage_prepayments_mortgage_id", "mortgage_prepayments")
    op.drop_table("mortgage_prepayments")
    op.drop_table("mortgages")
