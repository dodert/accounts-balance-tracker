"""Which day is "today".

The browser sends its own date in the X-Local-Date header with every API call, and "today" is that date while
the request runs: the user's day, also near midnight or in another time zone than the server. Without the header
(another client, an old page), or with a date more than a day away from the UTC date, it is the server's date
(the TZ setting).

Only for what the user sees (which accounts are available today, which day's exchange rate, file names). The
server's own bookkeeping (backups, sessions, request limits, cache ages) keeps the server's clock.
"""
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone

HEADER = "x-local-date"

_local_date: ContextVar[date | None] = ContextVar("local_date", default=None)


def today() -> date:
    """The user's date during a request that sent it, else the server's."""
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
    """Sets today() for each request from its X-Local-Date header (plain ASGI, so the value reaches the other
    middleware and the routes, also those that run in a thread)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        value = next((v.decode("latin-1") for k, v in scope["headers"] if k == HEADER.encode()), None)
        token = _local_date.set(parse_local_date(value))
        try:
            await self.app(scope, receive, send)
        finally:
            _local_date.reset(token)
