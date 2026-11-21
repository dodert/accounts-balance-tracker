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


def pytest_configure(config):
    config.addinivalue_line("markers", "settings(**kwargs): other Settings for the E2E server (tests/test_e2e.py)")
    config.addinivalue_line("markers", "tour: the E2E page shows the guided tour on its own (tests/test_e2e.py)")
