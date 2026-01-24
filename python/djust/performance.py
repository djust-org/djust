"""
Performance tracking and profiling for djust debug panel.

This module provides comprehensive timing and performance monitoring for LiveView
operations, including database query tracking, memory profiling, and detailed
timing breakdowns.
"""

import time
import threading
import functools
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from contextlib import contextmanager


@dataclass
class TimingNode:
    """Represents a node in the timing tree."""

    name: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    children: List["TimingNode"] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[Dict[str, str]] = field(default_factory=list)

    def end(self) -> float:
        """End this timing node and calculate duration."""
        self.end_time = time.perf_counter()
        self.duration_ms = (self.end_time - self.start_time) * 1000
        return self.duration_ms

    def add_child(self, name: str, **metadata) -> "TimingNode":
        """Add a child timing node."""
        child = TimingNode(name=name, start_time=time.perf_counter(), metadata=metadata)
        self.children.append(child)
        return child

    def add_warning(self, warning_type: str, message: str, **details):
        """Add a performance warning to this node."""
        self.warnings.append({"type": warning_type, "message": message, **details})

    def to_dict(self) -> Dict[str, Any]:
        """Convert timing tree to dictionary for JSON serialization."""
        result = {
            "name": self.name,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
            "warnings": self.warnings,
        }

        if self.children:
            result["children"] = [child.to_dict() for child in self.children]

        return result


class QueryTracker:
    """Tracks database queries during request execution."""

    def __init__(self):
        self.queries: List[Dict[str, Any]] = []
        self._original_debug_cursor = None
        self._tracking = False

    def start_tracking(self):
        """Start tracking database queries."""
        if self._tracking:
            return

        self._tracking = True
        self.queries = []

        # Hook into Django's query logging
        from django.db import connection
        from django.conf import settings

        # Enable debug mode temporarily
        self._original_debug = settings.DEBUG
        settings.DEBUG = True

        # Store initial query count
        self._initial_query_count = len(connection.queries)

    def stop_tracking(self) -> List[Dict[str, Any]]:
        """Stop tracking and return collected queries."""
        if not self._tracking:
            return []

        from django.db import connection
        from django.conf import settings

        # Get new queries since tracking started
        new_queries = connection.queries[self._initial_query_count :]

        # Process queries
        for query in new_queries:
            sql = query["sql"]
            time_ms = float(query["time"]) * 1000  # Convert to milliseconds

            # Detect common performance issues
            warnings = []

            # N+1 detection (simple heuristic)
            if sql.startswith("SELECT") and "WHERE" in sql and "id" in sql.lower():
                similar_count = sum(
                    1 for q in self.queries if self._is_similar_query(q["sql"], sql)
                )
                if similar_count > 1:
                    warnings.append(
                        {
                            "type": "n_plus_one",
                            "message": f"Possible N+1 query pattern detected ({similar_count} similar queries)",
                        }
                    )

            # Missing index detection
            if "WHERE" in sql and time_ms > 50:
                warnings.append(
                    {"type": "slow_query", "message": f"Slow query detected ({time_ms:.1f}ms)"}
                )

            # Large result set detection
            if sql.startswith("SELECT") and "LIMIT" not in sql.upper():
                warnings.append(
                    {
                        "type": "missing_limit",
                        "message": "Query without LIMIT clause may return large result set",
                    }
                )

            self.queries.append({"sql": sql, "time_ms": time_ms, "warnings": warnings})

        # Restore original debug setting
        settings.DEBUG = self._original_debug
        self._tracking = False

        return self.queries

    def _is_similar_query(self, sql1: str, sql2: str) -> bool:
        """Check if two queries are similar (potential N+1 pattern)."""
        # Simple heuristic: same table and similar structure
        # Remove values for comparison
        import re

        # Remove quoted values and numbers
        pattern = re.compile(r"'[^']*'|\d+")
        sql1_normalized = pattern.sub("?", sql1)
        sql2_normalized = pattern.sub("?", sql2)

        return sql1_normalized == sql2_normalized


class MemoryTracker:
    """Tracks memory usage during request execution."""

    def __init__(self):
        self.initial_memory = None
        self.peak_memory = None
        self.final_memory = None
        self.enabled = False

        # Check if psutil is available
        try:
            import psutil  # noqa: F401

            self.enabled = True
        except ImportError:
            pass

    def start_tracking(self):
        """Start tracking memory usage."""
        if not self.enabled:
            return

        try:
            import psutil
            import os

            process = psutil.Process(os.getpid())
            self.initial_memory = process.memory_info().rss / 1024 / 1024  # MB
            self.peak_memory = self.initial_memory
        except ImportError:
            self.enabled = False

    def update_peak(self):
        """Update peak memory usage."""
        if not self.enabled:
            return

        try:
            import psutil
            import os

            process = psutil.Process(os.getpid())
            current = process.memory_info().rss / 1024 / 1024  # MB
            self.peak_memory = max(self.peak_memory, current)
        except ImportError:
            self.enabled = False

    def stop_tracking(self) -> Dict[str, float]:
        """Stop tracking and return memory statistics."""
        if not self.enabled or self.initial_memory is None:
            return {}

        try:
            import psutil
            import os

            process = psutil.Process(os.getpid())
            self.final_memory = process.memory_info().rss / 1024 / 1024  # MB

            return {
                "initial_mb": round(self.initial_memory, 2),
                "peak_mb": round(self.peak_memory, 2),
                "final_mb": round(self.final_memory, 2),
                "delta_mb": round(self.final_memory - self.initial_memory, 2),
            }
        except ImportError:
            return {}


class PerformanceTracker:
    """Main performance tracking coordinator."""

    _thread_local = threading.local()

    @classmethod
    def get_current(cls) -> Optional["PerformanceTracker"]:
        """Get the current performance tracker for this thread."""
        return getattr(cls._thread_local, "tracker", None)

    @classmethod
    def set_current(cls, tracker: Optional["PerformanceTracker"]):
        """Set the current performance tracker for this thread."""
        cls._thread_local.tracker = tracker

    def __init__(self):
        self.root_node = None
        self.current_node = None
        self.query_tracker = QueryTracker()
        self.memory_tracker = MemoryTracker()
        self.context_size = 0
        self.patch_count = 0

    def start(self, operation: str) -> TimingNode:
        """Start tracking a new operation."""
        node = TimingNode(name=operation, start_time=time.perf_counter())

        if self.root_node is None:
            self.root_node = node
        else:
            if self.current_node:
                self.current_node.children.append(node)

        self.current_node = node
        return node

    @contextmanager
    def track(self, operation: str, **metadata):
        """Context manager for tracking an operation."""
        node = self.start(operation)
        node.metadata.update(metadata)

        # Start query tracking if this is a handler
        if operation == "Event Handler":
            self.query_tracker.start_tracking()
            self.memory_tracker.start_tracking()

        try:
            yield node
        finally:
            node.end()

            # Stop query tracking and add results
            if operation == "Event Handler":
                queries = self.query_tracker.stop_tracking()
                memory_stats = self.memory_tracker.stop_tracking()

                if queries:
                    node.metadata["queries"] = queries
                    node.metadata["query_count"] = len(queries)
                    node.metadata["query_time_ms"] = sum(q["time_ms"] for q in queries)

                    # Add query timing as child node
                    query_node = node.add_child(
                        "Database Queries",
                        count=len(queries),
                        total_ms=node.metadata["query_time_ms"],
                    )
                    query_node.duration_ms = node.metadata["query_time_ms"]

                    # Check for performance issues
                    n_plus_one_count = sum(
                        1
                        for q in queries
                        if any(w["type"] == "n_plus_one" for w in q.get("warnings", []))
                    )
                    if n_plus_one_count > 0:
                        node.add_warning(
                            "n_plus_one",
                            f"N+1 query pattern detected ({n_plus_one_count} queries)",
                            query_count=n_plus_one_count,
                        )

                    slow_queries = [q for q in queries if q["time_ms"] > 50]
                    if slow_queries:
                        node.add_warning(
                            "slow_queries",
                            f"{len(slow_queries)} slow queries (>50ms)",
                            queries=slow_queries,
                        )

                if memory_stats:
                    node.metadata["memory"] = memory_stats

                    # Memory warning
                    if memory_stats.get("delta_mb", 0) > 10:
                        node.add_warning(
                            "memory_usage",
                            f'High memory usage: {memory_stats["delta_mb"]}MB increase',
                            memory_stats=memory_stats,
                        )

            # Performance warnings based on duration
            if node.duration_ms and node.duration_ms > 100:
                if operation == "Event Handler" and node.duration_ms > 200:
                    node.add_warning(
                        "slow_handler",
                        f"Slow event handler: {node.duration_ms:.1f}ms",
                        threshold=200,
                    )
                elif operation == "Template Render" and node.duration_ms > 50:
                    node.add_warning(
                        "slow_template",
                        f"Slow template rendering: {node.duration_ms:.1f}ms",
                        threshold=50,
                    )

            # Move back to parent node
            if self.current_node and self.current_node.name == operation:
                # Find parent node
                parent = self._find_parent_node(self.root_node, self.current_node)
                self.current_node = parent

    def _find_parent_node(self, root: TimingNode, target: TimingNode) -> Optional[TimingNode]:
        """Find the parent of a target node in the tree."""
        if root == target:
            return None

        for child in root.children:
            if child == target:
                return root
            parent = self._find_parent_node(child, target)
            if parent:
                return parent

        return None

    def track_context_size(self, context: Dict[str, Any]):
        """Track the size of the context data."""
        import sys

        self.context_size = sys.getsizeof(str(context))  # Rough estimate

    def track_patches(self, patch_count: int, patches: Optional[List[Dict[str, Any]]] = None):
        """Track the number of patches generated and provide actionable recommendations.

        Args:
            patch_count: The number of patches generated
            patches: Optional list of patch dictionaries for detailed analysis
        """
        self.patch_count = patch_count

        # Warning for excessive patches with detailed recommendations
        if patch_count > 20 and self.current_node:
            recommendations = self._generate_patch_recommendations(patch_count, patches)
            self.current_node.add_warning(
                "excessive_patches",
                f"Excessive Patches: {patch_count} patches generated",
                patch_count=patch_count,
                recommendations=recommendations,
                docs_url="https://djust.dev/docs/performance/lists",
            )

    def _generate_patch_recommendations(
        self, patch_count: int, patches: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, str]]:
        """Generate specific recommendations based on patch analysis.

        Args:
            patch_count: The number of patches generated
            patches: Optional list of patch dictionaries for detailed analysis

        Returns:
            List of recommendation dictionaries with 'title' and 'description' keys
        """
        recommendations = []

        if patches:
            # Analyze patch types
            insert_count = sum(1 for p in patches if p.get("type") == "InsertChild")
            remove_count = sum(1 for p in patches if p.get("type") == "RemoveChild")
            replace_count = sum(1 for p in patches if p.get("type") == "Replace")
            move_count = sum(1 for p in patches if p.get("type") == "MoveChild")

            # High insert/remove ratio suggests list without keys
            if insert_count > 10 or remove_count > 10:
                recommendations.append(
                    {
                        "title": "Add keys to list items",
                        "description": (
                            f"Detected {insert_count} inserts and {remove_count} removes. "
                            'Add data-key="{{ item.id }}" to list items for efficient reordering.'
                        ),
                        "code_example": '<div class="item" data-key="{{ item.id }}">{{ item.name }}</div>',
                        "priority": "high",
                    }
                )

            # Many replace patches suggest full re-renders
            if replace_count > 5:
                recommendations.append(
                    {
                        "title": "Reduce full node replacements",
                        "description": (
                            f"Detected {replace_count} full node replacements. "
                            "Consider restructuring templates to minimize DOM changes."
                        ),
                        "priority": "medium",
                    }
                )

            # No move patches but high churn suggests missing keys
            if move_count == 0 and (insert_count > 5 or remove_count > 5):
                recommendations.append(
                    {
                        "title": "Enable keyed diffing",
                        "description": (
                            "List reordering is creating new nodes instead of moving existing ones. "
                            "Add unique data-key attributes to enable efficient MoveChild operations."
                        ),
                        "priority": "high",
                    }
                )

        # General recommendations based on patch count
        if patch_count > 100:
            recommendations.append(
                {
                    "title": "Use pagination",
                    "description": (
                        f"{patch_count} patches is very high. "
                        "Paginate lists to 50 items per page to reduce DOM size and patch count."
                    ),
                    "code_example": "{% include 'djust/pagination.html' with page_obj=page_obj %}",
                    "priority": "high",
                }
            )
        elif patch_count > 50:
            recommendations.append(
                {
                    "title": "Consider JIT serialization",
                    "description": (
                        "Use the JIT serialization pattern to defer data loading until needed. "
                        "This reduces initial render size and improves responsiveness."
                    ),
                    "code_example": "@property\ndef items(self):\n    return self._items_cache or self._load_items()",
                    "priority": "medium",
                }
            )

        # If no specific recommendations, add general guidance
        if not recommendations:
            recommendations.append(
                {
                    "title": "Review list rendering",
                    "description": (
                        f"{patch_count} patches generated. Common causes: "
                        "(1) Lists without data-key attributes, "
                        "(2) Large datasets without pagination, "
                        "(3) Nested structures with frequent updates."
                    ),
                    "priority": "medium",
                }
            )

        return recommendations

    def get_summary(self) -> Dict[str, Any]:
        """Get complete performance summary."""
        if not self.root_node:
            return {}

        return {
            "timing": self.root_node.to_dict(),
            "total_ms": self.root_node.duration_ms,
            "context_size_bytes": self.context_size,
            "patch_count": self.patch_count,
        }


# Decorator for tracking function performance
def track_performance(operation: str):
    """Decorator to track performance of a function."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            tracker = PerformanceTracker.get_current()
            if not tracker:
                return func(*args, **kwargs)

            with tracker.track(operation):
                return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            tracker = PerformanceTracker.get_current()
            if not tracker:
                return await func(*args, **kwargs)

            with tracker.track(operation):
                return await func(*args, **kwargs)

        # Return appropriate wrapper based on function type
        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return wrapper

    return decorator
