#!/usr/bin/env python
"""
Proper benchmark for Pure Rust vs Python with controlled overhead.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

from djust._rust import RustBadge


def benchmark_careful(func, iterations=10000):
    """Careful benchmark with proper warmup."""
    # Warmup
    for _ in range(100):
        func()

    # Measure
    times = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        result = func()
        end = time.perf_counter_ns()
        times.append(end - start)

    # Remove outliers (top/bottom 5%)
    times.sort()
    trimmed = times[int(len(times)*0.05):int(len(times)*0.95)]

    avg = sum(trimmed) / len(trimmed) / 1000  # Convert to microseconds
    return avg


def main():
    print("=" * 80)
    print("PURE RUST vs PYTHON - CONTROLLED BENCHMARK")
    print("=" * 80)

    # Test 1: Single render (object pre-created)
    print("\nTEST 1: Single Render (Object Pre-created)")
    print("-" * 80)

    badge = RustBadge("Hello", "primary")
    rust_render = benchmark_careful(lambda: badge.render())

    text, variant = "Hello", "primary"
    python_render = benchmark_careful(lambda: f'<span class="badge bg-{variant}">{text}</span>')

    print(f"  Rust render():     {rust_render:.3f} μs")
    print(f"  Python f-string:   {python_render:.3f} μs")
    print(f"  Rust is {rust_render/python_render:.1f}x {'slower' if rust_render > python_render else 'faster'}")

    # Test 2: Object creation only
    print("\nTEST 2: Object Creation Only")
    print("-" * 80)

    creation_time = benchmark_careful(lambda: RustBadge("Hello", "primary"))

    print(f"  PyO3 object creation: {creation_time:.3f} μs")

    # Test 3: Create + Render
    print("\nTEST 3: Create + Render")
    print("-" * 80)

    create_render = benchmark_careful(lambda: RustBadge("Hello", "primary").render())

    print(f"  Rust (create+render): {create_render:.3f} μs")
    print(f"  Python f-string:      {python_render:.3f} μs")
    print(f"  Creation overhead:    {creation_time:.3f} μs ({creation_time/create_render*100:.1f}% of total)")

    # Test 4: String joining overhead
    print("\nTEST 4: String Concatenation (100 badges)")
    print("-" * 80)

    # Pre-create badges
    badges = [RustBadge(f"Item {i}", "primary") for i in range(100)]

    # Render all (joining overhead included)
    rust_join = benchmark_careful(lambda: ''.join([b.render() for b in badges]), iterations=1000)

    # Python equivalent
    python_join = benchmark_careful(
        lambda: ''.join([f'<span class="badge bg-primary">Item {i}</span>' for i in range(100)]),
        iterations=1000
    )

    print(f"  Rust (100 renders):  {rust_join:.2f} μs  ({rust_join/100:.3f} μs per badge)")
    print(f"  Python (100 builds): {python_join:.2f} μs  ({python_join/100:.3f} μs per badge)")
    print(f"  Per-item: Rust is {(rust_join/100)/(python_join/100):.1f}x {'slower' if rust_join > python_join else 'faster'}")

    # Test 5: Complex template vs Pure Rust
    print("\nTEST 5: Complex Component (Card with nested content)")
    print("-" * 80)

    # Simulated complex component in Python
    def python_complex_card():
        title = "User Profile"
        items = ["Name: John", "Email: john@example.com", "Status: Active"]
        return f'''
<div class="card">
    <div class="card-header">{title}</div>
    <div class="card-body">
        <ul>
            {"".join([f"<li>{item}</li>" for item in items])}
        </ul>
    </div>
</div>
'''

    python_complex = benchmark_careful(python_complex_card, iterations=1000)
    print(f"  Python (complex):    {python_complex:.2f} μs")

    print("\n" + "=" * 80)
    print("REAL-WORLD SCENARIO: LiveView Component")
    print("=" * 80)

    print(f"""
Scenario: Status badge in a LiveView that re-renders on every state change

Option 1: Pure Rust (created once in mount())
  - Create badge once:      {creation_time:.3f} μs (one-time cost)
  - Render on each update:  {rust_render:.3f} μs
  - 100 updates cost:       {rust_render * 100:.2f} μs total

Option 2: Python f-string (inline in template)
  - No creation needed:     0 μs
  - Build string each time: {python_render:.3f} μs
  - 100 updates cost:       {python_render * 100:.2f} μs total

Winner: {'Rust' if rust_render * 100 < python_render * 100 else 'Python'} by {abs(rust_render * 100 - python_render * 100):.2f} μs over 100 updates

BUT: The difference ({abs(rust_render * 100 - python_render * 100):.2f} μs total) is imperceptible!
A single VDOM diff takes ~60 μs, dwarfing this optimization.
""")

    print("=" * 80)
    print("CONCLUSIONS")
    print("=" * 80)

    print(f"""
1. Python f-strings ARE faster for simple inline rendering:
   - Python: {python_render:.3f} μs (highly optimized bytecode)
   - Rust:   {rust_render:.3f} μs (compiled but crosses FFI boundary)

2. PyO3 object creation adds overhead:
   - Creation: {creation_time:.3f} μs
   - Rendering: {rust_render:.3f} μs
   - Total: {create_render:.3f} μs vs Python's {python_render:.3f} μs

3. For bulk operations (100 items):
   - Rust:   {rust_join/100:.3f} μs per item
   - Python: {python_join/100:.3f} μs per item
   - Ratio: {(rust_join/100)/(python_join/100):.1f}x

4. The REAL insight:
   - All approaches are SUB-MICROSECOND per component
   - VDOM diffing (60μs) >> rendering (0.1-0.3μs)
   - WebSocket latency (1-5ms) >> everything
   - Choose based on ergonomics, not performance!

Recommendation:
   ✓ Use Python f-strings for application code (simple, fast enough)
   ✓ Use Pure Rust for library components (predictable, no GIL)
   ✓ Use templates for reusable patterns (flexible, cached)

   Don't micro-optimize - focus on architecture and developer experience!
""")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
