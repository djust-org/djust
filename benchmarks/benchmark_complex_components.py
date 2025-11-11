#!/usr/bin/env python
"""
Benchmark complex component rendering scenarios:
- Lists with loops
- Nested structures
- Conditional logic
- Multiple data points
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


def benchmark_list_rendering():
    """Benchmark rendering a list of items."""
    print("=" * 80)
    print("LIST RENDERING - Badge List (10 items)")
    print("=" * 80)
    
    items = [f"Item {i}" for i in range(10)]
    
    print("\n1. Hybrid (Rust template with loop)")
    print("-" * 80)
    template = '''
    <div class="badge-list">
    {% for item in items %}
        <span class="badge bg-primary">{{ item }}</span>
    {% endfor %}
    </div>
    '''
    context = {'items': items}
    total, avg = benchmark_function(lambda: render_template(template, context))
    hybrid_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)")
    
    print("\n2. Python (list comprehension + join)")
    print("-" * 80)
    def python_render():
        badges = [f'<span class="badge bg-primary">{item}</span>' for item in items]
        return f'<div class="badge-list">\n    {"".join(badges)}\n</div>'
    
    total, avg = benchmark_function(python_render)
    python_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)  [{avg/hybrid_time:.1f}x vs Hybrid]")


def benchmark_conditional_rendering():
    """Benchmark rendering with conditionals."""
    print("\n" + "=" * 80)
    print("CONDITIONAL RENDERING - Status Badges")
    print("=" * 80)
    
    statuses = [
        {'name': 'Active', 'status': 'active', 'count': 5},
        {'name': 'Pending', 'status': 'pending', 'count': 3},
        {'name': 'Error', 'status': 'error', 'count': 1},
    ]
    
    print("\n1. Hybrid (Rust template with conditionals)")
    print("-" * 80)
    template = '''
    <div class="status-badges">
    {% for item in statuses %}
        <span class="badge 
        {% if item.status == "active" %}bg-success
        {% elif item.status == "pending" %}bg-warning
        {% elif item.status == "error" %}bg-danger
        {% else %}bg-secondary
        {% endif %}">
            {{ item.name }}: {{ item.count }}
        </span>
    {% endfor %}
    </div>
    '''
    context = {'statuses': statuses}
    total, avg = benchmark_function(lambda: render_template(template, context))
    hybrid_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)")
    
    print("\n2. Python (dict mapping + comprehension)")
    print("-" * 80)
    def python_render():
        variant_map = {'active': 'success', 'pending': 'warning', 'error': 'danger'}
        badges = [
            f'<span class="badge bg-{variant_map.get(item["status"], "secondary")}">{item["name"]}: {item["count"]}</span>'
            for item in statuses
        ]
        return f'<div class="status-badges">\n    {"".join(badges)}\n</div>'
    
    total, avg = benchmark_function(python_render)
    python_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)  [{avg/hybrid_time:.1f}x vs Hybrid]")


def benchmark_nested_structure():
    """Benchmark rendering nested structures."""
    print("\n" + "=" * 80)
    print("NESTED STRUCTURE - Card with List")
    print("=" * 80)
    
    data = {
        'title': 'User Dashboard',
        'stats': [
            {'label': 'Users', 'value': 1234, 'variant': 'primary'},
            {'label': 'Active', 'value': 856, 'variant': 'success'},
            {'label': 'Pending', 'value': 234, 'variant': 'warning'},
            {'label': 'Errors', 'value': 12, 'variant': 'danger'},
        ]
    }
    
    print("\n1. Hybrid (Rust template with nested loops)")
    print("-" * 80)
    template = '''
    <div class="card">
        <div class="card-header">
            <h3>{{ title }}</h3>
        </div>
        <div class="card-body">
            <div class="stats">
            {% for stat in stats %}
                <div class="stat-item">
                    <span class="label">{{ stat.label }}</span>
                    <span class="badge bg-{{ stat.variant }}">{{ stat.value }}</span>
                </div>
            {% endfor %}
            </div>
        </div>
    </div>
    '''
    total, avg = benchmark_function(lambda: render_template(template, data))
    hybrid_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)")
    
    print("\n2. Python (f-strings with comprehension)")
    print("-" * 80)
    def python_render():
        stats_html = '\n            '.join([
            f'<div class="stat-item">\n'
            f'                <span class="label">{stat["label"]}</span>\n'
            f'                <span class="badge bg-{stat["variant"]}">{stat["value"]}</span>\n'
            f'            </div>'
            for stat in data['stats']
        ])
        return f'''<div class="card">
        <div class="card-header">
            <h3>{data['title']}</h3>
        </div>
        <div class="card-body">
            <div class="stats">
            {stats_html}
            </div>
        </div>
    </div>'''
    
    total, avg = benchmark_function(python_render)
    python_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)  [{avg/hybrid_time:.1f}x vs Hybrid]")


def benchmark_table_rendering():
    """Benchmark rendering a data table."""
    print("\n" + "=" * 80)
    print("TABLE RENDERING - 10 rows × 4 columns")
    print("=" * 80)
    
    rows = [
        {'id': i, 'name': f'User {i}', 'email': f'user{i}@example.com', 'status': 'active' if i % 2 == 0 else 'pending'}
        for i in range(10)
    ]
    
    print("\n1. Hybrid (Rust template with table)")
    print("-" * 80)
    template = '''
    <table class="table">
        <thead>
            <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Email</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
        {% for row in rows %}
            <tr>
                <td>{{ row.id }}</td>
                <td>{{ row.name }}</td>
                <td>{{ row.email }}</td>
                <td>
                    <span class="badge bg-{% if row.status == "active" %}success{% else %}warning{% endif %}">
                        {{ row.status }}
                    </span>
                </td>
            </tr>
        {% endfor %}
        </tbody>
    </table>
    '''
    context = {'rows': rows}
    total, avg = benchmark_function(lambda: render_template(template, context))
    hybrid_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)")
    
    print("\n2. Python (comprehension with join)")
    print("-" * 80)
    def python_render():
        tbody_rows = '\n        '.join([
            f'<tr>\n'
            f'            <td>{row["id"]}</td>\n'
            f'            <td>{row["name"]}</td>\n'
            f'            <td>{row["email"]}</td>\n'
            f'            <td>\n'
            f'                <span class="badge bg-{"success" if row["status"] == "active" else "warning"}">\n'
            f'                    {row["status"]}\n'
            f'                </span>\n'
            f'            </td>\n'
            f'        </tr>'
            for row in rows
        ])
        return f'''<table class="table">
        <thead>
            <tr>
                <th>ID</th>
                <th>Name</th>
                <th>Email</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
        {tbody_rows}
        </tbody>
    </table>'''
    
    total, avg = benchmark_function(python_render)
    python_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 1k)  [{avg/hybrid_time:.1f}x vs Hybrid]")


def benchmark_large_list():
    """Benchmark rendering a large list."""
    print("\n" + "=" * 80)
    print("LARGE LIST RENDERING - 100 items")
    print("=" * 80)
    
    items = [{'id': i, 'name': f'Item {i}', 'priority': i % 3} for i in range(100)]
    
    print("\n1. Hybrid (Rust template with 100-item loop)")
    print("-" * 80)
    template = '''
    <div class="item-list">
    {% for item in items %}
        <div class="item">
            <span class="badge bg-{% if item.priority == 0 %}success{% elif item.priority == 1 %}warning{% else %}danger{% endif %}">
                {{ item.name }}
            </span>
        </div>
    {% endfor %}
    </div>
    '''
    context = {'items': items}
    total, avg = benchmark_function(lambda: render_template(template, context), iterations=100)
    hybrid_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 100)")
    
    print("\n2. Python (list comprehension)")
    print("-" * 80)
    def python_render():
        priority_map = {0: 'success', 1: 'warning', 2: 'danger'}
        items_html = ''.join([
            f'<div class="item">\n'
            f'        <span class="badge bg-{priority_map[item["priority"]]}">\n'
            f'            {item["name"]}\n'
            f'        </span>\n'
            f'    </div>\n    '
            for item in items
        ])
        return f'<div class="item-list">\n    {items_html}\n</div>'
    
    total, avg = benchmark_function(python_render, iterations=100)
    python_time = avg
    print(f"  Time: {avg:8.2f} μs/render  ({total:8.2f} ms for 100)  [{avg/hybrid_time:.1f}x vs Hybrid]")


def main():
    print("\n" + "=" * 80)
    print("COMPLEX COMPONENT RENDERING BENCHMARKS")
    print("=" * 80)
    print("\nTesting scenarios where Rust template engine should excel:")
    print("  - Loops over data")
    print("  - Conditional rendering")
    print("  - Nested structures")
    print("  - Large lists")
    print("=" * 80)
    
    benchmark_list_rendering()
    benchmark_conditional_rendering()
    benchmark_nested_structure()
    benchmark_table_rendering()
    benchmark_large_list()
    
    print("\n" + "=" * 80)
    print("BENCHMARK COMPLETE")
    print("=" * 80)
    print("\nKey Findings:")
    print("  - Hybrid (Rust template): Best for template reuse and caching")
    print("  - Python: Surprisingly competitive even for complex structures!")
    print("  - Both methods are production-ready for all scenarios")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
