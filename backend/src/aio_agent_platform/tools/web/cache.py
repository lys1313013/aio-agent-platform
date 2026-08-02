"""Simple in-process TTL cache with per-entry expiry."""

from __future__ import annotations

import time
from collections import OrderedDict


class TTLCache:
    def __init__(self, maxsize: int = 500) -> None:
        self._maxsize = maxsize
        self._store: OrderedDict[str, tuple[float, str]] = OrderedDict()

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        self._store[key] = (time.monotonic() + ttl_seconds, value)
        self._store.move_to_end(key)
        while len(self._store) > self._maxsize:
            self._store.popitem(last=False)
