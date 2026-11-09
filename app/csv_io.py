"""CSV export and import of balances: one row per account and date.

Columns: account, type, currency, institution, date, amount, note, available_from. The import also
takes the Spanish names (cuenta, tipo, moneda, entidad, fecha, saldo, nota, disponible_desde) and
only needs account, date and amount; available_from is only used when an account is created. Amounts may use a decimal point or comma ("1234.56", "1.234,56", "1,234.56"); dates
YYYY-MM-DD or DD/MM/YYYY.

An account is found by its name and institution (names are unique per institution). Without an
institution in the row, an account with that name is used if there is only one, or the one without
an institution.
"""
import csv
import io
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Account, AccountType, Balance

COLUMNS = ["account", "type", "currency", "institution", "date", "amount", "note", "available_from"]
ALIASES = {
    "account": "account", "cuenta": "account", "name": "account", "nombre": "account",
    "type": "type", "tipo": "type",
    "currency": "currency", "moneda": "currency", "divisa": "currency",
    "institution": "institution", "entidad": "institution", "banco": "institution", "bank": "institution",
    "date": "date", "fecha": "date",
    "amount": "amount", "balance": "amount", "saldo": "amount", "importe": "amount",
    "note": "note", "nota": "note", "notes": "note", "notas": "note",
    "available_from": "available_from", "available": "available_from",
    "disponible_desde": "available_from", "disponible": "available_from",
}
REQUIRED = {"account", "date", "amount"}
# A text cell starting with one of these runs as a formula in Excel or LibreOffice (CSV injection)
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def safe_text(value: str) -> str:
    """Exported text that a spreadsheet shows as text: a leading ' when it would start a formula."""
    return f"'{value}" if value.startswith(FORMULA_START) else value


def unsafe_text(value: str) -> str:
    """The text as it was before safe_text, so an exported file imported back finds the same accounts."""
    return value[1:] if value.startswith("'") and value[1:].startswith(FORMULA_START) else value


def export_csv(db: Session) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(COLUMNS)
    accounts = db.scalars(select(Account).order_by(Account.name))
    for account in accounts:
        for b in account.balances:
            writer.writerow([safe_text(account.name), account.type.value, account.currency,
                             safe_text(account.institution or ""), b.date.isoformat(), f"{b.amount:.2f}",
                             safe_text(b.note or ""),
                             account.available_from.isoformat() if account.available_from else ""])
    return out.getvalue()


def parse_amount(text: str) -> Decimal:
    value = re.sub(r"[\s €$£]", "", text)
    if "," in value and "." in value:
        # The one that comes last is the decimal separator
        thousands = "." if value.rfind(",") > value.rfind(".") else ","
        value = value.replace(thousands, "")
    value = value.replace(",", ".")
    try:
        amount = Decimal(value)
    except InvalidOperation:
        raise ValueError(f"invalid amount '{text}'") from None
    if not amount.is_finite() or abs(amount) >= Decimal("1e12"):
        raise ValueError(f"invalid amount '{text}'")
    return amount.quantize(Decimal("0.01"))


def parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"invalid date '{text}'")


def decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def detect_delimiter(header: str) -> str:
    return max((";", ",", "\t"), key=header.count)


MAX_LISTED = 200  # duplicates listed in the answer; the count is always the full one


def _label(account: Account) -> str:
    return f"{account.name} ({account.institution})" if account.institution else account.name


def import_csv(db: Session, data: bytes, default_currency: str) -> dict:
    text = decode(data)
    delimiter = detect_delimiter(text.split("\n", 1)[0])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader, None)
    if not header:
        raise ValueError("The file is empty")
    columns = {ALIASES[h.strip().lower()]: i for i, h in reversed(list(enumerate(header))) if h.strip().lower() in ALIASES}
    missing = REQUIRED - set(columns)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}")

    accounts = list(db.scalars(select(Account)))

    def find(name: str, institution: str) -> Account | None:
        same = [a for a in accounts if a.name.lower() == name.lower()]
        if institution:
            return next((a for a in same if (a.institution or "").lower() == institution.lower()), None)
        if len(same) > 1:
            same = [a for a in same if not a.institution]
            if not same:
                raise ValueError(f"there are several accounts named '{name}': add the institution")
        return same[0] if same else None

    result = {"accounts_created": 0, "balances_created": 0, "duplicates_count": 0, "duplicates": [], "errors": []}
    known: dict[int, set[date]] = {}  # days with a balance, per account (in the database or added from the file)
    for line, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        cell = lambda key: row[columns[key]].strip() if key in columns and columns[key] < len(row) else ""  # noqa: E731
        try:
            name = unsafe_text(cell("account"))
            if not name or len(name) > 100:
                raise ValueError("missing or too long account name")
            day = parse_date(cell("date"))
            amount = parse_amount(cell("amount"))
            institution = unsafe_text(cell("institution"))[:100]
            account = find(name, institution)
            if account is None:
                kind = cell("type").lower() or "bank"
                currency = (cell("currency") or default_currency).upper()
                if kind not in AccountType.__members__:
                    raise ValueError(f"unknown type '{kind}'")
                if not re.fullmatch(r"[A-Z]{3}", currency):
                    raise ValueError(f"invalid currency '{currency}'")
                account = Account(name=name, type=AccountType(kind), currency=currency,
                                  institution=institution or None, archived=False,
                                  available_from=parse_date(cell("available_from")) if cell("available_from") else None)
                db.add(account)
                db.flush()
                accounts.append(account)
                result["accounts_created"] += 1
            note = unsafe_text(cell("note"))[:300] or None
            if account.id not in known:
                known[account.id] = set(db.scalars(select(Balance.date).where(Balance.account_id == account.id)))
            if day in known[account.id]:
                # Never overwritten: the balance already there stays, and the row is reported
                result["duplicates_count"] += 1
                if len(result["duplicates"]) < MAX_LISTED:
                    result["duplicates"].append(f"Line {line}: {_label(account)}: there is already a balance on {day.isoformat()}")
                continue
            db.add(Balance(account_id=account.id, date=day, amount=amount, note=note))
            known[account.id].add(day)
            result["balances_created"] += 1
        except ValueError as exc:
            result["errors"].append(f"Line {line}: {exc}")
    db.commit()
    return result
