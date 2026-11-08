"""Country of an IP address, for the admin's list of users (multi-user mode). Looked up locally: the IP
is never sent to another service.

1. Behind Cloudflare, the CF-IPCountry header (turn on "Add visitor location headers" in Cloudflare's
   Managed Transforms). Used only when CF-Connecting-IP is the client IP the app sees.
2. Otherwise the free "IP to Country Lite" database of DB-IP (CC BY 4.0, https://db-ip.com), downloaded
   once a month to DATA_DIR. If it can't be downloaded, the country is just left empty.
"""
import gzip
import ipaddress
import logging
import shutil
import threading
import time
import urllib.request
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

DBIP_URL = "https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz"
REFRESH_SECONDS = 32 * 24 * 3600


def previous_month(day: date) -> str:
    return f"{day.year - 1}-12" if day.month == 1 else f"{day.year}-{day.month - 1:02d}"


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "accounts-balance-tracker"})
    with urllib.request.urlopen(request, timeout=60) as response, gzip.GzipFile(fileobj=response) as data:
        tmp = target.with_suffix(".tmp")
        with open(tmp, "wb") as file:
            shutil.copyfileobj(data, file)
        tmp.replace(target)


class Countries:
    def __init__(self, data_dir: Path, download_db: bool = True, fetch=download):
        self.path = data_dir / "dbip-country-lite.mmdb"
        self._fetch = fetch
        self._reader = None
        self._lock = threading.Lock()
        self._load()
        if download_db and self._outdated():
            threading.Thread(target=self.refresh, daemon=True).start()

    def _outdated(self) -> bool:
        return not self.path.exists() or time.time() - self.path.stat().st_mtime > REFRESH_SECONDS

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            import maxminddb
            reader = maxminddb.open_database(str(self.path))
        except Exception as error:  # a damaged file: no countries, but the app works
            log.warning("Could not open %s: %s", self.path, error)
            return
        with self._lock:
            old, self._reader = self._reader, reader
        if old is not None:
            old.close()

    def refresh(self) -> None:
        """Download this month's database (or last month's, early in the month)."""
        today = date.today()
        for month in (f"{today:%Y-%m}", previous_month(today)):
            try:
                self._fetch(DBIP_URL.format(month=month), self.path)
            except Exception as error:
                log.info("Country database %s not downloaded: %s", month, error)
                continue
            self._load()
            return
        log.warning("Could not download the country database; countries are left empty")

    def lookup(self, ip: str) -> str | None:
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if not address.is_global:
            return None  # LAN, Docker network, localhost
        with self._lock:
            reader = self._reader
            if reader is None:
                return None
            try:
                record = reader.get(str(address))
            except ValueError:
                return None
        code = ((record or {}).get("country") or {}).get("iso_code")
        return code.upper() if isinstance(code, str) and len(code) == 2 else None

    def country(self, ip: str, headers) -> str | None:
        """The visitor's country (ISO code, e.g. ES), or None if unknown."""
        cf_country, cf_ip = headers.get("cf-ipcountry"), headers.get("cf-connecting-ip")
        if cf_country and cf_ip == ip and len(cf_country) == 2 and cf_country.isalpha() and cf_country.upper() != "XX":
            return cf_country.upper()
        return self.lookup(ip)
