"""
Benchmarks comparing Django Rust Live with pure Django and other frameworks
"""

import time
import statistics
from typing import List, Callable


def benchmark(func: Callable, iterations: int = 1000) -> dict:
    """Run a function multiple times and collect statistics"""
    times: List[float] = []

    # Warmup
    for _ in range(10):
        func()

    # Actual benchmark
    for _ in range(iterations):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return {
        'mean': statistics.mean(times),
        'median': statistics.median(times),
        'stdev': statistics.stdev(times) if len(times) > 1 else 0,
        'min': min(times),
        'max': max(times),
        'p95': statistics.quantiles(times, n=20)[18],  # 95th percentile
        'p99': statistics.quantiles(times, n=100)[98],  # 99th percentile
    }


def print_results(name: str, results: dict):
    """Print benchmark results in a nice format"""
    print(f"\n{name}")
    print("=" * 60)
    print(f"Mean:   {results['mean']:.3f} ms")
    print(f"Median: {results['median']:.3f} ms")
    print(f"Min:    {results['min']:.3f} ms")
    print(f"Max:    {results['max']:.3f} ms")
    print(f"P95:    {results['p95']:.3f} ms")
    print(f"P99:    {results['p99']:.3f} ms")
    print(f"StdDev: {results['stdev']:.3f} ms")


def benchmark_template_rendering():
    """Benchmark template rendering"""
    print("\n" + "=" * 60)
    print("TEMPLATE RENDERING BENCHMARK")
    print("=" * 60)

    # Django template
    from django.template import Template as DjangoTemplate, Context

    django_template = DjangoTemplate("""
        <div>
            <h1>{{ title }}</h1>
            <ul>
            {% for item in items %}
                <li>{{ item.name }} - {{ item.value }}</li>
            {% endfor %}
            </ul>
        </div>
    """)

    context_data = {
        'title': 'Benchmark',
        'items': [{'name': f'Item {i}', 'value': i} for i in range(100)]
    }

    def render_django():
        context = Context(context_data)
        return django_template.render(context)

    # Django Rust Live template
    try:
        from django_rust_live import render_template

        template_str = """
            <div>
                <h1>{{ title }}</h1>
                <ul>
                {% for item in items %}
                    <li>{{ item.name }} - {{ item.value }}</li>
                {% endfor %}
                </ul>
            </div>
        """

        def render_rust():
            return render_template(template_str, context_data)

        django_results = benchmark(render_django)
        rust_results = benchmark(render_rust)

        print_results("Django Template", django_results)
        print_results("Django Rust Live Template", rust_results)

        speedup = django_results['mean'] / rust_results['mean']
        print(f"\n🚀 Speedup: {speedup:.1f}x faster")

    except ImportError:
        print("\n⚠️  django_rust_live not installed. Run 'maturin develop' first.")
        django_results = benchmark(render_django)
        print_results("Django Template", django_results)


def benchmark_vdom_diffing():
    """Benchmark Virtual DOM diffing"""
    print("\n" + "=" * 60)
    print("VIRTUAL DOM DIFFING BENCHMARK")
    print("=" * 60)

    try:
        from django_rust_live import diff_html

        old_html = """
            <div class="container">
                <h1>Counter: 0</h1>
                <ul>
                    <li>Item 1</li>
                    <li>Item 2</li>
                    <li>Item 3</li>
                </ul>
            </div>
        """

        new_html = """
            <div class="container">
                <h1>Counter: 1</h1>
                <ul>
                    <li>Item 1</li>
                    <li>Item 2</li>
                    <li>Item 3</li>
                    <li>Item 4</li>
                </ul>
            </div>
        """

        def do_diff():
            return diff_html(old_html, new_html)

        results = benchmark(do_diff, iterations=10000)
        print_results("VDOM Diff", results)

        if results['p99'] < 1.0:
            print("\n✨ Sub-millisecond P99 latency achieved!")

    except ImportError:
        print("\n⚠️  django_rust_live not installed. Run 'maturin develop' first.")


def benchmark_large_list():
    """Benchmark rendering large lists"""
    print("\n" + "=" * 60)
    print("LARGE LIST RENDERING BENCHMARK (10,000 items)")
    print("=" * 60)

    from django.template import Template as DjangoTemplate, Context

    django_template = DjangoTemplate("""
        <ul>
        {% for item in items %}
            <li>{{ item }}</li>
        {% endfor %}
        </ul>
    """)

    items = list(range(10000))

    def render_django():
        context = Context({'items': items})
        return django_template.render(context)

    try:
        from django_rust_live import render_template

        template_str = """
            <ul>
            {% for item in items %}
                <li>{{ item }}</li>
            {% endfor %}
            </ul>
        """

        def render_rust():
            return render_template(template_str, {'items': items})

        django_results = benchmark(render_django, iterations=100)
        rust_results = benchmark(render_rust, iterations=100)

        print_results("Django Template", django_results)
        print_results("Django Rust Live Template", rust_results)

        speedup = django_results['mean'] / rust_results['mean']
        print(f"\n🚀 Speedup: {speedup:.1f}x faster")

    except ImportError:
        print("\n⚠️  django_rust_live not installed. Run 'maturin develop' first.")
        django_results = benchmark(render_django, iterations=100)
        print_results("Django Template", django_results)


if __name__ == '__main__':
    import django
    from django.conf import settings

    if not settings.configured:
        settings.configure(
            DEBUG=True,
            SECRET_KEY='benchmark-secret-key',
            TEMPLATES=[{
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
                'OPTIONS': {
                    'string_if_invalid': '',
                }
            }]
        )
        django.setup()

    print("\n" + "=" * 60)
    print("DJANGO RUST LIVE BENCHMARKS")
    print("=" * 60)

    benchmark_template_rendering()
    benchmark_vdom_diffing()
    benchmark_large_list()

    print("\n" + "=" * 60)
    print("BENCHMARK COMPLETE")
    print("=" * 60)
