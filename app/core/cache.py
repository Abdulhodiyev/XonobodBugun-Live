import time
from typing import Any


class SimpleTTLCache:
    """Лёгкий in-memory кэш с мгновенным сбросом при записи в БД."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._store.get(key)
        if not item:
            return None
        expires_at, value = item
        if time.monotonic() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self._store[key] = (time.monotonic() + ttl_seconds, value)

    def invalidate(self, *keys: str) -> None:
        if not keys:
            self._store.clear()
            return
        for key in keys:
            prefix = f"{key}:"
            to_delete = [k for k in self._store if k == key or k.startswith(prefix)]
            for k in to_delete:
                self._store.pop(k, None)


app_cache = SimpleTTLCache()