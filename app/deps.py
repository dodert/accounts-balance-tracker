"""What the routers share: the app's state (built once by create_app) and small helpers."""
import html
import ipaddress
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import Auth
from .config import Settings
from .demo import COOKIE as DEMO_COOKIE, Demo, VisitorDemos
from .fx import Fetcher, SharedRates
from .geo import Countries
from .history import latest_balances
from .models import Account, Balance, Milestone
from .ratelimit import TooManyTries, TryLimit
from .schemas import AccountOut
from .totp import Totp
from .users import Users

STATIC_DIR = Path(__file__).parent / "static"
# Demos anyone can start (multi-user mode), per client IP (IPv6 per /64) and hour
DEMOS_PER_IP = 10
DUPLICATE_NAME = "An account with that name already exists in that institution"


@dataclass
class Context:
    settings: Settings
    auth: Auth
    users: Users | None
    totp: Totp | None
    countries: Countries | None
    engine: Any  # single user: the SQLite engine; multi-user: None
    SessionLocal: Any
    demo: Demo  # sample data in a temporary database, for the browsers in demo mode
    visitor_demos: VisitorDemos  # multi-user mode: one per visitor, usable without an account
    fetch_rate: Fetcher
    shared_rates: SharedRates | None  # multi-user: one store of rates for everybody
    version: str
    new_demos: TryLimit = field(default_factory=lambda: TryLimit(DEMOS_PER_IP, 3600))
    pages: "Pages" = None

    @property
    def multi(self) -> bool:
        return self.settings.multi_user

    def visitor_demo(self, request: Request) -> Demo | None:
        """The demo of this browser: its own one in multi-user mode, the shared one otherwise."""
        visitor = request.cookies.get(DEMO_COOKIE)
        if self.multi:
            return self.visitor_demos.get(visitor)
        return self.demo if visitor == "1" else None

    def in_demo(self, request: Request) -> bool:
        return self.visitor_demo(request) is not None

    @staticmethod
    def demo_lang(request: Request) -> str:
        return request.cookies.get(DEMO_COOKIE + "_lang") or "en"

    @contextmanager
    def open_db(self, request: Request):
        # Demo mode uses its own database: the real one is not even opened
        current_demo = self.visitor_demo(request)
        if current_demo is not None:
            with current_demo.sessionmaker(self.demo_lang(request))() as db:
                yield db
        elif self.multi:
            with self.users.database(request.state.user) as db:
                yield db
        else:
            with self.SessionLocal() as db:
                yield db

    def get_db(self, request: Request):
        """Dependency: a session on the database of this request (the user's, the demo's or the only one)."""
        with self.open_db(request) as db:
            yield db

    def set_demo(self, response: Response, request: Request, on: bool, lang: str = "en") -> Response:
        if on:
            if self.multi:
                visitor = request.cookies.get(DEMO_COOKIE)
                if self.visitor_demos.get(visitor) is None:
                    # Anyone can start one: a limit per client, so one can't push out everybody else's demos
                    try:
                        with self.new_demos.attempt(limit_key(client_ip(request))) as attempt:
                            attempt.wrong()  # counts one demo
                    except TooManyTries as error:
                        raise HTTPException(429, "Too many demos started from your address, try again later",
                                            headers={"Retry-After": str(error.retry_after)}) from None
                    visitor = self.visitor_demos.start(lang)
            else:
                self.demo.sessionmaker(lang)
                visitor = "1"
            for name, value in ((DEMO_COOKIE, visitor), (DEMO_COOKIE + "_lang", lang)):
                response.set_cookie(name, value, httponly=True, samesite="lax", secure=is_https(request))
        else:
            response.delete_cookie(DEMO_COOKIE)
            response.delete_cookie(DEMO_COOKIE + "_lang")
        return response


class Pages:
    """The HTML pages in static/, with the asset version in their links, and the English texts."""

    def __init__(self, asset_version: str, multi: bool):
        self.asset_version = asset_version
        page = lambda name: (STATIC_DIR / name).read_text(encoding="utf-8").replace("{{ASSET_VERSION}}", asset_version)  # noqa: E731
        self.index, self.login, self.totp = page("index.html"), page("login.html"), page("totp.html")
        self.register, self.recovery, self.recover = page("register.html"), page("recovery.html"), page("recover.html")
        self.account, self.message_page = page("account.html"), page("message.html")
        self.english = json.loads((STATIC_DIR / "i18n" / "en.json").read_text(encoding="utf-8"))
        self.login = self.login.replace("{{RECOVER}}", (
            f'<a class="totp-back" href="/recover" data-i18n="login.forgot">{self.english["login.forgot"]}</a>'
            f'<a class="totp-back" href="/demo" data-i18n="login.demo">{self.english["login.demo"]}</a>') if multi else "")

    @staticmethod
    def fill(template: str, **values: str) -> HTMLResponse:
        for name, value in values.items():
            template = template.replace("{{" + name + "}}", value)
        return HTMLResponse(template, headers={"Cache-Control": "no-store"})

    def message(self, key: str, css: str = "login-error") -> str:
        return f'<p class="{css}" data-i18n="{key}">{html.escape(self.english[key])}</p>'


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


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


def get_milestone_or_404(db: Session, milestone_id: int) -> Milestone:
    milestone = db.get(Milestone, milestone_id)
    if not milestone:
        raise HTTPException(404, "Milestone not found")
    return milestone


def commit(db: Session, conflict: str) -> None:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, conflict) from None


def account_out(account: Account, latest: dict[int, dict]) -> AccountOut:
    """The account with its last, previous and first balance (`latest`: from history.latest_balances)."""
    out = AccountOut.model_validate(account)
    found = latest.get(account.id, {})
    if "last" in found:
        out.balance_date, amount = found["last"]
        out.balance = amount
    if "previous" in found:
        out.previous_date, amount = found["previous"]
        out.previous_balance = amount
        out.first_date, amount = found["first"]
        out.first_balance = amount
    return out


def one_account_out(db: Session, account: Account) -> AccountOut:
    return account_out(account, latest_balances(db, [account.id]))
