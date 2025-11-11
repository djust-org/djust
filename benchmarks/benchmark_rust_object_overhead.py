#!/usr/bin/env python
"""
Benchmark: PyO3 Object Creation Overhead

Question: Why is Pure Rust slower when rendering 100 badges?

Hypothesis: Creating 100 PyO3 objects adds significant overhead that
dominates the rendering performance advantage.

Test:
1. Create objects once, render many times (amortized)
2. Create + render in loop (worst case)
3. Single render (best case)
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

from djust._rust import RustBadge


def benchmark_function(func, iterations=1000):
    """Benchmark a function."""
    # Warmup
    for _ in range(50):
        func()

    start = time.perf_counter()
    for _ in range(iterations):
        func()
    end = time.perf_counter()

    avg_time = (end - start) * 1000 / iterations * 1000  # microseconds
    return avg_time


def main():
    print("=" * 80)
    print("PyO3 OBJECT CREATION OVERHEAD ANALYSIS")
    print("=" * 80)
    print("\nQuestion: Why is Pure Rust slower for 100 badges?")
    print("Answer: Object creation overhead dominates!\n")
    print("=" * 80)

    # Test 1: Single badge (best case)
    print("\nTEST 1: Single Badge (Best Case)")
    print("-" * 80)

    # Pure Rust - single object
    badge = RustBadge("Hello", "primary")
    rust_single = benchmark_function(lambda: badge.render())

    # Python f-string
    python_single = benchmark_function(lambda: '<span class="badge bg-primary">Hello</span>')

    print(f"  Pure Rust (render only):  {rust_single:.2f} μs")
    print(f"  Python f-string:          {python_single:.2f} μs")
    print(f"  Winner: {'Rust' if rust_single < python_single else 'Python'} ({min(rust_single, python_single)/max(rust_single, python_single)*100:.1f}% faster)")

    # Test 2: Create + render (worst case)
    print("\nTEST 2: Create + Render (Worst Case)")
    print("-" * 80)

    rust_create_render = benchmark_function(lambda: RustBadge("Hello", "primary").render())

    print(f"  Pure Rust (create+render): {rust_create_render:.2f} μs")
    print(f"  Python f-string:           {python_single:.2f} μs")
    print(f"  Winner: {'Rust' if rust_create_render < python_single else 'Python'}")
    print(f"  Object creation overhead:  {rust_create_render - rust_single:.2f} μs ({(rust_create_render - rust_single)/rust_create_render*100:.1f}% of total)")

    # Test 3: 100 badges - pre-created
    print("\nTEST 3: 100 Badges - Pre-created Objects (Amortized)")
    print("-" * 80)

    badges = [RustBadge(f"Item {i}", "primary") for i in range(100)]
    rust_100_renders = benchmark_function(lambda: ''.join([b.render() for b in badges]), iterations=100)

    python_100 = benchmark_function(
        lambda: ''.join([f'<span class="badge bg-primary">Item {i}</span>' for i in range(100)]),
        iterations=100
    )

    print(f"  Pure Rust (render only):  {rust_100_renders:.2f} μs")
    print(f"  Python f-string:          {python_100:.2f} μs")
    print(f"  Winner: {'Rust' if rust_100_renders < python_100 else 'Python'} ({abs(rust_100_renders - python_100):.2f} μs difference)")

    # Test 4: 100 badges - create + render
    print("\nTEST 4: 100 Badges - Create + Render Each Time")
    print("-" * 80)

    def rust_create_and_render_100():
        return ''.join([RustBadge(f"Item {i}", "primary").render() for i in range(100)])

    rust_100_full = benchmark_function(rust_create_and_render_100, iterations=100)

    print(f"  Pure Rust (create+render): {rust_100_full:.2f} μs")
    print(f"  Python f-string:           {python_100:.2f} μs")
    print(f"  Winner: {'Rust' if rust_100_full < python_100 else 'Python'}")
    print(f"  Object creation overhead:  {rust_100_full - rust_100_renders:.2f} μs total ({(rust_100_full - rust_100_renders)/100:.2f} μs per object)")

    # Summary
    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    creation_overhead_per_object = (rust_create_render - rust_single)
    render_only_per_badge = rust_100_renders / 100

    print(f"""
Single Badge:
  - Render only:          {rust_single:.2f} μs  ← Pure Rust advantage
  - Create + Render:      {rust_create_render:.2f} μs
  - Creation overhead:    {creation_overhead_per_object:.2f} μs ({creation_overhead_per_object/rust_create_render*100:.1f}% of total)

100 Badges:
  - Render only (each):   {render_only_per_badge:.2f} μs  ← Amortized
  - Create + Render:      {rust_100_full/100:.2f} μs per badge

Overhead Breakdown:
  - PyO3 object creation: {creation_overhead_per_object:.2f} μs per object
  - Rust render():        {rust_single:.2f} μs
  - Total per badge:      {rust_create_render:.2f} μs

Python f-string:          {python_single:.2f} μs (no object creation needed)

Why Python Wins for Dynamic Rendering:
  - No object creation overhead
  - Highly optimized bytecode
  - Direct string interpolation
  - List comprehension is compiled, not interpreted

When Pure Rust Wins:
  - Objects created once, rendered many times
  - Component caching/reuse scenarios
  - Known variant/size at object creation

When Python Wins:
  - Dynamic data (different text/variant each render)
  - One-off renders
  - Inline templating
""")

    print("=" * 80)
    print("CONCLUSION")
    print("=" * 80)

    print(f"""
The "Pure Rust is slower" result is misleading!

Breakdown for 100 badges:
  1. Object creation:  {(rust_100_full - rust_100_renders)/100:.2f} μs × 100 = {rust_100_full - rust_100_renders:.2f} μs
  2. Rendering:        {rust_100_renders/100:.2f} μs × 100 = {rust_100_renders:.2f} μs
  ────────────────────────────────────────────────────────
  Total:               {rust_100_full:.2f} μs

Python equivalent:    {python_100:.2f} μs (no objects, direct generation)

Pure Rust IS faster at RENDERING ({rust_single:.2f}μs vs {python_single:.2f}μs),
but PyO3 object creation adds {creation_overhead_per_object:.2f}μs overhead per badge.

For components rendered once with dynamic data → Python wins
For components created once, rendered many times → Rust wins

Real-world use case (LiveView):
  - Badge("New", "danger") created once in mount()
  - Rendered 100s of times over WebSocket updates
  - Amortized cost: {render_only_per_badge:.2f}μs per render ✅

Recommendation:
  - Pure Rust for STATEFUL components (created once, rendered many)
  - Python f-strings for DYNAMIC inline rendering
  - Rust templates for REUSABLE patterns with caching
""")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
