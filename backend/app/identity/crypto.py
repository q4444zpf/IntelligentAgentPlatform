import base64
import hashlib
import hmac
import secrets
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _decode_base64url(value: str) -> bytes:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError("invalid encrypted value encoding") from error


def hash_opaque_token(raw: str, key: bytes) -> str:
    return hmac.new(key, raw.encode("utf-8"), hashlib.sha256).hexdigest()


class EnvelopeCipher:
    def __init__(self, current_key_id: str, keys: Mapping[str, bytes]) -> None:
        if current_key_id not in keys:
            raise ValueError("current encryption key ID is not configured")
        if any(len(key) != 32 for key in keys.values()):
            raise ValueError("AES-GCM keys must be 32 bytes")
        self.current_key_id = current_key_id
        self.current_key = keys[current_key_id]
        self.keys = dict(keys)

    def encrypt(self, value: bytes) -> dict[str, str]:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self.current_key).encrypt(
            nonce,
            value,
            self.current_key_id.encode(),
        )
        return {
            "kid": self.current_key_id,
            "nonce": base64.urlsafe_b64encode(nonce).decode().rstrip("="),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode().rstrip("="),
        }

    def decrypt(self, value: Mapping[str, str]) -> bytes:
        try:
            key_id = value["kid"]
            key = self.keys[key_id]
            nonce = _decode_base64url(value["nonce"])
            ciphertext = _decode_base64url(value["ciphertext"])
        except KeyError as error:
            raise ValueError("unknown or incomplete encrypted value") from error
        if len(nonce) != 12:
            raise ValueError("invalid encrypted value nonce")
        return AESGCM(key).decrypt(nonce, ciphertext, key_id.encode())
