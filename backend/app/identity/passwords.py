"""Password hashing and verification for local platform accounts."""

import base64
import hashlib
import hmac
import secrets


_ALGORITHM = "pbkdf2-sha256"
_ITERATIONS = 310_000
_SALT_BYTES = 16
_MIN_ITERATIONS = 100_000
_MAX_ITERATIONS = 1_000_000
_DIGEST_BYTES = hashlib.sha256().digest_size


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _ITERATIONS
    )
    return "$".join((
        _ALGORITHM,
        str(_ITERATIONS),
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    ))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_encoded, digest_encoded = encoded.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        iteration_count = int(iterations)
        if not _MIN_ITERATIONS <= iteration_count <= _MAX_ITERATIONS:
            return False
        salt = base64.b64decode(salt_encoded.encode("ascii"), validate=True)
        expected = base64.b64decode(digest_encoded.encode("ascii"), validate=True)
        if not _SALT_BYTES <= len(salt) <= 64 or len(expected) != _DIGEST_BYTES:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iteration_count
        )
    except (UnicodeEncodeError, ValueError, OverflowError):
        return False
    return hmac.compare_digest(candidate, expected)
