"""Multi-user mode (MULTI_USER=true): invited users, each with their own encrypted database.

- DATA_DIR/users.db (not encrypted, for the admin): users, invitations, blocked emails, logged-out sessions.
  It holds no balances: only names, emails, dates and each user's data key *wrapped* (see crypto.py).
- DATA_DIR/users/<id>.db.enc: each user's SQLite database, encrypted with their data key. It is decrypted in
  memory for each request and written back encrypted (atomic write) only when something changed.
- Keys: the password and the recovery key each wrap the user's *account key*, which wraps the *data key* that
  encrypts the database (older users, until they change their password, have no data key apart: the account
  key encrypts the database).
- While logged in, the data key travels in the session cookie, encrypted with a key derived from the server
  secret; the server never stores it. The account key never leaves the server. Changing the password (or
  recovering the account) logs out the user's other sessions and re-encrypts the database with a new data key,
  so a data key that got out (e.g. in a stolen cookie) stops opening it; the recovery key keeps working.
"""
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    LargeBinary,
    String,
    create_engine,
    delete,
    event,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from . import crypto, totp
from .backups import backups_dir, copy_private, copy_sqlite
from .db import make_engine
from .migrate import (
    IMPORT_BACKUP,
    MIGRATIONS_DIR,
    PRIVATE,
    USERS_MIGRATIONS_DIR,
    _create_private,
    alembic_config,
    prune_backups,
    upgrade_database,
)

log = logging.getLogger(__name__)

USER_COOKIE = "abt_user"
# After the right password of a user with a one-time code: the session waits here until the code is right
USER_PENDING_COOKIE = "abt_user_pending"
PENDING_SECONDS = 5 * 60
SESSION_SECONDS = 30 * 24 * 3600
INVITATION_DAYS = 7
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,31}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 10


class SystemBase(DeclarativeBase):
    pass


class User(SystemBase):
    __tablename__ = "users"
    # Ids are never reused: a deleted user's cookie or files can't ever match a new user
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # None = never
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # The data key, wrapped with the password (Argon2id with these salt and params) and with the recovery key
    kdf_salt: Mapped[bytes] = mapped_column(LargeBinary)
    kdf_params: Mapped[str] = mapped_column(String(200))
    key_by_password: Mapped[bytes] = mapped_column(LargeBinary)
    recovery_salt: Mapped[bytes] = mapped_column(LargeBinary)
    key_by_recovery: Mapped[bytes] = mapped_column(LargeBinary)
    # The data key, wrapped with the account key (the one the password and the recovery key wrap). None: an
    # account from before data keys were apart, whose database the account key itself encrypts
    key_by_account: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    session_epoch: Mapped[int] = mapped_column(Integer, default=0)  # +1 logs out every session
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Optional one-time code: the secret, sealed with a key from the server secret (None: off), and the step of the
    # last code accepted, so each code works once
    totp_secret: Mapped[str | None] = mapped_column(String(200), nullable=True)
    totp_last_counter: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Invitation(SystemBase):
    __tablename__ = "invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)  # the link's token is shown once, never stored
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)  # only this email can use it
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class BlockedEmail(SystemBase):
    __tablename__ = "blocked_emails"

    email: Mapped[str] = mapped_column(String(254), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class RevokedSession(SystemBase):
    __tablename__ = "revoked_sessions"

    nonce: Mapped[str] = mapped_column(String(32), primary_key=True)
    expires: Mapped[int] = mapped_column(Integer)


class DatabaseTooBig(Exception):
    """A change would make the user's database bigger than the limit: it is not saved."""


class SignupError(ValueError):
    """Why a sign-up was refused; `code` names the message shown (register.error.<code>)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass
class LoggedIn:
    user_id: int
    username: str
    key: bytes
    nonce: str


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def expired(user: "User") -> bool:
    return user.expires_at is not None and user.expires_at <= now()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def db_aad(user_id: int) -> bytes:
    return f"abt-db:{user_id}".encode()


def key_aad(user_id: int) -> bytes:
    return f"abt-key:{user_id}".encode()


def data_key_aad(user_id: int) -> bytes:
    return f"abt-data-key:{user_id}".encode()


def upgrade_users_db(engine) -> None:
    """Bring users.db to the latest revision of its own Alembic tree (app/migrations_users), with a private backup
    before any migration. A users.db from before that tree (made with create_all, no alembic_version) is first
    stamped with the revision it already has, without changing it: 0001, or 0002 if it has key_by_account."""
    with engine.connect() as connection:
        tables = set(inspect(connection).get_table_names())
        if "users" in tables and "alembic_version" not in tables:
            columns = {c["name"] for c in inspect(connection).get_columns("users")}
            config = alembic_config(USERS_MIGRATIONS_DIR)
            config.attributes["connection"] = connection
            command.stamp(config, "0002" if "key_by_account" in columns else "0001")
            connection.commit()
    upgrade_database(engine, USERS_MIGRATIONS_DIR)


class UserDatabases:
    """The encrypted database files, opened one request at a time per user."""

    def __init__(self, folder: Path, max_bytes: int = 0):
        self.folder = folder
        self.max_bytes = max_bytes  # 0 = no limit
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        self._locks: dict[int, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        self.head = ScriptDirectory.from_config(config).get_current_head()

    def path(self, user_id: int) -> Path:
        return self.folder / f"{user_id}.db.enc"

    def _rekey_path(self, user_id: int) -> Path:
        # The database encrypted with a new data key, until users.db has that key (see rekey)
        return self.folder / f"{user_id}.db.enc.rekey"

    def _lock(self, user_id: int) -> threading.Lock:
        with self._locks_lock:
            return self._locks.setdefault(user_id, threading.Lock())

    def _write(self, user_id: int, key: bytes, plain: bytes, path: Path | None = None) -> None:
        """Encrypt and replace the file in one step: a crash leaves the old file or the new one, never half."""
        path = path or self.path(user_id)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(crypto.seal(key, plain, db_aad(user_id)))
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp, path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    @staticmethod
    def _engine(connection: sqlite3.Connection):
        connection.execute("PRAGMA foreign_keys=ON")
        return create_engine("sqlite://", creator=lambda: connection, poolclass=StaticPool)

    def create(self, user_id: int, key: bytes) -> None:
        """An empty database with the current schema."""
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            engine = self._engine(connection)
            upgrade_database(engine)
            self._write(user_id, key, connection.serialize())
            engine.dispose()
        finally:
            connection.close()

    @contextmanager
    def rekey(self, user_id: int, old: bytes, new: bytes):
        """Re-encrypt the database (and its backups) with the `new` data key. The caller saves the new key in
        users.db inside the `with`; only then does the new file replace the old one. A crash before that save
        leaves the old file in use (the new one is thrown away when opened); after it, the next open finishes."""
        with self._lock(user_id):
            path, pending = self.path(user_id), self._rekey_path(user_id)
            if path.exists():
                self._write(user_id, new, crypto.unseal(old, path.read_bytes(), db_aad(user_id)), pending)
            yield
            if pending.exists():
                os.replace(pending, path)
            for backup in self.folder.glob(f"{user_id}.db.enc.backup-*"):
                try:
                    plain = crypto.unseal(old, backup.read_bytes(), db_aad(user_id))
                except crypto.WrongKey:
                    continue
                self._write(user_id, new, plain, backup)

    def _finish_rekey(self, user_id: int, key: bytes) -> None:
        """A re-encryption cut short (see rekey): keep the new file if `key` (the one in users.db) opens it."""
        pending = self._rekey_path(user_id)
        if not pending.exists():
            return
        try:
            crypto.unseal(key, pending.read_bytes(), db_aad(user_id))
        except crypto.WrongKey:
            pending.unlink()  # the new key was never saved: the old file is the right one
            return
        os.replace(pending, self.path(user_id))

    @contextmanager
    def open(self, user_id: int, key: bytes):
        """A session on the user's database, decrypted in memory. Each commit writes it back encrypted, before the
        answer is sent; a commit that would make it too big raises DatabaseTooBig and is not saved."""
        with self._lock(user_id):
            self._finish_rekey(user_id, key)
            path = self.path(user_id)
            plain = crypto.unseal(key, path.read_bytes(), db_aad(user_id))
            connection = sqlite3.connect(":memory:", check_same_thread=False)
            try:
                connection.deserialize(plain)
                engine = self._engine(connection)
                if self._migrate(user_id, engine, path):
                    self._write(user_id, key, connection.serialize())
                saved = connection.total_changes

                def save(_session):
                    nonlocal saved
                    if connection.total_changes == saved:
                        return
                    data = connection.serialize()
                    if self.max_bytes and len(data) > self.max_bytes:
                        raise DatabaseTooBig
                    self._write(user_id, key, data)
                    saved = connection.total_changes

                with sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)() as db:
                    event.listen(db, "after_commit", save)
                    yield db
                engine.dispose()
            finally:
                connection.close()

    def _migrate(self, user_id: int, engine, path: Path) -> bool:
        """Bring an older database to the current schema; its encrypted file is copied first."""
        with engine.connect() as connection:
            current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
        if current == self.head:
            return False
        backup = path.with_name(f"{path.name}.backup-{current}-to-{self.head}-{datetime.now():%Y%m%d-%H%M%S}")
        _create_private(backup)
        shutil.copyfile(path, backup)
        log.warning("User %s: database backed up to %s before migrating it", user_id, backup.name)
        upgrade_database(engine)  # raises without changing anything if the schema is from a newer version
        return True

    def backup_before_import(self, user_id: int) -> Path:
        """Copy of the encrypted file before an import replaces all the data (the newest few are kept). Called
        while the database is open (so with its lock held): the file has everything committed until then."""
        path = self.path(user_id)
        backup = path.with_name(f"{path.name}.backup-{IMPORT_BACKUP}-{datetime.now():%Y%m%d-%H%M%S}")
        _create_private(backup)
        shutil.copyfile(path, backup)
        prune_backups(self.folder, f"{path.name}.backup-{IMPORT_BACKUP}-*")
        return backup

    def size(self, user_id: int) -> int:
        path = self.path(user_id)
        return path.stat().st_size if path.exists() else 0

    def delete(self, user_id: int) -> None:
        """The user's database, its backups next to it and its copies in the daily backups (backups.py)."""
        daily = backups_dir(self.folder.parent).glob(f"*/{self.folder.name}/{user_id}.db.enc")
        for path in [self.path(user_id), self._rekey_path(user_id), *self.folder.glob(f"{user_id}.db.enc.backup-*"),
                     *daily]:
            path.unlink(missing_ok=True)

    def backup_into(self, folder: Path) -> None:
        """A copy of every user's encrypted file (as it is: still encrypted) in `folder`, each taken under that
        user's lock, so never while it is being written."""
        folder.mkdir(mode=0o700, exist_ok=True)
        for path in sorted(self.folder.glob("*.db.enc")):
            user_id = path.name.removesuffix(".db.enc")
            if not user_id.isdigit():
                continue
            with self._lock(int(user_id)):
                if path.exists():
                    copy_private(path, folder / path.name)


class Users:
    """Accounts, invitations and sessions of the multi-user mode."""

    def __init__(self, data_dir: Path, server_secret: bytes, admin_username: str, account_days: int = 0,
                 max_users: int = 0, max_db_bytes: int = 0):
        system_db = self.system_db = data_dir / "users.db"
        data_dir.mkdir(parents=True, exist_ok=True)
        _create_private(system_db)
        engine = make_engine(f"sqlite:///{system_db}")
        upgrade_users_db(engine)  # raises without changing anything if users.db is from a newer version
        self.Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        self.databases = UserDatabases(data_dir / "users", max_db_bytes)
        self.account_days, self.max_users = account_days, max_users
        self._session_key = crypto.derive(server_secret, b"abt-user-session")
        self._totp_key = crypto.derive(server_secret, b"abt-user-totp")
        self._totp_lock = threading.Lock()  # two codes checked at once can't both take the same step
        self.admin_username = admin_username.lower()
        # Same work for an unknown user as for a known one, so the time taken doesn't tell which names exist
        self._dummy_salt = crypto.new_salt()

    # --- Invitations ---

    def invite(self, email: str | None = None) -> str:
        """A new single-use invitation; returns its token (shown once in the link, stored only as a hash)."""
        email = (email or "").strip().lower() or None
        if email and not EMAIL.match(email):
            raise SignupError("email")
        token = secrets.token_urlsafe(24)
        with self.Session() as db:
            if email:  # a new invitation for an email replaces its open ones, so only the newest link works
                db.execute(delete(Invitation).where(Invitation.email == email, Invitation.used_at.is_(None)))
            db.add(Invitation(token_hash=token_hash(token), email=email, created_at=now(),
                              expires_at=now() + timedelta(days=INVITATION_DAYS)))
            db.commit()
        return token

    def invitation(self, token: str) -> Invitation | None:
        """The invitation of this token, if it can still be used."""
        with self.Session() as db:
            invitation = db.scalar(select(Invitation).where(Invitation.token_hash == token_hash(token)))
        if invitation is None or invitation.used_at is not None or invitation.expires_at < now():
            return None
        return invitation

    def open_invitations(self) -> list[Invitation]:
        with self.Session() as db:
            return list(db.scalars(select(Invitation).where(Invitation.used_at.is_(None), Invitation.expires_at >= now())
                                   .order_by(Invitation.created_at.desc())))

    def cancel_invitation(self, invitation_id: int) -> None:
        with self.Session() as db:
            invitation = db.get(Invitation, invitation_id)
            if invitation is not None and invitation.used_at is None:
                db.delete(invitation)
                db.commit()

    # --- Sign-up, login, passwords ---

    def sign_up(self, token: str, username: str, email: str, password: str, ip: str | None = None,
                country: str | None = None) -> tuple[LoggedIn, str]:
        """Create the user and their empty database; returns the session and the recovery key (shown once)."""
        username, email = username.strip().lower(), email.strip().lower()
        invitation = self.invitation(token)
        if invitation is None:
            raise SignupError("invitation")
        if invitation.email:
            email = invitation.email
        if not USERNAME.match(username) or username == self.admin_username:
            raise SignupError("username")
        if not EMAIL.match(email):
            raise SignupError("email")
        if len(password) < MIN_PASSWORD:
            raise SignupError("password")
        account_key, key = crypto.new_key(), crypto.new_key()
        salt, recovery_salt = crypto.new_salt(), crypto.new_salt()
        recovery = crypto.new_recovery_key()
        params = crypto.kdf_params()
        by_password_key = crypto.password_key(password, salt, json.loads(params))
        by_recovery_key = crypto.recovery_key(recovery, recovery_salt)
        with self.Session() as db:
            # Claim the invitation first, in one step: of sign-ups sent at the same time with the same link, only
            # one gets it (any error below undoes the claim, as nothing is committed)
            claimed = db.execute(update(Invitation).where(Invitation.id == invitation.id, Invitation.used_at.is_(None))
                                 .values(used_at=now()))
            if claimed.rowcount != 1:
                raise SignupError("invitation")
            if db.get(BlockedEmail, email) is not None:
                raise SignupError("blocked")
            if db.scalar(select(User).where((User.username == username) | (User.email == email))) is not None:
                raise SignupError("taken")
            if self.max_users and db.scalar(select(func.count()).select_from(User)) >= self.max_users:
                raise SignupError("full")
            invitation = db.get(Invitation, invitation.id)
            user = User(username=username, email=email, created_at=now(), kdf_salt=salt, kdf_params=params,
                        key_by_password=b"", recovery_salt=recovery_salt, key_by_recovery=b"",
                        last_login_at=now(), last_ip=ip, last_country=country,
                        expires_at=now() + timedelta(days=self.account_days) if self.account_days else None)
            db.add(user)
            db.flush()  # the id ties the wrapped keys and the database to this user
            user.key_by_password = crypto.seal(by_password_key, account_key, key_aad(user.id))
            user.key_by_recovery = crypto.seal(by_recovery_key, account_key, key_aad(user.id))
            user.key_by_account = crypto.seal(account_key, key, data_key_aad(user.id))
            invitation.used_at, invitation.user_id = now(), user.id
            self.databases.create(user.id, key)
            try:
                db.commit()
            except BaseException:
                self.databases.delete(user.id)
                raise
            return self._logged_in(user, key), recovery

    def _logged_in(self, user: User, key: bytes) -> LoggedIn:
        return LoggedIn(user.id, user.username, key, secrets.token_hex(16))

    def _unwrap(self, user: User, password: str) -> bytes:
        """The account key, if the password opens it."""
        kek = crypto.password_key(password, user.kdf_salt, json.loads(user.kdf_params))
        return crypto.unseal(kek, user.key_by_password, key_aad(user.id))

    @staticmethod
    def _data_key(user: User, account_key: bytes) -> bytes:
        if user.key_by_account is None:
            return account_key  # an account from before data keys were apart
        return crypto.unseal(account_key, user.key_by_account, data_key_aad(user.id))

    def _new_data_key(self, db: Session, user: User, account_key: bytes, password: str) -> bytes:
        """Set the new password, re-encrypt the database with a new data key and commit; returns that key."""
        old, new = self._data_key(user, account_key), crypto.new_key()
        with self.databases.rekey(user.id, old, new):
            user.key_by_account = crypto.seal(account_key, new, data_key_aad(user.id))
            self._set_password(user, account_key, password)
            db.commit()
        return new

    def login(self, username: str, password: str, ip: str | None = None, country: str | None = None) -> LoggedIn | None:
        """The session if the password opens the user's key; None otherwise (also for blocked or unknown users)."""
        with self.Session() as db:
            user = db.scalar(select(User).where(User.username == username.strip().lower()))
            if user is None:
                crypto.password_key(password, self._dummy_salt, crypto.ARGON2)
                return None
            try:
                key = self._data_key(user, self._unwrap(user, password))
            except crypto.WrongKey:
                return None
            if user.blocked or expired(user):
                return None
            if not self.databases.path(user.id).exists():
                self.databases.create(user.id, key)  # deleted when the account expired, then extended by the admin
            user.last_login_at, user.last_ip, user.last_country = now(), ip, country
            db.commit()
            return self._logged_in(user, key)

    def change_password(self, user_id: int, current: str, new: str) -> LoggedIn | None:
        """Set the new password, re-encrypt the database with a new data key and log out the other sessions; None if
        `current` is wrong."""
        if len(new) < MIN_PASSWORD:
            raise SignupError("password")
        with self.Session() as db:
            user = db.get(User, user_id)
            try:
                account_key = self._unwrap(user, current)
            except crypto.WrongKey:
                return None
            return self._logged_in(user, self._new_data_key(db, user, account_key, new))

    def recover(self, username: str, recovery: str, new: str) -> LoggedIn | None:
        """Set a new password with the recovery key; None if they don't match."""
        if len(new) < MIN_PASSWORD:
            raise SignupError("password")
        with self.Session() as db:
            user = db.scalar(select(User).where(User.username == username.strip().lower()))
            if user is None or user.blocked:
                return None
            try:
                account_key = crypto.unseal(crypto.recovery_key(recovery, user.recovery_salt), user.key_by_recovery,
                                            key_aad(user.id))
            except crypto.WrongKey:
                return None
            return self._logged_in(user, self._new_data_key(db, user, account_key, new))

    def _set_password(self, user: User, account_key: bytes, password: str) -> None:
        user.kdf_salt, user.kdf_params = crypto.new_salt(), crypto.kdf_params()
        kek = crypto.password_key(password, user.kdf_salt, json.loads(user.kdf_params))
        user.key_by_password = crypto.seal(kek, account_key, key_aad(user.id))
        user.session_epoch += 1

    # --- One-time codes (optional, per user) ---

    def _totp_aad(self, user_id: int) -> bytes:
        return f"abt-user-totp:{user_id}".encode()

    def totp_on(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return user is not None and user.totp_secret is not None

    def set_totp(self, user_id: int, password: str, secret: str, code: str) -> str | None:
        """Turn the one-time code on with this secret, if `code` is its current code and the password is right.
        Returns None when done, or the reason: "password" or "code"."""
        with self.Session() as db:
            user = db.get(User, user_id)
            try:
                self._unwrap(user, password)
            except crypto.WrongKey:
                return "password"
            try:
                counter = totp.check(secret, code)
            except (ValueError, TypeError):  # not a base32 secret
                counter = None
            if counter is None:
                return "code"
            sealed = crypto.seal(self._totp_key, secret.encode(), self._totp_aad(user_id))
            user.totp_secret, user.totp_last_counter = base64.b64encode(sealed).decode(), counter
            db.commit()
        return None

    def remove_totp(self, user_id: int, password: str | None = None) -> bool:
        """Turn the one-time code off; with a password (the user), only if it is right; without (the admin), always."""
        with self.Session() as db:
            user = db.get(User, user_id)
            if user is None:
                return False
            if password is not None:
                try:
                    self._unwrap(user, password)
                except crypto.WrongKey:
                    return False
            user.totp_secret = user.totp_last_counter = None
            db.commit()
        return True

    def verify_totp(self, user_id: int, code: str, now: float | None = None) -> bool:
        """Whether `code` is the user's current one-time code, not used before (it is then used up)."""
        with self._totp_lock, self.Session() as db:
            user = db.get(User, user_id)
            if user is None or user.totp_secret is None:
                return False
            secret = crypto.unseal(self._totp_key, base64.b64decode(user.totp_secret), self._totp_aad(user_id)).decode()
            last = user.totp_last_counter if user.totp_last_counter is not None else -1
            counter = totp.check(secret, code, last, now)
            if counter is None:
                return False
            user.totp_last_counter = counter
            db.commit()
            return True

    # --- Sessions (cookie) ---

    def cookie(self, session: LoggedIn, pending: bool = False) -> str:
        """The session cookie; `pending`: the short-lived one that waits for the one-time code, which never works as
        a session (sealed for another purpose)."""
        with self.Session() as db:
            epoch = db.get(User, session.user_id).session_epoch
        payload = {"u": session.user_id, "g": epoch, "e": int(time.time()) + (PENDING_SECONDS if pending else SESSION_SECONDS),
                   "n": session.nonce, "k": base64.b64encode(session.key).decode()}
        sealed = crypto.seal(self._session_key, json.dumps(payload).encode(), self._cookie_aad(pending))
        return base64.urlsafe_b64encode(sealed).decode()

    @staticmethod
    def _cookie_aad(pending: bool) -> bytes:
        return b"abt-user-pending" if pending else b"abt-user-session"

    def session(self, cookie: str | None, pending: bool = False) -> LoggedIn | None:
        """The logged-in user of this cookie, if it is still valid (not expired, revoked, blocked or outdated)."""
        if not cookie or len(cookie) > 1000:
            return None
        try:
            payload = json.loads(crypto.unseal(self._session_key, base64.urlsafe_b64decode(cookie),
                                               self._cookie_aad(pending)))
        except (ValueError, crypto.WrongKey):
            return None
        if payload["e"] < time.time():
            return None
        with self.Session() as db:
            user = db.get(User, payload["u"])
            if (user is None or user.blocked or expired(user) or user.session_epoch != payload["g"]
                    or db.get(RevokedSession, payload["n"])):
                return None
            return LoggedIn(user.id, user.username, base64.b64decode(payload["k"]), payload["n"])

    def revoke(self, cookie: str | None) -> None:
        """Log this session out for good (a copied cookie stops working too)."""
        session = self.session(cookie)
        if session is None:
            return
        with self.Session() as db:
            db.execute(text("DELETE FROM revoked_sessions WHERE expires < :t"), {"t": int(time.time())})
            db.add(RevokedSession(nonce=session.nonce, expires=int(time.time()) + SESSION_SECONDS))
            db.commit()

    @contextmanager
    def database(self, session: LoggedIn):
        with self.databases.open(session.user_id, session.key) as db:
            yield db

    def get_user(self, user_id: int) -> User | None:
        with self.Session() as db:
            return db.get(User, user_id)

    # --- Admin ---

    def list_users(self) -> list[tuple[User, int]]:
        """Every user, newest first, with the size of their encrypted database in bytes."""
        with self.Session() as db:
            users = list(db.scalars(select(User).order_by(User.created_at.desc(), User.id.desc())))
        return [(user, self.databases.size(user.id)) for user in users]

    def set_blocked(self, user_id: int, blocked: bool) -> None:
        """A blocked user can't log in and is logged out at once; their data is kept."""
        with self.Session() as db:
            user = db.get(User, user_id)
            if user is not None:
                user.blocked = blocked
                db.commit()

    def delete_user(self, user_id: int, block_email: bool = False) -> None:
        """Delete the user and their database (and its backups), for good."""
        with self.Session() as db:
            user = db.get(User, user_id)
            if user is None:
                return
            if block_email and db.get(BlockedEmail, user.email) is None:
                db.add(BlockedEmail(email=user.email, created_at=now()))
            db.delete(user)
            db.commit()
        with self.databases._lock(user_id):
            self.databases.delete(user_id)

    def extend(self, user_id: int, days: int | None) -> None:
        """`days` more (from today if it already expired), or no expiry with None. An account blocked because it
        expired is unblocked; it starts again with an empty database (the old one was deleted)."""
        with self.Session() as db:
            user = db.get(User, user_id)
            if user is None:
                return
            if days is None:
                was_expired = expired(user)
                user.expires_at = None
            else:
                was_expired = expired(user)
                start = user.expires_at if user.expires_at and not was_expired else now()
                user.expires_at = start + timedelta(days=days)
            if was_expired:
                user.blocked = False
            db.commit()

    def purge_expired(self) -> int:
        """Delete the database of every expired account and block it. Returns how many."""
        with self.Session() as db:
            users = list(db.scalars(select(User).where(User.expires_at.is_not(None), User.expires_at <= now())))
            purged = 0
            for user in users:
                if user.blocked and not self.databases.path(user.id).exists():
                    continue
                with self.databases._lock(user.id):
                    self.databases.delete(user.id)
                user.blocked = True
                purged += 1
                log.warning("Account %s expired: its database was deleted and the user blocked", user.username)
            db.commit()
        return purged

    def backup_into(self, folder: Path) -> None:
        """The daily backup (backups.py): users.db and every user's encrypted database, as they are."""
        copy_sqlite(self.system_db, folder / self.system_db.name)
        self.databases.backup_into(folder / self.databases.folder.name)

    def blocked_emails(self) -> list[BlockedEmail]:
        with self.Session() as db:
            return list(db.scalars(select(BlockedEmail).order_by(BlockedEmail.email)))

    def block_email(self, email: str) -> None:
        email = email.strip().lower()
        if not EMAIL.match(email):
            raise SignupError("email")
        with self.Session() as db:
            if db.get(BlockedEmail, email) is None:
                db.add(BlockedEmail(email=email, created_at=now()))
                db.commit()

    def unblock_email(self, email: str) -> None:
        with self.Session() as db:
            blocked = db.get(BlockedEmail, email.strip().lower())
            if blocked is not None:
                db.delete(blocked)
                db.commit()
