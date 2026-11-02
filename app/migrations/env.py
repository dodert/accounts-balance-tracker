from alembic import context

from app.db import Base
from app import models  # noqa: F401  (registers tables on Base.metadata)

config = context.config
connection = config.attributes["connection"]
context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=connection.dialect.name == "sqlite",
)
with context.begin_transaction():
    context.run_migrations()
