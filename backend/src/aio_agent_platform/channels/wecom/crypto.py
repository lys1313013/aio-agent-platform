"""WeCom callback crypto — msg_signature verification and AES-256-CBC decryption.

Reference: https://developer.work.weixin.qq.com/document/path/90930

Two key differences from Feishu's scheme:
  - the signature is SHA-1 (not SHA-256) over the four values sorted lexically;
  - the AES key is the base64-decoded 43-char EncodingAESKey (padded to 44 with
    ``=``), IV = key[:16], and the plaintext is
    ``random16 + len4(big-endian) + msg + receiveid``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def verify_signature(
    token: str,
    timestamp: str,
    nonce: str,
    encrypt: str,
    msg_signature: str,
) -> bool:
    """Return True when msg_signature == SHA1(sorted([token, timestamp, nonce, encrypt]))."""
    content = "".join(sorted([token, timestamp, nonce, encrypt])).encode("utf-8")
    computed = hashlib.sha1(content).hexdigest()
    return hmac.compare_digest(computed, msg_signature)


def decrypt(encoding_aes_key: str, encrypted: str) -> str:
    """AES-256-CBC decrypt a WeCom callback payload, returning the inner message.

    Strips the 16-byte random prefix, the 4-byte big-endian length and the
    trailing receiveid; the returned value is exactly the message body.
    """
    key = base64.b64decode(encoding_aes_key + "=")  # 43 chars -> 44 -> 32 bytes
    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    plaintext = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    pad_len = plaintext[-1]
    plaintext = plaintext[:-pad_len]

    random16 = plaintext[:16]  # noqa: F841 — fixed 16-byte random prefix
    msg_len = struct.unpack(">I", plaintext[16:20])[0]
    msg = plaintext[20 : 20 + msg_len]
    return msg.decode("utf-8")
