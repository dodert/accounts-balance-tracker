"""Alembic environment for users.db (multi-user mode): its own tree, apart from the users' balance databases."""
from alembic import context

from app.users import SystemBase

config = context.config
connection = config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=SystemBase.metadata,
    render_as_batch=True,  # SQLite changes a table by rebuilding it
)
with context.begin_transaction():
    context.run_migrations()
