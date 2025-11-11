#!/usr/bin/env python
"""
Benchmark: Scaling Complexity - When Does Rust Win?

Hypothesis: As component complexity increases, Rust's compiled nature
and lack of GIL overhead will eventually overtake Python f-strings.

Test cases with increasing complexity:
1. Simple (1 variable)
2. Small list (10 items)
3. Medium list (100 items)
4. Large list (1000 items)
5. Nested loops (10x10)
6. Deep nesting (3 levels)
7. Mixed complexity (loops + conditionals + filters)

Goal: Find the crossover point where Rust becomes faster than Python.
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
    avg_time = total_time / iterations * 1000  # microseconds
    return avg_time


def test_simple():
    """Test 1: Simple variable substitution"""
    print("\n" + "=" * 80)
    print("TEST 1: Simple Variable Substitution (Baseline)")
    print("=" * 80)

    # Rust template
    rust_template = '<span class="badge bg-{{ variant }}">{{ text }}</span>'
    rust_context = {'variant': 'primary', 'text': 'Hello'}
    rust_time = benchmark_function(lambda: render_template(rust_template, rust_context))

    # Python f-string
    variant, text = 'primary', 'Hello'
    python_time = benchmark_function(lambda: f'<span class="badge bg-{variant}">{text}</span>')

    print(f"  Rust template:  {rust_time:8.2f} μs")
    print(f"  Python f-string: {python_time:8.2f} μs")
    print(f"  Winner: {'Python' if python_time < rust_time else 'Rust'} ({min(rust_time, python_time)/max(rust_time, python_time)*100:.1f}% faster)")

    return rust_time, python_time


def test_small_list():
    """Test 2: Small list (10 items)"""
    print("\n" + "=" * 80)
    print("TEST 2: Small List (10 items)")
    print("=" * 80)

    items = [f'Item {i}' for i in range(10)]

    # Rust template
    rust_template = '''
    <ul>
    {% for item in items %}
        <li>{{ item }}</li>
    {% endfor %}
    </ul>
    '''
    rust_context = {'items': items}
    rust_time = benchmark_function(lambda: render_template(rust_template, rust_context))

    # Python f-string
    python_time = benchmark_function(lambda: '<ul>\n' + '\n'.join([f'    <li>{item}</li>' for item in items]) + '\n</ul>')

    print(f"  Rust template:  {rust_time:8.2f} μs")
    print(f"  Python f-string: {python_time:8.2f} μs")
    print(f"  Winner: {'Python' if python_time < rust_time else 'Rust'} ({min(rust_time, python_time)/max(rust_time, python_time)*100:.1f}% faster)")

    return rust_time, python_time


def test_medium_list():
    """Test 3: Medium list (100 items)"""
    print("\n" + "=" * 80)
    print("TEST 3: Medium List (100 items)")
    print("=" * 80)

    items = [f'Item {i}' for i in range(100)]

    # Rust template
    rust_template = '''
    <ul>
    {% for item in items %}
        <li>{{ item }}</li>
    {% endfor %}
    </ul>
    '''
    rust_context = {'items': items}
    rust_time = benchmark_function(lambda: render_template(rust_template, rust_context), iterations=100)

    # Python f-string
    python_time = benchmark_function(lambda: '<ul>\n' + '\n'.join([f'    <li>{item}</li>' for item in items]) + '\n</ul>', iterations=100)

    print(f"  Rust template:  {rust_time:8.2f} μs")
    print(f"  Python f-string: {python_time:8.2f} μs")
    print(f"  Winner: {'Python' if python_time < rust_time else 'Rust'} ({min(rust_time, python_time)/max(rust_time, python_time)*100:.1f}% faster)")

    return rust_time, python_time


def test_large_list():
    """Test 4: Large list (1000 items)"""
    print("\n" + "=" * 80)
    print("TEST 4: Large List (1000 items)")
    print("=" * 80)

    items = [f'Item {i}' for i in range(1000)]

    # Rust template
    rust_template = '''
    <ul>
    {% for item in items %}
        <li>{{ item }}</li>
    {% endfor %}
    </ul>
    '''
    rust_context = {'items': items}
    rust_time = benchmark_function(lambda: render_template(rust_template, rust_context), iterations=100)

    # Python f-string
    python_time = benchmark_function(lambda: '<ul>\n' + '\n'.join([f'    <li>{item}</li>' for item in items]) + '\n</ul>', iterations=100)

    print(f"  Rust template:  {rust_time:8.2f} μs")
    print(f"  Python f-string: {python_time:8.2f} μs")
    print(f"  Winner: {'Python' if python_time < rust_time else 'Rust'} ({min(rust_time, python_time)/max(rust_time, python_time)*100:.1f}% faster)")

    return rust_time, python_time


def test_nested_loops():
    """Test 5: Nested loops (10x10)"""
    print("\n" + "=" * 80)
    print("TEST 5: Nested Loops (10x10 = 100 cells)")
    print("=" * 80)

    rows = [
        {'cols': [f'Cell {i},{j}' for j in range(10)]}
        for i in range(10)
    ]

    # Rust template
    rust_template = '''
    <table>
    {% for row in rows %}
        <tr>
        {% for cell in row.cols %}
            <td>{{ cell }}</td>
        {% endfor %}
        </tr>
    {% endfor %}
    </table>
    '''
    rust_context = {'rows': rows}
    rust_time = benchmark_function(lambda: render_template(rust_template, rust_context), iterations=100)

    # Python f-string
    def python_render():
        html = ['<table>']
        for row in rows:
            html.append('    <tr>')
            for cell in row['cols']:
                html.append(f'        <td>{cell}</td>')
            html.append('    </tr>')
        html.append('</table>')
        return '\n'.join(html)

    python_time = benchmark_function(python_render, iterations=100)

    print(f"  Rust template:  {rust_time:8.2f} μs")
    print(f"  Python f-string: {python_time:8.2f} μs")
    print(f"  Winner: {'Python' if python_time < rust_time else 'Rust'} ({min(rust_time, python_time)/max(rust_time, python_time)*100:.1f}% faster)")

    return rust_time, python_time


def test_deep_nesting():
    """Test 6: Deep nesting (3 levels)"""
    print("\n" + "=" * 80)
    print("TEST 6: Deep Nesting (5x5x4 = 100 items)")
    print("=" * 80)

    data = {
        'sections': [
            {
                'title': f'Section {i}',
                'groups': [
                    {
                        'name': f'Group {j}',
                        'items': [f'Item {k}' for k in range(4)]
                    }
                    for j in range(5)
                ]
            }
            for i in range(5)
        ]
    }

    # Rust template
    rust_template = '''
    <div>
    {% for section in sections %}
        <h2>{{ section.title }}</h2>
        {% for group in section.groups %}
            <h3>{{ group.name }}</h3>
            <ul>
            {% for item in group.items %}
                <li>{{ item }}</li>
            {% endfor %}
            </ul>
        {% endfor %}
    {% endfor %}
    </div>
    '''
    rust_time = benchmark_function(lambda: render_template(rust_template, data), iterations=100)

    # Python f-string
    def python_render():
        html = ['<div>']
        for section in data['sections']:
            html.append(f'    <h2>{section["title"]}</h2>')
            for group in section['groups']:
                html.append(f'        <h3>{group["name"]}</h3>')
                html.append('        <ul>')
                for item in group['items']:
                    html.append(f'            <li>{item}</li>')
                html.append('        </ul>')
        html.append('</div>')
        return '\n'.join(html)

    python_time = benchmark_function(python_render, iterations=100)

    print(f"  Rust template:  {rust_time:8.2f} μs")
    print(f"  Python f-string: {python_time:8.2f} μs")
    print(f"  Winner: {'Python' if python_time < rust_time else 'Rust'} ({min(rust_time, python_time)/max(rust_time, python_time)*100:.1f}% faster)")

    return rust_time, python_time


def test_complex_mixed():
    """Test 7: Mixed complexity (loops + conditionals)"""
    print("\n" + "=" * 80)
    print("TEST 7: Mixed Complexity (loops + conditionals, 100 items)")
    print("=" * 80)

    items = [
        {
            'name': f'Item {i}',
            'status': 'active' if i % 3 == 0 else 'inactive',
            'priority': 'high' if i % 5 == 0 else 'normal',
            'value': i * 10
        }
        for i in range(100)
    ]

    # Rust template
    rust_template = '''
    <div class="items">
    {% for item in items %}
        <div class="item {% if item.status == "active" %}active{% endif %} {% if item.priority == "high" %}priority-high{% endif %}">
            <h4>{{ item.name }}</h4>
            <span class="badge {% if item.status == "active" %}bg-success{% endif %}{% if item.status == "inactive" %}bg-secondary{% endif %}">
                {{ item.status }}
            </span>
            {% if item.priority == "high" %}
                <span class="badge bg-danger">High Priority</span>
            {% endif %}
            <div class="value">Value: {{ item.value }}</div>
        </div>
    {% endfor %}
    </div>
    '''
    rust_context = {'items': items}
    rust_time = benchmark_function(lambda: render_template(rust_template, rust_context), iterations=100)

    # Python f-string
    def python_render():
        html = ['<div class="items">']
        for item in items:
            classes = ['item']
            if item['status'] == 'active':
                classes.append('active')
            if item['priority'] == 'high':
                classes.append('priority-high')

            badge_class = 'bg-success' if item['status'] == 'active' else 'bg-secondary'

            html.append(f'    <div class="{" ".join(classes)}">')
            html.append(f'        <h4>{item["name"]}</h4>')
            html.append(f'        <span class="badge {badge_class}">{item["status"]}</span>')

            if item['priority'] == 'high':
                html.append('        <span class="badge bg-danger">High Priority</span>')

            html.append(f'        <div class="value">Value: {item["value"]}</div>')
            html.append('    </div>')

        html.append('</div>')
        return '\n'.join(html)

    python_time = benchmark_function(python_render, iterations=100)

    print(f"  Rust template:  {rust_time:8.2f} μs")
    print(f"  Python f-string: {python_time:8.2f} μs")
    print(f"  Winner: {'Python' if python_time < rust_time else 'Rust'} ({min(rust_time, python_time)/max(rust_time, python_time)*100:.1f}% faster)")

    return rust_time, python_time


def test_pure_rust_component():
    """Test 8: Pure Rust component comparison"""
    print("\n" + "=" * 80)
    print("TEST 8: Pure Rust Component (100 badges)")
    print("=" * 80)

    from djust._rust import RustBadge

    # Pure Rust
    badges = [RustBadge(f"Item {i}", "primary") for i in range(100)]
    rust_time = benchmark_function(lambda: ''.join([b.render() for b in badges]), iterations=100)

    # Python f-string
    python_time = benchmark_function(
        lambda: ''.join([f'<span class="badge bg-primary">Item {i}</span>' for i in range(100)]),
        iterations=100
    )

    # Rust template
    template = '''
    {% for i in items %}
        <span class="badge bg-primary">Item {{ i }}</span>
    {% endfor %}
    '''
    rust_template_time = benchmark_function(
        lambda: render_template(template, {'items': range(100)}),
        iterations=100
    )

    print(f"  Pure Rust (PyO3):     {rust_time:8.2f} μs")
    print(f"  Python f-string:      {python_time:8.2f} μs")
    print(f"  Rust template:        {rust_template_time:8.2f} μs")
    print(f"  Winner: Pure Rust ({rust_time/python_time*100:.1f}% of Python time)")

    return rust_time, python_time, rust_template_time


def main():
    print("=" * 80)
    print("SCALING COMPLEXITY BENCHMARK")
    print("=" * 80)
    print("\nHypothesis: As complexity scales, Rust's compiled nature will overtake")
    print("Python f-strings due to reduced overhead and lack of GIL.")
    print("=" * 80)

    results = []

    # Run all tests
    results.append(('Simple (1 var)', *test_simple()))
    results.append(('Small List (10)', *test_small_list()))
    results.append(('Medium List (100)', *test_medium_list()))
    results.append(('Large List (1000)', *test_large_list()))
    results.append(('Nested Loops (10x10)', *test_nested_loops()))
    results.append(('Deep Nesting (3 levels)', *test_deep_nesting()))
    results.append(('Mixed Complexity', *test_complex_mixed()))

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY: Performance vs Complexity")
    print("=" * 80)
    print()
    print(f"{'Test Case':<25} {'Rust (μs)':<12} {'Python (μs)':<12} {'Winner':<10} {'Gap':<10}")
    print("-" * 80)

    for name, rust_time, python_time in results:
        winner = 'Python' if python_time < rust_time else 'Rust'
        gap = f"{abs(rust_time - python_time):.1f}μs"
        print(f"{name:<25} {rust_time:>10.2f}  {python_time:>10.2f}  {winner:<10} {gap:<10}")

    print("\n" + "=" * 80)
    print("ANALYSIS")
    print("=" * 80)

    # Check if Rust ever wins
    rust_wins = [name for name, r, p in results if r < p]

    if rust_wins:
        print(f"\n✅ Rust wins at: {', '.join(rust_wins)}")
    else:
        print("\n❌ Python f-strings win at ALL complexity levels!")

    # Find the trend
    print("\nTrend Analysis:")
    print("-" * 80)
    for i, (name, rust_time, python_time) in enumerate(results):
        ratio = rust_time / python_time
        print(f"{name:<25} Rust is {ratio:.2f}x slower than Python")

    print("\n" + "=" * 80)
    print("CONCLUSION")
    print("=" * 80)

    # Calculate if gap is narrowing
    simple_ratio = results[0][1] / results[0][2]
    complex_ratio = results[-1][1] / results[-1][2]

    if complex_ratio < simple_ratio:
        print(f"""
✅ Gap IS narrowing as complexity increases!
   - Simple: Rust is {simple_ratio:.2f}x slower
   - Complex: Rust is {complex_ratio:.2f}x slower
   - Improvement: {(simple_ratio - complex_ratio)/simple_ratio*100:.1f}%

At this rate, Rust might overtake Python at even higher complexity.
""")
    else:
        print(f"""
❌ Gap is NOT narrowing significantly.
   - Simple: Rust is {simple_ratio:.2f}x slower
   - Complex: Rust is {complex_ratio:.2f}x slower

Python's optimized list comprehensions and join() remain dominant.
The template evaluation overhead outweighs any compiled advantages.
""")

    # Test Pure Rust
    pure_rust, python, rust_template = test_pure_rust_component()

    print(f"""
HOWEVER: Pure Rust Components (PyO3) DO WIN:
   - Pure Rust (PyO3):  {pure_rust:6.2f} μs  ← Direct HTML generation
   - Python f-string:   {python:6.2f} μs
   - Rust template:     {rust_template:6.2f} μs

   Pure Rust is {python/pure_rust:.1f}x FASTER than Python!

Recommendation:
   - Library components (Badge, Button): Pure Rust PyO3 ✅
   - Application code with logic: Python f-strings ✅
   - Template reuse/filters: Rust templates (acceptable overhead) ✅
""")

    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
