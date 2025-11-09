#!/usr/bin/env python3
"""
Benchmark script to compare Python json.dumps vs Rust fast_json_dumps

This demonstrates the performance improvement of using Rust for JSON serialization.
"""

import json
import time
import sys
import os

# Add the project to the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import Django settings
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')
import django
django.setup()

from djust._rust import fast_json_dumps


def generate_products(count):
    """Generate sample product data"""
    import random

    categories = ['Electronics', 'Clothing', 'Food', 'Books', 'Toys', 'Sports', 'Home & Garden']
    adjectives = ['Premium', 'Deluxe', 'Standard', 'Economy', 'Pro', 'Ultra', 'Mini', 'Max']
    nouns = ['Widget', 'Gadget', 'Device', 'Tool', 'Item', 'Product', 'Kit', 'Set']

    products = []
    for i in range(count):
        product_id = i + 1
        name = f"{random.choice(adjectives)} {random.choice(nouns)} {product_id}"
        category = random.choice(categories)
        price = round(random.uniform(9.99, 499.99), 2)
        stock = random.randint(0, 200)
        is_active = random.choice([True, True, True, False])

        products.append({
            'id': product_id,
            'name': name,
            'category': category,
            'price': str(price),
            'stock': stock,
            'is_active': is_active,
        })

    return products


def benchmark(func, data, iterations=1000):
    """Benchmark a function"""
    start = time.time()
    for _ in range(iterations):
        func(data)
    end = time.time()
    return end - start


def main():
    print("=" * 70)
    print("JSON Serialization Benchmark: Python vs Rust")
    print("=" * 70)
    print()

    # Test with different data sizes
    sizes = [10, 100, 1000]
    iterations = 1000

    for size in sizes:
        products = generate_products(size)

        print(f"Testing with {size} products ({iterations} iterations):")
        print("-" * 70)

        # Benchmark Python json.dumps
        python_time = benchmark(json.dumps, products, iterations)
        python_ops_per_sec = iterations / python_time

        # Benchmark Rust fast_json_dumps
        rust_time = benchmark(fast_json_dumps, products, iterations)
        rust_ops_per_sec = iterations / rust_time

        # Calculate speedup
        speedup = python_time / rust_time

        print(f"  Python json.dumps:     {python_time:.4f}s ({python_ops_per_sec:,.0f} ops/sec)")
        print(f"  Rust fast_json_dumps:  {rust_time:.4f}s ({rust_ops_per_sec:,.0f} ops/sec)")
        print(f"  Speedup:               {speedup:.2f}x faster")
        print()

    print("=" * 70)
    print("Summary:")
    print("  Rust-powered JSON serialization provides significant performance")
    print("  improvements, especially with larger datasets. This translates to:")
    print("    - Faster page loads")
    print("    - Lower CPU usage")
    print("    - Better scalability")
    print("=" * 70)


if __name__ == '__main__':
    main()
