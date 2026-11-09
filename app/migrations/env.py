from alembic import context

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.db import Base

config = context.config
connection = config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=True,  # SQLite changes a table by rebuilding it
)
with context.begin_transaction():
    context.run_migrations()
