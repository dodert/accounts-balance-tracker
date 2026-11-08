"""Encryption of the users' databases (multi-user mode).

Each user's database is encrypted with its own random data key (AES-256-GCM). That key is stored
only wrapped (encrypted) twice: with a key derived from the user's password (Argon2id, slow on
purpose) and with a key derived from a recovery key shown once at sign-up. Without one of the two,
nobody (the admin included) can read the database. Changing the password only re-wraps the key.
"""
import base64
import json
import secrets

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 16
# Argon2id cost for new passwords; each user keeps the one their key was wrapped with, so it can be raised later
ARGON2 = {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 1}


class WrongKey(Exception):
    """The key does not open this data: wrong password or recovery key, or the data was changed."""


def new_key() -> bytes:
    return secrets.token_bytes(KEY_BYTES)


def new_salt() -> bytes:
    return secrets.token_bytes(SALT_BYTES)


def password_key(password: str, salt: bytes, params: dict) -> bytes:
    return hash_secret_raw(password.encode(), salt, hash_len=KEY_BYTES, type=Type.ID, **params)


def kdf_params() -> str:
    return json.dumps(ARGON2)


def new_recovery_key() -> str:
    """120 random bits as 24 letters and digits, in groups of 4: ABCD-EFGH-..."""
    text = base64.b32encode(secrets.token_bytes(15)).decode()
    return "-".join(text[i:i + 4] for i in range(0, len(text), 4))


def recovery_key(text: str, salt: bytes) -> bytes:
    # The recovery key is random and long, so a fast derivation is enough
    normalized = "".join(c for c in text.upper() if c.isalnum()).encode()
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, info=b"abt-recovery").derive(normalized)


def derive(secret: bytes, purpose: bytes) -> bytes:
    """A key for one purpose from the server secret."""
    return HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=purpose).derive(secret)


def seal(key: bytes, data: bytes, aad: bytes) -> bytes:
    """nonce + ciphertext; `aad` ties it to its place (e.g. the user id), so it can't be moved elsewhere."""
    nonce = secrets.token_bytes(NONCE_BYTES)
    return nonce + AESGCM(key).encrypt(nonce, data, aad)


def unseal(key: bytes, blob: bytes, aad: bytes) -> bytes:
    if len(blob) <= NONCE_BYTES:
        raise WrongKey
    try:
        return AESGCM(key).decrypt(blob[:NONCE_BYTES], blob[NONCE_BYTES:], aad)
    except InvalidTag:
        raise WrongKey from None
