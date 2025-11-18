"""
Integration tests for Django ORM Query Optimizer with real database queries
"""

import pytest
from django.test import TestCase
from django.test.utils import override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

from djust.optimization.query_optimizer import (
    analyze_queryset_optimization,
    optimize_queryset,
)

# Import models
import sys
import os

demo_project_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "demo_project"
)
sys.path.insert(0, demo_project_path)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

import django

django.setup()

from django.contrib.auth.models import User
from djust_rentals.models import Lease, Property, Tenant


class QueryOptimizationIntegrationTestCase(TestCase):
    """Integration test with real database queries."""

    @classmethod
    def setUpTestData(cls):
        """Create test data."""
        # Create users
        user1 = User.objects.create(
            username="john",
            email="john@example.com",
            first_name="John",
            last_name="Doe",
        )
        user2 = User.objects.create(
            username="jane",
            email="jane@example.com",
            first_name="Jane",
            last_name="Smith",
        )

        # Create tenants
        tenant1 = Tenant.objects.create(
            user=user1, phone="555-1234", emergency_contact_name="Contact 1", emergency_contact_phone="555-0001"
        )
        tenant2 = Tenant.objects.create(
            user=user2, phone="555-5678", emergency_contact_name="Contact 2", emergency_contact_phone="555-0002"
        )

        # Create properties
        prop1 = Property.objects.create(
            name="Property 1",
            address="123 Main St",
            city="City 1",
            state="CA",
            zip_code="12345",
            property_type="apartment",
            bedrooms=2,
            bathrooms=1.0,
            square_feet=1000,
            monthly_rent=1000,
            security_deposit=1000,
        )
        prop2 = Property.objects.create(
            name="Property 2",
            address="456 Oak Ave",
            city="City 2",
            state="NY",
            zip_code="67890",
            property_type="house",
            bedrooms=3,
            bathrooms=2.0,
            square_feet=1500,
            monthly_rent=1500,
            security_deposit=1500,
        )

        # Create leases
        from datetime import date, timedelta

        Lease.objects.create(
            property=prop1,
            tenant=tenant1,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=1000,
            security_deposit=1000,
            status="active",
        )
        Lease.objects.create(
            property=prop2,
            tenant=tenant2,
            start_date=date.today(),
            end_date=date.today() + timedelta(days=365),
            monthly_rent=1500,
            security_deposit=1500,
            status="active",
        )

    def test_n_plus_1_without_optimization(self):
        """Verify N+1 queries occur without optimization."""
        with CaptureQueriesContext(connection) as ctx:
            leases = list(Lease.objects.all())

            # Access nested attributes (triggers N+1)
            for lease in leases:
                _ = lease.property.name
                _ = lease.tenant.user.email

        # Should have multiple queries: 1 for leases + 2 for properties + 2 for tenants + 2 for users
        # Exact count may vary, but should be > 3
        query_count_without_opt = len(ctx.captured_queries)
        assert query_count_without_opt >= 5, f"Expected >= 5 queries, got {query_count_without_opt}"

    def test_optimization_eliminates_n_plus_1(self):
        """Verify optimization eliminates N+1 queries."""
        # Analyze and optimize
        optimization = analyze_queryset_optimization(
            Lease, ["property.name", "tenant.user.email"]
        )

        with CaptureQueriesContext(connection) as ctx:
            leases = Lease.objects.all()
            leases = optimize_queryset(leases, optimization)
            leases = list(leases)  # Execute query

            # Access nested attributes (should use JOINs, not N+1)
            for lease in leases:
                _ = lease.property.name
                _ = lease.tenant.user.email

        # Should have exactly 1 query with JOINs
        query_count_with_opt = len(ctx.captured_queries)
        assert query_count_with_opt == 1, f"Expected 1 query, got {query_count_with_opt}"

        # Verify JOIN was used
        query = ctx.captured_queries[0]["sql"]
        assert "JOIN" in query.upper(), "Expected JOIN in optimized query"

    def test_query_count_comparison(self):
        """Compare query counts with and without optimization."""
        # Without optimization
        with CaptureQueriesContext(connection) as ctx_unopt:
            leases = list(Lease.objects.all())
            for lease in leases:
                _ = lease.property.name
                _ = lease.property.address
                _ = lease.tenant.user.email

        queries_without = len(ctx_unopt.captured_queries)

        # With optimization
        optimization = analyze_queryset_optimization(
            Lease, ["property.name", "property.address", "tenant.user.email"]
        )

        with CaptureQueriesContext(connection) as ctx_opt:
            leases = Lease.objects.all()
            leases = optimize_queryset(leases, optimization)
            leases = list(leases)
            for lease in leases:
                _ = lease.property.name
                _ = lease.property.address
                _ = lease.tenant.user.email

        queries_with = len(ctx_opt.captured_queries)

        # Should reduce queries significantly
        assert queries_with < queries_without, f"Expected fewer queries with optimization: {queries_with} vs {queries_without}"
        # Should ideally be 1 query
        assert queries_with <= 2, f"Expected <= 2 queries with optimization, got {queries_with}"

    def test_real_world_dashboard_query_count(self):
        """Test query count for real dashboard template."""
        paths = [
            "property.name",
            "property.address",
            "tenant.user.first_name",
            "tenant.user.last_name",
            "tenant.user.email",
        ]

        optimization = analyze_queryset_optimization(Lease, paths)

        with CaptureQueriesContext(connection) as ctx:
            leases = Lease.objects.filter(status="active")
            leases = optimize_queryset(leases, optimization)
            leases = list(leases)

            # Simulate template rendering
            for lease in leases:
                _ = lease.property.name
                _ = lease.property.address
                _ = lease.tenant.user.email
                _ = lease.tenant.user.first_name
                _ = lease.tenant.user.last_name

        # Should be 1 query (with JOINs)
        assert len(ctx.captured_queries) == 1, f"Expected 1 query, got {len(ctx.captured_queries)}"

    def test_complex_nested_optimization(self):
        """Test optimization with deeply nested relationships."""
        paths = [
            "property.name",
            "property.city",
            "property.state",
            "tenant.user.email",
            "tenant.user.first_name",
            "tenant.user.last_name",
            "tenant.phone",
        ]

        optimization = analyze_queryset_optimization(Lease, paths)

        with CaptureQueriesContext(connection) as ctx:
            leases = Lease.objects.all()
            leases = optimize_queryset(leases, optimization)
            leases = list(leases)

            # Access all fields
            for lease in leases:
                _ = lease.property.name
                _ = lease.property.city
                _ = lease.property.state
                _ = lease.tenant.user.email
                _ = lease.tenant.user.first_name
                _ = lease.tenant.user.last_name
                _ = lease.tenant.phone

        # Should be 1 query with multiple JOINs
        assert len(ctx.captured_queries) == 1
        query = ctx.captured_queries[0]["sql"]
        # Should have JOINs for property, tenant, and user
        assert query.upper().count("JOIN") >= 2, "Expected multiple JOINs"

    def test_optimization_with_filter(self):
        """Test that optimization works with filtered QuerySets."""
        optimization = analyze_queryset_optimization(
            Lease, ["property.name", "tenant.user.email"]
        )

        with CaptureQueriesContext(connection) as ctx:
            leases = Lease.objects.filter(status="active")
            leases = optimize_queryset(leases, optimization)
            leases = list(leases)

            for lease in leases:
                _ = lease.property.name
                _ = lease.tenant.user.email

        # Should be 1 query
        assert len(ctx.captured_queries) == 1
        query = ctx.captured_queries[0]["sql"]
        # Should have both filter and JOINs
        assert "status" in query.lower() or "active" in query.lower()
        assert "JOIN" in query.upper()

    def test_optimization_with_ordering(self):
        """Test that optimization works with ordered QuerySets."""
        optimization = analyze_queryset_optimization(
            Lease, ["property.name", "tenant.user.email"]
        )

        with CaptureQueriesContext(connection) as ctx:
            leases = Lease.objects.all().order_by("-start_date")
            leases = optimize_queryset(leases, optimization)
            leases = list(leases)

            for lease in leases:
                _ = lease.property.name
                _ = lease.tenant.user.email

        # Should be 1 query
        assert len(ctx.captured_queries) == 1
        query = ctx.captured_queries[0]["sql"]
        # Should have ORDER BY
        assert "ORDER BY" in query.upper()

    def test_optimization_preserves_query_results(self):
        """Test that optimization doesn't change query results."""
        # Get results without optimization
        leases_unopt = list(Lease.objects.all())
        results_unopt = [
            {
                "property_name": lease.property.name,
                "tenant_email": lease.tenant.user.email,
            }
            for lease in leases_unopt
        ]

        # Get results with optimization
        optimization = analyze_queryset_optimization(
            Lease, ["property.name", "tenant.user.email"]
        )
        leases_opt = Lease.objects.all()
        leases_opt = optimize_queryset(leases_opt, optimization)
        leases_opt = list(leases_opt)
        results_opt = [
            {
                "property_name": lease.property.name,
                "tenant_email": lease.tenant.user.email,
            }
            for lease in leases_opt
        ]

        # Results should be identical
        assert len(results_opt) == len(results_unopt)
        assert results_opt == results_unopt


class PerformanceTestCase(TestCase):
    """Performance tests with larger datasets."""

    @classmethod
    def setUpTestData(cls):
        """Create larger test dataset."""
        from datetime import date, timedelta

        # Create 20 users, tenants, properties, and leases
        for i in range(20):
            user = User.objects.create(
                username=f"user{i}",
                email=f"user{i}@example.com",
                first_name=f"First{i}",
                last_name=f"Last{i}",
            )
            tenant = Tenant.objects.create(
                user=user, phone=f"555-{i:04d}", emergency_contact_name=f"Contact {i}", emergency_contact_phone=f"555-{i+1000:04d}"
            )
            prop = Property.objects.create(
                name=f"Property {i}",
                address=f"{i} Main St",
                city=f"City {i}",
                state="CA",
                zip_code=f"{12345+i}",
                property_type="apartment",
                bedrooms=2,
                bathrooms=1.0,
                square_feet=1000,
                monthly_rent=1000 + i * 100,
                security_deposit=1000 + i * 100,
            )
            Lease.objects.create(
                property=prop,
                tenant=tenant,
                start_date=date.today(),
                end_date=date.today() + timedelta(days=365),
                monthly_rent=1000 + i * 100,
                security_deposit=1000 + i * 100,
                status="active",
            )

    def test_large_dataset_optimization(self):
        """Test optimization with larger dataset (20 records)."""
        # Without optimization
        with CaptureQueriesContext(connection) as ctx_unopt:
            leases = list(Lease.objects.all())
            for lease in leases:
                _ = lease.property.name
                _ = lease.tenant.user.email

        queries_without = len(ctx_unopt.captured_queries)

        # With optimization
        optimization = analyze_queryset_optimization(
            Lease, ["property.name", "tenant.user.email"]
        )

        with CaptureQueriesContext(connection) as ctx_opt:
            leases = Lease.objects.all()
            leases = optimize_queryset(leases, optimization)
            leases = list(leases)
            for lease in leases:
                _ = lease.property.name
                _ = lease.tenant.user.email

        queries_with = len(ctx_opt.captured_queries)

        # Should reduce from ~41 queries to 1 query
        # (1 for leases + 20 for properties + 20 for tenants -> 1 query with JOINs)
        assert queries_without >= 40, f"Expected >= 40 queries without optimization, got {queries_without}"
        assert queries_with == 1, f"Expected 1 query with optimization, got {queries_with}"

        # Calculate improvement
        improvement = (queries_without - queries_with) / queries_without * 100
        print(f"\nQuery reduction: {queries_without} -> {queries_with} ({improvement:.1f}% improvement)")
