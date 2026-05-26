import threading
import time
from typing import Any, Optional

_store: dict[str, tuple[Any, float]] = {}
_lock = threading.Lock()


def cache_get(key: str) -> Optional[Any]:
    with _lock:
        entry = _store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.monotonic() < expires_at:
            return value
        del _store[key]
        return None


def cache_set(key: str, value: Any, ttl_seconds: int = 60) -> None:
    with _lock:
        _store[key] = (value, time.monotonic() + ttl_seconds)


def cache_invalidate(key: str) -> None:
    with _lock:
        _store.pop(key, None)


def cache_invalidate_prefix(prefix: str) -> None:
    """Remove all cache entries whose key starts with the given prefix."""
    with _lock:
        keys = [k for k in _store if k.startswith(prefix)]
        for k in keys:
            del _store[k]
