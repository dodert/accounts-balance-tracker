from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def upgrade_database(engine: Engine) -> None:
    """Bring the schema to the latest revision (runs on every start)."""
    with engine.begin() as connection:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
