import time
from typing import Any, Optional

_store: dict[str, tuple[Any, float]] = {}


def cache_get(key: str) -> Optional[Any]:
    entry = _store.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if time.monotonic() < expires_at:
        return value
    del _store[key]
    return None


def cache_set(key: str, value: Any, ttl_seconds: int = 60) -> None:
    _store[key] = (value, time.monotonic() + ttl_seconds)


def cache_invalidate(key: str) -> None:
    _store.pop(key, None)
