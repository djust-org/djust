"""
djust Performance Profiler

Provides detailed performance metrics for LiveView operations, including:
- Render times (template rendering, VDOM diffing)
- State synchronization times
- Event handler execution times
- Memory usage tracking
- Network transfer sizes

Usage:
    from djust.profiler import profiler, profile

    # Automatic profiling with decorator
    class MyLiveView(LiveView):
        @profile('expensive_operation')
        def handle_search(self, query):
            ...

    # Manual profiling
    with profiler.profile('custom_operation'):
        do_something()

    # Get all metrics
    metrics = profiler.get_metrics()

    # Enable/disable profiling
    profiler.enable()
    profiler.disable()
"""

import time
import logging
import statistics
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProfileMetric:
    """A single profiling metric with timing statistics."""

    name: str
    count: int = 0
    total_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    last_ms: float = 0.0
    samples: List[float] = field(default_factory=list)
    max_samples: int = 100

    def record(self, duration_ms: float):
        """Record a timing sample."""
        self.count += 1
        self.total_ms += duration_ms
        self.last_ms = duration_ms
        self.min_ms = min(self.min_ms, duration_ms)
        self.max_ms = max(self.max_ms, duration_ms)

        # Keep last N samples for percentile calculations
        self.samples.append(duration_ms)
        if len(self.samples) > self.max_samples:
            self.samples.pop(0)

    @property
    def avg_ms(self) -> float:
        """Average duration in milliseconds."""
        return self.total_ms / self.count if self.count > 0 else 0.0

    @property
    def p50_ms(self) -> float:
        """50th percentile (median) in milliseconds."""
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        return sorted_samples[len(sorted_samples) // 2]

    @property
    def p95_ms(self) -> float:
        """95th percentile in milliseconds."""
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        idx = int(len(sorted_samples) * 0.95)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    @property
    def p99_ms(self) -> float:
        """99th percentile in milliseconds."""
        if not self.samples:
            return 0.0
        sorted_samples = sorted(self.samples)
        idx = int(len(sorted_samples) * 0.99)
        return sorted_samples[min(idx, len(sorted_samples) - 1)]

    @property
    def std_dev_ms(self) -> float:
        """Standard deviation in milliseconds."""
        if len(self.samples) < 2:
            return 0.0
        return statistics.stdev(self.samples)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "count": self.count,
            "total_ms": round(self.total_ms, 3),
            "avg_ms": round(self.avg_ms, 3),
            "min_ms": round(self.min_ms, 3) if self.count > 0 else 0,
            "max_ms": round(self.max_ms, 3),
            "last_ms": round(self.last_ms, 3),
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "p99_ms": round(self.p99_ms, 3),
            "std_dev_ms": round(self.std_dev_ms, 3),
        }

    def reset(self):
        """Reset all metrics."""
        self.count = 0
        self.total_ms = 0.0
        self.min_ms = float("inf")
        self.max_ms = 0.0
        self.last_ms = 0.0
        self.samples.clear()


class DjustProfiler:
    """
    Thread-safe performance profiler for djust operations.

    Tracks timing metrics for various operations and provides
    aggregated statistics for analysis and debugging.
    """

    # Standard operation names
    OP_RENDER = "render"
    OP_DIFF = "vdom_diff"
    OP_STATE_SYNC = "state_sync"
    OP_STATE_LOAD = "state_load"
    OP_STATE_SAVE = "state_save"
    OP_EVENT_HANDLE = "event_handle"
    OP_SERIALIZATION = "serialization"
    OP_COMPRESSION = "compression"
    OP_TEMPLATE_COMPILE = "template_compile"

    def __init__(self):
        self._enabled = False
        self._metrics: Dict[str, ProfileMetric] = {}
        self._lock = threading.Lock()

        # Memory tracking
        self._memory_samples: List[Dict[str, Any]] = []
        self._max_memory_samples = 100

        # Per-request tracking (thread-local)
        self._request_timings = threading.local()

    def enable(self):
        """Enable profiling."""
        self._enabled = True
        logger.info("[Profiler] Profiling enabled")

    def disable(self):
        """Disable profiling."""
        self._enabled = False
        logger.info("[Profiler] Profiling disabled")

    @property
    def is_enabled(self) -> bool:
        """Check if profiling is enabled."""
        return self._enabled

    def record(self, operation: str, duration_ms: float):
        """
        Record a timing metric.

        Args:
            operation: Name of the operation
            duration_ms: Duration in milliseconds
        """
        if not self._enabled:
            return

        with self._lock:
            if operation not in self._metrics:
                self._metrics[operation] = ProfileMetric(name=operation)
            self._metrics[operation].record(duration_ms)

    @contextmanager
    def profile(self, operation: str):
        """
        Context manager for profiling a code block.

        Usage:
            with profiler.profile('my_operation'):
                do_something()
        """
        if not self._enabled:
            yield
            return

        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000
            self.record(operation, duration_ms)

    def start_request(self, request_id: Optional[str] = None):
        """
        Start tracking a new request.

        Stores per-operation timings for the current request in thread-local storage.
        """
        if not self._enabled:
            return

        self._request_timings.timings = {
            "request_id": request_id or str(time.time()),
            "start_time": time.perf_counter(),
            "operations": {},
        }

    def end_request(self) -> Optional[Dict[str, Any]]:
        """
        End request tracking and return timing data.

        Returns:
            Dictionary with request timings, or None if not enabled
        """
        if not self._enabled:
            return None

        timings = getattr(self._request_timings, "timings", None)
        if not timings:
            return None

        total_ms = (time.perf_counter() - timings["start_time"]) * 1000

        return {
            "request_id": timings["request_id"],
            "total_ms": round(total_ms, 3),
            "operations": timings["operations"],
        }

    def record_memory(self, state_bytes: int, metadata: Optional[Dict[str, Any]] = None):
        """
        Record memory usage sample.

        Args:
            state_bytes: Size of state in bytes
            metadata: Optional additional metadata
        """
        if not self._enabled:
            return

        sample = {
            "timestamp": time.time(),
            "state_bytes": state_bytes,
            "state_kb": round(state_bytes / 1024, 2),
            **(metadata or {}),
        }

        with self._lock:
            self._memory_samples.append(sample)
            if len(self._memory_samples) > self._max_memory_samples:
                self._memory_samples.pop(0)

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get all profiling metrics.

        Returns:
            Dictionary with all metrics organized by category
        """
        with self._lock:
            # Organize metrics by category
            render_ops = {}
            state_ops = {}
            event_ops = {}
            other_ops = {}

            for name, metric in self._metrics.items():
                data = metric.to_dict()
                if name.startswith(("render", "vdom", "template")):
                    render_ops[name] = data
                elif name.startswith(("state", "serial", "compress")):
                    state_ops[name] = data
                elif name.startswith("event"):
                    event_ops[name] = data
                else:
                    other_ops[name] = data

            return {
                "enabled": self._enabled,
                "rendering": render_ops,
                "state_management": state_ops,
                "event_handling": event_ops,
                "other": other_ops,
                "memory": {
                    "samples": self._memory_samples[-10:],  # Last 10 samples
                    "total_samples": len(self._memory_samples),
                },
                "summary": self._get_summary(),
            }

    def _get_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        if not self._metrics:
            return {"message": "No metrics recorded yet"}

        total_calls = sum(m.count for m in self._metrics.values())
        total_time_ms = sum(m.total_ms for m in self._metrics.values())

        # Find slowest operations
        slowest = sorted(self._metrics.values(), key=lambda m: m.avg_ms, reverse=True)[:5]

        # Find most frequent operations
        most_frequent = sorted(self._metrics.values(), key=lambda m: m.count, reverse=True)[:5]

        return {
            "total_operations": total_calls,
            "total_time_ms": round(total_time_ms, 3),
            "unique_operations": len(self._metrics),
            "slowest_operations": [{"name": m.name, "avg_ms": round(m.avg_ms, 3)} for m in slowest],
            "most_frequent": [{"name": m.name, "count": m.count} for m in most_frequent],
        }

    def get_metric(self, operation: str) -> Optional[Dict[str, Any]]:
        """Get metrics for a specific operation."""
        with self._lock:
            metric = self._metrics.get(operation)
            if metric:
                return metric.to_dict()
            return None

    def reset(self):
        """Reset all metrics."""
        with self._lock:
            self._metrics.clear()
            self._memory_samples.clear()
        logger.info("[Profiler] Metrics reset")

    def reset_metric(self, operation: str):
        """Reset metrics for a specific operation."""
        with self._lock:
            if operation in self._metrics:
                self._metrics[operation].reset()


# Global profiler instance
profiler = DjustProfiler()


def profile(operation: str):
    """
    Decorator to profile a function or method.

    Usage:
        @profile('my_operation')
        def my_function():
            ...

        class MyLiveView(LiveView):
            @profile('handle_click')
            def handle_click(self, event):
                ...
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not profiler.is_enabled:
                return func(*args, **kwargs)

            start = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                profiler.record(operation, duration_ms)

        return wrapper

    return decorator


class ProfilerMiddleware:
    """
    Django middleware for request-level profiling.

    Add to MIDDLEWARE in settings.py:
        MIDDLEWARE = [
            ...
            'djust.profiler.ProfilerMiddleware',
        ]
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not profiler.is_enabled:
            return self.get_response(request)

        request_id = f"{request.path}:{time.time()}"
        profiler.start_request(request_id)

        response = self.get_response(request)

        request_timing = profiler.end_request()
        if request_timing:
            # Add timing info to response header (useful for debugging)
            response["X-Djust-Profiler-Time"] = str(request_timing.get("total_ms", 0))

        return response


# Auto-enable profiler in DEBUG mode
def auto_configure():
    """Auto-configure profiler based on Django settings."""
    try:
        from django.conf import settings

        if getattr(settings, "DEBUG", False):
            from djust.config import config

            if config.get("profiler_enabled", False):
                profiler.enable()
                logger.info("[Profiler] Auto-enabled in DEBUG mode")
    except Exception:
        pass  # Not in Django context or settings not configured


# Try to auto-configure when module is imported
try:
    auto_configure()
except Exception:
    pass
