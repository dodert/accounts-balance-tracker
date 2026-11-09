from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("/data")
    # The database is a SQLite file in data_dir. PostgreSQL is no longer supported: this is only read to stop
    # with an explanation if an older setup still points to one (see check_database_url)
    database_url: str = ""

    # Currency for new accounts (ISO 4217 code); totals in several currencies are added up in it
    default_currency: str = "EUR"
    # Exchange rates (European Central Bank, through the Frankfurter API)
    fx_url: str = "https://api.frankfurter.app"

    # Build info, set by the Docker image (shown in the footer)
    app_version: str = ""
    git_sha: str = ""
    git_ref: str = ""
    build_date: str = ""

    # Login: set both to ask for this user and password; leave both empty for no login
    auth_username: str = ""
    auth_password: str = ""
    # Also ask for a one-time code from an authenticator app (needs the user and password above)
    auth_totp: bool = False
    # Several users, each with their own encrypted database (registration by invitation). The user and
    # password above are then the admin's, and the one-time code is always asked to the admin
    multi_user: bool = False
    # Multi-user: download DB-IP's free country database once a month, for the admin's list of users
    country_db: bool = True
    # Multi-user: days an account lasts from sign-up (0 = no limit); then its database is deleted and the
    # user blocked, unless the admin extends it. Most users, and most size of one user's database
    account_days: int = 30
    max_users: int = 100
    max_db_mb: int = 10
    # Multi-user: most requests a day to the exchange-rate service, for everybody together (the rates are shared).
    # The rates of many days (a converted history) take one request, so a few a day are enough
    fx_daily_limit: int = 30
    # Interactive API docs (/docs, /redoc) and the schema (/openapi.json): off by default, so production does not show the API
    api_docs: bool = False

    @field_validator("auth_totp", "multi_user", "country_db", "api_docs", mode="before")
    @classmethod
    def _empty_is_false(cls, value):
        # An empty field in the Unraid template or docker-compose means "off"
        return False if isinstance(value, str) and not value.strip() else value

    @field_validator("default_currency", mode="before")
    @classmethod
    def _currency(cls, value):
        value = (value or "EUR").strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("DEFAULT_CURRENCY must be a 3-letter ISO 4217 code, e.g. EUR")
        return value

    @property
    def resolved_database_url(self) -> str:
        check_database_url(self.database_url)
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'balances.db'}"


POSTGRES_REMOVED = (
    "DATABASE_URL points to a database that is not SQLite ({scheme}). PostgreSQL is no longer supported "
    "(version 0.19.0): the data is now always a SQLite file in the data folder. To move your data, without losing "
    "any: 1) run the previous version (0.18.x) with this DATABASE_URL and use Import / export -> Export everything "
    "(JSON); 2) remove DATABASE_URL and start this version; 3) use Import / export -> Import JSON (replace or merge). "
    "Nothing was changed in that database.")


def check_database_url(url: str) -> None:
    """Only SQLite: a PostgreSQL (or other) URL stops the app, explaining how to move the data."""
    if url and not url.startswith("sqlite"):
        raise RuntimeError(POSTGRES_REMOVED.format(scheme=url.split(":", 1)[0]))


settings = Settings()
