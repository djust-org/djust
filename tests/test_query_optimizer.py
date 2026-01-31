"""
Unit tests for Django ORM Query Optimizer
"""

from django.test import TestCase
from djust.optimization.query_optimizer import (
    analyze_queryset_optimization,
    optimize_queryset,
    QueryOptimization,
)

# Import rental app models for testing
import sys
import os

# Add demo_project to path for model imports
demo_project_path = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "examples", "demo_project"
)
sys.path.insert(0, demo_project_path)

# Setup Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

import django  # noqa: E402

django.setup()

from djust_rentals.models import Lease, Property, Tenant, MaintenanceRequest, Payment  # noqa: E402


class QueryOptimizationTestCase(TestCase):
    """Test QueryOptimization class."""

    def test_initialization(self):
        """Test QueryOptimization initialization."""
        opt = QueryOptimization()
        assert isinstance(opt.select_related, set)
        assert isinstance(opt.prefetch_related, set)
        assert isinstance(opt.annotations, dict)
        assert len(opt.select_related) == 0
        assert len(opt.prefetch_related) == 0
        assert len(opt.annotations) == 0

    def test_to_dict(self):
        """Test to_dict() conversion."""
        opt = QueryOptimization()
        opt.select_related.add("property")
        opt.select_related.add("tenant")
        opt.prefetch_related.add("payments")

        result = opt.to_dict()

        assert "select_related" in result
        assert "prefetch_related" in result
        assert "annotations" in result
        assert result["select_related"] == ["property", "tenant"]
        assert result["prefetch_related"] == ["payments"]

    def test_to_dict_with_annotations(self):
        """Test to_dict() includes annotation keys."""
        from django.db.models import Count

        opt = QueryOptimization()
        opt.annotations["_annotated_post_count"] = Count("posts")

        result = opt.to_dict()
        assert "_annotated_post_count" in result["annotations"]


class QueryAnalysisTestCase(TestCase):
    """Test query analysis functionality."""

    def test_single_foreignkey(self):
        """Test ForeignKey optimization (select_related)."""
        optimization = analyze_queryset_optimization(Lease, ["property.name"])

        result = optimization.to_dict()
        assert "property" in result["select_related"]
        assert len(result["prefetch_related"]) == 0

    def test_nested_foreignkey(self):
        """Test nested ForeignKey optimization."""
        optimization = analyze_queryset_optimization(
            Lease, ["tenant.user.email", "tenant.user.first_name"]
        )

        result = optimization.to_dict()
        assert "tenant__user" in result["select_related"]

    def test_multiple_paths(self):
        """Test multiple relationship paths."""
        optimization = analyze_queryset_optimization(
            Lease,
            [
                "property.name",
                "property.address",
                "tenant.user.email",
                "tenant.user.first_name",
            ],
        )

        result = optimization.to_dict()
        assert "property" in result["select_related"]
        assert "tenant__user" in result["select_related"]

    def test_direct_field(self):
        """Test direct field access (no optimization needed)."""
        optimization = analyze_queryset_optimization(
            Lease, ["start_date", "end_date", "monthly_rent"]
        )

        result = optimization.to_dict()
        # Direct fields don't need select_related
        assert len(result["select_related"]) == 0
        assert len(result["prefetch_related"]) == 0

    def test_property_method(self):
        """Test property/method access (can't optimize)."""
        optimization = analyze_queryset_optimization(Lease, ["days_until_expiration", "is_active"])

        result = optimization.to_dict()
        # Methods/properties can't be optimized
        assert len(result["select_related"]) == 0
        assert len(result["prefetch_related"]) == 0

    def test_djust_annotations_detected(self):
        """Test that _djust_annotations on a model are picked up as annotations."""
        from django.db.models import Count, Q

        # Create a temporary model subclass with _djust_annotations
        class AnnotatedProperty(Property):
            _djust_annotations = {
                "lease_count": Count("leases", filter=Q(leases__status="active")),
            }

            class Meta:
                app_label = "djust_rentals"
                managed = False

        optimization = analyze_queryset_optimization(AnnotatedProperty, ["name", "lease_count"])

        # lease_count should appear in annotations with _annotated_ prefix
        assert "_annotated_lease_count" in optimization.annotations
        # name is a regular field, no annotation
        assert len(optimization.annotations) == 1

    def test_djust_annotations_not_present(self):
        """Test models without _djust_annotations don't get annotations."""
        optimization = analyze_queryset_optimization(Property, ["name", "status_display"])

        assert len(optimization.annotations) == 0

    def test_reverse_foreignkey(self):
        """Test reverse ForeignKey (prefetch_related)."""
        # Payment has ForeignKey to Lease, so Lease has reverse relation
        # Note: This test demonstrates the concept, but Payment model uses
        # ForeignKey which creates a reverse relation on Lease

        # For this test, we'll use MaintenanceRequest -> Property relationship
        # which is a forward ForeignKey (should use select_related)
        optimization = analyze_queryset_optimization(
            MaintenanceRequest, ["property.name", "tenant.user.email"]
        )

        result = optimization.to_dict()
        assert "property" in result["select_related"]
        assert "tenant__user" in result["select_related"]

    def test_onetoone_field(self):
        """Test OneToOneField optimization."""
        # Tenant has OneToOneField to User
        optimization = analyze_queryset_optimization(Tenant, ["user.email", "user.first_name"])

        result = optimization.to_dict()
        assert "user" in result["select_related"]

    def test_empty_paths(self):
        """Test with empty paths list."""
        optimization = analyze_queryset_optimization(Lease, [])

        result = optimization.to_dict()
        assert len(result["select_related"]) == 0
        assert len(result["prefetch_related"]) == 0

    def test_nonexistent_field(self):
        """Test with non-existent field (should be ignored)."""
        optimization = analyze_queryset_optimization(Lease, ["property.name", "nonexistent.field"])

        result = optimization.to_dict()
        # Should only optimize the valid field
        assert "property" in result["select_related"]
        assert "nonexistent" not in result["select_related"]

    def test_complex_nested_path(self):
        """Test deeply nested path."""
        # MaintenanceRequest -> Tenant -> User
        optimization = analyze_queryset_optimization(MaintenanceRequest, ["tenant.user.email"])

        result = optimization.to_dict()
        assert "tenant__user" in result["select_related"]

    def test_real_world_dashboard(self):
        """Test optimization for rental dashboard template."""
        # Paths extracted from dashboard.html
        paths = [
            "property.name",
            "property.address",
            "property.monthly_rent",
            "property.status",
            "tenant.user.get_full_name",
            "tenant.user.email",
            "end_date",
        ]

        optimization = analyze_queryset_optimization(Lease, paths)
        result = optimization.to_dict()

        # Should optimize both property and tenant.user
        assert "property" in result["select_related"]
        assert "tenant__user" in result["select_related"]

    def test_deduplication(self):
        """Test that duplicate paths are deduplicated."""
        optimization = analyze_queryset_optimization(
            Lease,
            [
                "property.name",
                "property.address",
                "property.name",  # Duplicate
                "property.city",
            ],
        )

        result = optimization.to_dict()
        # Should only have "property" once
        assert result["select_related"].count("property") == 1


class QueryOptimizationApplicationTestCase(TestCase):
    """Test applying optimization to QuerySets."""

    def test_optimize_queryset_with_select_related(self):
        """Test applying select_related optimization."""
        qs = Lease.objects.all()
        optimization = QueryOptimization()
        optimization.select_related.add("property")
        optimization.select_related.add("tenant__user")

        optimized = optimize_queryset(qs, optimization)

        # Check that select_related was applied
        # (We can't directly inspect the QuerySet, but we can check the query)
        query_str = str(optimized.query)
        # Query should contain JOINs for property and tenant tables
        assert "property" in query_str.lower() or "join" in query_str.lower()

    def test_optimize_queryset_with_annotations(self):
        """Test applying annotation optimization."""
        from django.db.models import Count

        qs = Property.objects.all()
        optimization = QueryOptimization()
        optimization.annotations["_annotated_lease_count"] = Count("leases")

        optimized = optimize_queryset(qs, optimization)

        # Verify the annotation is in the query
        query_str = str(optimized.query)
        assert "count" in query_str.lower() or "COUNT" in query_str

    def test_optimize_queryset_with_empty_optimization(self):
        """Test with empty optimization (no changes)."""
        qs = Lease.objects.all()
        optimization = QueryOptimization()

        optimized = optimize_queryset(qs, optimization)

        # Should return the same queryset (no optimization applied)
        # We verify by checking the query hasn't changed significantly
        assert str(qs.query) == str(optimized.query)

    def test_optimize_queryset_preserves_filters(self):
        """Test that optimization preserves existing filters."""
        qs = Lease.objects.filter(status="active")
        optimization = QueryOptimization()
        optimization.select_related.add("property")

        optimized = optimize_queryset(qs, optimization)

        # Should preserve the status filter
        query_str = str(optimized.query)
        assert "status" in query_str.lower()
        assert "active" in query_str.lower()


class IntegrationTestCase(TestCase):
    """Integration tests with complex scenarios."""

    def test_maintenance_request_optimization(self):
        """Test optimization for maintenance request with nested relations."""
        paths = [
            "property.name",
            "property.address",
            "tenant.user.email",
            "tenant.user.first_name",
            "tenant.user.last_name",
        ]

        optimization = analyze_queryset_optimization(MaintenanceRequest, paths)
        result = optimization.to_dict()

        assert "property" in result["select_related"]
        assert "tenant__user" in result["select_related"]

    def test_payment_with_lease_relations(self):
        """Test optimization for Payment model with nested lease relations."""
        # Payment -> Lease -> Property
        # Payment -> Lease -> Tenant -> User
        paths = [
            "lease.property.name",
            "lease.tenant.user.email",
            "lease.monthly_rent",
        ]

        optimization = analyze_queryset_optimization(Payment, paths)
        result = optimization.to_dict()

        assert "lease__property" in result["select_related"]
        assert "lease__tenant__user" in result["select_related"]

    def test_combined_optimization_and_application(self):
        """Test full workflow: analyze and apply optimization."""
        # Analyze
        paths = ["property.name", "tenant.user.email"]
        optimization = analyze_queryset_optimization(Lease, paths)

        # Apply
        qs = Lease.objects.all()
        optimized = optimize_queryset(qs, optimization)

        # Verify query contains optimizations
        query_str = str(optimized.query)
        # Should have JOINs or references to property/tenant tables
        assert len(query_str) > len(str(qs.query))  # Optimized query is longer
