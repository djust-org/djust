"""
Security tests for LiveComponent rendering.

Tests that mark_safe(f"...") patterns properly escape user input to prevent XSS.
"""

import pytest
from django.test import RequestFactory

from djust.components.base import LiveComponent


class TestComponentSecurityEscaping:
    """Test that component rendering properly escapes potentially malicious input."""

    @pytest.fixture
    def request_factory(self):
        return RequestFactory()

    def test_component_id_with_script_tag_is_escaped(self, request_factory):
        """Component ID containing script tags should be HTML-escaped in inline templates."""
        request = request_factory.get("/")

        class TestComponent(LiveComponent):
            template = "<div>{{ content }}</div>"

            def mount(self, request, **kwargs):
                pass

            def get_context_data(self, **kwargs):
                return {"content": "test"}

        component = TestComponent(request=request)
        # Manually set a malicious component_id
        component.component_id = '<script>alert("xss")</script>'

        html = component.render()

        # Script tags should be escaped
        assert '<script>alert("xss")</script>' not in html
        assert '&lt;script&gt;' in html or '&#x3C;script&#x3E;' in html
        assert 'data-component-id=' in html

    def test_component_id_with_quotes_is_escaped(self, request_factory):
        """Component ID containing quotes should be HTML-escaped in inline templates."""
        request = request_factory.get("/")

        class TestComponent(LiveComponent):
            template = "<div>{{ content }}</div>"

            def mount(self, request, **kwargs):
                pass

            def get_context_data(self, **kwargs):
                return {"content": "test"}

        component = TestComponent(request=request)
        # Malicious ID that breaks out of attribute
        component.component_id = '" onload="alert(1)'

        html = component.render()

        # Should not allow attribute injection
        assert 'onload="alert(1)' not in html
        assert '&quot;' in html or '&#34;' in html or '&#x22;' in html

    def test_component_id_with_angle_brackets_is_escaped(self, request_factory):
        """Component ID containing angle brackets should be HTML-escaped in inline templates."""
        request = request_factory.get("/")

        class TestComponent(LiveComponent):
            template = "<div>{{ content }}</div>"

            def mount(self, request, **kwargs):
                pass

            def get_context_data(self, **kwargs):
                return {"content": "test"}

        component = TestComponent(request=request)
        component.component_id = "<img src=x onerror=alert(1)>"

        html = component.render()

        # Angle brackets should be escaped
        assert '<img src=x' not in html
        assert '&lt;' in html or '&#x3C;' in html

    def test_normal_component_id_renders_correctly(self, request_factory):
        """Normal component IDs should render without issues in inline templates."""
        request = request_factory.get("/")

        class TestComponent(LiveComponent):
            template = "<div>{{ content }}</div>"

            def mount(self, request, **kwargs):
                pass

            def get_context_data(self, **kwargs):
                return {"content": "Hello World"}

        component = TestComponent(request=request)
        # Normal UUID-based component ID
        component.component_id = "TestComponent_abc123"

        html = component.render()

        # Should contain the component ID without escaping
        assert 'data-component-id="TestComponent_abc123"' in html
        assert "Hello World" in html

    def test_component_content_is_not_double_escaped(self, request_factory):
        """Template-rendered content should not be double-escaped in inline templates."""
        request = request_factory.get("/")

        class TestComponent(LiveComponent):
            template = "<div>{{ content }}</div>"

            def mount(self, request, **kwargs):
                pass

            def get_context_data(self, **kwargs):
                # Django templates auto-escape this
                return {"content": "<b>Bold</b>"}

        component = TestComponent(request=request)

        html = component.render()

        # Template should have already escaped the content
        # We should see &lt;b&gt; not &amp;lt;b&amp;gt;
        if "&lt;" in html:  # If template escaped it
            assert "&amp;lt;" not in html  # Should not be double-escaped


class TestExampleComponentSecurity:
    """Test example components for XSS vulnerabilities."""

    @pytest.fixture
    def request_factory(self):
        return RequestFactory()

    def test_status_badge_escapes_malicious_label(self, request_factory):
        """StatusBadge component should escape malicious labels."""
        from djust_rentals.components.status_badge import StatusBadge

        # Create badge with XSS attempt in label
        badge = StatusBadge(
            status="active",
            label='<script>alert("xss")</script>',
            color='green'
        )

        html = badge.render()

        # Script tags should be escaped
        assert '<script>alert("xss")</script>' not in html
        assert '&lt;script&gt;' in html or '&#x3C;script&#x3E;' in html

    def test_status_badge_escapes_img_onerror(self, request_factory):
        """StatusBadge component should escape img onerror XSS."""
        from djust_rentals.components.status_badge import StatusBadge

        badge = StatusBadge(
            status="urgent",
            label='<img src=x onerror=alert(1)>',
            color='red'
        )

        html = badge.render()

        # XSS attempt should be escaped - the < and > should be HTML entities
        assert '<img src=x onerror=alert(1)>' not in html  # Raw HTML should not appear
        assert '&lt;img' in html or '&#x3C;img' in html  # Opening tag should be escaped
        assert '&gt;' in html or '&#x3E;' in html  # Closing tag should be escaped

    def test_data_table_escapes_malicious_headers(self, request_factory):
        """DataTable component should escape malicious header values."""
        from djust_rentals.components.data_table import DataTable

        # Headers with XSS attempts
        headers = ['<script>alert("xss")</script>', "Value"]
        rows = [{"col1": "Test", "col2": "123"}]

        table = DataTable(headers=headers, rows=rows)
        html = table.render()

        # Script tags in headers should be escaped
        assert '<script>alert("xss")</script>' not in html

    def test_data_table_escapes_malicious_cell_values(self, request_factory):
        """DataTable component should escape malicious cell values."""
        from djust_rentals.components.data_table import DataTable

        headers = ["Name", "Value"]
        rows = [
            {"Name": '<img src=x onerror=alert(1)>', "Value": "123"}
        ]

        table = DataTable(headers=headers, rows=rows)
        html = table.render()

        # XSS attempt should be escaped - the < and > should be HTML entities
        assert '<img src=x onerror=alert(1)>' not in html  # Raw HTML should not appear
        assert '&lt;img' in html or '&#x3C;img' in html  # Opening tag should be escaped
        assert '&gt;' in html or '&#x3E;' in html  # Closing tag should be escaped

    def test_page_header_escapes_malicious_title(self, request_factory):
        """PageHeader component should escape malicious titles."""
        from djust_rentals.components.page_header import PageHeader

        header = PageHeader(
            title='<script>alert("xss")</script>',
            subtitle="Safe subtitle"
        )
        html = header.render()

        # Script tags should be escaped
        assert '<script>alert("xss")</script>' not in html
        assert '&lt;script&gt;' in html or '&#x3C;script&#x3E;' in html

    def test_stat_card_escapes_malicious_label(self, request_factory):
        """StatCard component should escape malicious labels."""
        from djust_rentals.components.stat_card import StatCard

        card = StatCard(
            label='<script>alert("xss")</script>',
            value="1234",
            icon="dollar-sign"
        )
        html = card.render()

        # Script tags should be escaped
        assert '<script>alert("xss")</script>' not in html
        assert '&lt;script&gt;' in html or '&#x3C;script&#x3E;' in html

    def test_stat_card_escapes_malicious_value(self, request_factory):
        """StatCard component should escape malicious values."""
        from djust_rentals.components.stat_card import StatCard

        card = StatCard(
            label="Revenue",
            value='<img src=x onerror=alert(1)>',
            icon="dollar-sign"
        )
        html = card.render()

        # XSS attempt should be escaped
        assert '<img src=x onerror=alert(1)>' not in html
        assert '&lt;img' in html or '&#x3C;img' in html

    def test_stat_card_escapes_malicious_trend(self, request_factory):
        """StatCard component should escape malicious trend values."""
        from djust_rentals.components.stat_card import StatCard

        card = StatCard(
            label="Sales",
            value="1000",
            trend='<script>alert("xss")</script>',
            trend_direction="up",
            icon="trending-up"
        )
        html = card.render()

        # Script tags in trend should be escaped
        assert '<script>alert("xss")</script>' not in html
        assert '&lt;script&gt;' in html or '&#x3C;script&#x3E;' in html

    def test_stat_card_escapes_malicious_icon(self, request_factory):
        """StatCard component should escape malicious icon names."""
        from djust_rentals.components.stat_card import StatCard

        card = StatCard(
            label="Users",
            value="500",
            icon='"><script>alert(1)</script><i x="',
        )
        html = card.render()

        # Script tags should be escaped
        assert '<script>alert(1)</script>' not in html
        assert '&lt;script&gt;' in html or '&#x3C;script&#x3E;' in html
