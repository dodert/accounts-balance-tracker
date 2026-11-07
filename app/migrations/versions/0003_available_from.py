"""Accounts: available_from, the day their balance becomes available (None = available)

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A plain ADD COLUMN (no table rebuild): existing accounts get NULL, i.e. available
    op.add_column("accounts", sa.Column("available_from", sa.Date(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("accounts") as batch:
        batch.drop_column("available_from")
