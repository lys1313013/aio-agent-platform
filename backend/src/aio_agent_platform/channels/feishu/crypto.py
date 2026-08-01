"""Feishu webhook crypto — signature verification and event decryption.

Reference: https://open.feishu.cn/document/ukTM5YjY5LzMy4i20kTN/event-subscription-configure-/request-encryption-and-verification-encryption-and-decryption-case-
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def verify_signature(
    verification_token: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify the X-Lark-Signature header.

    Returns True when the signature matches. The comparison is constant-time
    to avoid leaking partial matches.
    """
    content = timestamp + nonce + verification_token + body.decode("utf-8", errors="replace")
    computed = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return _constant_time_eq(computed, signature)


def decrypt_event(encrypt_key: str, encrypted: str) -> dict[str, Any]:
    """Decrypt the ``encrypt`` field of a Feishu event payload.

    Feishu uses AES-256-CBC with the key = SHA256(encrypt_key), IV = first 16
    bytes of the ciphertext, and PKCS#7 padding.
    """
    import base64

    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    raw = base64.b64decode(encrypted)
    iv = raw[:16]
    ciphertext = raw[16:]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    plaintext = decryptor.update(ciphertext) + decryptor.finalize()
    # Remove PKCS#7 padding.
    pad_len = plaintext[-1]
    plaintext = plaintext[:-pad_len]
    return json.loads(plaintext.decode("utf-8"))


def _constant_time_eq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
