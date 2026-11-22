import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app, repository_version

ROOT = Path(__file__).resolve().parent.parent


def test_manifest_has_a_semver_version():
    """The release-please manifest holds the app's version as X.Y.Z."""
    version = json.loads((ROOT / ".release-please-manifest.json").read_text())["."]
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert repository_version() == version


def test_release_config_is_a_simple_release_without_component_in_tag():
    """release-please makes plain vX.Y.Z tags, the ones the CI builds as releases."""
    config = json.loads((ROOT / "release-please-config.json").read_text())
    assert config["include-component-in-tag"] is False
    assert config["packages"]["."]["release-type"] == "simple"


def test_version_comes_from_the_image_when_set(tmp_path):
    """APP_VERSION (set by the image) is the version the app reports."""
    client = TestClient(create_app(Settings(data_dir=tmp_path, app_version="1.2.3-main.4")))
    assert client.get("/api/about").json()["version"] == "1.2.3-main.4"


def test_version_falls_back_to_the_manifest(tmp_path):
    """Without APP_VERSION (or with the image's default "dev"), the app reports the manifest's version."""
    client = TestClient(create_app(Settings(data_dir=tmp_path, app_version="dev")))
    assert client.get("/api/about").json()["version"] == repository_version()


def test_no_version_in_the_package():
    """The version is no longer written in app/__init__.py, so PRs don't conflict over it."""
    assert "__version__" not in (ROOT / "app" / "__init__.py").read_text()
