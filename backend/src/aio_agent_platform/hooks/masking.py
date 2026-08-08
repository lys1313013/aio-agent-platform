"""敏感字段脱敏 — 防止密码/token 等泄露到 webhook 负载与日志。"""

from __future__ import annotations

from collections.abc import Mapping

# 命中的键（小写比较）值一律打码
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "authorization",
    "auth",
    "credential",
    "access_key",
    "secret_key",
    "private_key",
}

_MASK = "***"


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return k in SENSITIVE_KEYS or k.endswith("_key") or k.endswith("_token") or k.endswith("_secret")


def mask_value(value: object, key: str = "") -> object:
    """对单个值脱敏（标量打码；容器递归）。"""
    if _is_sensitive_key(key):
        return _MASK
    if isinstance(value, Mapping):
        return {k: mask_value(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [mask_value(v, key) for v in value]
    if isinstance(value, tuple):
        return tuple(mask_value(v, key) for v in value)
    return value


def mask_payload(payload: dict) -> dict:
    """对统一负载做递归脱敏（外壳 + data）。"""
    return {k: mask_value(v, k) for k, v in payload.items()}
