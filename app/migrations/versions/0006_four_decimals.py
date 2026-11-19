"""Amounts of balances and milestones keep up to 4 decimals (#120)

Revision ID: 0006
Revises: 0005
"""
import warnings

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TABLES = ("balances", "milestones")


def upgrade() -> None:
    # SQLite keeps the column's type only in the table's definition, so each table is rebuilt (copied whole: every
    # row and amount stays as it is; upgrade_database turns the foreign keys off meanwhile and checks them after)
    with warnings.catch_warnings():
        # Reflecting the tables also reads `accounts`, whose expression index (0002) SQLAlchemy can't reflect: it is
        # not part of these tables and stays as it is
        warnings.filterwarnings("ignore", "Skipped unsupported reflection of expression-based index")
        for table in TABLES:
            with op.batch_alter_table(table, recreate="always") as batch:
                batch.alter_column("amount", type_=sa.Numeric(16, 4), existing_type=sa.Numeric(14, 2),
                                   existing_nullable=False)


def downgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.alter_column("amount", type_=sa.Numeric(14, 2), existing_type=sa.Numeric(16, 4),
                               existing_nullable=False)
