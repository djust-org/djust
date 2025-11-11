#!/usr/bin/env python
"""
Simple benchmark for pure Rust components without Django.

Tests RustBadge and RustButton directly.
"""

import sys
import time
from pathlib import Path

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

from djust._rust import RustBadge, RustButton


def benchmark_function(func, iterations=1000):
    """Benchmark a function over N iterations."""
    # Warmup
    for _ in range(100):
        func()
    
    # Actual benchmark
    start = time.perf_counter()
    for _ in range(iterations):
        func()
    end = time.perf_counter()
    
    total_time = (end - start) * 1000  # Convert to ms
    avg_time = total_time / iterations * 1000  # Convert to μs
    
    return total_time, avg_time


def benchmark_badge():
    """Benchmark Badge component rendering."""
    print("=" * 70)
    print("BADGE COMPONENT BENCHMARKS (Pure Rust)")
    print("=" * 70)
    
    # Test configurations
    configs = [
        ("Small", "secondary", "sm", False),
        ("Medium", "primary", "md", False),
        ("Large Pill", "success", "lg", True),
    ]
    
    for text, variant, size, pill in configs:
        print(f"\n{text} Badge ({variant}, {size}, pill={pill}):")
        print("-" * 70)
        
        # Pure Rust
        rust_badge = RustBadge(text, variant, size, pill)
        total, avg = benchmark_function(lambda: rust_badge.render(), iterations=10000)
        print(f"  10,000 iterations: {total:8.2f} ms total")
        print(f"  Average:           {avg:8.2f} μs/render")
        print(f"  Rate:              {1000000/avg:,.0f} renders/second")


def benchmark_button():
    """Benchmark Button component rendering."""
    print("\n" + "=" * 70)
    print("BUTTON COMPONENT BENCHMARKS (Pure Rust)")
    print("=" * 70)
    
    # Test configurations
    configs = [
        ("Small", "primary", "sm", False, False),
        ("Large Outline", "success", "lg", False, True),
        ("Disabled", "danger", "md", True, False),
    ]
    
    for text, variant, size, disabled, outline in configs:
        print(f"\n{text} Button ({variant}, {size}, disabled={disabled}, outline={outline}):")
        print("-" * 70)
        
        # Pure Rust
        rust_button = RustButton(text, variant, size, disabled, outline)
        total, avg = benchmark_function(lambda: rust_button.render(), iterations=10000)
        print(f"  10,000 iterations: {total:8.2f} ms total")
        print(f"  Average:           {avg:8.2f} μs/render")
        print(f"  Rate:              {1000000/avg:,.0f} renders/second")


def benchmark_bulk_rendering():
    """Benchmark rendering many components at once."""
    print("\n" + "=" * 70)
    print("BULK RENDERING BENCHMARKS (Pure Rust)")
    print("=" * 70)
    
    counts = [10, 100, 1000, 10000]
    
    for count in counts:
        print(f"\nRendering {count:,} components:")
        print("-" * 70)
        
        # Create components
        badges = [RustBadge(f"Item {i}", "primary", "md", False) for i in range(count)]
        
        # Warmup
        for badge in badges[:10]:
            badge.render()
        
        # Benchmark
        start = time.perf_counter()
        for badge in badges:
            badge.render()
        total_time = (time.perf_counter() - start) * 1000
        
        print(f"  Total time:  {total_time:10.2f} ms")
        print(f"  Per component: {total_time/count*1000:8.2f} μs")
        print(f"  Rate:          {count/total_time*1000:,.0f} components/second")


def main():
    print("\n" + "=" * 70)
    print("DJUST PURE RUST COMPONENT PERFORMANCE BENCHMARKS")
    print("=" * 70)
    print("\nTesting RustBadge and RustButton PyO3 classes")
    print("Direct Rust rendering without template parsing")
    print("=" * 70)
    
    benchmark_badge()
    benchmark_button()
    benchmark_bulk_rendering()
    
    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETE")
    print("=" * 70)
    print("\nExpected performance:")
    print("  - ~1-2 μs per component render")
    print("  - ~500,000-1,000,000 components/second")
    print("  - 10,000 components in ~10-20 ms")
    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
