from __future__ import annotations

import threading
import time
import base64
from dataclasses import dataclass
from typing import Generic, TypeVar

from ..settings import settings
from .monitoring import logger, metrics


T = TypeVar("T")


@dataclass
class _CacheItem(Generic[T]):
    value: T
    expires_at: float


class TTLCache(Generic[T]):
    def __init__(self, name: str, default_ttl_s: int = 60):
        self.name = name
        self.default_ttl_s = default_ttl_s
        self._items: dict[str, _CacheItem[T]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self._redis = self._create_redis_client()

    def _create_redis_client(self):
        backend = (settings.CACHE_BACKEND or "auto").strip().lower()
        if backend == "memory" or not settings.REDIS_URL:
            return None

        try:
            import redis

            client = redis.from_url(settings.REDIS_URL, socket_timeout=1.0, socket_connect_timeout=1.0)
            client.ping()
            metrics.record_provider_status("redis", "ok")
            return client
        except Exception as exc:
            metrics.record_provider_status("redis", f"error:{type(exc).__name__}")
            if backend == "redis":
                logger.warning("redis_cache_unavailable cache=%s error=%s", self.name, exc)
            return None

    def _redis_key(self, key: str) -> str:
        return f"smartlens:{self.name}:{key}"

    def _encode_value(self, value):
        if isinstance(value, bytes):
            return {"__type": "bytes", "value": base64.b64encode(value).decode("ascii")}
        if isinstance(value, dict):
            return {item_key: self._encode_value(item_value) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [self._encode_value(item) for item in value]
        return value

    def _decode_value(self, value):
        if isinstance(value, dict) and value.get("__type") == "bytes":
            return base64.b64decode(str(value.get("value", "")).encode("ascii"))
        if isinstance(value, dict):
            return {item_key: self._decode_value(item_value) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [self._decode_value(item) for item in value]
        return value

    def get(self, key: str) -> T | None:
        if self._redis is not None:
            try:
                import json

                raw = self._redis.get(self._redis_key(key))
                if raw is not None:
                    self.hits += 1
                    metrics.record_provider_status(f"{self.name}_redis_cache", "hit")
                    return self._decode_value(json.loads(raw))  # type: ignore[return-value]
                self.misses += 1
                metrics.record_provider_status(f"{self.name}_redis_cache", "miss")
                return None
            except Exception as exc:
                metrics.record_provider_status("redis", f"error:{type(exc).__name__}")
                logger.warning("redis_cache_get_failed cache=%s error=%s", self.name, exc)

        now = time.monotonic()
        with self._lock:
            item = self._items.get(key)
            if not item:
                self.misses += 1
                return None
            if item.expires_at <= now:
                self._items.pop(key, None)
                self.misses += 1
                return None
            self.hits += 1
            return item.value

    def set(self, key: str, value: T, ttl_s: int | None = None) -> None:
        ttl = int(ttl_s or self.default_ttl_s)
        if self._redis is not None:
            try:
                import json

                self._redis.setex(
                    self._redis_key(key),
                    ttl,
                    json.dumps(self._encode_value(value), separators=(",", ":")),
                )
                return
            except Exception as exc:
                metrics.record_provider_status("redis", f"error:{type(exc).__name__}")
                logger.warning("redis_cache_set_failed cache=%s error=%s", self.name, exc)

        expires_at = time.monotonic() + float(ttl)
        with self._lock:
            self._items[key] = _CacheItem(value=value, expires_at=expires_at)

    def clear(self) -> None:
        if self._redis is not None:
            try:
                for key in self._redis.scan_iter(self._redis_key("*")):
                    self._redis.delete(key)
            except Exception as exc:
                metrics.record_provider_status("redis", f"error:{type(exc).__name__}")
                logger.warning("redis_cache_clear_failed cache=%s error=%s", self.name, exc)
        with self._lock:
            self._items.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, int | str]:
        with self._lock:
            return {
                "name": self.name,
                "size": len(self._items),
                "hits": self.hits,
                "misses": self.misses,
                "default_ttl_s": self.default_ttl_s,
                "backend": "redis" if self._redis is not None else "memory",
            }
