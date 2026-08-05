import base64

import pytest
from cryptography.exceptions import InvalidTag

from app.identity.crypto import EnvelopeCipher, hash_opaque_token


def test_ciphertext_uses_current_key_and_old_key_still_decrypts():
    old = bytes(range(32))
    current = bytes(reversed(range(32)))
    cipher = EnvelopeCipher(current_key_id="k2", keys={"k1": old, "k2": current})
    encrypted = cipher.encrypt(b"csrf-secret")
    assert encrypted["kid"] == "k2"
    assert cipher.decrypt(encrypted) == b"csrf-secret"


def test_cipher_decrypts_ciphertext_written_by_a_previous_key():
    old = bytes(range(32))
    current = bytes(reversed(range(32)))
    old_cipher = EnvelopeCipher(current_key_id="k1", keys={"k1": old})
    cipher = EnvelopeCipher(current_key_id="k2", keys={"k1": old, "k2": current})
    assert cipher.decrypt(old_cipher.encrypt(b"csrf-secret")) == b"csrf-secret"


def test_tampered_aes_gcm_ciphertext_fails_authentication():
    cipher = EnvelopeCipher(current_key_id="k1", keys={"k1": bytes(range(32))})
    encrypted = cipher.encrypt(b"csrf-secret")
    ciphertext = bytearray(base64.urlsafe_b64decode(encrypted["ciphertext"] + "="))
    ciphertext[0] ^= 1
    encrypted["ciphertext"] = base64.urlsafe_b64encode(ciphertext).decode().rstrip("=")
    with pytest.raises(InvalidTag):
        cipher.decrypt(encrypted)


def test_hash_opaque_token_uses_hmac_sha256():
    assert (
        hash_opaque_token("token", b"key")
        == "646b8eac0c4ae4178299c9bd924e2bf073dfb07e024901a8c864f65b9462bf0d"
    )
