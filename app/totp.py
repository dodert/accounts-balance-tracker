"""Optional second step for the login: a one-time code (TOTP, RFC 6238) from an authenticator app
(Google Authenticator, Aegis, 1Password...). Turned on with AUTH_TOTP=true.

The secret is created on first use and kept in DATA_DIR/.totp_secret. Until a first code has been
entered the app shows it (as a QR code and as text) after the password, so it can be added to the
app; after that only the code is asked. Deleting the file shows a new secret at the next login.
"""
import base64
import hashlib
import hmac
import io
import json
import secrets
import struct
import time
from pathlib import Path
from urllib.parse import quote

import qrcode
import qrcode.image.svg

ISSUER = "Accounts Balance Tracker"
STEP = 30
DIGITS = 6


def code_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


class Totp:
    def __init__(self, data_dir: Path):
        self.path = data_dir / ".totp_secret"
        self._last_counter = -1  # a code is accepted once, so a code seen over a shoulder can't be replayed
        self._load()  # creates the secret on first start

    def _load(self) -> dict:
        if self.path.exists():
            try:
                return json.loads(self.path.read_text())
            except ValueError:
                # Never replace it silently: a new, unconfirmed secret would be shown to anyone with the password
                raise ValueError(f"{self.path} is damaged; delete it to set up the one-time code again") from None
        data = {"secret": base64.b32encode(secrets.token_bytes(20)).decode(), "confirmed": False}
        self._save(data)
        return data

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data))
        self.path.chmod(0o600)

    @property
    def confirmed(self) -> bool:
        return self._load()["confirmed"]

    @property
    def secret(self) -> str:
        return self._load()["secret"]

    def verify(self, code: str, now: float | None = None) -> bool:
        """The code of now, or of the step before or after (clocks drift a little)."""
        code = "".join(code.split())
        if not (code.isdigit() and len(code) == DIGITS):
            return False
        current = int((now if now is not None else time.time()) // STEP)
        for counter in (current - 1, current, current + 1):
            if counter > self._last_counter and hmac.compare_digest(code, code_at(self.secret, counter)):
                self._last_counter = counter
                return True
        return False

    def confirm(self) -> None:
        self._save({"secret": self.secret, "confirmed": True})

    def uri(self, account: str) -> str:
        label = quote(f"{ISSUER}:{account}", safe=":")
        return f"otpauth://totp/{label}?secret={self.secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"

    def qr_svg(self, account: str) -> str:
        image = qrcode.make(self.uri(account), image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        out = io.BytesIO()
        image.save(out)
        return out.getvalue().decode().split("?>", 1)[-1].strip()  # inline: without the XML declaration
