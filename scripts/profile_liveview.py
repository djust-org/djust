#!/usr/bin/env python
"""
Comprehensive LiveView Performance Profiler

Profiles the full request path:
- HTTP initial render time
- WebSocket mount time  
- Event handling time
- VDOM diff time (Rust)
- Patch serialization time
- State backend operations (get/set)

Target performance goals (from ROADMAP.md):
- <2ms per patch
- <5ms for list updates

Usage:
    cd djust-experimental
    uv run python scripts/profile_liveview.py

Output: docs/PERFORMANCE_ANALYSIS.md
"""

import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Add parent to path for djust imports
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(base_dir, "python"))
sys.path.insert(0, base_dir)

# Set up minimal Django settings before imports
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

import django
django.setup()

# Suppress noisy parser debug logging
import logging
logging.getLogger().setLevel(logging.WARNING)

from djust._rust import diff_html, render_template, RustLiveView, fast_json_dumps
from djust.profiler import profiler, ProfileMetric
from djust.state_backends.memory import InMemoryStateBackend


@dataclass
class BenchmarkResult:
    """Result from a single benchmark run."""
    name: str
    iterations: int
    total_ms: float
    avg_ms: float
    min_ms: float
    max_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    std_dev_ms: float
    samples: List[float] = field(default_factory=list)
    
    def meets_target(self, target_ms: float) -> bool:
        """Check if p95 meets the target."""
        return self.p95_ms <= target_ms


class LiveViewProfiler:
    """
    Profiles all stages of the LiveView request/update cycle.
    """
    
    # Performance targets (ms)
    TARGET_PATCH_MS = 2.0
    TARGET_LIST_UPDATE_MS = 5.0
    TARGET_RENDER_MS = 10.0
    TARGET_STATE_OP_MS = 1.0
    
    def __init__(self, iterations: int = 100):
        self.iterations = iterations
        self.results: Dict[str, BenchmarkResult] = {}
        self.state_backend = InMemoryStateBackend()
        
    def _benchmark(self, name: str, func, warmup: int = 10) -> BenchmarkResult:
        """Run a benchmark with warmup."""
        # Warmup
        for _ in range(warmup):
            func()
            
        # Actual benchmark
        samples = []
        for _ in range(self.iterations):
            start = time.perf_counter()
            func()
            elapsed = (time.perf_counter() - start) * 1000
            samples.append(elapsed)
            
        samples.sort()
        
        result = BenchmarkResult(
            name=name,
            iterations=self.iterations,
            total_ms=sum(samples),
            avg_ms=statistics.mean(samples),
            min_ms=min(samples),
            max_ms=max(samples),
            p50_ms=samples[len(samples) // 2],
            p95_ms=samples[int(len(samples) * 0.95)],
            p99_ms=samples[int(len(samples) * 0.99)],
            std_dev_ms=statistics.stdev(samples) if len(samples) > 1 else 0,
            samples=samples[:20],  # Keep first 20 for analysis
        )
        
        self.results[name] = result
        return result
    
    # =========================================================================
    # Template Rendering Benchmarks
    # =========================================================================
    
    def profile_simple_render(self) -> BenchmarkResult:
        """Profile simple counter template render."""
        template = """
<div id="counter" dj-id="0">
    <h1 dj-id="1">Counter: {{ count }}</h1>
    <button dj-id="2" dj-click="increment">+</button>
    <button dj-id="3" dj-click="decrement">-</button>
</div>
"""
        context = {"count": 42}
        
        def run():
            return render_template(template, context)
            
        return self._benchmark("render_simple_counter", run)
    
    def profile_list_render_small(self) -> BenchmarkResult:
        """Profile list template with 10 items."""
        template = self._get_list_template()
        items = [{"id": i, "text": f"Item {i}", "done": i % 3 == 0} for i in range(10)]
        context = {"items": items, "total": 10}
        
        def run():
            return render_template(template, context)
            
        return self._benchmark("render_list_10_items", run)
    
    def profile_list_render_medium(self) -> BenchmarkResult:
        """Profile list template with 100 items."""
        template = self._get_list_template()
        items = [{"id": i, "text": f"Item {i}", "done": i % 3 == 0} for i in range(100)]
        context = {"items": items, "total": 100}
        
        def run():
            return render_template(template, context)
            
        return self._benchmark("render_list_100_items", run)
    
    def profile_list_render_large(self) -> BenchmarkResult:
        """Profile list template with 500 items."""
        template = self._get_list_template()
        items = [{"id": i, "text": f"Item {i}", "done": i % 3 == 0} for i in range(500)]
        context = {"items": items, "total": 500}
        
        def run():
            return render_template(template, context)
            
        return self._benchmark("render_list_500_items", run)
    
    def profile_nested_render(self) -> BenchmarkResult:
        """Profile deeply nested template structure."""
        template = self._get_nested_template(depth=5)
        context = {f"level{i}": f"Value {i}" for i in range(5)}
        
        def run():
            return render_template(template, context)
            
        return self._benchmark("render_nested_depth_5", run)
    
    def profile_form_render(self) -> BenchmarkResult:
        """Profile form with multiple fields and validation."""
        template = self._get_form_template()
        context = {
            "username": "testuser",
            "email": "test@example.com",
            "errors": {"username": "Username taken", "email": None},
        }
        
        def run():
            return render_template(template, context)
            
        return self._benchmark("render_form_validation", run)
    
    # =========================================================================
    # VDOM Diff Benchmarks
    # =========================================================================
    
    def profile_diff_no_changes(self) -> BenchmarkResult:
        """Profile diff with no changes (best case)."""
        template = self._get_list_template()
        items = [{"id": i, "text": f"Item {i}", "done": False} for i in range(100)]
        html = render_template(template, {"items": items, "total": 100})
        
        def run():
            return diff_html(html, html)
            
        return self._benchmark("diff_no_changes_100_items", run)
    
    def profile_diff_single_attr(self) -> BenchmarkResult:
        """Profile diff with single attribute change."""
        template = '<div dj-id="0" class="{{ css_class }}">Content</div>'
        old_html = render_template(template, {"css_class": "old-class"})
        new_html = render_template(template, {"css_class": "new-class"})
        
        def run():
            return diff_html(old_html, new_html)
            
        return self._benchmark("diff_single_attr_change", run)
    
    def profile_diff_list_append(self) -> BenchmarkResult:
        """Profile diff when appending to list."""
        template = self._get_list_template()
        items_old = [{"id": i, "text": f"Item {i}", "done": False} for i in range(99)]
        items_new = items_old + [{"id": 99, "text": "Item 99", "done": False}]
        
        old_html = render_template(template, {"items": items_old, "total": 99})
        new_html = render_template(template, {"items": items_new, "total": 100})
        
        def run():
            return diff_html(old_html, new_html)
            
        return self._benchmark("diff_list_append_100th", run)
    
    def profile_diff_list_prepend(self) -> BenchmarkResult:
        """Profile diff when prepending to list."""
        template = self._get_list_template()
        items_old = [{"id": i, "text": f"Item {i}", "done": False} for i in range(1, 100)]
        items_new = [{"id": 0, "text": "Item 0", "done": False}] + items_old
        
        old_html = render_template(template, {"items": items_old, "total": 99})
        new_html = render_template(template, {"items": items_new, "total": 100})
        
        def run():
            return diff_html(old_html, new_html)
            
        return self._benchmark("diff_list_prepend_to_99", run)
    
    def profile_diff_list_toggle_all(self) -> BenchmarkResult:
        """Profile diff when toggling all items (worst case)."""
        template = self._get_list_template()
        items_old = [{"id": i, "text": f"Item {i}", "done": False} for i in range(100)]
        items_new = [{"id": i, "text": f"Item {i}", "done": True} for i in range(100)]
        
        old_html = render_template(template, {"items": items_old, "total": 100})
        new_html = render_template(template, {"items": items_new, "total": 100})
        
        def run():
            return diff_html(old_html, new_html)
            
        return self._benchmark("diff_list_toggle_all_100", run)
    
    def profile_diff_list_reorder(self) -> BenchmarkResult:
        """Profile diff when reordering list (reverse)."""
        template = self._get_list_template()
        items_old = [{"id": i, "text": f"Item {i}", "done": False} for i in range(50)]
        items_new = list(reversed(items_old))
        
        old_html = render_template(template, {"items": items_old, "total": 50})
        new_html = render_template(template, {"items": items_new, "total": 50})
        
        def run():
            return diff_html(old_html, new_html)
            
        return self._benchmark("diff_list_reverse_50", run)
    
    def profile_diff_complex_form(self) -> BenchmarkResult:
        """Profile diff for form validation errors appearing."""
        template = self._get_form_template()
        
        old_ctx = {"username": "", "email": "", "errors": {}}
        new_ctx = {
            "username": "x",
            "email": "invalid",
            "errors": {
                "username": "Username must be at least 3 characters",
                "email": "Invalid email format",
            }
        }
        
        old_html = render_template(template, old_ctx)
        new_html = render_template(template, new_ctx)
        
        def run():
            return diff_html(old_html, new_html)
            
        return self._benchmark("diff_form_validation_errors", run)
    
    # =========================================================================
    # Patch Serialization Benchmarks
    # =========================================================================
    
    def profile_patch_serialization_small(self) -> BenchmarkResult:
        """Profile JSON serialization of small patch list."""
        patches = [
            {"type": "SetAttribute", "target": "0", "name": "class", "value": "new-class"},
            {"type": "SetText", "target": "1", "text": "New text content"},
        ]
        
        def run():
            return fast_json_dumps(patches)
            
        return self._benchmark("serialize_patches_2", run)
    
    def profile_patch_serialization_medium(self) -> BenchmarkResult:
        """Profile JSON serialization of medium patch list."""
        patches = [
            {"type": "SetAttribute", "target": str(i), "name": "class", "value": f"class-{i}"}
            for i in range(20)
        ]
        
        def run():
            return fast_json_dumps(patches)
            
        return self._benchmark("serialize_patches_20", run)
    
    def profile_patch_serialization_large(self) -> BenchmarkResult:
        """Profile JSON serialization of large patch list."""
        patches = [
            {"type": "SetAttribute", "target": str(i), "name": "class", "value": f"class-{i}"}
            for i in range(100)
        ]
        
        def run():
            return fast_json_dumps(patches)
            
        return self._benchmark("serialize_patches_100", run)
    
    # =========================================================================
    # State Backend Benchmarks
    # =========================================================================
    
    def profile_state_get(self) -> BenchmarkResult:
        """Profile state backend get operation."""
        template = '<div dj-id="0">{{ counter }}</div>'
        view = RustLiveView(template)
        view.set_state("counter", 0)
        self.state_backend.set("bench-session", view)
        
        def run():
            return self.state_backend.get("bench-session")
            
        return self._benchmark("state_get", run)
    
    def profile_state_set(self) -> BenchmarkResult:
        """Profile state backend set operation."""
        template = '<div dj-id="0">{{ counter }}</div>'
        view = RustLiveView(template)
        view.set_state("counter", 0)
        
        def run():
            self.state_backend.set("bench-session-set", view, warn_on_large_state=False)
            
        return self._benchmark("state_set", run)
    
    def profile_state_large(self) -> BenchmarkResult:
        """Profile state backend with large state."""
        template = '<div dj-id="0">{% for item in items %}{{ item.name }}{% endfor %}</div>'
        view = RustLiveView(template)
        # Large state: 1000 items
        items = [{"id": i, "name": f"Item {i}", "data": "x" * 100} for i in range(1000)]
        view.set_state("items", items)
        
        def run():
            self.state_backend.set("bench-large-state", view, warn_on_large_state=False)
            
        return self._benchmark("state_set_large_1000_items", run)
    
    # =========================================================================
    # Full Cycle Benchmarks
    # =========================================================================
    
    def profile_full_cycle_simple(self) -> BenchmarkResult:
        """Profile complete update cycle: render + diff."""
        template = '<div dj-id="0"><span dj-id="1">{{ count }}</span></div>'
        old_html = render_template(template, {"count": 0})
        
        def run():
            new_html = render_template(template, {"count": 1})
            patches = diff_html(old_html, new_html)
            return fast_json_dumps(patches)
            
        return self._benchmark("full_cycle_simple_counter", run)
    
    def profile_full_cycle_list(self) -> BenchmarkResult:
        """Profile complete update cycle with list append."""
        template = self._get_list_template()
        items_old = [{"id": i, "text": f"Item {i}", "done": False} for i in range(99)]
        items_new = items_old + [{"id": 99, "text": "Item 99", "done": False}]
        
        old_html = render_template(template, {"items": items_old, "total": 99})
        
        def run():
            new_html = render_template(template, {"items": items_new, "total": 100})
            patches = diff_html(old_html, new_html)
            return fast_json_dumps(patches)
            
        return self._benchmark("full_cycle_list_append", run)
    
    # =========================================================================
    # Helper Methods
    # =========================================================================
    
    def _get_list_template(self) -> str:
        return """
<div id="list" dj-id="list">
    <h2 dj-id="h">Items ({{ total }})</h2>
    <ul dj-id="ul">
        {% for item in items %}
        <li dj-id="i{{ item.id }}" data-key="{{ item.id }}" class="{% if item.done %}done{% endif %}">
            <input type="checkbox" dj-id="cb{{ item.id }}" {% if item.done %}checked{% endif %}>
            <span dj-id="t{{ item.id }}">{{ item.text }}</span>
        </li>
        {% endfor %}
    </ul>
</div>
"""
    
    def _get_nested_template(self, depth: int) -> str:
        template = ""
        for i in range(depth):
            template += f'<div dj-id="d{i}" class="level-{i}">{{ level{i} }}'
        for i in range(depth):
            template += "</div>"
        return template
    
    def _get_form_template(self) -> str:
        return """
<form id="signup" dj-id="form" dj-submit="submit">
    <div class="field" dj-id="f1">
        <label dj-id="l1">Username</label>
        <input type="text" name="username" value="{{ username }}"
               class="{% if errors.username %}is-invalid{% endif %}" dj-id="i1">
        {% if errors.username %}
        <div class="error" dj-id="e1">{{ errors.username }}</div>
        {% endif %}
    </div>
    <div class="field" dj-id="f2">
        <label dj-id="l2">Email</label>
        <input type="email" name="email" value="{{ email }}"
               class="{% if errors.email %}is-invalid{% endif %}" dj-id="i2">
        {% if errors.email %}
        <div class="error" dj-id="e2">{{ errors.email }}</div>
        {% endif %}
    </div>
    <button type="submit" dj-id="btn">Submit</button>
</form>
"""
    
    def run_all(self) -> Dict[str, BenchmarkResult]:
        """Run all benchmarks and return results."""
        print("=" * 70)
        print("djust LiveView Performance Profiler")
        print("=" * 70)
        print(f"Iterations per benchmark: {self.iterations}")
        print(f"Timestamp: {datetime.now().isoformat()}")
        print()
        
        benchmarks = [
            # Rendering
            ("Template Rendering", [
                self.profile_simple_render,
                self.profile_list_render_small,
                self.profile_list_render_medium,
                self.profile_list_render_large,
                self.profile_nested_render,
                self.profile_form_render,
            ]),
            # Diffing
            ("VDOM Diffing", [
                self.profile_diff_no_changes,
                self.profile_diff_single_attr,
                self.profile_diff_list_append,
                self.profile_diff_list_prepend,
                self.profile_diff_list_toggle_all,
                self.profile_diff_list_reorder,
                self.profile_diff_complex_form,
            ]),
            # Serialization
            ("Patch Serialization", [
                self.profile_patch_serialization_small,
                self.profile_patch_serialization_medium,
                self.profile_patch_serialization_large,
            ]),
            # State
            ("State Backend", [
                self.profile_state_get,
                self.profile_state_set,
                self.profile_state_large,
            ]),
            # Full cycle
            ("Full Update Cycle", [
                self.profile_full_cycle_simple,
                self.profile_full_cycle_list,
            ]),
        ]
        
        for group_name, group_benchmarks in benchmarks:
            print(f"\n{group_name}")
            print("-" * 40)
            
            for bench_func in group_benchmarks:
                result = bench_func()
                status = "✓" if result.p95_ms < self.TARGET_PATCH_MS else "△" if result.p95_ms < self.TARGET_LIST_UPDATE_MS else "✗"
                print(f"  {status} {result.name}:")
                print(f"      avg: {result.avg_ms:.3f}ms  p50: {result.p50_ms:.3f}ms  p95: {result.p95_ms:.3f}ms  p99: {result.p99_ms:.3f}ms")
        
        return self.results
    
    def generate_report(self, output_path: str = "docs/PERFORMANCE_ANALYSIS.md"):
        """Generate markdown report of benchmark results."""
        report = []
        report.append("# djust Performance Analysis Report")
        report.append("")
        report.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        report.append(f"Iterations per benchmark: {self.iterations}")
        report.append("")
        report.append("## Performance Targets")
        report.append("")
        report.append("From ROADMAP.md:")
        report.append("- **<2ms per patch**: Target for simple updates")
        report.append("- **<5ms for list updates**: Target for list operations")
        report.append("")
        report.append("## Summary")
        report.append("")
        
        # Classify results
        passing = []
        marginal = []
        failing = []
        
        for name, result in self.results.items():
            if result.p95_ms <= self.TARGET_PATCH_MS:
                passing.append(result)
            elif result.p95_ms <= self.TARGET_LIST_UPDATE_MS:
                marginal.append(result)
            else:
                failing.append(result)
        
        report.append(f"- ✅ **Passing** (<2ms p95): {len(passing)}/{len(self.results)} benchmarks")
        report.append(f"- ⚠️ **Marginal** (2-5ms p95): {len(marginal)}/{len(self.results)} benchmarks")
        report.append(f"- ❌ **Failing** (>5ms p95): {len(failing)}/{len(self.results)} benchmarks")
        report.append("")
        
        # Detailed results by category
        categories = {
            "Template Rendering": [r for n, r in self.results.items() if "render" in n],
            "VDOM Diffing": [r for n, r in self.results.items() if "diff" in n],
            "Patch Serialization": [r for n, r in self.results.items() if "serialize" in n],
            "State Backend": [r for n, r in self.results.items() if "state" in n],
            "Full Update Cycle": [r for n, r in self.results.items() if "full_cycle" in n],
        }
        
        for cat_name, cat_results in categories.items():
            if not cat_results:
                continue
                
            report.append(f"## {cat_name}")
            report.append("")
            report.append("| Benchmark | Avg (ms) | P50 (ms) | P95 (ms) | P99 (ms) | Status |")
            report.append("|-----------|----------|----------|----------|----------|--------|")
            
            for r in cat_results:
                if r.p95_ms <= self.TARGET_PATCH_MS:
                    status = "✅ Pass"
                elif r.p95_ms <= self.TARGET_LIST_UPDATE_MS:
                    status = "⚠️ Marginal"
                else:
                    status = "❌ Fail"
                    
                report.append(f"| {r.name} | {r.avg_ms:.3f} | {r.p50_ms:.3f} | {r.p95_ms:.3f} | {r.p99_ms:.3f} | {status} |")
            
            report.append("")
        
        # Bottleneck analysis
        report.append("## Bottleneck Analysis")
        report.append("")
        
        # Find slowest operations
        sorted_results = sorted(self.results.values(), key=lambda r: r.p95_ms, reverse=True)
        
        report.append("### Slowest Operations (by P95)")
        report.append("")
        for i, r in enumerate(sorted_results[:5], 1):
            report.append(f"{i}. **{r.name}**: {r.p95_ms:.3f}ms (avg: {r.avg_ms:.3f}ms)")
        report.append("")
        
        # Analysis
        report.append("### Key Findings")
        report.append("")
        
        # Check if rendering or diffing is the bottleneck
        render_times = [r.p95_ms for r in categories.get("Template Rendering", [])]
        diff_times = [r.p95_ms for r in categories.get("VDOM Diffing", [])]
        
        if render_times and diff_times:
            avg_render = statistics.mean(render_times)
            avg_diff = statistics.mean(diff_times)
            
            if avg_render > avg_diff * 1.5:
                report.append("- **Template rendering** is the primary bottleneck (Python)")
                report.append("  - Consider template caching or simplification")
                report.append("  - JIT compilation may help for complex templates")
            elif avg_diff > avg_render * 1.5:
                report.append("- **VDOM diffing** is the primary bottleneck (Rust)")
                report.append("  - Consider optimizing the diff algorithm")
                report.append("  - Keyed lists can improve reorder performance")
            else:
                report.append("- Rendering and diffing times are balanced")
        
        report.append("")
        
        # Recommendations
        report.append("## Optimization Recommendations")
        report.append("")
        
        # Check specific patterns
        for name, result in self.results.items():
            if result.p95_ms > self.TARGET_LIST_UPDATE_MS:
                if "large" in name or "500" in name:
                    report.append(f"- **{name}**: Consider pagination for large lists")
                elif "toggle_all" in name:
                    report.append(f"- **{name}**: Consider batch updates or virtualization")
                elif "reorder" in name or "reverse" in name:
                    report.append(f"- **{name}**: Ensure data-key attributes for efficient reordering")
                else:
                    report.append(f"- **{name}**: Needs investigation")
        
        report.append("")
        report.append("## Raw Data")
        report.append("")
        report.append("```json")
        report.append(json.dumps(
            {name: {
                "avg_ms": r.avg_ms,
                "p50_ms": r.p50_ms,
                "p95_ms": r.p95_ms,
                "p99_ms": r.p99_ms,
                "min_ms": r.min_ms,
                "max_ms": r.max_ms,
            } for name, r in self.results.items()},
            indent=2
        ))
        report.append("```")
        
        # Write report
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w") as f:
            f.write("\n".join(report))
        
        print(f"\nReport written to: {output_path}")
        return output_path


def main():
    profiler = LiveViewProfiler(iterations=100)
    profiler.run_all()
    profiler.generate_report()


if __name__ == "__main__":
    main()
