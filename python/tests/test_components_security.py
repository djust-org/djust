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
        assert "&lt;script&gt;" in html or "&#x3C;script&#x3E;" in html
        assert "data-component-id=" in html

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
        assert "&quot;" in html or "&#34;" in html or "&#x22;" in html

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
        assert "<img src=x" not in html
        assert "&lt;" in html or "&#x3C;" in html

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
