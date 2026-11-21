"""app_settings: the owner's settings kept with their data, such as the guided tour already seen (#156)

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A new table: nothing existing changes
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(50), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
