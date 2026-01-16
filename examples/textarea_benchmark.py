"""
Benchmark TextArea component rendering performance.
"""

import time
import django
from django.conf import settings

if not settings.configured:
    settings.configure(DEBUG=True, SECRET_KEY='benchmark')
    django.setup()

from djust.components.ui import TextArea
from djust.components.ui.textarea_simple import _RUST_AVAILABLE


def benchmark_textarea(iterations=10000):
    """Benchmark TextArea rendering"""

    # Create component
    textarea = TextArea(
        name="description",
        label="Description",
        placeholder="Enter description...",
        rows=5,
        required=True,
        help_text="Maximum 500 characters",
    )

    # Warm up
    for _ in range(100):
        textarea.render()

    # Benchmark
    start = time.perf_counter()
    for _ in range(iterations):
        textarea.render()
    end = time.perf_counter()

    total_time = (end - start) * 1000  # Convert to milliseconds
    avg_time = total_time / iterations
    avg_time_us = avg_time * 1000  # Convert to microseconds

    return total_time, avg_time, avg_time_us


if __name__ == "__main__":
    print("=" * 70)
    print("TextArea Component Benchmark")
    print("=" * 70)
    print()

    if _RUST_AVAILABLE:
        print("✓ Rust implementation available")
    else:
        print("⚠ Using Python/template fallback")
    print()

    iterations = 10000
    print(f"Running {iterations:,} iterations...")
    print()

    total_ms, avg_ms, avg_us = benchmark_textarea(iterations)

    print(f"Total time: {total_ms:.2f} ms")
    print(f"Average time: {avg_ms:.4f} ms ({avg_us:.2f} μs)")
    print(f"Throughput: {iterations / (total_ms / 1000):.0f} renders/second")
    print()

    # Expected performance
    if _RUST_AVAILABLE:
        expected = "~1-2 μs (Rust)"
    else:
        expected = "~5-100 μs (Python/Template)"

    print(f"Expected: {expected}")

    if _RUST_AVAILABLE and avg_us < 5:
        print("✓ Performance is excellent (pure Rust)")
    elif avg_us < 20:
        print("✓ Performance is good (hybrid/template)")
    else:
        print("⚠ Performance could be improved (Python fallback)")

    print()
    print("=" * 70)
