"""Optional login: one user and password from AUTH_USERNAME / AUTH_PASSWORD, no user management.

A successful login sets a signed cookie (HMAC, no server-side sessions). The signing key mixes a
secret kept in DATA_DIR with the password, so changing the password logs everyone out. Logging out
puts that cookie on a revoked list in DATA_DIR until it would have expired anyway, so a copied
cookie stops working too.
"""
import hashlib
import hmac
import secrets
import time
from pathlib import Path

COOKIE = "abt_session"
# Password checked, one-time code still to enter (only with AUTH_TOTP)
PENDING_COOKIE = "abt_pending"
PENDING_SECONDS = 5 * 60
SESSION_SECONDS = 30 * 24 * 3600
# Reachable without logging in: the login page itself, its assets, icons and the Docker health check
PUBLIC_PATHS = {"/login", "/login/totp", "/logout", "/favicon.ico", "/apple-touch-icon.png", "/api/health"}
PUBLIC_PREFIXES = ("/static/",)


class Auth:
    def __init__(self, username: str, password: str, data_dir: Path):
        if bool(username) != bool(password):
            raise ValueError("Set both AUTH_USERNAME and AUTH_PASSWORD to turn the login on, or neither to turn it off")
        self.username = username
        self.password = password
        self.enabled = bool(username)
        self._key = hmac.new(self._secret(data_dir), password.encode(), hashlib.sha256).digest() if self.enabled else b""
        self._revoked_path = data_dir / ".revoked_sessions"
        self._revoked = self._load_revoked() if self.enabled else {}

    @staticmethod
    def _secret(data_dir: Path) -> bytes:
        """Random secret kept in DATA_DIR, so sessions survive restarts."""
        path = data_dir / ".session_secret"
        if not path.exists():
            data_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(secrets.token_hex(32))
            path.chmod(0o600)
        return path.read_text().strip().encode()

    def _load_revoked(self) -> dict[str, int]:
        """Signature -> expiry of logged-out sessions, without the ones that have expired."""
        revoked = {}
        if self._revoked_path.exists():
            for line in self._revoked_path.read_text().splitlines():
                signature, _, expires = line.partition(" ")
                if expires.isdigit() and int(expires) > time.time():
                    revoked[signature] = int(expires)
        return revoked

    def revoke(self, token: str | None) -> None:
        """Log this session out for good (other devices stay logged in)."""
        if not self.valid_session(token):
            return
        expires, _, signature = token.split(".")
        self._revoked = {s: e for s, e in self._revoked.items() if e > time.time()}
        self._revoked[signature] = int(expires)
        self._revoked_path.write_text("".join(f"{s} {e}\n" for s, e in self._revoked.items()))
        self._revoked_path.chmod(0o600)

    def check_credentials(self, username: str, password: str) -> bool:
        # Compare hashes so the time taken doesn't reveal the length of the real values
        def same(a: str, b: str) -> bool:
            return hmac.compare_digest(hashlib.sha256(a.encode()).digest(), hashlib.sha256(b.encode()).digest())
        user_ok = same(username, self.username)
        pass_ok = same(password, self.password)
        return self.enabled and user_ok and pass_ok

    def _sign(self, expires: str, nonce: str) -> str:
        return hmac.new(self._key, f"{self.username}|{expires}|{nonce}".encode(), hashlib.sha256).hexdigest()

    def new_session(self) -> str:
        """expires.nonce.signature; the random nonce tells apart two logins in the same second."""
        expires, nonce = str(int(time.time()) + SESSION_SECONDS), secrets.token_hex(8)
        return f"{expires}.{nonce}.{self._sign(expires, nonce)}"

    def valid_session(self, token: str | None) -> bool:
        parts = (token or "").split(".")
        if len(parts) != 3:
            return False
        expires, nonce, signature = parts
        # A forged cookie must not crash the request: ASCII digits only, and no absurd lengths
        if not (token.isascii() and expires.isdigit() and len(expires) <= 12) or int(expires) < time.time():
            return False
        return hmac.compare_digest(signature, self._sign(expires, nonce)) and signature not in self._revoked

    def _sign_pending(self, expires: str) -> str:
        # Its own key, so a pending cookie can never be reshaped into a valid session cookie
        key = hmac.new(self._key, b"pending-cookie", hashlib.sha256).digest()
        return hmac.new(key, f"{self.username}|{expires}".encode(), hashlib.sha256).hexdigest()

    def new_pending(self) -> str:
        """Short-lived proof that the password was right, while the one-time code is asked."""
        expires = str(int(time.time()) + PENDING_SECONDS)
        return f"{expires}.{self._sign_pending(expires)}"

    def valid_pending(self, token: str | None) -> bool:
        expires, _, signature = (token or "").partition(".")
        if not (expires.isascii() and expires.isdigit() and len(expires) <= 12) or int(expires) < time.time():
            return False
        return hmac.compare_digest(signature.encode(), self._sign_pending(expires).encode())

    @staticmethod
    def is_public(path: str) -> bool:
        return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
