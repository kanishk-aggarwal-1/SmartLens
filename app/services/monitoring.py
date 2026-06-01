from __future__ import annotations

import logging
import statistics
import threading
from collections import defaultdict, deque


logger = logging.getLogger("smartlens")
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)


class MetricsRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=200))
        self._counts: dict[str, int] = defaultdict(int)
        self._errors: dict[str, int] = defaultdict(int)
        self._events: dict[str, int] = defaultdict(int)
        self._provider_status: dict[str, str] = {}

    def record_latency(self, name: str, duration_ms: float, success: bool = True) -> None:
        with self._lock:
            self._latencies[name].append(duration_ms)
            self._counts[name] += 1
            if not success:
                self._errors[name] += 1

    def record_provider_status(self, provider: str, status: str) -> None:
        with self._lock:
            self._provider_status[provider] = status

    def increment(self, name: str, *, tags: dict[str, str] | None = None, count: int = 1) -> None:
        tag_text = ""
        if tags:
            tag_text = ",".join(f"{key}={value}" for key, value in sorted(tags.items()))
        key = f"{name}|{tag_text}" if tag_text else name
        with self._lock:
            self._events[key] += count

    def summary(self) -> dict[str, object]:
        with self._lock:
            entries: dict[str, dict[str, float | int]] = {}
            for name, samples in self._latencies.items():
                sample_list = list(samples)
                if not sample_list:
                    continue
                sorted_samples = sorted(sample_list)
                p95_index = max(0, int(round(0.95 * (len(sorted_samples) - 1))))
                entries[name] = {
                    "count": self._counts[name],
                    "errors": self._errors[name],
                    "avg_ms": round(statistics.fmean(sample_list), 2),
                    "max_ms": round(max(sample_list), 2),
                    "p95_ms": round(sorted_samples[p95_index], 2),
                }

            return {
                "latency": entries,
                "events": dict(self._events),
                "providers": dict(self._provider_status),
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
