"""users.db as in the first multi-user release (0.15.0)

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("kdf_salt", sa.LargeBinary(), nullable=False),
        sa.Column("kdf_params", sa.String(200), nullable=False),
        sa.Column("key_by_password", sa.LargeBinary(), nullable=False),
        sa.Column("recovery_salt", sa.LargeBinary(), nullable=False),
        sa.Column("key_by_recovery", sa.LargeBinary(), nullable=False),
        sa.Column("session_epoch", sa.Integer(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(), nullable=True),
        sa.Column("last_ip", sa.String(45), nullable=True),
        sa.Column("last_country", sa.String(2), nullable=True),
        sa.Column("totp_secret", sa.String(200), nullable=True),
        sqlite_autoincrement=True,  # ids are never reused
    )
    op.create_table(
        "invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
    )
    op.create_table(
        "blocked_emails",
        sa.Column("email", sa.String(254), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "revoked_sessions",
        sa.Column("nonce", sa.String(32), primary_key=True),
        sa.Column("expires", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    for table in ("revoked_sessions", "blocked_emails", "invitations", "users"):
        op.drop_table(table)
