"""The Unraid template must offer every setting the app reads, so a new variable can't be forgotten."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "unraid" / "accounts-balance-tracker.xml"
# Set at build time or fixed by the container, not by the user
NOT_USER_SETTINGS = {"data_dir", "app_version", "git_sha", "git_ref", "build_date"}
# Container variables the template offers on purpose (PORT is the port mapping, UMASK keeps its default)
CONTAINER_VARS = {"PUID", "PGID", "FORWARDED_ALLOW_IPS"}


def _targets():
    root = ET.parse(TEMPLATE).getroot()
    return {c.get("Target"): c for c in root.iter("Config") if c.get("Type") == "Variable"}


def test_every_setting_is_in_the_template():
    """Every app setting is in the Unraid template."""
    expected = {name.upper() for name in Settings.model_fields} - {n.upper() for n in NOT_USER_SETTINGS}
    missing = expected - _targets().keys()
    assert not missing, f"Add these to {TEMPLATE.name}: {sorted(missing)}"


def test_container_vars_are_in_the_template():
    """The container's own variables are in the Unraid template."""
    assert CONTAINER_VARS <= _targets().keys()


def test_template_defaults_match_the_app():
    """The Unraid template's defaults match the app's."""
    targets = _targets()
    for name, field in Settings.model_fields.items():
        config = targets.get(name.upper())
        if config is None or name in NOT_USER_SETTINGS:
            continue
        default = field.default
        want = str(default).lower() if isinstance(default, bool) else str(default)
        assert config.get("Default") == want, f"{name.upper()}: template {config.get('Default')!r}, app {want!r}"


def test_every_setting_is_in_the_readme():
    """Every app setting is documented in the README."""
    readme = (ROOT / "README.md").read_text()
    table = readme.split("## Settings", 1)[1].split("\n## ", 1)[0]
    documented = set(re.findall(r"`([A-Z_]+)`", table))
    expected = {name.upper() for name in Settings.model_fields} - {n.upper() for n in NOT_USER_SETTINGS}
    assert not expected - documented, f"Add to the README's Settings table: {sorted(expected - documented)}"


# Only these are hidden behind "Show more settings"; every other variable is shown
HIDDEN_VARS = {"API_DOCS", "PUID", "PGID", "FORWARDED_ALLOW_IPS"}


def test_only_some_variables_are_hidden():
    """Only API_DOCS, PUID, PGID and FORWARDED_ALLOW_IPS are hidden in Unraid."""
    for target, config in _targets().items():
        want = "advanced" if target in HIDDEN_VARS else "always"
        assert config.get("Display") == want, f"{target}: Display should be {want!r}"
