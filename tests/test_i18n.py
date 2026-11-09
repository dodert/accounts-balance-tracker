"""Interface translations: every language has the same texts as English, and every text
the page uses exists."""
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

STATIC = Path(__file__).parent.parent / "app" / "static"
LOCALES = sorted((STATIC / "i18n").glob("*.json"))
EN = json.loads((STATIC / "i18n" / "en.json").read_text(encoding="utf-8"))
# Keys built at run time: t(`type.${a.type}`)
DYNAMIC = {"type": ["bank", "savings", "investment", "pension", "crypto", "cash", "loan", "other"]}


def test_languages_endpoint(tmp_path):
    """The list of languages includes English and Spanish."""
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    languages = client.get("/api/languages").json()
    assert {"code": "en", "name": "English"} in languages
    assert {"code": "es", "name": "Español"} in languages


@pytest.mark.parametrize("path", LOCALES, ids=lambda p: p.stem)
def test_locale_has_every_english_key(path):
    """Each translation has every English text, with the same placeholders."""
    strings = json.loads(path.read_text(encoding="utf-8"))
    assert set(strings) == set(EN), path.name
    for key, text in strings.items():
        # Placeholders such as {n} must survive translation
        assert set(re.findall(r"\{(\w+)\}", text)) == set(re.findall(r"\{(\w+)\}", EN[key])), key


def test_every_key_used_by_the_page_exists():
    """Every text the pages use has an English translation."""
    html = "".join((STATIC / name).read_text(encoding="utf-8") for name in (
        "index.html", "login.html", "totp.html", "register.html", "recovery.html", "recover.html", "account.html",
        "admin.html", "message.html"))
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', html))
    used |= set(re.findall(r'(?<![\w.])t\("([a-z_.0-9]+)"', js))
    used |= {f"{group}.{key}" for group, keys in DYNAMIC.items() for key in keys}
    assert used - set(EN) == set()


def test_account_types_match_the_model():
    """The account types in the page match the ones in the database model."""
    from app.models import AccountType
    assert DYNAMIC["type"] == [t.value for t in AccountType]
    assert f"const ACCOUNT_TYPES = {json.dumps(DYNAMIC['type'])};" in (STATIC / "app.js").read_text()
