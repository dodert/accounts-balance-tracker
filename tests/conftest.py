import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR")))


def pytest_collection_modifyitems(items):
    """Each test's docstring (first line) goes into the JUnit report, for the CI's test summary."""
    for item in items:
        doc = (getattr(item, "function", None).__doc__ or "").strip().splitlines()
        if doc:
            item.user_properties.append(("description", doc[0]))
