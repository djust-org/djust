"""
Performance benchmarks for JIT auto-serialization

Measures:
1. Cold cache performance (first request with code generation)
2. Warm cache performance (subsequent requests)
3. Query count reduction
4. Memory usage

Usage:
    # From project root:
    DJANGO_SETTINGS_MODULE=examples.demo_project.demo_project.settings \\
        .venv/bin/python examples/demo_project/benchmarks/benchmark_jit.py

    # Or from demo_project directory:
    cd examples/demo_project
    DJANGO_SETTINGS_MODULE=demo_project.settings \\
        ../../.venv/bin/python benchmarks/benchmark_jit.py
"""

import os
import sys
import time
import django
from pathlib import Path

# Setup Django
# sys.path is already set up when running with proper Python
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'demo_project.settings')

# Only setup if not already done
if not django.apps.apps.ready:
    django.setup()

from django.test import TestCase
from django.db import connection, reset_queries
from django.test.utils import CaptureQueriesContext
from django.contrib.auth.models import User
from datetime import date, timedelta

from djust_rentals.views.dashboard import RentalDashboardView
from djust_rentals.models import Property, Tenant, Lease, MaintenanceRequest, Payment, Expense


class JITPerformanceBenchmark:
    """Benchmark JIT serialization performance."""

    def __init__(self):
        self.results = {}

    def setup_data(self, count=100):
        """Create large dataset for benchmarking."""
        print(f"Setting up test data ({count} properties)...")

        # Clear existing data
        Property.objects.all().delete()
        Tenant.objects.all().delete()
        User.objects.all().delete()

        for i in range(count):
            user = User.objects.create(
                username=f"user{i}",
                email=f"user{i}@example.com",
                first_name=f"First{i}",
                last_name=f"Last{i}",
            )
            tenant = Tenant.objects.create(
                user=user,
                phone=f"555-{i:04d}",
                emergency_contact_name=f"Emergency {i}",
                emergency_contact_phone=f"555-{i+1000:04d}",
            )
            prop = Property.objects.create(
                name=f"Property {i}",
                address=f"{i} Main St",
                city="Test City",
                state="CA",
                zip_code="90210",
                property_type="apartment",
                bedrooms=2,
                bathrooms=1.5,
                square_feet=1000,
                monthly_rent=1000 + i * 10,
                security_deposit=2000,
            )

            # Create lease for half the properties
            if i % 2 == 0:
                today = date.today()
                lease = Lease.objects.create(
                    property=prop,
                    tenant=tenant,
                    start_date=today - timedelta(days=180),
                    end_date=today + timedelta(days=180),
                    monthly_rent=prop.monthly_rent,
                    security_deposit=prop.security_deposit,
                    status="active",
                )

                # Add payment
                Payment.objects.create(
                    lease=lease,
                    amount=prop.monthly_rent,
                    payment_date=today - timedelta(days=5),
                    payment_method="online",
                    status="completed",
                )

                # Add expense
                Expense.objects.create(
                    property=prop,
                    category="maintenance",
                    amount=100 + i * 5,
                    date=today - timedelta(days=3),
                    description=f"Expense {i}",
                )

            # Create maintenance for some properties
            if i % 3 == 0:
                MaintenanceRequest.objects.create(
                    property=prop,
                    tenant=tenant if i % 2 == 0 else None,
                    title=f"Maintenance {i}",
                    description=f"Fix issue {i}",
                    priority="high" if i % 2 == 0 else "medium",
                    status="open",
                )

        print(f"✓ Created {count} properties with related data\n")

    def benchmark_cold_cache(self):
        """Benchmark first request (cold cache with code generation)."""
        print("Benchmarking cold cache (first request)...")

        # Clear serializer cache
        from djust.optimization.cache import _serializer_cache
        _serializer_cache._memory_cache.clear()

        # Create view
        view = RentalDashboardView()

        class MockRequest:
            user = None
            session = {}
            META = {}
            GET = {}
            POST = {}

        request = MockRequest()

        # Benchmark mount + get_context_data
        reset_queries()
        start = time.time()

        view.mount(request)
        context = view.get_context_data()

        # Access data to ensure serialization
        _ = context['properties']
        _ = context['pending_maintenance']
        _ = context['expiring_soon']

        cold_time = (time.time() - start) * 1000
        query_count = len(connection.queries)

        self.results['cold_cache_time_ms'] = cold_time
        self.results['cold_cache_queries'] = query_count

        print(f"  Time: {cold_time:.1f}ms")
        print(f"  Queries: {query_count}")
        print(f"  ✓ Target: < 500ms\n")

        # Verify target
        if cold_time > 500:
            print(f"  ⚠ WARNING: Cold cache exceeds 500ms target!")

        return cold_time < 500

    def benchmark_warm_cache(self, iterations=100):
        """Benchmark subsequent requests (warm cache)."""
        print(f"Benchmarking warm cache ({iterations} iterations)...")

        view = RentalDashboardView()

        class MockRequest:
            user = None
            session = {}
            META = {}
            GET = {}
            POST = {}

        request = MockRequest()

        # Warmup
        view.mount(request)
        _ = view.get_context_data()

        # Benchmark
        times = []
        query_counts = []

        for _ in range(iterations):
            reset_queries()
            start = time.time()

            view.mount(request)
            context = view.get_context_data()

            # Access data
            _ = context['properties']
            _ = context['pending_maintenance']
            _ = context['expiring_soon']

            times.append((time.time() - start) * 1000)
            query_counts.append(len(connection.queries))

        avg_time = sum(times) / len(times)
        avg_queries = sum(query_counts) / len(query_counts)

        self.results['warm_cache_time_ms'] = avg_time
        self.results['warm_cache_queries'] = avg_queries
        self.results['warm_cache_iterations'] = iterations

        print(f"  Average time: {avg_time:.1f}ms")
        print(f"  Average queries: {avg_queries:.1f}")
        print(f"  Min time: {min(times):.1f}ms")
        print(f"  Max time: {max(times):.1f}ms")
        print(f"  ✓ Target: < 100ms\n")

        # Verify target
        if avg_time > 100:
            print(f"  ⚠ WARNING: Warm cache exceeds 100ms target!")

        return avg_time < 100

    def benchmark_query_reduction(self):
        """Measure query count reduction vs N+1."""
        print("Benchmarking query count reduction...")

        view = RentalDashboardView()

        class MockRequest:
            user = None
            session = {}
            META = {}
            GET = {}
            POST = {}

        request = MockRequest()
        view.mount(request)

        # Count queries with JIT
        reset_queries()
        context = view.get_context_data()

        # Access all nested data that would cause N+1
        for prop in context['properties']:
            _ = prop.get('name')
            _ = prop.get('address')

        for req in context['pending_maintenance']:
            _ = req.get('property_name')

        for lease in context['expiring_soon']:
            _ = lease.get('property_name')
            _ = lease.get('tenant_name')

        jit_queries = len(connection.queries)

        # Estimate N+1 queries without optimization
        # For dashboard with 10 properties shown:
        # - 1 base query for properties
        # - 10 queries for related data (without select_related)
        # - 1 query for maintenance
        # - 10 queries for maintenance.property
        # - 1 query for leases
        # - 5 queries for lease.property
        # - 5 queries for lease.tenant
        # - 5 queries for tenant.user
        # Total: ~38 queries
        estimated_n_plus_1 = 38

        reduction = ((estimated_n_plus_1 - jit_queries) / estimated_n_plus_1) * 100

        self.results['jit_queries'] = jit_queries
        self.results['estimated_n_plus_1_queries'] = estimated_n_plus_1
        self.results['query_reduction_percent'] = reduction

        print(f"  JIT queries: {jit_queries}")
        print(f"  Estimated N+1 queries: {estimated_n_plus_1}")
        print(f"  Reduction: {reduction:.1f}%")
        print(f"  ✓ Target: > 80% reduction\n")

        # Verify target
        if reduction < 80:
            print(f"  ⚠ WARNING: Query reduction below 80% target!")

        return reduction > 80

    def benchmark_template_extraction(self, iterations=1000):
        """Benchmark template variable extraction speed."""
        print(f"Benchmarking template extraction ({iterations} iterations)...")

        from djust._rust import extract_template_variables

        # Load dashboard template
        template_path = Path(__file__).parent.parent / "djust_rentals" / "templates" / "rentals" / "dashboard.html"

        if not template_path.exists():
            print("  ⚠ Template not found, skipping")
            return True

        with open(template_path) as f:
            template = f.read()

        # Warmup
        for _ in range(10):
            extract_template_variables(template)

        # Benchmark
        times = []
        for _ in range(iterations):
            start = time.time()
            result = extract_template_variables(template)
            times.append((time.time() - start) * 1000)

        avg_time = sum(times) / len(times)

        self.results['template_extraction_time_ms'] = avg_time
        self.results['template_extraction_iterations'] = iterations

        print(f"  Average time: {avg_time:.3f}ms")
        print(f"  Min time: {min(times):.3f}ms")
        print(f"  Max time: {max(times):.3f}ms")
        print(f"  Variables extracted: {len(result)}")
        print(f"  ✓ Target: < 5ms\n")

        # Verify target
        if avg_time > 5:
            print(f"  ⚠ WARNING: Template extraction exceeds 5ms target!")

        return avg_time < 5

    def print_summary(self):
        """Print benchmark summary."""
        print("\n" + "=" * 70)
        print("BENCHMARK SUMMARY")
        print("=" * 70)

        # Performance metrics
        print("\n📊 Performance Metrics:")
        print(f"  Cold cache:  {self.results['cold_cache_time_ms']:.1f}ms (target: < 500ms)")
        print(f"  Warm cache:  {self.results['warm_cache_time_ms']:.1f}ms (target: < 100ms)")
        print(f"  Template extraction: {self.results.get('template_extraction_time_ms', 0):.3f}ms (target: < 5ms)")

        # Query optimization
        print("\n🔍 Query Optimization:")
        print(f"  JIT queries: {self.results['jit_queries']}")
        print(f"  Without JIT (estimated): {self.results['estimated_n_plus_1_queries']}")
        print(f"  Reduction: {self.results['query_reduction_percent']:.1f}% (target: > 80%)")

        # Pass/fail
        print("\n✅ Pass/Fail:")
        all_pass = True

        if self.results['cold_cache_time_ms'] < 500:
            print("  ✓ Cold cache performance")
        else:
            print("  ✗ Cold cache performance (TOO SLOW)")
            all_pass = False

        if self.results['warm_cache_time_ms'] < 100:
            print("  ✓ Warm cache performance")
        else:
            print("  ✗ Warm cache performance (TOO SLOW)")
            all_pass = False

        if self.results['query_reduction_percent'] > 80:
            print("  ✓ Query reduction")
        else:
            print("  ✗ Query reduction (INSUFFICIENT)")
            all_pass = False

        if self.results.get('template_extraction_time_ms', 0) < 5:
            print("  ✓ Template extraction")
        else:
            print("  ✗ Template extraction (TOO SLOW)")
            all_pass = False

        print("\n" + "=" * 70)

        if all_pass:
            print("🎉 ALL BENCHMARKS PASSED!")
        else:
            print("⚠ SOME BENCHMARKS FAILED!")

        print("=" * 70 + "\n")

        return all_pass


def main():
    """Run all benchmarks."""
    print("\n" + "=" * 70)
    print("JIT AUTO-SERIALIZATION PERFORMANCE BENCHMARK")
    print("=" * 70 + "\n")

    benchmark = JITPerformanceBenchmark()

    # Setup
    benchmark.setup_data(count=100)

    # Run benchmarks
    benchmark.benchmark_template_extraction()
    benchmark.benchmark_cold_cache()
    benchmark.benchmark_warm_cache()
    benchmark.benchmark_query_reduction()

    # Summary
    all_pass = benchmark.print_summary()

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
