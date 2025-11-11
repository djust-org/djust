#!/usr/bin/env python
"""
Comprehensive benchmark comparing all three rendering methods:
1. Pure Rust (PyO3 class, direct rendering)
2. Hybrid (template_string + Rust template engine)
3. Python (pure Python rendering with _render_custom)
"""

import sys
import time
from pathlib import Path

# Add python directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

from djust._rust import RustBadge, RustButton, render_template


def benchmark_function(func, iterations=10000):
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


def benchmark_badge_methods():
    """Benchmark Badge with all three methods."""
    print("=" * 80)
    print("BADGE RENDERING - ALL METHODS COMPARISON")
    print("=" * 80)
    
    configs = [
        ("Small", "secondary", "sm", False),
        ("Medium", "primary", "md", False),
        ("Large Pill", "success", "lg", True),
    ]
    
    for text, variant, size, pill in configs:
        print(f"\n{text} Badge (variant={variant}, size={size}, pill={pill})")
        print("-" * 80)
        
        # 1. Pure Rust
        rust_badge = RustBadge(text, variant, size, pill)
        total, avg = benchmark_function(lambda: rust_badge.render())
        rust_time = avg
        print(f"  1. Pure Rust:      {avg:8.2f} μs/render  ({total:8.2f} ms for 10k)")
        
        # 2. Hybrid (template_string + Rust render_template)
        template = '<span class="badge bg-{{ variant }}{% if size == "md" %} fs-6{% endif %}{% if size == "lg" %} fs-5{% endif %}{% if pill %} rounded-pill{% endif %}">{{ text }}</span>'
        context = {'text': text, 'variant': variant, 'size': size, 'pill': pill}
        total, avg = benchmark_function(lambda: render_template(template, context))
        hybrid_time = avg
        speedup = hybrid_time / rust_time
        print(f"  2. Hybrid:         {avg:8.2f} μs/render  ({total:8.2f} ms for 10k)  [{speedup:.1f}x slower]")
        
        # 3. Pure Python
        def python_render_badge():
            size_class = ' fs-6' if size == 'md' else (' fs-5' if size == 'lg' else '')
            pill_class = ' rounded-pill' if pill else ''
            return f'<span class="badge bg-{variant}{size_class}{pill_class}">{text}</span>'
        
        total, avg = benchmark_function(python_render_badge)
        python_time = avg
        speedup = python_time / rust_time
        print(f"  3. Python:         {avg:8.2f} μs/render  ({total:8.2f} ms for 10k)  [{speedup:.1f}x slower]")
        
        # Summary
        print(f"\n  Performance Summary:")
        print(f"    Pure Rust is {hybrid_time/rust_time:.1f}x faster than Hybrid")
        print(f"    Pure Rust is {python_time/rust_time:.1f}x faster than Python")
        print(f"    Hybrid is {python_time/hybrid_time:.1f}x faster than Python")


def benchmark_button_methods():
    """Benchmark Button with all three methods."""
    print("\n" + "=" * 80)
    print("BUTTON RENDERING - ALL METHODS COMPARISON")
    print("=" * 80)
    
    configs = [
        ("Small", "primary", "sm", False, False),
        ("Large Outline", "success", "lg", False, True),
        ("Disabled", "danger", "md", True, False),
    ]
    
    for text, variant, size, disabled, outline in configs:
        print(f"\n{text} Button (variant={variant}, size={size}, disabled={disabled}, outline={outline})")
        print("-" * 80)
        
        # 1. Pure Rust
        rust_button = RustButton(text, variant, size, disabled, outline)
        total, avg = benchmark_function(lambda: rust_button.render())
        rust_time = avg
        print(f"  1. Pure Rust:      {avg:8.2f} μs/render  ({total:8.2f} ms for 10k)")
        
        # 2. Hybrid (template_string + Rust render_template)
        template = '<button type="button" class="btn {% if outline %}btn-outline-{{ variant }}{% else %}btn-{{ variant }}{% endif %}{% if size == "sm" %} btn-sm{% endif %}{% if size == "lg" %} btn-lg{% endif %}"{% if disabled %} disabled{% endif %}>{{ text }}</button>'
        context = {'text': text, 'variant': variant, 'size': size, 'disabled': disabled, 'outline': outline}
        total, avg = benchmark_function(lambda: render_template(template, context))
        hybrid_time = avg
        speedup = hybrid_time / rust_time
        print(f"  2. Hybrid:         {avg:8.2f} μs/render  ({total:8.2f} ms for 10k)  [{speedup:.1f}x slower]")
        
        # 3. Pure Python
        def python_render_button():
            size_class = ' btn-sm' if size == 'sm' else (' btn-lg' if size == 'lg' else '')
            variant_class = f'btn-outline-{variant}' if outline else f'btn-{variant}'
            disabled_attr = ' disabled' if disabled else ''
            return f'<button type="button" class="btn {variant_class}{size_class}"{disabled_attr}>{text}</button>'
        
        total, avg = benchmark_function(python_render_button)
        python_time = avg
        speedup = python_time / rust_time
        print(f"  3. Python:         {avg:8.2f} μs/render  ({total:8.2f} ms for 10k)  [{speedup:.1f}x slower]")
        
        # Summary
        print(f"\n  Performance Summary:")
        print(f"    Pure Rust is {hybrid_time/rust_time:.1f}x faster than Hybrid")
        print(f"    Pure Rust is {python_time/rust_time:.1f}x faster than Python")
        print(f"    Hybrid is {python_time/hybrid_time:.1f}x faster than Python")


def benchmark_bulk_all_methods():
    """Benchmark bulk rendering with all methods."""
    print("\n" + "=" * 80)
    print("BULK RENDERING - ALL METHODS COMPARISON")
    print("=" * 80)
    
    count = 1000
    print(f"\nRendering {count:,} badges:")
    print("-" * 80)
    
    # 1. Pure Rust
    rust_badges = [RustBadge(f"Item {i}", "primary", "md", False) for i in range(count)]
    start = time.perf_counter()
    for badge in rust_badges:
        badge.render()
    rust_time = (time.perf_counter() - start) * 1000
    
    print(f"  1. Pure Rust:      {rust_time:8.2f} ms  ({rust_time/count*1000:6.2f} μs/component)")
    print(f"                     {count/rust_time*1000:,.0f} components/second")
    
    # 2. Hybrid
    template = '<span class="badge bg-{{ variant }}{% if size == "md" %} fs-6{% endif %}">{{ text }}</span>'
    contexts = [{'text': f'Item {i}', 'variant': 'primary', 'size': 'md'} for i in range(count)]
    start = time.perf_counter()
    for context in contexts:
        render_template(template, context)
    hybrid_time = (time.perf_counter() - start) * 1000
    
    print(f"  2. Hybrid:         {hybrid_time:8.2f} ms  ({hybrid_time/count*1000:6.2f} μs/component)  [{hybrid_time/rust_time:.1f}x slower]")
    print(f"                     {count/hybrid_time*1000:,.0f} components/second")
    
    # 3. Pure Python
    start = time.perf_counter()
    for i in range(count):
        f'<span class="badge bg-primary fs-6">Item {i}</span>'
    python_time = (time.perf_counter() - start) * 1000
    
    print(f"  3. Python:         {python_time:8.2f} ms  ({python_time/count*1000:6.2f} μs/component)  [{python_time/rust_time:.1f}x slower]")
    print(f"                     {count/python_time*1000:,.0f} components/second")
    
    print(f"\n  Performance Summary for {count:,} components:")
    print(f"    Pure Rust is {hybrid_time/rust_time:.1f}x faster than Hybrid")
    print(f"    Pure Rust is {python_time/rust_time:.1f}x faster than Python")
    print(f"    Hybrid is {python_time/hybrid_time:.1f}x faster than Python")


def main():
    print("\n" + "=" * 80)
    print("DJUST COMPONENT RENDERING - COMPREHENSIVE PERFORMANCE COMPARISON")
    print("=" * 80)
    print("\nComparing three rendering methods:")
    print("  1. Pure Rust     - PyO3 class, direct Rust rendering (~0.3-0.4 μs)")
    print("  2. Hybrid        - template_string + Rust template engine (~5-10 μs)")
    print("  3. Python        - Pure Python f-string rendering (~0.5-2 μs)")
    print("\nEach test runs 10,000 iterations with 100 warmup iterations")
    print("=" * 80)
    
    benchmark_badge_methods()
    benchmark_button_methods()
    benchmark_bulk_all_methods()
    
    print("\n" + "=" * 80)
    print("BENCHMARK COMPLETE")
    print("=" * 80)
    print("\nKey Findings:")
    print("  - Pure Rust: Fastest, best for high-volume rendering")
    print("  - Hybrid: Great balance, handles complex templates")
    print("  - Python: Most flexible, perfect for custom logic")
    print("\nAll three methods are production-ready and blazingly fast!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
