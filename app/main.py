"""The app: create_app builds the state shared by the routers (deps.Context) and puts the pieces together.
The routes are in app/routes, one module per area; what runs around every request is in middleware.py."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError
from starlette.concurrency import run_in_threadpool

from . import __version__, middleware
from .auth import Auth
from .backups import make_daily_backup
from .clock import LocalDateMiddleware, use_utc
from .config import Settings
from .config import settings as default_settings
from .db import make_engine, make_sessionmaker
from .demo import VisitorDemos
from .deps import STATIC_DIR, Context, Pages
from .fx import Fetcher, SharedRates, frankfurter
from .geo import Countries
from .migrate import sqlite_file, upgrade_database
from .routes import accounts, admin, balances, info, login, milestones, rates, transfer
from .routes import demo as demo_routes
from .totp import Totp
from .users import DatabaseTooBig, Users

PURGE_EVERY = 3600  # seconds between checks for expired accounts
BACKUP_EVERY = 3600  # seconds between checks for today's backup (backups.py)

use_utc()  # the server's own clock is UTC; the user's dates come from the browser (clock.py)


def log_to_console() -> None:
    """The app's own messages (e.g. where each exchange rate came from) in the container log, next to uvicorn's."""
    app_log = logging.getLogger("app")
    if not app_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        app_log.addHandler(handler)
        app_log.setLevel(logging.INFO)
        app_log.propagate = False



def create_app(settings: Settings = default_settings, fx_fetcher: Fetcher | None = None) -> FastAPI:
    log_to_console()
    auth = Auth(settings.auth_username, settings.auth_password, settings.data_dir)
    if settings.auth_totp and not auth.enabled:
        raise ValueError("AUTH_TOTP needs AUTH_USERNAME and AUTH_PASSWORD")
    multi = settings.multi_user
    if multi:
        if not auth.enabled:
            raise ValueError("MULTI_USER needs AUTH_USERNAME and AUTH_PASSWORD (the admin's login)")
        # The admin has no data of its own; each user has an encrypted database (see users.py)
        users = Users(settings.data_dir, Auth._secret(settings.data_dir), settings.auth_username,
                      settings.account_days, settings.max_users, settings.max_db_mb * 1024 * 1024)
        totp = Totp(settings.data_dir)  # always asked to the admin
        countries = Countries(settings.data_dir, settings.country_db)  # for the admin's list of users
        engine = SessionLocal = None
    else:
        users = countries = None
        totp = Totp(settings.data_dir) if settings.auth_totp else None
        engine = make_engine(settings.resolved_database_url)
        SessionLocal = make_sessionmaker(engine)
        upgrade_database(engine)
    visitor_demos = VisitorDemos()  # sample data in temporary databases, one per browser in demo mode
    fetch_rate = fx_fetcher or frankfurter(settings.fx_url)
    # Multi-user: one store of rates for everybody, with a daily limit of requests to the service
    shared_rates = SharedRates(settings.data_dir / "rates.db", fetch_rate, settings.fx_daily_limit) if multi else None

    version = settings.app_version if settings.app_version and settings.app_version != "dev" else __version__

    async def purge_expired_accounts():
        """Multi-user: delete the database of expired accounts (and block them), now and every hour."""
        while True:
            try:
                await run_in_threadpool(users.purge_expired)
            except Exception:
                logging.getLogger(__name__).exception("Could not delete the expired accounts")
            await asyncio.sleep(PURGE_EVERY)

    def backup_now():
        """Today's backup, if not made yet (see backups.py)."""
        database = None if multi else sqlite_file(engine)
        return make_daily_backup(settings.data_dir, settings.backup_keep, database, users)

    async def daily_backups():
        """A backup on start if today has none, then checked every hour, so a restart never skips a day."""
        while True:
            try:
                await run_in_threadpool(backup_now)
            except Exception:
                logging.getLogger(__name__).exception("Could not make the daily backup")
            await asyncio.sleep(BACKUP_EVERY)

    @asynccontextmanager
    async def lifespan(_app):
        purge = asyncio.create_task(purge_expired_accounts()) if multi else None
        backups = asyncio.create_task(daily_backups()) if settings.backup_keep > 0 else None
        yield
        for task in (purge, backups):
            if task is not None:
                task.cancel()
        visitor_demos.close()  # the demo databases are temporary

    # API docs only when API_DOCS=true: otherwise /docs, /redoc and /openapi.json answer 404
    docs = {} if settings.api_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Accounts Balance Tracker", version=version, lifespan=lifespan, **docs)
    app.state.users = users
    app.state.backup_now = backup_now
    ctx = Context(settings=settings, auth=auth, users=users, totp=totp, countries=countries,
                  engine=engine, SessionLocal=SessionLocal, visitor_demos=visitor_demos,
                  fetch_rate=fetch_rate, shared_rates=shared_rates, version=version)
    # Version the asset URLs so browsers fetch the new JS/CSS after an image update
    ctx.pages = Pages(f"{version}-{settings.git_sha[:7]}" if settings.git_sha else version, multi)

    @app.exception_handler(DatabaseTooBig)
    async def database_too_big(_request: Request, _error: DatabaseTooBig):
        return JSONResponse({"detail": f"Your data would be over the limit of {settings.max_db_mb} MB: the change "
                                       "was not saved"}, status_code=413)

    @app.exception_handler(OperationalError)
    async def database_full(_request: Request, error: OperationalError):
        # A demo database past its size limit (see demo.py); anything else is a real error
        if "database or disk is full" in str(error.orig):
            return JSONResponse({"detail": "The demo data is over its size limit: the change was not saved"},
                                status_code=413)
        raise error

    middleware.install(app, ctx)
    app.add_middleware(LocalDateMiddleware)  # outermost: "today" is the browser's date (clock.py)
    for area in (info, demo_routes, accounts, balances, milestones, rates, transfer, login, admin):
        app.include_router(area.router(ctx))

    # --- Pages ---

    # The page's JavaScript modules, with the version in the path: their imports ("./api.js") are relative, so
    # after an update the browser asks for every module at the new address, not an old copy from its cache
    app.mount("/static/v/{version}", StaticFiles(directory=STATIC_DIR), name="versioned-static")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Browsers and bookmark tools ask for these at the root, whatever the page links
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "icons" / "favicon.ico", media_type="image/x-icon")

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def apple_touch_icon():
        return FileResponse(STATIC_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")

    index_html = ctx.pages.index

    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache"})

    return app
