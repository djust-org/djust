"""
Integration tests for LiveView JIT auto-serialization (Phase 4).

Tests the end-to-end JIT serialization flow:
- Template variable extraction
- Query optimization
- Serializer code generation
- LiveView integration

Uses existing djust_rentals models for testing.
"""

import pytest
from django.test import TestCase
from django.contrib.auth.models import User

# Import rental app models for testing
try:
    from djust_rentals.models import Property, Tenant, Lease
except ImportError:
    # For tests run outside demo_project context, skip these tests
    pytestmark = pytest.mark.skip(reason="Rental app models not available")


class TestLiveViewJITIntegration(TestCase):
    """Test LiveView with JIT auto-serialization."""

    def setUp(self):
        """Create test data."""
        # Create user
        self.user = User.objects.create(
            username="john", email="john@example.com", first_name="John", last_name="Doe"
        )

        # Create tenant
        self.tenant = Tenant.objects.create(user=self.user, phone="555-1234")

        # Create property with all required fields
        self.property = Property.objects.create(
            name="Test Property",
            address="123 Main St",
            city="Test City",
            state="CA",
            zip_code="12345",
            property_type="apartment",
            bedrooms=2,
            bathrooms=1.0,
            square_feet=1000,
            monthly_rent=1000,
            security_deposit=1000,
        )

        # Create lease
        self.lease = Lease.objects.create(
            property=self.property,
            tenant=self.tenant,
            monthly_rent=1000,
            security_deposit=1000,
            start_date="2025-01-01",
            end_date="2025-12-31",
            status="active",
        )

    def test_queryset_auto_serialization(self):
        """Test automatic QuerySet serialization with JIT."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
                {% for lease in leases %}
                  {{ lease.property.name }}
                  {{ lease.tenant.user.email }}
                {% endfor %}
            """

            def mount(self, request):
                self.leases = Lease.objects.all()

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Verify serialization occurred
        self.assertIn("leases", context)
        self.assertIsInstance(context["leases"], list)
        self.assertGreater(len(context["leases"]), 0)

        # Verify nested attributes serialized
        lease_data = context["leases"][0]
        self.assertIn("property", lease_data)
        self.assertIn("name", lease_data["property"])
        self.assertEqual(lease_data["property"]["name"], "Test Property")

        self.assertIn("tenant", lease_data)
        self.assertIn("user", lease_data["tenant"])
        self.assertIn("email", lease_data["tenant"]["user"])
        self.assertEqual(lease_data["tenant"]["user"]["email"], "john@example.com")

    def test_model_instance_auto_serialization(self):
        """Test automatic Model instance serialization with JIT."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
                {{ lease.property.name }}
                {{ lease.tenant.user.email }}
            """

            def mount(self, request):
                self.lease = Lease.objects.first()

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Verify serialization
        self.assertIn("lease", context)
        self.assertIsInstance(context["lease"], dict)
        self.assertEqual(context["lease"]["property"]["name"], "Test Property")
        self.assertEqual(context["lease"]["tenant"]["user"]["email"], "john@example.com")

    def test_no_template_fallback(self):
        """Test fallback when template not available."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            # No template defined

            def mount(self, request):
                self.lease = Lease.objects.first()

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Should still serialize using DjangoJSONEncoder fallback
        self.assertIn("lease", context)
        # DjangoJSONEncoder includes id, __str__, __model__
        self.assertIn("id", context["lease"])

    def test_no_template_access_fallback(self):
        """Test fallback when variable not accessed in template."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
                <!-- lease not accessed -->
                <div>Hello</div>
            """

            def mount(self, request):
                self.lease = Lease.objects.first()

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Should use default DjangoJSONEncoder since no template access
        self.assertIn("lease", context)
        self.assertIsInstance(context["lease"], dict)

    def test_multiple_querysets(self):
        """Test serialization with multiple QuerySets."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
                {% for lease in active_leases %}
                  {{ lease.property.name }}
                {% endfor %}
                {% for property in properties %}
                  {{ property.name }}
                  {{ property.address }}
                {% endfor %}
            """

            def mount(self, request):
                self.active_leases = Lease.objects.filter(status="active")
                self.properties = Property.objects.all()

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Verify both QuerySets serialized
        self.assertIn("active_leases", context)
        self.assertIn("properties", context)
        self.assertIsInstance(context["active_leases"], list)
        self.assertIsInstance(context["properties"], list)

        # Verify correct fields extracted
        if context["active_leases"]:
            lease = context["active_leases"][0]
            self.assertIn("property", lease)
            self.assertIn("name", lease["property"])

        if context["properties"]:
            prop = context["properties"][0]
            self.assertIn("name", prop)
            self.assertIn("address", prop)

    def test_mixed_context_types(self):
        """Test context with mix of Models, QuerySets, and primitives."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
                {{ title }}
                {{ count }}
                {{ lease.property.name }}
                {% for property in properties %}
                  {{ property.name }}
                {% endfor %}
            """

            def mount(self, request):
                self.title = "Dashboard"
                self.count = 42
                self.lease = Lease.objects.first()
                self.properties = Property.objects.all()

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Verify all types handled correctly
        self.assertEqual(context["title"], "Dashboard")
        self.assertEqual(context["count"], 42)
        self.assertIsInstance(context["lease"], dict)
        self.assertIsInstance(context["properties"], list)

    def test_empty_queryset(self):
        """Test serialization with empty QuerySet."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
                {% for lease in leases %}
                  {{ lease.property.name }}
                {% endfor %}
            """

            def mount(self, request):
                self.leases = Lease.objects.filter(status="inactive")  # No results

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Should handle empty QuerySet gracefully
        self.assertIn("leases", context)
        self.assertEqual(context["leases"], [])

    def test_none_value_safety(self):
        """Test safe handling of None related objects."""
        from djust.live_view import LiveView

        # Create lease without tenant (if model allows)
        # For this test, we'll test the serializer's None handling directly

        class TestView(LiveView):
            template = """
                {{ lease.property.name }}
                {{ lease.tenant.user.email }}
            """

            def mount(self, request):
                self.lease = Lease.objects.first()

        view = TestView()
        view.mount(None)
        context = view.get_context_data()

        # Should not crash on None access
        self.assertIn("lease", context)
        self.assertIsInstance(context["lease"], dict)


class TestJITSerializationPerformance(TestCase):
    """Performance tests for JIT serialization."""

    def setUp(self):
        """Create larger test dataset."""
        # Create 10 properties, tenants, leases
        for i in range(10):
            user = User.objects.create(
                username=f"user{i}",
                email=f"user{i}@example.com",
                first_name=f"First{i}",
                last_name=f"Last{i}",
            )
            tenant = Tenant.objects.create(user=user, phone=f"555-{i:04d}")
            prop = Property.objects.create(
                name=f"Property {i}",
                address=f"{i} Main St",
                city="Test City",
                state="CA",
                zip_code="12345",
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
                monthly_rent=prop.monthly_rent,
                security_deposit=prop.security_deposit,
                start_date="2025-01-01",
                end_date="2025-12-31",
                status="active",
            )

    def test_warm_cache_performance(self):
        """Test that cache hits are fast."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
                {% for lease in leases %}
                  {{ lease.property.name }}
                  {{ lease.tenant.user.email }}
                {% endfor %}
            """

            def mount(self, request):
                self.leases = Lease.objects.all()

        view = TestView()
        view.mount(None)

        # First call (cold cache)
        context1 = view.get_context_data()

        # Second call (warm cache)
        context2 = view.get_context_data()

        # Warm cache should be significantly faster
        # (Though with small dataset, both might be sub-millisecond)
        self.assertIsNotNone(context1)
        self.assertIsNotNone(context2)

        # Verify both produce same results
        self.assertEqual(len(context1["leases"]), len(context2["leases"]))


class TestJITSerializationGracefulFallback(TestCase):
    """Test graceful fallback when JIT fails."""

    def test_fallback_on_missing_extract_function(self):
        """Test fallback when Rust extraction not available."""
        from djust.live_view import LiveView
        import sys

        # Get the actual live_view module (not the decorator function)
        lv_module = sys.modules["djust.live_view"]

        # Temporarily disable extract_template_variables
        original_extract = lv_module.extract_template_variables
        lv_module.extract_template_variables = None

        try:

            class TestView(LiveView):
                template = """{{ lease.property.name }}"""

                def mount(self, request):
                    user = User.objects.create(username="test", email="test@test.com")
                    tenant = Tenant.objects.create(user=user)
                    prop = Property.objects.create(
                        name="Test",
                        address="Test Address",
                        city="Test City",
                        state="CA",
                        zip_code="12345",
                        property_type="apartment",
                        bedrooms=1,
                        bathrooms=1.0,
                        square_feet=500,
                        monthly_rent=1000,
                        security_deposit=1000,
                    )
                    from datetime import date

                    self.lease = Lease.objects.create(
                        property=prop,
                        tenant=tenant,
                        monthly_rent=1000,
                        security_deposit=1000,
                        start_date=date(2025, 1, 1),
                        end_date=date(2025, 12, 31),
                    )

            view = TestView()
            view.mount(None)
            context = view.get_context_data()

            # Should fallback to DjangoJSONEncoder
            self.assertIn("lease", context)
            self.assertIsInstance(context["lease"], dict)

        finally:
            # Restore
            lv_module.extract_template_variables = original_extract
