from __future__ import annotations

import logging
import statistics
import threading
import time
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger("smartlens")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Redis hash keys for persisted counters.
# Only counts/errors/events are persisted; latency windows are in-memory only.
_R_COUNTS = "smartlens:metrics:counts"
_R_ERRORS = "smartlens:metrics:errors"
_R_EVENTS = "smartlens:metrics:events"


class MetricsRegistry:
    """Thread-safe metrics registry with optional Redis persistence.

    Counters (request counts, error counts, event counts) are dual-written to
    Redis so they survive process restarts.  Latency samples are kept only in
    the rolling in-memory deque — they are inherently ephemeral.

    Redis is initialised lazily on first write so that unit tests (which never
    set REDIS_URL) never attempt a connection.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._counts: dict[str, int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)
        self._events: dict[str, int] = defaultdict(int)
        self._provider_status: dict[str, str] = {}

        # Redis state — initialised lazily to avoid import-time side-effects.
        self._redis: Any = None
        self._redis_ready = False
        self._redis_init_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Redis helpers
    # ------------------------------------------------------------------

    def _get_redis(self) -> Any:
        """Return a live Redis client, or None if unavailable / not configured."""
        if self._redis_ready:
            return self._redis
        with self._redis_init_lock:
            if self._redis_ready:
                return self._redis
            self._redis_ready = True
            try:
                from ..settings import settings as _s  # lazy import

                redis_url = _s.REDIS_URL
                if not redis_url:
                    return None

                import redis as _redis  # type: ignore[import]

                client = _redis.from_url(
                    redis_url,
                    socket_timeout=0.5,
                    socket_connect_timeout=0.5,
                    decode_responses=True,
                )
                client.ping()
                self._redis = client
                logger.info("metrics_redis_connected url=%s", redis_url)
                self._load_from_redis()
            except Exception as exc:
                logger.warning("metrics_redis_unavailable error=%s", exc)
                self._redis = None
        return self._redis

    def _load_from_redis(self) -> None:
        """Restore persisted counters from Redis into in-memory dicts."""
        r = self._redis
        if r is None:
            return
        try:
            counts = r.hgetall(_R_COUNTS) or {}
            errors = r.hgetall(_R_ERRORS) or {}
            events = r.hgetall(_R_EVENTS) or {}
            with self._lock:
                for k, v in counts.items():
                    self._counts[k] = int(v)
                for k, v in errors.items():
                    self._errors[k] = int(v)
                for k, v in events.items():
                    self._events[k] = int(v)
            logger.info(
                "metrics_loaded_from_redis counts=%d errors=%d events=%d",
                len(counts),
                len(errors),
                len(events),
            )
        except Exception as exc:
            logger.warning("metrics_load_from_redis_failed error=%s", exc)

    def _redis_hincrby(self, key: str, field: str, amount: int = 1) -> None:
        """Fire-and-forget Redis HINCRBY; errors are silently swallowed."""
        r = self._get_redis()
        if r is None:
            return
        try:
            r.hincrby(key, field, amount)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    def record_latency(self, name: str, duration_ms: float, success: bool = True) -> None:
        with self._lock:
            self._latencies[name].append(duration_ms)
            self._counts[name] += 1
            if not success:
                self._errors[name] += 1
        # Persist counter increments to Redis (non-blocking; errors ignored).
        self._redis_hincrby(_R_COUNTS, name)
        if not success:
            self._redis_hincrby(_R_ERRORS, name)

    def record_provider_status(self, provider: str, status: str) -> None:
        with self._lock:
            self._provider_status[provider] = status

    def increment(self, name: str, *, tags: dict[str, str] | None = None, count: int = 1) -> None:
        tag_text = ""
        if tags:
            tag_text = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        key = f"{name}|{tag_text}" if tag_text else name
        with self._lock:
            self._events[key] += count
        self._redis_hincrby(_R_EVENTS, key, count)

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def summary(self) -> dict[str, object]:
        """Full internal summary (used by the authenticated /metrics endpoint)."""
        with self._lock:
            entries: dict[str, dict[str, float | int]] = {}
            for name, samples in self._latencies.items():
                sample_list = list(samples)
                if not sample_list:
                    continue
                sorted_samples = sorted(sample_list)
                n = len(sorted_samples)
                p50_index = max(0, int(round(0.50 * (n - 1))))
                p95_index = max(0, int(round(0.95 * (n - 1))))
                entries[name] = {
                    "count": self._counts[name],
                    "errors": self._errors[name],
                    "avg_ms": round(statistics.fmean(sample_list), 2),
                    "max_ms": round(max(sample_list), 2),
                    "p50_ms": round(sorted_samples[p50_index], 2),
                    "p95_ms": round(sorted_samples[p95_index], 2),
                }
            return {
                "latency": entries,
                "events": dict(self._events),
                "providers": dict(self._provider_status),
            }

    def public_summary(self, cache_stats: dict[str, Any]) -> dict[str, Any]:
        """Non-sensitive aggregates for the public GET /metrics/summary endpoint.

        Exposes only opaque numeric aggregates — no API keys, no user data,
        no internal error strings.
        """
        # Snapshot under lock; compute outside to keep critical section short.
        with self._lock:
            latencies_snap = {k: list(v) for k, v in self._latencies.items()}
            counts_snap = dict(self._counts)
            errors_snap = dict(self._errors)
            events_snap = dict(self._events)

        # --- Provider latency stats (p50 / p95 / avg) ---
        _provider_keys = [
            "provider.google_directions",
            "provider.street_view",
            "provider.gemini_chat",
            "provider.gemini_translate",
            "provider.weather",
        ]
        providers: dict[str, Any] = {}
        for pkey in _provider_keys:
            samples = latencies_snap.get(pkey)
            if not samples:
                continue
            sorted_s = sorted(samples)
            n = len(sorted_s)
            p50_i = max(0, int(round(0.50 * (n - 1))))
            p95_i = max(0, int(round(0.95 * (n - 1))))
            display = pkey.replace("provider.", "")
            providers[display] = {
                "count": counts_snap.get(pkey, 0),
                "errors": errors_snap.get(pkey, 0),
                "avg_ms": round(statistics.fmean(samples), 1),
                "p50_ms": round(sorted_s[p50_i], 1),
                "p95_ms": round(sorted_s[p95_i], 1),
            }

        # --- Request counts for key endpoints ---
        _tracked_paths = ["/api/route", "/api/chat", "/api/translate", "/", "/health"]
        requests: dict[str, Any] = {}
        for path in _tracked_paths:
            http_key = f"http {path}"
            count = counts_snap.get(http_key, 0)
            if count > 0:
                requests[path] = {
                    "count": count,
                    "errors": errors_snap.get(http_key, 0),
                }

        # --- Cache hit rates ---
        cache_out: dict[str, Any] = {}
        for cname, stats in cache_stats.items():
            hits = int(stats.get("hits", 0))
            misses = int(stats.get("misses", 0))
            total = hits + misses
            cache_out[cname] = {
                "hits": hits,
                "misses": misses,
                "hit_rate": round(hits / total, 3) if total > 0 else None,
                "backend": stats.get("backend", "memory"),
            }

        # --- AI mode usage ---
        ai: dict[str, int] = {
            "gemini_chat": events_snap.get("ai_mode|mode=gemini,op=chat", 0),
            "gemini_translate": events_snap.get("ai_mode|mode=gemini,op=translate", 0),
            "mock_chat": events_snap.get("ai_mode|mode=mock,op=chat", 0),
            "mock_translate": events_snap.get("ai_mode|mode=mock,op=translate", 0),
        }

        routes_loaded: int = events_snap.get("routes_loaded", 0)

        return {
            "providers": providers,
            "cache": cache_out,
            "ai": ai,
            "routes_loaded": routes_loaded,
            "requests": requests,
            "updated_at": round(time.time(), 3),
        }

    def prometheus_text(self) -> str:
        with self._lock:
            lines = [
                "# HELP smartlens_request_count Total requests observed by path.",
                "# TYPE smartlens_request_count counter",
            ]
            for name, count in sorted(self._counts.items()):
                safe_name = name.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'smartlens_request_count{{name="{safe_name}"}} {count}')

            lines.extend(
                [
                    "# HELP smartlens_request_error_count Total 5xx responses observed by path.",
                    "# TYPE smartlens_request_error_count counter",
                ]
            )
            for name, count in sorted(self._errors.items()):
                safe_name = name.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'smartlens_request_error_count{{name="{safe_name}"}} {count}')

            lines.extend(
                [
                    "# HELP smartlens_event_count Application security and runtime events.",
                    "# TYPE smartlens_event_count counter",
                ]
            )
            for key, count in sorted(self._events.items()):
                event_name, _, tag_text = key.partition("|")
                labels = [f'event="{event_name.replace(chr(34), chr(92) + chr(34))}"']
                if tag_text:
                    for item in tag_text.split(","):
                        tag_key, _, tag_value = item.partition("=")
                        labels.append(
                            f'{tag_key}="{tag_value.replace(chr(34), chr(92) + chr(34))}"'
                        )
                lines.append(f"smartlens_event_count{{{','.join(labels)}}} {count}")

            return "\n".join(lines) + "\n"


metrics = MetricsRegistry()
