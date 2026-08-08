"""HMAC-SHA256 请求签名（webhook 动作）。"""

from __future__ import annotations

import hashlib
import hmac


def sign_hook(body: bytes, secret: str) -> str:
    """对请求体做 HMAC-SHA256，返回 hex 小写签名。"""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify_hook_signature(body: bytes, secret: str, signature: str) -> bool:
    """校验签名（常量时间比较，防时序攻击）。"""
    return hmac.compare_digest(sign_hook(body, secret), signature)
