"""Optional second step for the login: a one-time code (TOTP, RFC 6238) from an authenticator app
(Google Authenticator, Aegis, 1Password...). Turned on with AUTH_TOTP=true.

The secret is created on first use and kept in DATA_DIR/.totp_secret. Until a first code has been
entered the app shows it (as a QR code and as text) after the password, so it can be added to the
app; after that only the code is asked. Deleting the file shows a new secret at the next login.

The file also keeps the step of the last code accepted, so a code is accepted only once, also after a
restart: a code seen over someone's shoulder can't be used again within its 90 seconds.
"""
import base64
import hashlib
import hmac
import io
import json
import os
import secrets
import struct
import threading
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
        self._lock = threading.Lock()  # two codes checked at once can't both take the same step
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
        """Written to a new file (0600 from the start) that then replaces the old one: a stop half way through
        never leaves a damaged file, which would stop the app."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = self.path.with_name(self.path.name + ".new")
        descriptor = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(json.dumps(data))
            file.flush()
            os.fsync(file.fileno())
        os.chmod(new, 0o600)
        os.replace(new, self.path)

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
        with self._lock:
            data = self._load()
            last = data.get("last_counter", -1)  # a code is accepted once, also after a restart
            for counter in (current - 1, current, current + 1):
                if counter > last and hmac.compare_digest(code, code_at(data["secret"], counter)):
                    self._save({**data, "last_counter": counter})
                    return True
        return False

    def confirm(self) -> None:
        with self._lock:
            self._save({**self._load(), "confirmed": True})

    def uri(self, account: str) -> str:
        label = quote(f"{ISSUER}:{account}", safe=":")
        return f"otpauth://totp/{label}?secret={self.secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP}"

    def qr_svg(self, account: str) -> str:
        image = qrcode.make(self.uri(account), image_factory=qrcode.image.svg.SvgPathImage, box_size=8, border=2)
        out = io.BytesIO()
        image.save(out)
        return out.getvalue().decode().split("?>", 1)[-1].strip()  # inline: without the XML declaration
