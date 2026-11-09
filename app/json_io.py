"""Export and import of all the data as JSON: every account with all its fields, balances and milestones.

Format (FORMAT_VERSION 1):

    {"format": "accounts-balance-tracker", "version": 1, "exported_at": "...", "app_version": "...",
     "accounts": [{"name": "...", "type": "bank", "currency": "EUR", "institution": null,
                   "available_from": null, "notes": null, "archived": false,
                   "balances": [{"date": "2026-01-31", "amount": "1234.56", "note": null}],
                   "milestones": [{"date": "2026-02-01", "amount": "300.00", "description": "..."}]}]}

Amounts are text, so they keep their exact decimals. Two ways to import:
- replace: the database ends up with exactly the file's data (the caller makes a backup first).
- merge: only what is not there yet is added. An account is found by its name and institution (as in the
  CSV import); a balance that already exists for that account and date, or the same milestone (account,
  date, amount and description), is not imported and is listed as a duplicate. An existing account keeps
  its own fields.
The whole file is checked before anything is changed: a file with an error changes nothing.
"""
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from .models import Account, AccountType, Balance, Milestone
from .schemas import Currency, DecimalAmount, Name, Note, Notes

FORMAT = "accounts-balance-tracker"
FORMAT_VERSION = 1
MAX_LISTED = 200  # duplicates listed in the answer; the count is always the full one


class _Item(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @field_validator("institution", "note", "notes", mode="before", check_fields=False)
    @classmethod
    def empty_is_none(cls, value):
        return None if isinstance(value, str) and not value.strip() else value


class BalanceIn(_Item):
    date: date
    amount: DecimalAmount
    note: Note | None = None


class MilestoneIn(_Item):
    date: date
    amount: DecimalAmount
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class AccountIn(_Item):
    name: Name
    type: AccountType = AccountType.bank
    currency: Currency = "EUR"
    institution: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    available_from: date | None = None
    notes: Notes | None = None
    archived: bool = False
    balances: list[BalanceIn] = []
    milestones: list[MilestoneIn] = []


class Backup(_Item):
    format: str
    version: int
    accounts: list[AccountIn]


def _text(value: Decimal) -> str:
    return f"{value:.2f}"


def export_json(db: Session, app_version: str = "") -> str:
    accounts = db.scalars(select(Account).order_by(Account.name, Account.institution, Account.id)
                          .options(selectinload(Account.balances), selectinload(Account.milestones)))
    data = {
        "format": FORMAT, "version": FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "app_version": app_version,
        "accounts": [{
            "name": a.name, "type": a.type.value, "currency": a.currency, "institution": a.institution,
            "available_from": a.available_from.isoformat() if a.available_from else None,
            "notes": a.notes, "archived": a.archived,
            "balances": [{"date": b.date.isoformat(), "amount": _text(b.amount), "note": b.note} for b in a.balances],
            "milestones": [{"date": m.date.isoformat(), "amount": _text(m.amount), "description": m.description}
                           for m in a.milestones],
        } for a in accounts],
    }
    return json.dumps(data, ensure_ascii=False, indent=1)


def _key(name: str, institution: str | None) -> tuple[str, str]:
    return name.lower(), (institution or "").lower()


def _label(name: str, institution: str | None) -> str:
    return f"{name} ({institution})" if institution else name


def parse_backup(data: bytes) -> Backup:
    """The file checked as a whole; ValueError with the reason if it is not a valid export."""
    try:
        raw = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("The file is not JSON") from None
    if not isinstance(raw, dict) or raw.get("format") != FORMAT:
        raise ValueError("The file is not an export of this app")
    if not isinstance(raw.get("version"), int) or raw["version"] > FORMAT_VERSION:
        raise ValueError(f"The file is from a newer version of the app (format {raw.get('version')}); update the app")
    try:
        backup = Backup.model_validate(raw)
    except ValidationError as exc:
        error = exc.errors()[0]
        where = ".".join(str(part) for part in error["loc"])
        raise ValueError(f"Invalid file: {where}: {error['msg']}") from None
    seen = set()
    for account in backup.accounts:
        key = _key(account.name, account.institution)
        if key in seen:
            raise ValueError(f"Invalid file: the account '{_label(account.name, account.institution)}' is there twice")
        seen.add(key)
        days = [b.date for b in account.balances]
        if len(days) != len(set(days)):
            raise ValueError(f"Invalid file: '{_label(account.name, account.institution)}' has two balances on the same day")
    return backup


def _new_account(account: AccountIn) -> Account:
    return Account(name=account.name, type=account.type, currency=account.currency, institution=account.institution,
                   available_from=account.available_from, notes=account.notes, archived=account.archived)


def import_json(db: Session, backup: Backup, replace: bool) -> dict:
    result = {"accounts_created": 0, "balances_created": 0, "milestones_created": 0,
              "duplicates_count": 0, "duplicates": []}

    def duplicate(text: str) -> None:
        result["duplicates_count"] += 1
        if len(result["duplicates"]) < MAX_LISTED:
            result["duplicates"].append(text)

    if replace:
        # Balances and milestones go with their account (ON DELETE CASCADE); deleted explicitly too, in case
        db.execute(delete(Balance))
        db.execute(delete(Milestone))
        db.execute(delete(Account))
        db.flush()
        existing: dict[tuple[str, str], Account] = {}
    else:
        existing = {_key(a.name, a.institution): a for a in db.scalars(select(Account))}

    for item in backup.accounts:
        label = _label(item.name, item.institution)
        account = existing.get(_key(item.name, item.institution))
        if account is None:
            account = _new_account(item)
            db.add(account)
            db.flush()
            result["accounts_created"] += 1
            days, milestones = set(), set()
        else:
            days = set(db.scalars(select(Balance.date).where(Balance.account_id == account.id)))
            milestones = {(m.date, m.amount, m.description)
                          for m in db.scalars(select(Milestone).where(Milestone.account_id == account.id))}
        for b in item.balances:
            if b.date in days:
                duplicate(f"{label}: balance on {b.date.isoformat()}")
                continue
            db.add(Balance(account_id=account.id, date=b.date, amount=b.amount, note=b.note))
            days.add(b.date)
            result["balances_created"] += 1
        for m in item.milestones:
            key = (m.date, m.amount, m.description)
            if key in milestones:
                duplicate(f"{label}: milestone '{m.description}' on {m.date.isoformat()}")
                continue
            db.add(Milestone(account_id=account.id, date=m.date, amount=m.amount, description=m.description))
            milestones.add(key)
            result["milestones_created"] += 1
    db.commit()
    return result
