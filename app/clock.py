"""The server's clock and the user's "today".

The server always runs in UTC (use_utc): backups, logs, sessions, request limits and cache ages, whatever TZ an
install sets.

The user's dates are in the user's own time zone (the browser's, or the one chosen in the settings): the page
sends that date in the X-Local-Date header with every API call (or ?local_date= on a download link), and today()
is that date while the request runs. Without it (another client, an old page), or with a date more than a day
away from the UTC date, today() is the UTC date. It is used for which accounts are available today, which day's
exchange rate, and the export file names.
"""
import os
import time
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs

HEADER = "x-local-date"
QUERY = "local_date"  # the same, on a link (a download sends no headers)


def use_utc() -> None:
    """Run the process in UTC, also when the container sets another TZ (older installs had one)."""
    os.environ["TZ"] = "UTC"
    if hasattr(time, "tzset"):
        time.tzset()

_local_date: ContextVar[date | None] = ContextVar("local_date", default=None)


def today() -> date:
    """The user's date during a request that sent it, else the UTC date."""
    return _local_date.get() or date.today()


def parse_local_date(value: str | None, utc_now: datetime | None = None) -> date | None:
    """The browser's date, if it is a valid date that some time zone has now (UTC-12 to UTC+14: at most a day
    from the UTC date); anything else is ignored."""
    if not value:
        return None
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return None
    utc_day = (utc_now or datetime.now(timezone.utc)).date()
    return day if abs(day - utc_day) <= timedelta(days=1) else None


class LocalDateMiddleware:
    """Sets today() for each request from its X-Local-Date header or local_date parameter (plain ASGI, so the value reaches the other
    middleware and the routes, also those that run in a thread)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        value = next((v.decode("latin-1") for k, v in scope["headers"] if k == HEADER.encode()), None)
        if value is None:
            value = (parse_qs(scope.get("query_string", b"").decode("latin-1")).get(QUERY) or [None])[0]
        token = _local_date.set(parse_local_date(value))
        try:
            await self.app(scope, receive, send)
        finally:
            _local_date.reset(token)
