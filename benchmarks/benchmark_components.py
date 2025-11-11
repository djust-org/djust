#!/usr/bin/env python
"""
Benchmark pure Rust vs Hybrid vs Python component rendering.

Tests Badge and Button components with different rendering methods.
"""

import sys
import time
from pathlib import Path

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

# Setup Django
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'tests.settings')
import django
django.setup()

from djust.components.ui import Badge, Button
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
    print("BADGE COMPONENT BENCHMARKS")
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
        
        # 1. Pure Rust
        rust_badge = RustBadge(text, variant, size, pill)
        total, avg = benchmark_function(lambda: rust_badge.render())
        print(f"  Pure Rust:     {avg:8.2f} μs/render  ({total:.2f} ms total)")
        
        # 2. Hybrid (Python Badge using Rust template engine)
        hybrid_badge = Badge(text, variant=variant, size=size, pill=pill)
        # Force hybrid by removing rust instance
        hybrid_badge._rust_instance = None
        total, avg = benchmark_function(lambda: hybrid_badge.render())
        print(f"  Hybrid:        {avg:8.2f} μs/render  ({total:.2f} ms total)")
        
        # 3. Python fallback
        # Temporarily disable Rust to force Python rendering
        from djust.components.ui.badge_simple import Badge as BadgeClass
        old_rust_class = BadgeClass._rust_impl_class
        BadgeClass._rust_impl_class = None
        python_badge = Badge(text, variant=variant, size=size, pill=pill)
        BadgeClass._rust_impl_class = old_rust_class
        
        total, avg = benchmark_function(lambda: python_badge.render())
        print(f"  Python:        {avg:8.2f} μs/render  ({total:.2f} ms total)")


def benchmark_button():
    """Benchmark Button component rendering."""
    print("\n" + "=" * 70)
    print("BUTTON COMPONENT BENCHMARKS")
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
        
        # 1. Pure Rust
        rust_button = RustButton(text, variant, size, disabled, outline)
        total, avg = benchmark_function(lambda: rust_button.render())
        print(f"  Pure Rust:     {avg:8.2f} μs/render  ({total:.2f} ms total)")
        
        # 2. Hybrid (Python Button using Rust template engine)
        hybrid_button = Button(text, variant=variant, size=size, disabled=disabled, outline=outline)
        # Force hybrid by removing rust instance
        hybrid_button._rust_instance = None
        total, avg = benchmark_function(lambda: hybrid_button.render())
        print(f"  Hybrid:        {avg:8.2f} μs/render  ({total:.2f} ms total)")
        
        # 3. Python fallback
        from djust.components.ui.button_simple import Button as ButtonClass
        old_rust_class = ButtonClass._rust_impl_class
        ButtonClass._rust_impl_class = None
        python_button = Button(text, variant=variant, size=size, disabled=disabled, outline=outline)
        ButtonClass._rust_impl_class = old_rust_class
        
        total, avg = benchmark_function(lambda: python_button.render())
        print(f"  Python:        {avg:8.2f} μs/render  ({total:.2f} ms total)")


def benchmark_bulk_rendering():
    """Benchmark rendering many components at once."""
    print("\n" + "=" * 70)
    print("BULK RENDERING BENCHMARKS (100 components)")
    print("=" * 70)
    
    counts = [10, 100, 1000]
    
    for count in counts:
        print(f"\nRendering {count} components:")
        print("-" * 70)
        
        # Pure Rust badges
        rust_badges = [RustBadge(f"Item {i}", "primary", "md", False) for i in range(count)]
        start = time.perf_counter()
        for badge in rust_badges:
            badge.render()
        rust_time = (time.perf_counter() - start) * 1000
        
        # Hybrid badges
        hybrid_badges = [Badge(f"Item {i}", variant="primary", size="md") for i in range(count)]
        for b in hybrid_badges:
            b._rust_instance = None
        start = time.perf_counter()
        for badge in hybrid_badges:
            badge.render()
        hybrid_time = (time.perf_counter() - start) * 1000
        
        # Python badges
        from djust.components.ui.badge_simple import Badge as BadgeClass
        old_rust_class = BadgeClass._rust_impl_class
        BadgeClass._rust_impl_class = None
        python_badges = [Badge(f"Item {i}", variant="primary", size="md") for i in range(count)]
        BadgeClass._rust_impl_class = old_rust_class
        start = time.perf_counter()
        for badge in python_badges:
            badge.render()
        python_time = (time.perf_counter() - start) * 1000
        
        print(f"  Pure Rust:  {rust_time:8.2f} ms  ({rust_time/count*1000:6.2f} μs/component)")
        print(f"  Hybrid:     {hybrid_time:8.2f} ms  ({hybrid_time/count*1000:6.2f} μs/component)")
        print(f"  Python:     {python_time:8.2f} ms  ({python_time/count*1000:6.2f} μs/component)")
        print(f"  Speedup:    Rust is {python_time/rust_time:.1f}x faster than Python")
        print(f"              Rust is {hybrid_time/rust_time:.1f}x faster than Hybrid")


def main():
    print("\n" + "=" * 70)
    print("DJUST COMPONENT RENDERING PERFORMANCE BENCHMARKS")
    print("=" * 70)
    print("\nComparing three rendering methods:")
    print("  1. Pure Rust     - PyO3 class, direct Rust rendering")
    print("  2. Hybrid        - template_string + Rust template engine")
    print("  3. Python        - Pure Python rendering")
    print("\nEach test runs 1000 iterations (with 100 warmup iterations)")
    print("=" * 70)
    
    benchmark_badge()
    benchmark_button()
    benchmark_bulk_rendering()
    
    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
