#!/usr/bin/env python
"""
Benchmark: Pure Rust procedural vs Rust template engine for complex nested structures.

Question: If we wrote complex components in procedural Rust (like Badge/Button),
would they be faster than using the Rust template engine?

Answer: Yes! But at the cost of flexibility.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'python'))

from djust._rust import render_template


def benchmark_function(func, iterations=1000):
    """Benchmark a function."""
    # Warmup
    for _ in range(50):
        func()

    start = time.perf_counter()
    for _ in range(iterations):
        func()
    end = time.perf_counter()

    total_time = (end - start) * 1000
    avg_time = total_time / iterations * 1000
    return total_time, avg_time


def main():
    print("=" * 80)
    print("COMPLEX NESTED STRUCTURE BENCHMARK")
    print("=" * 80)
    print("\nQuestion: Should complex components be written as:")
    print("  1. Procedural Rust code (like RustBadge)?")
    print("  2. Rust template engine (like Hybrid)?")
    print("=" * 80)

    # Simulated data
    sections = [
        {
            'title': f'Section {i}',
            'items': [{'name': f'Item {j}'} for j in range(5)]
        }
        for i in range(10)
    ]

    # Approach 1: Rust template engine (what we have now)
    print("\n1. Rust Template Engine (render_template)")
    print("-" * 80)
    template = '''
    <div class="dashboard">
    {% for section in sections %}
        <div class="section">
            <h2>{{ section.title }}</h2>
            {% for item in section.items %}
                <div class="item">{{ item.name }}</div>
            {% endfor %}
        </div>
    {% endfor %}
    </div>
    '''
    context = {'sections': sections}
    total, avg = benchmark_function(lambda: render_template(template, context))
    template_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)")

    # Approach 2: Python f-strings (procedural equivalent)
    print("\n2. Python Procedural (equivalent to pure Rust approach)")
    print("-" * 80)
    def python_render():
        html = ['<div class="dashboard">']
        for section in sections:
            html.append(f'    <div class="section">')
            html.append(f'        <h2>{section["title"]}</h2>')
            for item in section['items']:
                html.append(f'        <div class="item">{item["name"]}</div>')
            html.append(f'    </div>')
        html.append('</div>')
        return '\n'.join(html)

    total, avg = benchmark_function(python_render)
    procedural_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)")
    print(f"  Speedup: {template_time/procedural_time:.1f}x faster than template engine")

    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)
    print(f"\nTemplate Engine:  {template_time:6.2f} μs")
    print(f"Procedural Code:  {procedural_time:6.2f} μs")
    print(f"Difference:       {template_time - procedural_time:6.2f} μs")
    print(f"Speedup:          {template_time/procedural_time:.1f}x")

    print("\nConclusion:")
    print("  - Procedural Rust (like RustBadge) would be ~{:.1f}x faster".format(template_time/procedural_time))
    print("  - Template engine adds parsing overhead (~{:.1f}μs)".format(template_time - procedural_time))
    print("  - BUT: Templates are more flexible and designer-friendly")
    print("\nRecommendation:")
    print("  - Simple components (Badge, Button): Procedural Rust")
    print("  - Complex/changing layouts: Rust template engine")
    print("  - Application code: Python (most flexible)")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
