import asyncio
from contextlib import asynccontextmanager
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import __version__
from .auth import COOKIE, PENDING_COOKIE, PENDING_SECONDS, SESSION_SECONDS, Auth
from .config import Settings, settings as default_settings
from .csv_io import export_csv, import_csv
from .db import make_engine, make_sessionmaker
from .demo import COOKIE as DEMO_COOKIE, Demo
from .fx import Fetcher, frankfurter, get_rate
from .history import accounts_with_balances, available_totals, currency_totals, history
from .migrate import upgrade_database
from .models import Account, Balance
from .schemas import (
    AccountIn, AccountOut, AccountUpdate, BalanceIn, BalanceOut, BalanceUpdate, BulkBalancesIn, BulkBalancesOut,
    CombinedTotal, CsvImportOut, CurrencyTotal, FxRateOut, HistoryOut,
)
from .totp import Totp

STATIC_DIR = Path(__file__).parent / "static"
MAX_CSV_BYTES = 20 * 1024 * 1024


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
    totp = Totp(settings.data_dir) if settings.auth_totp else None
    engine = make_engine(settings.resolved_database_url)
    SessionLocal = make_sessionmaker(engine)
    upgrade_database(engine)
    demo = Demo()  # sample data in a temporary database, for the browsers in demo mode
    fetch_rate = fx_fetcher or frankfurter(settings.fx_url)

    version = settings.app_version if settings.app_version and settings.app_version != "dev" else __version__

    @asynccontextmanager
    async def lifespan(_app):
        yield
        demo.close()  # the demo database is temporary

    app = FastAPI(title="Accounts Balance Tracker", version=version, lifespan=lifespan)

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next):
        # Another website open in the browser must not be able to post forms here (e.g. a CSV import)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("sec-fetch-site") == "cross-site":
            return Response("Cross-site request blocked", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        if not auth.enabled or auth.is_public(request.url.path) or auth.valid_session(request.cookies.get(COOKIE)):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Login required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    def in_demo(request: Request) -> bool:
        return request.cookies.get(DEMO_COOKIE) == "1"

    def get_db(request: Request):
        # Demo mode uses its own database: the real one is not even opened
        maker = demo.sessionmaker(demo_lang(request)) if in_demo(request) else SessionLocal
        with maker() as db:
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
        with SessionLocal() as db:
            schema = db.scalar(text("SELECT version_num FROM alembic_version"))
        return {
            "version": version,
            "commit": settings.git_sha or None,
            "ref": settings.git_ref or None,
            "build_date": settings.build_date or None,
            "database": engine.dialect.name,
            "schema": schema,
            "auth": auth.enabled,
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
            for name, value in ((DEMO_COOKIE, "1"), (DEMO_COOKIE + "_lang", lang)):
                response.set_cookie(name, value, httponly=True, samesite="lax", secure=is_https(request))
        else:
            response.delete_cookie(DEMO_COOKIE)
            response.delete_cookie(DEMO_COOKIE + "_lang")
        return response

    @app.post("/api/demo", status_code=204)
    def start_demo(request: Request, lang: str = Query("en", pattern=r"^[a-z]{2}$")):
        """Demo mode on for this browser: made-up sample data, in a database apart from the real one."""
        demo.sessionmaker(lang)
        return set_demo(Response(status_code=204), request, True, lang)

    @app.delete("/api/demo", status_code=204)
    def stop_demo(request: Request):
        """Back to the real data."""
        return set_demo(Response(status_code=204), request, False)

    @app.post("/api/demo/reset", status_code=204)
    def reset_demo(request: Request, lang: str = Query(None, pattern=r"^[a-z]{2}$")):
        """Build the sample data again (undoes the changes made in demo mode)."""
        if not in_demo(request):
            raise HTTPException(409, "Not in demo mode")
        lang = lang or demo_lang(request)
        demo.reset(lang)
        return set_demo(Response(status_code=204), request, True, lang)

    @app.get("/demo", include_in_schema=False)
    def demo_link(request: Request, lang: str = Query("en", pattern=r"^[a-z]{2}$")):
        """A link that opens the app in demo mode (e.g. to show it to someone)."""
        demo.sessionmaker(lang)
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

    def combined_total(db: Session, refresh: bool) -> CombinedTotal:
        target = settings.default_currency
        total = available = Decimal(0)
        rates, missing = [], []
        for c in currency_totals(accounts_with_balances(db)):
            try:
                rate = get_rate(db, fetch_rate, c["currency"], target, refresh=refresh)
            except LookupError:
                missing.append(c["currency"])
                continue
            total += Decimal(str(c["total"])) * rate.rate
            available += Decimal(str(c["available"])) * rate.rate
            if c["currency"] != target:
                rates.append(FxRateOut(**vars(rate)))
        return CombinedTotal(currency=target, total=round(total, 2), available=round(available, 2), rates=rates, missing=missing)

    @app.get("/api/summary/combined", response_model=CombinedTotal)
    def summary_combined(db: Session = Depends(get_db)):
        """All currencies in the default one. Today's rates are fetched the first time each day."""
        return combined_total(db, refresh=False)

    @app.post("/api/fx/refresh", response_model=CombinedTotal)
    def fx_refresh(db: Session = Depends(get_db)):
        """Fetch today's rates again now, and the combined total with them."""
        return combined_total(db, refresh=True)

    @app.get("/api/fx", response_model=FxRateOut)
    def fx_rate(
        base: str = Query(..., alias="from", pattern=r"^[A-Za-z]{3}$"),
        quote: str = Query(..., alias="to", pattern=r"^[A-Za-z]{3}$"),
        day: date | None = Query(None, alias="date"),
        db: Session = Depends(get_db),
    ):
        """The rate from one currency to another on a day (default today)."""
        try:
            return FxRateOut(**vars(get_rate(db, fetch_rate, base, quote, day)))
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

    # --- CSV ---

    @app.get("/api/export/csv")
    def export(db: Session = Depends(get_db)):
        filename = f"balances-{date.today().isoformat()}.csv"
        return Response(export_csv(db), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/api/import/csv", response_model=CsvImportOut)
    async def import_file(file: UploadFile = File(...), db: Session = Depends(get_db)):
        data = await file.read(MAX_CSV_BYTES + 1)
        if len(data) > MAX_CSV_BYTES:
            raise HTTPException(413, "File too large")
        try:
            return import_csv(db, data, settings.default_currency)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    # --- Pages ---

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # Version the asset URLs so browsers fetch the new JS/CSS after an image update
    asset_version = f"{version}-{settings.git_sha[:7]}" if settings.git_sha else version
    page = lambda name: (STATIC_DIR / name).read_text(encoding="utf-8").replace("{{ASSET_VERSION}}", asset_version)  # noqa: E731
    index_html, login_html, totp_html = page("index.html"), page("login.html"), page("totp.html")

    # Browsers and bookmark tools ask for these at the root, whatever the page links
    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return FileResponse(STATIC_DIR / "icons" / "favicon.ico", media_type="image/x-icon")

    @app.get("/apple-touch-icon.png", include_in_schema=False)
    def apple_touch_icon():
        return FileResponse(STATIC_DIR / "icons" / "apple-touch-icon.png", media_type="image/png")

    failed_login_lock = asyncio.Lock()

    def is_https(request: Request) -> bool:
        return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    def logged_in(request: Request) -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, auth.new_session(), max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                            secure=is_https(request))
        response.delete_cookie(PENDING_COOKIE)
        return response

    wrong_codes = 0  # wrong one-time codes in a row, for the whole app

    async def failed(location: str, delay: float = 1) -> RedirectResponse:
        # One wrong password or code at a time for the whole app: parallel requests wait in line,
        # so guessing can't be sped up by sending many at once
        async with failed_login_lock:
            await asyncio.sleep(delay)
        return RedirectResponse(location, status_code=303)

    @app.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if not auth.enabled or auth.valid_session(request.cookies.get(COOKIE)):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_html, headers={"Cache-Control": "no-cache"})

    @app.post("/login", include_in_schema=False)
    async def login(request: Request, username: str = Form(""), password: str = Form("")):
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        if not auth.check_credentials(username, password):
            return await failed("/login?error=1")
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
        nonlocal wrong_codes
        if not totp or not auth.valid_pending(request.cookies.get(PENDING_COOKIE)):
            return RedirectResponse("/login", status_code=303)
        if not totp.verify(code):
            # 1 s for the first few, then doubling up to a minute
            wrong_codes += 1
            return await failed("/login/totp?error=1", min(60, 2 ** max(0, wrong_codes - 5)))
        wrong_codes = 0
        if not totp.confirmed:
            totp.confirm()
        return logged_in(request)

    # POST only, so another website can't log you out with a link or an image
    @app.post("/logout", include_in_schema=False)
    def logout(request: Request):
        auth.revoke(request.cookies.get(COOKIE))
        response = RedirectResponse("/login" if auth.enabled else "/", status_code=303)
        response.delete_cookie(COOKIE)
        return response

    @app.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(index_html, headers={"Cache-Control": "no-cache"})

    return app
