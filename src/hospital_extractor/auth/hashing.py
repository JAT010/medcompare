"""Password hashing and token digests (P24).

Uses stdlib ``hashlib.scrypt`` with a per-password salt and constant-time
verification (``hmac.compare_digest``).  No plaintext passwords are stored,
logged, or returned.  No custom cryptography — only established primitives.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Final

__all__ = [
    "hash_password",
    "hash_token",
    "new_token",
    "new_user_id",
    "verify_password",
]

_SCRYPT_N: Final = 2**14
_SCRYPT_R: Final = 8
_SCRYPT_P: Final = 1
_SCRYPT_DKLEN: Final = 32
_SALT_BYTES: Final = 16


def hash_password(password: str) -> str:
    """Return ``scrypt$n$r$p$salt_hex$hash_hex`` for *password*."""
    if not password:
        raise ValueError("password must not be empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return (
        f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}$"
        f"{salt.hex()}${digest.hex()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verify of *password* against an encoded hash."""
    if not password or not encoded:
        return False
    try:
        algorithm, n_s, r_s, p_s, salt_hex, hash_hex = encoded.split("$", 5)
    except ValueError:
        return False
    if algorithm != "scrypt":
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except ValueError:
        return False
    try:
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=len(expected) or _SCRYPT_DKLEN,
        )
    except ValueError:
        return False
    return hmac.compare_digest(digest, expected)


def new_token() -> str:
    """Opaque bearer token (returned once to the client)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Server-side digest of a bearer token (never store the raw token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_user_id() -> str:
    return f"u_{secrets.token_hex(8)}"
