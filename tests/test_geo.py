import gzip
from pathlib import Path

from app.geo import Countries, previous_month


class Reader:
    def __init__(self, table):
        self.table = table

    def get(self, ip):
        return self.table.get(ip)

    def close(self):
        pass


def countries(tmp_path, table=None):
    c = Countries(tmp_path, download_db=False)
    if table is not None:
        c._reader = Reader(table)
    return c


def test_cloudflare_header_when_it_comes_with_the_client_ip(tmp_path):
    """Cloudflare's country header is used only when it belongs to the client's IP."""
    c = countries(tmp_path)
    assert c.country("203.0.113.9", {"cf-connecting-ip": "203.0.113.9", "cf-ipcountry": "es"}) == "ES"
    # Not for another IP (someone faking the header), nor "unknown" (XX)
    assert c.country("203.0.113.9", {"cf-connecting-ip": "198.51.100.1", "cf-ipcountry": "ES"}) is None
    assert c.country("203.0.113.9", {"cf-connecting-ip": "203.0.113.9", "cf-ipcountry": "XX"}) is None


def test_local_database(tmp_path):
    """The country comes from the local IP database; private addresses have none."""
    c = countries(tmp_path, {"8.8.8.8": {"country": {"iso_code": "US"}}, "1.1.1.1": {}})
    assert c.country("8.8.8.8", {}) == "US"
    assert c.country("1.1.1.1", {}) is None
    # Private and local addresses have no country
    assert c.lookup("192.168.1.10") is None and c.lookup("127.0.0.1") is None and c.lookup("not an ip") is None


def test_no_database_means_no_country(tmp_path):
    """Without the IP database there is no country, and no error."""
    assert countries(tmp_path).country("8.8.8.8", {}) is None


def test_refresh_tries_this_month_then_the_previous_one(tmp_path):
    """The IP database download tries this month's file, then last month's, without failing."""
    asked = []

    def fetch(url, target: Path):
        asked.append(url)
        raise OSError("offline")

    c = Countries(tmp_path, download_db=False, fetch=fetch)
    c.refresh()  # never raises: the country just stays empty
    assert len(asked) == 2 and asked[0].startswith("https://download.db-ip.com/free/dbip-country-lite-")


def test_previous_month():
    """The previous month is worked out correctly, also across a year."""
    from datetime import date
    assert previous_month(date(2026, 1, 5)) == "2025-12"
    assert previous_month(date(2026, 11, 2)) == "2026-10"
