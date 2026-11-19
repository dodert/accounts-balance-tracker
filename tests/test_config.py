"""Settings read from environment variables."""
import pytest
from pydantic import ValidationError

from app.config import Settings

NUMERIC = {"ACCOUNT_DAYS": 30, "MAX_USERS": 100, "MAX_DB_MB": 10, "FX_DAILY_LIMIT": 30, "BACKUP_KEEP": 7}


@pytest.mark.parametrize("name", NUMERIC)
def test_empty_numeric_setting_uses_the_default(monkeypatch, tmp_path, name):
    """An empty numeric variable (as an empty field in the Unraid template) means its default."""
    monkeypatch.setenv(name, "")
    assert getattr(Settings(data_dir=tmp_path), name.lower()) == NUMERIC[name]


def test_numeric_setting_with_spaces_is_read(monkeypatch, tmp_path):
    """A number with spaces around it is read as that number."""
    monkeypatch.setenv("BACKUP_KEEP", " 3 ")
    assert Settings(data_dir=tmp_path).backup_keep == 3


def test_numeric_setting_that_is_not_a_number_names_the_variable(monkeypatch, tmp_path):
    """A numeric variable that is not a number fails with a message naming it."""
    monkeypatch.setenv("MAX_USERS", "many")
    with pytest.raises(ValidationError, match="MAX_USERS must be a whole number"):
        Settings(data_dir=tmp_path)
