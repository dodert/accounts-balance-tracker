"""Account names are unique per institution, not overall

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

INDEX = "uq_account_name_institution"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        # SQLite: the unique constraint on name has no name; give it one so the table can be rebuilt without it
        with op.batch_alter_table("accounts", recreate="always",
                                  naming_convention={"uq": "uq_%(table_name)s_%(column_0_name)s"}) as batch:
            batch.drop_constraint("uq_accounts_name", type_="unique")
    else:
        op.drop_constraint("accounts_name_key", "accounts", type_="unique")
    # One account per name and institution (no institution counts as one more)
    op.create_index(INDEX, "accounts", ["name", sa.text("coalesce(institution, '')")], unique=True)


def downgrade() -> None:
    op.drop_index(INDEX, "accounts")
    with op.batch_alter_table("accounts") as batch:
        batch.create_unique_constraint("accounts_name_key", ["name"])
