"""
Integration tests for rendering model data a LiveView supplies from ``get_context_data()``.

An explicit view's context is exactly its declared ``state()`` fields plus what
``get_context_data()`` returns (Phase 4's JIT serialization of the attribute
context ran only for the legacy exposure policy, which is gone): models and
QuerySets reach the template through the engine's floor-enforcing handles, and
the template's relation paths resolve against them.

Uses existing djust_rentals models for testing.
"""

import pytest
from djust.live_view import LiveView
from djust.testing import LiveViewTestClient
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

    def _render(self, template, **context):
        class TestView(LiveView):
            exposure_policy = "explicit"

            def get_context_data(self, **kwargs):
                ctx = super().get_context_data(**kwargs)
                ctx.update({name: make() for name, make in context.items()})
                return ctx

        TestView.template = "<div dj-root>" + template + "</div>"
        client = LiveViewTestClient(TestView)
        client.mount()
        return client.render()

    def test_queryset_renders_its_relations(self):
        """A QuerySet's rows resolve their relation paths in the template."""
        html = self._render(
            "{% for lease in leases %}{{ lease.property.name }}|{{ lease.tenant.user.email }}"
            "{% endfor %}",
            leases=lambda: Lease.objects.all(),
        )
        self.assertIn("Test Property|john@example.com", html)

    def test_model_instance_renders_its_relations(self):
        html = self._render(
            "{{ lease.property.name }}|{{ lease.tenant.user.email }}",
            lease=lambda: Lease.objects.first(),
        )
        self.assertIn("Test Property|john@example.com", html)

    def test_multiple_querysets(self):
        html = self._render(
            "{% for lease in active_leases %}[{{ lease.property.name }}]{% endfor %}"
            "{% for property in properties %}[{{ property.name }}:{{ property.address }}]"
            "{% endfor %}",
            active_leases=lambda: Lease.objects.filter(status="active"),
            properties=lambda: Property.objects.all(),
        )
        self.assertIn("[Test Property][Test Property:123 Main St]", html)

    def test_mixed_context_types(self):
        html = self._render(
            "{{ title }}|{{ count }}|{{ lease.property.name }}"
            "{% for property in properties %}|{{ property.name }}{% endfor %}",
            title=lambda: "Dashboard",
            count=lambda: 42,
            lease=lambda: Lease.objects.first(),
            properties=lambda: Property.objects.all(),
        )
        self.assertIn("Dashboard|42|Test Property|Test Property", html)

    def test_empty_queryset(self):
        html = self._render(
            "{% for lease in leases %}[{{ lease.property.name }}]{% endfor %}done",
            leases=lambda: Lease.objects.filter(status="inactive"),
        )
        self.assertIn("<div dj-root>done</div>", html)

    def test_a_missing_model_renders_empty(self):
        """A ``None`` model, and every path read off it, renders empty."""
        html = self._render(
            "<{{ lease.property.name }}|{{ lease.tenant.user.email }}>", lease=lambda: None
        )
        self.assertIn("<|>", html)

    def test_repeated_renders_agree(self):
        """A second render of the same view (a warm cache) gives the same page."""

        def make():
            class TestView(LiveView):
                exposure_policy = "explicit"
                template = (
                    "<div dj-root>{% for lease in leases %}{{ lease.property.name }}"
                    "{% endfor %}</div>"
                )

                def get_context_data(self, **kwargs):
                    ctx = super().get_context_data(**kwargs)
                    ctx["leases"] = Lease.objects.all()
                    return ctx

            client = LiveViewTestClient(TestView)
            client.mount()
            return client

        client = make()
        self.assertEqual(client.render(), client.render())
