"""Initial schema: accounts and balances

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ACCOUNT_TYPES = ("bank", "savings", "investment", "pension", "crypto", "cash", "loan", "other")


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("type", sa.Enum(*ACCOUNT_TYPES, name="accounttype", native_enum=False, length=10), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("institution", sa.String(100)),
        sa.Column("notes", sa.Text()),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "balances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("note", sa.String(300)),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "date", name="uq_balance_account_date"),
    )


def downgrade() -> None:
    op.drop_table("balances")
    op.drop_table("accounts")
