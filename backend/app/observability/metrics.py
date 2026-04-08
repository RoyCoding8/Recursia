"""Lightweight in-memory metrics recorder for orchestration observability."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any


@dataclass(slots=True)
class MetricSample:
    name: str
    value: float
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class MetricsSnapshot:
    counters: dict[str, float]
    gauges: dict[str, float]
    timings: dict[str, list[float]]
    counts: dict[str, int]


class MetricsRecorder:
    """Thread-safe process-local metrics utility for MVP instrumentation."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._timings: dict[str, list[float]] = {}

    def increment(self, name: str, value: float = 1.0, **tags: str) -> None:
        _ = tags
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + value

    def gauge(self, name: str, value: float, **tags: str) -> None:
        _ = tags
        with self._lock:
            self._gauges[name] = value

    def timing(self, name: str, value_ms: float, **tags: str) -> None:
        _ = tags
        with self._lock:
            self._timings.setdefault(name, []).append(value_ms)

    def snapshot(self) -> MetricsSnapshot:
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            timings = {k: list(v) for k, v in self._timings.items()}

        counts = {name: len(samples) for name, samples in timings.items()}
        return MetricsSnapshot(
            counters=counters,
            gauges=gauges,
            timings=timings,
            counts=counts,
        )

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._timings.clear()

    # Domain-specific helpers aligned to requirements/NFR observability set.
    def record_ttft(
        self, ttft_ms: float, *, run_id: str | None = None, node_id: str | None = None
    ) -> None:
        _ = (run_id, node_id)
        self.timing("ttft_ms", ttft_ms)

    def record_node_duration(
        self,
        duration_ms: float,
        *,
        run_id: str | None = None,
        node_id: str | None = None,
    ) -> None:
        _ = (run_id, node_id)
        self.timing("node_duration_ms", duration_ms)

    def record_checker_result(self, passed: bool, *, scope: str = "node") -> None:
        key = "checker_pass_total" if passed else "checker_fail_total"
        self.increment(key)
        self.increment(f"checker_{scope}_total")

    def record_retry(self, *, reason: str = "checker_fail") -> None:
        _ = reason
        self.increment("retry_total")

    def record_blocked_human(
        self, *, reason: str = "checker_failed_consecutive_threshold"
    ) -> None:
        _ = reason
        self.increment("blocked_human_total")

    def as_dict(self) -> dict[str, Any]:
        snap = self.snapshot()
        return {
            "counters": snap.counters,
            "gauges": snap.gauges,
            "timings": snap.timings,
            "counts": snap.counts,
        }


default_metrics = MetricsRecorder()


__all__ = [
    "MetricSample",
    "MetricsRecorder",
    "MetricsSnapshot",
    "default_metrics",
]
