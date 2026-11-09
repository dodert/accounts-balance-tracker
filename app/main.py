import asyncio
import html
import ipaddress
from contextlib import asynccontextmanager, contextmanager
import json
import logging
import math
from datetime import date, datetime
from urllib.parse import quote, urlsplit
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from . import __version__
from .auth import COOKIE, PENDING_COOKIE, PENDING_SECONDS, SESSION_SECONDS, Auth
from .config import Settings, settings as default_settings
from .csv_io import export_csv, import_csv
from .db import make_engine, make_sessionmaker
from .demo import COOKIE as DEMO_COOKIE, Demo, VisitorDemos
from .geo import Countries
from .fx import DbStore, Fetcher, SharedRates, SharedStore, frankfurter, get_rate, own_rates, rates_for_days
from .history import accounts_with_balances, available_totals, currency_totals, history
from .migrate import upgrade_database
from .models import Account, Balance, Milestone
from .schemas import (
    AccountIn, AccountOut, AccountUpdate, BalanceIn, BalanceOut, BalanceUpdate, BulkBalancesIn, BulkBalancesOut,
    CombinedTotal, CsvImportOut, CurrencyTotal, FxRateOut, HistoryOut, MilestoneIn, MilestoneOut,
)
from .ratelimit import TooManyTries, TryLimit
from .totp import Totp
from .users import USER_COOKIE, SESSION_SECONDS as USER_SESSION_SECONDS, DatabaseTooBig, SignupError, Users, expired

STATIC_DIR = Path(__file__).parent / "static"
MAX_CSV_BYTES = 20 * 1024 * 1024
MAX_DEMO_CSV_BYTES = 1024 * 1024  # anyone can try the demo: smaller files, so one import can't take long
NEW_INVITE_COOKIE = "abt_new_invite"
# Multi-user mode: reachable without logging in (with /invite/<token>)
MULTI_USER_PUBLIC = {"/recover"}
# Multi-user mode: these start the demo, also without an account
DEMO_ENTRY = {"/demo", "/api/demo"}
PURGE_EVERY = 3600  # seconds between checks for expired accounts
WRONG_PASSWORD_DELAY = 1  # seconds
# Wrong tries allowed per client IP in LOGIN_WINDOW seconds; then that IP waits (others don't)
WRONG_PASSWORDS_PER_IP = 10
WRONG_CODES_PER_IP = 10
# Wrong one-time codes from all IPs together (only reachable with the right password), so that many
# IPs can't guess the 6 digits between them
WRONG_CODES_TOTAL = 30
LOGIN_WINDOW = 15 * 60
# Wrong passwords for one user name from all IPs together, so many addresses (e.g. an IPv6 /64) can't
# guess one password between them. Recovery keys are not limited this way: they are too long to guess
WRONG_PASSWORDS_PER_USER = 30
# Password checks (Argon2id: 64 MB and ~0.1 s each) running at once; the rest wait without taking a thread
ARGON2_AT_ONCE = 4
# CSV imports running at once (each takes a thread while it parses and stores the file)
IMPORTS_AT_ONCE = 2
# Demos anyone can start (multi-user mode), per client IP (IPv6 per /64) and hour
DEMOS_PER_IP = 10
# Scripts only from the app itself (no inline ones); inline style attributes are used by the charts
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# The interactive API docs (/docs, /redoc) are FastAPI's pages: an inline script and files from its CDN
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; "
            "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}
# The API docs and their schema (only with API_DOCS=true): multi-user mode shows them only to logged-in users,
# not to demo visitors
API_DOCS = DOCS_PATHS | {"/openapi.json"}


def account_out(account: Account) -> AccountOut:
    out = AccountOut.model_validate(account)
    balances = account.balances
    if balances:
        out.balance, out.balance_date = float(balances[-1].amount), balances[-1].date
    if len(balances) > 1:
        out.previous_balance, out.previous_date = float(balances[-2].amount), balances[-2].date
        out.first_balance, out.first_date = float(balances[0].amount), balances[0].date
    return out


DUPLICATE_NAME = "An account with that name already exists in that institution"


def create_app(settings: Settings = default_settings, fx_fetcher: Fetcher | None = None) -> FastAPI:
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
        users = None
        totp = Totp(settings.data_dir) if settings.auth_totp else None
        engine = make_engine(settings.resolved_database_url)
        SessionLocal = make_sessionmaker(engine)
        upgrade_database(engine)
    demo = Demo()  # sample data in a temporary database, for the browsers in demo mode
    visitor_demos = VisitorDemos()  # multi-user mode: one per visitor, usable without an account
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

    @asynccontextmanager
    async def lifespan(_app):
        purge = asyncio.create_task(purge_expired_accounts()) if multi else None
        yield
        if purge is not None:
            purge.cancel()
        demo.close()  # the demo database is temporary
        visitor_demos.close()

    # API docs only when API_DOCS=true: otherwise /docs, /redoc and /openapi.json answer 404
    docs = {} if settings.api_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Accounts Balance Tracker", version=version, lifespan=lifespan, **docs)
    app.state.users = users

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

    def foreign_request(request: Request) -> bool:
        """Sent by a page of another origin: another website, or another app on the same host (another
        port) or domain, which browsers call same-site and send the SameSite=Lax cookie to."""
        site = request.headers.get("sec-fetch-site")
        if site is not None:
            return site not in ("same-origin", "none")  # none: typed or bookmarked by the user
        # Browsers without Sec-Fetch-Site: compare the Origin they send with the host asked for
        origin = request.headers.get("origin")
        return origin is not None and (origin == "null" or urlsplit(origin).netloc != request.headers.get("host"))

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next):
        # Another website open in the browser must not be able to post forms here (e.g. a CSV import)
        if request.method not in ("GET", "HEAD", "OPTIONS") and foreign_request(request):
            return Response("Cross-site request blocked", status_code=403)
        return await call_next(request)

    def login_required(request: Request) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Login required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    def is_admin(request: Request) -> bool:
        return auth.valid_session(request.cookies.get(COOKIE))

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if not multi:
            if not auth.enabled or auth.is_public(path) or is_admin(request):
                return await call_next(request)
            return login_required(request)
        # Multi-user: the admin pages need the admin; the rest of the app needs a user
        if auth.is_public(path) or path in MULTI_USER_PUBLIC or path.startswith("/invite/"):
            return await call_next(request)
        if path == "/admin" or path.startswith("/admin/"):
            return await call_next(request) if is_admin(request) else login_required(request)
        session = await run_in_threadpool(users.session, request.cookies.get(USER_COOKIE))
        if session is not None:
            request.state.user = session
            return await call_next(request)
        # The demo can be tried without an account: each visitor gets their own sample data
        if not path.startswith("/account") and path not in API_DOCS and (path in DEMO_ENTRY or in_demo(request)):
            return await call_next(request)
        if is_admin(request) and not path.startswith("/api/"):
            return RedirectResponse("/admin", status_code=303)
        return login_required(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # Added last, so it runs first: also on the answers of the checks above (401, 403...)
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", DOCS_CSP if request.url.path in DOCS_PATHS else CSP)
        headers.setdefault("X-Frame-Options", "DENY")  # no other website can show the app in a frame
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        if is_https(request):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response

    def visitor_demo(request: Request) -> Demo | None:
        """The demo of this browser: its own one in multi-user mode, the shared one otherwise."""
        visitor = request.cookies.get(DEMO_COOKIE)
        if multi:
            return visitor_demos.get(visitor)
        return demo if visitor == "1" else None

    def in_demo(request: Request) -> bool:
        return visitor_demo(request) is not None

    @contextmanager
    def open_db(request: Request):
        # Demo mode uses its own database: the real one is not even opened
        current_demo = visitor_demo(request)
        if current_demo is not None:
            with current_demo.sessionmaker(demo_lang(request))() as db:
                yield db
        elif multi:
            with users.database(request.state.user) as db:
                yield db
        else:
            with SessionLocal() as db:
                yield db

    def get_db(request: Request):
        with open_db(request) as db:
            yield db

    def get_account_or_404(db: Session, account_id: int) -> Account:
        account = db.get(Account, account_id)
        if not account:
            raise HTTPException(404, "Account not found")
        return account

    def get_balance_or_404(db: Session, balance_id: int) -> Balance:
        balance = db.get(Balance, balance_id)
        if not balance:
            raise HTTPException(404, "Balance not found")
        return balance

    def commit(db: Session, conflict: str) -> None:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, conflict) from None

    # --- Info ---

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": version}

    @app.get("/api/about")
    def about(request: Request):
        if multi:
            database, schema = "sqlite", users.databases.head
        else:
            with SessionLocal() as db:
                schema = db.scalar(text("SELECT version_num FROM alembic_version"))
            database = engine.dialect.name
        session = getattr(request.state, "user", None)
        expires = None
        if session is not None:
            user = users.get_user(session.user_id)
            expires = user.expires_at if user else None
        return {
            "version": version,
            "commit": settings.git_sha or None,
            "ref": settings.git_ref or None,
            "build_date": settings.build_date or None,
            "database": database,
            "schema": schema,
            "auth": auth.enabled,
            "user": session.username if session else None,
            "expires": expires.isoformat() if expires else None,
            "days_left": max(0, (expires - datetime.now()).days) if expires else None,
            "demo": in_demo(request),
            "default_currency": settings.default_currency,
        }

    @app.get("/api/languages")
    def list_languages():
        """Interface languages: one JSON file per language in static/i18n, named by its code."""
        languages = []
        for path in sorted((STATIC_DIR / "i18n").glob("*.json")):
            name = json.loads(path.read_text(encoding="utf-8")).get("_name", path.stem)
            languages.append({"code": path.stem, "name": name})
        return languages

    # --- Demo mode ---

    def demo_lang(request: Request) -> str:
        return request.cookies.get(DEMO_COOKIE + "_lang") or "en"

    def set_demo(response: Response, request: Request, on: bool, lang: str = "en") -> Response:
        if on:
            if multi:
                visitor = request.cookies.get(DEMO_COOKIE)
                if visitor_demos.get(visitor) is None:
                    # Anyone can start one: a limit per client, so one can't push out everybody else's demos
                    try:
                        with new_demos.attempt(limit_key(client_ip(request))) as attempt:
                            attempt.wrong()  # counts one demo
                    except TooManyTries as error:
                        raise HTTPException(429, "Too many demos started from your address, try again later",
                                            headers={"Retry-After": str(error.retry_after)}) from None
                    visitor = visitor_demos.start(lang)
            else:
                demo.sessionmaker(lang)
                visitor = "1"
            for name, value in ((DEMO_COOKIE, visitor), (DEMO_COOKIE + "_lang", lang)):
                response.set_cookie(name, value, httponly=True, samesite="lax", secure=is_https(request))
        else:
            response.delete_cookie(DEMO_COOKIE)
            response.delete_cookie(DEMO_COOKIE + "_lang")
        return response

    @app.post("/api/demo", status_code=204)
    def start_demo(request: Request, lang: str = Query("en", pattern=r"^[a-z]{2}$")):
        """Demo mode on for this browser: made-up sample data, in a database apart from the real one."""
        return set_demo(Response(status_code=204), request, True, lang)

    @app.delete("/api/demo", status_code=204)
    def stop_demo(request: Request):
        """Back to the real data."""
        if multi:
            visitor_demos.stop(request.cookies.get(DEMO_COOKIE))
        return set_demo(Response(status_code=204), request, False)

    @app.post("/api/demo/reset", status_code=204)
    def reset_demo(request: Request, lang: str = Query(None, pattern=r"^[a-z]{2}$")):
        """Build the sample data again (undoes the changes made in demo mode)."""
        if not in_demo(request):
            raise HTTPException(409, "Not in demo mode")
        lang = lang or demo_lang(request)
        visitor_demo(request).reset(lang)
        return set_demo(Response(status_code=204), request, True, lang)

    @app.get("/demo", include_in_schema=False)
    def demo_link(request: Request, lang: str = Query("en", pattern=r"^[a-z]{2}$")):
        """A link that opens the app in demo mode (e.g. to show it to someone)."""
        return set_demo(RedirectResponse("/", status_code=303), request, True, lang)

    # --- Accounts ---

    @app.get("/api/accounts", response_model=list[AccountOut])
    def list_accounts(archived: bool = True, db: Session = Depends(get_db)):
        accounts = accounts_with_balances(db)
        return [account_out(a) for a in accounts if archived or not a.archived]

    @app.post("/api/accounts", response_model=AccountOut, status_code=201)
    def create_account(data: AccountIn, db: Session = Depends(get_db)):
        values = data.model_dump()
        values["currency"] = values["currency"] or settings.default_currency
        account = Account(**values)
        db.add(account)
        commit(db, DUPLICATE_NAME)
        db.refresh(account)
        return account_out(account)

    @app.get("/api/accounts/{account_id}", response_model=AccountOut)
    def get_account(account_id: int, db: Session = Depends(get_db)):
        return account_out(get_account_or_404(db, account_id))

    @app.patch("/api/accounts/{account_id}", response_model=AccountOut)
    def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
        account = get_account_or_404(db, account_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is None and key in ("name", "type", "currency", "archived"):
                continue
            setattr(account, key, value)
        commit(db, DUPLICATE_NAME)
        db.refresh(account)
        return account_out(account)

    @app.delete("/api/accounts/{account_id}", status_code=204)
    def delete_account(account_id: int, db: Session = Depends(get_db)):
        db.delete(get_account_or_404(db, account_id))
        db.commit()

    # --- Balances ---

    @app.get("/api/accounts/{account_id}/balances", response_model=list[BalanceOut])
    def list_balances(account_id: int, db: Session = Depends(get_db)):
        get_account_or_404(db, account_id)
        return db.scalars(select(Balance).where(Balance.account_id == account_id).order_by(Balance.date.desc())).all()

    @app.post("/api/accounts/{account_id}/balances", response_model=BalanceOut, status_code=201)
    def add_balance(account_id: int, data: BalanceIn, db: Session = Depends(get_db)):
        """Record the balance on a day; replaces the one already recorded on that day, if any."""
        get_account_or_404(db, account_id)
        balance = db.scalar(select(Balance).where(Balance.account_id == account_id, Balance.date == data.date))
        if balance:
            balance.amount, balance.note = data.amount, data.note
        else:
            balance = Balance(account_id=account_id, **data.model_dump())
            db.add(balance)
        db.commit()
        db.refresh(balance)
        return balance

    @app.patch("/api/balances/{balance_id}", response_model=BalanceOut)
    def update_balance(balance_id: int, data: BalanceUpdate, db: Session = Depends(get_db)):
        balance = get_balance_or_404(db, balance_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is None and key in ("date", "amount"):
                continue
            setattr(balance, key, value)
        commit(db, "This account already has a balance on that date")
        db.refresh(balance)
        return balance

    @app.delete("/api/balances/{balance_id}", status_code=204)
    def delete_balance(balance_id: int, db: Session = Depends(get_db)):
        db.delete(get_balance_or_404(db, balance_id))
        db.commit()

    # --- Milestones: notable expenses, drawn as vertical lines on the charts ---

    def get_milestone_or_404(db: Session, milestone_id: int) -> Milestone:
        milestone = db.get(Milestone, milestone_id)
        if not milestone:
            raise HTTPException(404, "Milestone not found")
        return milestone

    @app.get("/api/milestones", response_model=list[MilestoneOut])
    def list_milestones(account_id: int | None = None, db: Session = Depends(get_db)):
        stmt = select(Milestone).order_by(Milestone.date, Milestone.id)
        if account_id is not None:
            stmt = stmt.where(Milestone.account_id == account_id)
        return db.scalars(stmt).all()

    @app.post("/api/accounts/{account_id}/milestones", response_model=MilestoneOut, status_code=201)
    def add_milestone(account_id: int, data: MilestoneIn, db: Session = Depends(get_db)):
        get_account_or_404(db, account_id)
        milestone = Milestone(account_id=account_id, **data.model_dump())
        db.add(milestone)
        db.commit()
        db.refresh(milestone)
        return milestone

    @app.put("/api/milestones/{milestone_id}", response_model=MilestoneOut)
    def update_milestone(milestone_id: int, data: MilestoneIn, db: Session = Depends(get_db)):
        milestone = get_milestone_or_404(db, milestone_id)
        for key, value in data.model_dump().items():
            setattr(milestone, key, value)
        db.commit()
        db.refresh(milestone)
        return milestone

    @app.delete("/api/milestones/{milestone_id}", status_code=204)
    def delete_milestone(milestone_id: int, db: Session = Depends(get_db)):
        db.delete(get_milestone_or_404(db, milestone_id))
        db.commit()

    @app.post("/api/balances/bulk", response_model=BulkBalancesOut)
    def bulk_balances(data: BulkBalancesIn, db: Session = Depends(get_db)):
        """Record the balance of several accounts on the same day (the "Update balances" form)."""
        ids = [item.account_id for item in data.items]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "Each account can only appear once")
        known = set(db.scalars(select(Account.id).where(Account.id.in_(ids))))
        if unknown := set(ids) - known:
            raise HTTPException(422, f"Unknown account ids: {sorted(unknown)}")
        existing = {b.account_id: b for b in db.scalars(
            select(Balance).where(Balance.account_id.in_(ids), Balance.date == data.date))}
        created = updated = 0
        for item in data.items:
            if balance := existing.get(item.account_id):
                balance.amount = item.amount
                updated += 1
            else:
                db.add(Balance(account_id=item.account_id, date=data.date, amount=item.amount))
                created += 1
        db.commit()
        return BulkBalancesOut(created=created, updated=updated)

    # --- Summary and history ---

    @app.get("/api/summary", response_model=list[CurrencyTotal])
    def summary(db: Session = Depends(get_db)):
        return currency_totals(accounts_with_balances(db))

    def multi_fallback(request: Request) -> list:
        """Multi-user: the rates the user's (or demo's) own database already has, used only when the shared store
        has none for a day. The user's database is opened and closed here: it is never open while the rate service
        is asked, so the user's other requests don't wait for it, and rates are never written to it."""
        if not multi:
            return []
        with open_db(request) as db:
            return own_rates(db)

    @contextmanager
    def rate_source(request: Request, fallback: list):
        """(store, fetcher) of the rates. Single user: the database's own table and the service. Multi-user: the
        store shared by everybody, which demos only read (a demo never makes the app ask the service)."""
        if multi:
            yield SharedStore(shared_rates, fallback), shared_rates.fetcher(network=not in_demo(request))
            return
        with open_db(request) as db:
            yield DbStore(db), fetch_rate

    def combined_total(request: Request, totals: list[dict], fallback: list, refresh: bool) -> CombinedTotal:
        target = settings.default_currency
        total = available = Decimal(0)
        rates, missing = [], []
        with rate_source(request, fallback) as (store, fetch):
            for c in totals:
                try:
                    rate = get_rate(store, fetch, c["currency"], target, refresh=refresh)
                except LookupError:
                    missing.append(c["currency"])
                    continue
                total += Decimal(str(c["total"])) * rate.rate
                available += Decimal(str(c["available"])) * rate.rate
                if c["currency"] != target:
                    rates.append(FxRateOut(**vars(rate)))
        return CombinedTotal(currency=target, total=round(total, 2), available=round(available, 2), rates=rates, missing=missing)

    def totals_and_rates(request: Request) -> tuple[list[dict], list]:
        with open_db(request) as db:
            return currency_totals(accounts_with_balances(db)), (own_rates(db) if multi else [])

    @app.get("/api/summary/combined", response_model=CombinedTotal)
    def summary_combined(request: Request):
        """All currencies in the default one. Today's rates are fetched the first time each day."""
        return combined_total(request, *totals_and_rates(request), refresh=False)

    @app.post("/api/fx/refresh", response_model=CombinedTotal)
    def fx_refresh(request: Request):
        """Fetch today's rates again now, and the combined total with them."""
        return combined_total(request, *totals_and_rates(request), refresh=True)

    @app.get("/api/fx", response_model=FxRateOut)
    def fx_rate(
        request: Request,
        base: str = Query(..., alias="from", pattern=r"^[A-Za-z]{3}$"),
        quote: str = Query(..., alias="to", pattern=r"^[A-Za-z]{3}$"),
        day: date | None = Query(None, alias="date"),
    ):
        """The rate from one currency to another on a day (default today)."""
        try:
            with rate_source(request, multi_fallback(request)) as (store, fetch):
                return FxRateOut(**vars(get_rate(store, fetch, base, quote, day)))
        except LookupError as exc:
            raise HTTPException(503, str(exc)) from None

    @app.get("/api/currencies", response_model=list[str])
    def currencies(db: Session = Depends(get_db)):
        return db.scalars(select(Account.currency).distinct().order_by(Account.currency)).all()

    @app.get("/api/history", response_model=HistoryOut)
    def get_history(
        currency: str | None = Query(None, pattern=r"^[A-Za-z]{3}$"),
        since: date | None = None,
        db: Session = Depends(get_db),
    ):
        currency = (currency or "").upper() or db.scalar(
            select(Account.currency).group_by(Account.currency).order_by(func.count().desc(), Account.currency).limit(1)
        ) or settings.default_currency
        accounts = accounts_with_balances(db, currency)
        dates, totals, per_account = history(accounts, since)
        return HistoryOut(
            currency=currency,
            dates=dates,
            total=[float(t) for t in totals],
            available=[float(t) for t in available_totals(accounts, dates, per_account)],
            accounts=[
                {"account_id": a.id, "name": a.name, "archived": a.archived,
                 "values": [None if v is None else float(v) for v in per_account[a.id]]}
                for a in accounts
            ],
        )

    @app.get("/api/history/converted", response_model=HistoryOut)
    def get_history_converted(request: Request, since: date | None = None):
        """Every account in the default currency, each date at that date's rate. Only the currencies
        and the dates are sent to the rate service (all the dates of a currency in one request); the
        amounts are converted here."""
        target = settings.default_currency
        with open_db(request) as db:
            accounts = accounts_with_balances(db)
            fallback = own_rates(db) if multi else []
        all_dates, _, all_values = history(accounts)
        dates, _, values = history(accounts, since)
        rates, converted, missing, stale = {}, [], [], False
        with rate_source(request, fallback) as (store, fetch):
            for currency in sorted({a.currency for a in accounts} - {target}):
                try:
                    rates[currency], old = rates_for_days(store, fetch, currency, target, set(all_dates) | set(dates))
                except LookupError:
                    missing.append(currency)
                    continue
                converted.append(currency)
                stale = stale or old
        used = [a for a in accounts if a.currency == target or a.currency in rates]

        def convert(account, days, amounts):
            if account.currency == target:
                return amounts
            return [None if v is None else v * rates[account.currency][d] for d, v in zip(days, amounts)]

        def totals(per_account, days):
            total = [sum((v[n] for v in per_account.values() if v[n] is not None), Decimal(0)) for n in range(len(days))]
            return total, available_totals(used, days, per_account)

        per_account = {a.id: convert(a, dates, values[a.id]) for a in used}
        total, available = totals(per_account, dates)
        all_per_account = {a.id: convert(a, all_dates, all_values[a.id]) for a in used}
        all_total, all_available = totals(all_per_account, all_dates)
        money = lambda v: None if v is None else float(round(v, 2))  # noqa: E731
        return HistoryOut(
            currency=target, dates=dates, total=[money(v) for v in total], available=[money(v) for v in available],
            accounts=[
                {"account_id": a.id, "name": a.name, "archived": a.archived, "currency": a.currency,
                 "start": money(next((v for v in all_per_account[a.id] if v is not None), None)),
                 "values": [money(v) for v in per_account[a.id]]}
                for a in used
            ],
            first_total=money(all_total[0]) if len(all_dates) > 1 else None,
            first_available=money(all_available[0]) if len(all_dates) > 1 else None,
            converted=converted, missing=missing, stale=stale,
        )

    # --- CSV ---

    @app.get("/api/export/csv")
    def export(db: Session = Depends(get_db)):
        filename = f"balances-{date.today().isoformat()}.csv"
        return Response(export_csv(db), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    import_slots = asyncio.Semaphore(IMPORTS_AT_ONCE)

    @app.post("/api/import/csv", response_model=CsvImportOut)
    async def import_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
        limit = MAX_DEMO_CSV_BYTES if in_demo(request) else MAX_CSV_BYTES
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File too large (most {limit // 1024 // 1024} MB)")
        try:
            # In a worker thread: a big file takes a while, and the app must keep answering meanwhile
            async with import_slots:
                return await run_in_threadpool(import_csv, db, data, settings.default_currency)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    # --- Pages ---

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Version the asset URLs so browsers fetch the new JS/CSS after an image update
    asset_version = f"{version}-{settings.git_sha[:7]}" if settings.git_sha else version
    page = lambda name: (STATIC_DIR / name).read_text(encoding="utf-8").replace("{{ASSET_VERSION}}", asset_version)  # noqa: E731
    index_html, login_html, totp_html = page("index.html"), page("login.html"), page("totp.html")
    register_html, recovery_html, recover_html = page("register.html"), page("recovery.html"), page("recover.html")
    account_html, admin_html, message_html = page("account.html"), page("admin.html"), page("message.html")
    english = json.loads((STATIC_DIR / "i18n" / "en.json").read_text(encoding="utf-8"))
    login_html = login_html.replace("{{RECOVER}}", (
        f'<a class="totp-back" href="/recover" data-i18n="login.forgot">{english["login.forgot"]}</a>'
        f'<a class="totp-back" href="/demo" data-i18n="login.demo">{english["login.demo"]}</a>') if multi else "")

    def fill(template: str, **values: str) -> HTMLResponse:
        for name, value in values.items():
            template = template.replace("{{" + name + "}}", value)
        return HTMLResponse(template, headers={"Cache-Control": "no-store"})

    def message(key: str, css: str = "login-error") -> str:
        return f'<p class="{css}" data-i18n="{key}">{html.escape(english[key])}</p>'

    # Browsers and bookmark tools ask for these at the root, whatever the page links
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "icons" / "favicon.ico", media_type="image/x-icon")

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def apple_touch_icon():
        return FileResponse(STATIC_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")

    password_tries = TryLimit(WRONG_PASSWORDS_PER_IP, LOGIN_WINDOW)
    new_demos = TryLimit(DEMOS_PER_IP, 3600)
    user_password_tries = TryLimit(WRONG_PASSWORDS_PER_USER, LOGIN_WINDOW)
    argon2_slots = asyncio.Semaphore(ARGON2_AT_ONCE)

    async def with_password_check(function, *args):
        """Run a function that derives a key from a password (Argon2id), a few at a time."""
        async with argon2_slots:
            return await run_in_threadpool(function, *args)
    code_tries = TryLimit(WRONG_CODES_PER_IP, LOGIN_WINDOW, allowed_total=WRONG_CODES_TOTAL)

    def is_https(request: Request) -> bool:
        return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    def logged_in(request: Request) -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, auth.new_session(), max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                            secure=is_https(request))
        response.delete_cookie(PENDING_COOKIE)
        return response

    def user_logged_in(request: Request, session, to: str = "/") -> RedirectResponse:
        return set_user_cookie(RedirectResponse(to, status_code=303), request, session)

    def set_user_cookie(response: Response, request: Request, session) -> Response:
        response.set_cookie(USER_COOKIE, users.cookie(session), max_age=USER_SESSION_SECONDS, httponly=True,
                            samesite="lax", secure=is_https(request))
        return response

    def client_ip(request: Request) -> str:
        # The real client behind a trusted reverse proxy (uvicorn --proxy-headers, FORWARDED_ALLOW_IPS)
        return request.client.host if request.client else "unknown"

    def limit_key(ip: str) -> str:
        """What the wrong tries are counted by: the IP, or its /64 for IPv6 (one connection usually has
        the whole /64, so counting each address would let it try 2^64 times)."""
        try:
            address = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if address.version == 6:
            if address.ipv4_mapped:
                return str(address.ipv4_mapped)
            return str(ipaddress.ip_network(f"{address}/64", strict=False))
        return ip

    def too_many(error: TooManyTries) -> Response:
        minutes = math.ceil(error.retry_after / 60)
        return Response(f"Too many wrong tries, try again in {minutes} minute{'s' if minutes > 1 else ''}",
                        status_code=429, headers={"Retry-After": str(error.retry_after)})

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if not auth.enabled or auth.valid_session(request.cookies.get(COOKIE)):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_html, headers={"Cache-Control": "no-cache"})

    @app.post("/login", include_in_schema=False)
    async def login(request: Request, username: str = Form(""), password: str = Form("")):
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        ip = client_ip(request)
        name = username.strip().lower()
        session = None
        try:
            with password_tries.attempt(limit_key(ip)) as attempt, user_password_tries.attempt(name) as user_attempt:
                if multi and username != auth.username:
                    session = await with_password_check(users.login, username, password, ip,
                                                        countries.country(ip, request.headers))
                    ok = session is not None
                else:
                    ok = auth.check_credentials(username, password)
                if not ok:
                    attempt.wrong()
                    user_attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return RedirectResponse("/login?error=1", status_code=303)
        except TooManyTries as error:
            return too_many(error)
        password_tries.reset(limit_key(ip))
        user_password_tries.reset(name)
        if session is not None:
            return user_logged_in(request, session)
        if not totp:
            return logged_in(request)
        # Password right: now the one-time code (or adding the secret to the app, the first time)
        response = RedirectResponse("/login/totp", status_code=303)
        response.set_cookie(PENDING_COOKIE, auth.new_pending(), max_age=PENDING_SECONDS, httponly=True,
                            samesite="lax", secure=is_https(request))
        return response

    @app.get("/login/totp", include_in_schema=False)
    def totp_page(request: Request):
        if not totp or not auth.valid_pending(request.cookies.get(PENDING_COOKIE)):
            return RedirectResponse("/login", status_code=303)
        # The secret is shown only until a first code proves it's in the authenticator app
        setup = "" if totp.confirmed else (
            f'<div class="totp-qr">{totp.qr_svg(auth.username)}</div>'
            f'<code class="totp-secret">{" ".join(totp.secret[i:i + 4] for i in range(0, len(totp.secret), 4))}</code>'
        )
        html = totp_html.replace("{{SETUP}}", setup).replace("{{MODE}}", "verify" if totp.confirmed else "setup")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @app.post("/login/totp", include_in_schema=False)
    async def totp_login(request: Request, code: str = Form("")):
        if not totp or not auth.valid_pending(request.cookies.get(PENDING_COOKIE)):
            return RedirectResponse("/login", status_code=303)
        ip = limit_key(client_ip(request))
        try:
            with code_tries.attempt(ip) as attempt:
                if not totp.verify(code):
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return RedirectResponse("/login/totp?error=1", status_code=303)
        except TooManyTries as error:
            return too_many(error)
        code_tries.reset(ip)
        if not totp.confirmed:
            totp.confirm()
        return logged_in(request)

    # POST only, so another website can't log you out with a link or an image
    @app.post("/logout", include_in_schema=False)
    def logout(request: Request):
        auth.revoke(request.cookies.get(COOKIE))
        response = RedirectResponse("/login" if auth.enabled else "/", status_code=303)
        response.delete_cookie(COOKIE)
        if multi:
            users.revoke(request.cookies.get(USER_COOKIE))
            response.delete_cookie(USER_COOKIE)
            visitor_demos.stop(request.cookies.get(DEMO_COOKIE))
            set_demo(response, request, False)
        return response

    # --- Multi-user mode: sign-up by invitation, passwords, admin ---

    def multi_only():
        if not multi:
            raise HTTPException(404, "Not found")

    def logged_in_notice(request: Request) -> str:
        """Who is already logged in in this browser: signing up logs them out here, so the page says it first."""
        session = users.session(request.cookies.get(USER_COOKIE))
        if session is None:
            return ""
        return (f'<p class="login-error"><span data-i18n="register.logged_in">{english["register.logged_in"]}</span> '
                f'<strong>{html.escape(session.username)}</strong>. <span data-i18n="register.logged_in_note">'
                f'{english["register.logged_in_note"]}</span></p>')

    def signup_page(request: Request, token: str, error: str = "", username: str = "", email: str = "") -> HTMLResponse:
        invitation = users.invitation(token)
        if invitation is None:
            response = fill(message_html, MESSAGE=message("register.error.invitation"))
            response.status_code = 404
            return response
        return fill(register_html, TOKEN=html.escape(token), ERROR=logged_in_notice(request) + error,
                    USERNAME=html.escape(username),
                    EMAIL=html.escape(invitation.email or email), EMAIL_READONLY="readonly" if invitation.email else "")

    @app.get("/invite/{token}", include_in_schema=False, dependencies=[Depends(multi_only)])
    def invite_page(request: Request, token: str):
        return signup_page(request, token)

    @app.post("/invite/{token}", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def sign_up(request: Request, token: str, username: str = Form(""), email: str = Form(""),
                      password: str = Form(""), password2: str = Form("")):
        if password != password2:
            return signup_page(request, token, message("register.error.mismatch"), username, email)
        try:
            ip = client_ip(request)
            session, recovery = await with_password_check(users.sign_up, token, username, email, password, ip,
                                                          countries.country(ip, request.headers))
        except SignupError as error:
            if error.code == "invitation":
                return signup_page(request, token)
            return signup_page(request, token, message(f"register.error.{error.code}"), username, email)
        # Whoever was logged in in this browser is logged out for good (the page said so)
        await run_in_threadpool(users.revoke, request.cookies.get(USER_COOKIE))
        return set_user_cookie(fill(recovery_html, KEY=html.escape(recovery)), request, session)

    @app.get("/recover", include_in_schema=False, dependencies=[Depends(multi_only)])
    def recover_page(error: str = ""):
        return fill(recover_html, ERROR=message("recover.error") if error else "")

    @app.post("/recover", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def recover(request: Request, username: str = Form(""), recovery_key: str = Form(""),
                      password: str = Form(""), password2: str = Form("")):
        if password != password2:
            return fill(recover_html, ERROR=message("register.error.mismatch"))
        ip = limit_key(client_ip(request))
        try:
            with password_tries.attempt(ip) as attempt:
                try:
                    session = await with_password_check(users.recover, username, recovery_key, password)
                except SignupError:
                    return fill(recover_html, ERROR=message("register.error.password"))
                if session is None:
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return fill(recover_html, ERROR=message("recover.error"))
        except TooManyTries as error:
            return too_many(error)
        return user_logged_in(request, session)

    def account_page(request: Request, note: str = "") -> HTMLResponse:
        return fill(account_html, USERNAME=html.escape(request.state.user.username), MESSAGE=note)

    @app.get("/account", include_in_schema=False, dependencies=[Depends(multi_only)])
    def account(request: Request, changed: bool = False):
        return account_page(request, message("account.changed", "login-ok") if changed else "")

    @app.post("/account/password", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def change_password(request: Request, current: str = Form(""), password: str = Form(""),
                              password2: str = Form("")):
        if password != password2:
            return account_page(request, message("register.error.mismatch"))
        ip = limit_key(client_ip(request))
        try:
            with password_tries.attempt(ip) as attempt:
                try:
                    session = await with_password_check(users.change_password, request.state.user.user_id, current,
                                                        password)
                except SignupError:
                    return account_page(request, message("register.error.password"))
                if session is None:
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return account_page(request, message("account.error"))
        except TooManyTries as error:
            return too_many(error)
        return user_logged_in(request, session, "/account?changed=1")

    def tr(key: str, tag: str = "span", css: str = "") -> str:
        """A translated text: English in the HTML, replaced by the page's language in the browser."""
        attr = f' class="{css}"' if css else ""
        return f'<{tag}{attr} data-i18n="{key}">{html.escape(english[key])}</{tag}>'

    def action(path: str, key: str, css: str = "", confirm: str = "") -> str:
        """A button that posts to `path` (with a confirmation question when `confirm` is a text key)."""
        extra = f' data-confirm="{confirm}"' if confirm else ""
        return (f'<form method="post" action="{path}"{extra}><button type="submit"{f" class={css!r}" if css else ""} '
                f'data-i18n="{key}">{html.escape(english[key])}</button></form>')

    def table(headers: list[str], rows: list[list[str]], empty: str) -> str:
        if not rows:
            return message(empty, "totp-help")
        head = "".join(f"<th>{tr(h) if h else ''}</th>" for h in headers)
        body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
        return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'

    def when(moment) -> str:
        return f"{moment:%Y-%m-%d %H:%M}" if moment else "—"

    def size(n: int) -> str:
        return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"

    def admin_page(new_link: str = "") -> HTMLResponse:
        user_rows, total = [], 0
        for user, db_size in users.list_users():
            total += db_size
            where = " · ".join(html.escape(x) for x in (user.last_ip, user.last_country) if x)
            status = (tr("admin.status_expired", css="status-blocked") if expired(user) else
                      tr("admin.status_blocked", css="status-blocked") if user.blocked else tr("admin.status_active"))
            if user.expires_at is None:
                expiry = tr("admin.never")
            else:
                days = (user.expires_at - datetime.now()).days
                expiry = when(user.expires_at) + (f"<br><small>{max(0, days)} d</small>" if days >= 0 else "")
            expiry += (f'<form method="post" action="/admin/users/{user.id}/extend" class="extend">'
                       f'<input name="days" type="number" min="1" max="3650" value="{settings.account_days or 30}" '
                       f'aria-label="days"><button type="submit" data-i18n="admin.extend">'
                       f'{html.escape(english["admin.extend"])}</button></form>')
            if user.expires_at is not None:
                expiry += action(f"/admin/users/{user.id}/no-expiry", "admin.no_expiry")
            actions = (action(f"/admin/users/{user.id}/unblock", "admin.unblock") if user.blocked else
                       action(f"/admin/users/{user.id}/block", "admin.block"))
            actions += action(f"/admin/users/{user.id}/delete", "admin.delete", "danger", "admin.confirm_delete")
            actions += action(f"/admin/users/{user.id}/delete?block_email=1", "admin.delete_block", "danger",
                              "admin.confirm_delete_block")
            user_rows.append([html.escape(user.username), html.escape(user.email), when(user.created_at),
                              f"{when(user.last_login_at)}<br><small>{where}</small>", size(db_size), expiry, status,
                              f'<div class="row-actions">{actions}</div>'])
        limit = f" / {settings.max_users}" if settings.max_users else ""
        summary = (f'<p class="totp-help">{tr("admin.user_count")}: <strong>{len(user_rows)}{limit}</strong> · '
                   f'{tr("admin.total_size")}: <strong>{size(total)}</strong></p>')
        invitation_rows = [[html.escape(i.email) if i.email else tr("admin.anyone", "em"), when(i.expires_at),
                            action(f"/admin/invitations/{i.id}/cancel", "admin.cancel")]
                           for i in users.open_invitations()]
        blocked_rows = [[html.escape(b.email), when(b.created_at),
                         action(f"/admin/blocked-emails/unblock?email={quote(b.email)}", "admin.unblock")]
                        for b in users.blocked_emails()]
        return fill(admin_html, NEW_LINK=new_link, SUMMARY=summary,
                    USERS=table(["admin.user", "admin.email", "admin.created", "admin.last_login", "admin.size",
                                 "admin.expires", "admin.status", ""], user_rows, "admin.no_users"),
                    INVITATIONS=table(["admin.email", "admin.expires", ""], invitation_rows, "admin.no_invitations"),
                    BLOCKED=table(["admin.email", "admin.created", ""], blocked_rows, "admin.no_blocked"))

    @app.get("/admin", include_in_schema=False, dependencies=[Depends(multi_only)])
    def admin(request: Request, error: str = ""):
        # The new invitation's link comes once in a short-lived cookie (Post/Redirect/Get), so reloading the
        # page shows the list again instead of sending the form again and making another invitation
        token = request.cookies.get(NEW_INVITE_COOKIE, "")
        if token:
            link = html.escape(f"{str(request.base_url).rstrip('/')}/invite/{token}")
            note = message("admin.new_link", "totp-help") + f'<code class="totp-secret">{link}</code>'
        else:
            note = message("register.error.email") if error == "email" else ""
        response = admin_page(note)
        if token:
            response.delete_cookie(NEW_INVITE_COOKIE, path="/admin")
        return response

    @app.post("/admin/invitations", include_in_schema=False, dependencies=[Depends(multi_only)])
    def create_invitation(request: Request, email: str = Form("")):
        try:
            token = users.invite(email)
        except SignupError:
            return RedirectResponse("/admin?error=email", status_code=303)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(NEW_INVITE_COOKIE, token, max_age=60, path="/admin", httponly=True, samesite="strict",
                            secure=is_https(request))
        return response

    @app.post("/admin/invitations/{invitation_id}/cancel", include_in_schema=False, dependencies=[Depends(multi_only)])
    def cancel_invitation(invitation_id: int):
        users.cancel_invitation(invitation_id)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/users/{user_id}/block", include_in_schema=False, dependencies=[Depends(multi_only)])
    def block_user(user_id: int):
        users.set_blocked(user_id, True)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/users/{user_id}/unblock", include_in_schema=False, dependencies=[Depends(multi_only)])
    def unblock_user(user_id: int):
        users.set_blocked(user_id, False)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/users/{user_id}/extend", include_in_schema=False, dependencies=[Depends(multi_only)])
    def extend_user(user_id: int, days: int = Form(30, ge=1, le=3650)):
        users.extend(user_id, days)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/users/{user_id}/no-expiry", include_in_schema=False, dependencies=[Depends(multi_only)])
    def no_expiry(user_id: int):
        users.extend(user_id, None)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/users/{user_id}/delete", include_in_schema=False, dependencies=[Depends(multi_only)])
    def delete_user(user_id: int, block_email: bool = False):
        users.delete_user(user_id, block_email)
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/blocked-emails", include_in_schema=False, dependencies=[Depends(multi_only)])
    def block_email(email: str = Form("")):
        try:
            users.block_email(email)
        except SignupError:
            return admin_page(message("register.error.email"))
        return RedirectResponse("/admin", status_code=303)

    @app.post("/admin/blocked-emails/unblock", include_in_schema=False, dependencies=[Depends(multi_only)])
    def unblock_email(email: str = Query("")):
        users.unblock_email(email)
        return RedirectResponse("/admin", status_code=303)

    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache"})

    return app
