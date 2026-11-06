from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    data_dir: Path = Path("/data")
    # Empty -> SQLite file in data_dir. Example Postgres:
    # postgresql+psycopg://user:pass@host:5432/balances
    database_url: str = ""

    # Currency for new accounts (ISO 4217 code)
    default_currency: str = "EUR"

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

    @field_validator("auth_totp", mode="before")
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
        if self.database_url:
            return self.database_url
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{self.data_dir / 'balances.db'}"


settings = Settings()
