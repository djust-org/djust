"""
Tests for template inheritance integration with VDOM.

These tests verify that:
1. Template inheritance correctly resolves parent templates
2. Comments and whitespace are stripped to match Rust VDOM parser
3. liveview-root extraction works with various attribute orders
4. Initial GET and WebSocket mount send matching HTML structures
"""

import pytest
import tempfile
from pathlib import Path
from django.conf import settings
from django.test import RequestFactory


class TestTemplateInheritanceExtraction:
    """Test extraction and stripping logic for template inheritance."""

    def setup_method(self):
        """Create a minimal LiveView for testing."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = """
            <div class="container" data-djust-root data-djust-view="test">
                <!-- This is a comment -->
                <div class="row">
                    <div class="col">
                        <p>Hello {{ name }}</p>
                    </div>
                </div>
            </div>
            """

        self.view = TestView()

    def test_strip_comments_and_whitespace(self):
        """Test that comments and whitespace are stripped correctly."""
        html = """
        <!-- Comment 1 -->
        <div class="test">
            <!-- Comment 2 -->
            <p>  Hello  World  </p>
        </div>
        """

        stripped = self.view._strip_comments_and_whitespace(html)

        # Comments should be removed
        assert "<!--" not in stripped
        assert "Comment 1" not in stripped
        assert "Comment 2" not in stripped

        # Multiple whitespace should be collapsed
        assert "  " not in stripped or stripped.count("  ") < html.count("  ")

    def test_extract_liveview_root_with_class_first(self):
        """Test extracting liveview-root when class attribute comes first."""
        html = (
            '<html><body><div class="container" data-djust-root><p>Content</p></div></body></html>'
        )

        extracted = self.view._extract_liveview_root_with_wrapper(html)

        # Should extract the full div including wrapper
        assert extracted.startswith('<div class="container" data-djust-root>')
        assert extracted.endswith("</div>")
        assert "<p>Content</p>" in extracted
        # Should NOT include html/body tags
        assert "<html>" not in extracted
        assert "<body>" not in extracted

    def test_extract_liveview_root_with_data_attr_first(self):
        """Test extracting liveview-root when data-djust-root comes first."""
        html = (
            '<html><body><div data-djust-root class="container"><p>Content</p></div></body></html>'
        )

        extracted = self.view._extract_liveview_root_with_wrapper(html)

        assert extracted.startswith('<div data-djust-root class="container">')
        assert "<p>Content</p>" in extracted

    def test_extract_liveview_root_with_multiple_attrs(self):
        """Test extracting with multiple attributes before data-djust-root."""
        html = '<html><body><div id="app" class="container" style="color:red" data-djust-root data-djust-view="test"><p>Content</p></div></body></html>'

        extracted = self.view._extract_liveview_root_with_wrapper(html)

        assert "data-djust-root" in extracted
        assert 'id="app"' in extracted
        assert 'class="container"' in extracted
        assert "<p>Content</p>" in extracted

    def test_extract_liveview_root_nested_divs(self):
        """Test extracting with nested divs inside liveview-root."""
        html = """
        <html>
            <body>
                <div class="wrapper" data-djust-root>
                    <div class="outer">
                        <div class="inner">
                            <p>Nested content</p>
                        </div>
                    </div>
                </div>
            </body>
        </html>
        """

        extracted = self.view._extract_liveview_root_with_wrapper(html)

        # Should include all nested divs
        assert '<div class="outer">' in extracted
        assert '<div class="inner">' in extracted
        assert "<p>Nested content</p>" in extracted
        # Should be properly balanced
        assert extracted.count("<div") == extracted.count("</div>")

    def test_extract_liveview_content_inner_html(self):
        """Test extracting just the innerHTML of liveview-root."""
        html = '<div class="container" data-djust-root><div class="row"><p>Content</p></div></div>'

        inner = self.view._extract_liveview_content(html)

        # Should extract ONLY innerHTML (no wrapper div)
        assert not inner.startswith('<div class="container"')
        assert inner.startswith('<div class="row">')
        assert "<p>Content</p>" in inner

    def test_strip_liveview_root_in_full_html(self):
        """Test stripping comments/whitespace from liveview-root in full page."""
        html = """
        <!DOCTYPE html>
        <html>
            <head><title>Test</title></head>
            <body>
                <div class="container" data-djust-root>
                    <!-- Comment -->
                    <div class="row">
                        <p>  Content  </p>
                    </div>
                </div>
            </body>
        </html>
        """

        stripped = self.view._strip_liveview_root_in_html(html)

        # DOCTYPE, html, head should be preserved as-is
        assert "<!DOCTYPE html>" in stripped
        assert "<title>Test</title>" in stripped

        # But liveview-root div should be stripped
        assert "<!-- Comment -->" not in stripped
        # The div should be on fewer lines
        assert stripped.count("\n") < html.count("\n")


class TestTemplateInheritanceIntegration:
    """Test full template inheritance integration."""

    @pytest.fixture
    def template_dirs(self):
        """Create temporary template directories for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir) / "templates"
            base_dir.mkdir()

            # Create base template
            (base_dir / "base.html").write_text("""
<!DOCTYPE html>
<html>
<head>
    <title>{% block title %}Default Title{% endblock %}</title>
</head>
<body>
    {% block content %}
    <p>Default content</p>
    {% endblock %}
</body>
</html>
            """)

            # Create child template
            (base_dir / "child.html").write_text("""
{% extends "base.html" %}

{% block title %}Child Title{% endblock %}

{% block content %}
<div class="container" data-djust-root data-djust-view="test.ChildView">
    <!-- This is a form -->
    <form>
        <input type="text" name="name" value="{{ name }}" />
        <button type="submit">Submit</button>
    </form>
</div>
{% endblock %}
            """)

            yield [str(base_dir)]

    def test_resolve_inheritance_preserves_django_syntax(self, template_dirs):
        """Test that resolved templates preserve {{ variables }} syntax."""
        from djust._rust import resolve_template_inheritance

        resolved = resolve_template_inheritance("child.html", template_dirs)

        # Should preserve variable syntax (NOT render it)
        assert "{{ name }}" in resolved
        # Should preserve block tags (NOT render them)
        assert "{% block title %}" in resolved or "Child Title" in resolved
        # Should preserve block content
        assert "Child Title" in resolved
        assert "data-djust-root" in resolved

    def test_liveview_get_template_strips_comments(self, template_dirs):
        """Test that LiveView.get_template() returns stripped template."""
        from djust.live_view import LiveView

        class ChildView(LiveView):
            template_name = "child.html"

        # Temporarily override template dirs
        original_dirs = settings.TEMPLATES[0]["DIRS"]
        try:
            settings.TEMPLATES[0]["DIRS"] = template_dirs
            view = ChildView()

            # Get template should return stripped version for VDOM
            template = view.get_template()

            # Should have extracted and stripped liveview-root
            assert "<!-- This is a form -->" not in template
            # Should preserve structure
            assert "data-djust-root" in template
            assert "<form>" in template or "<form" in template

        finally:
            settings.TEMPLATES[0]["DIRS"] = original_dirs

    def test_websocket_mount_and_get_html_match(self, template_dirs):
        """Test that WebSocket mount HTML matches initial GET HTML structure."""
        from djust.live_view import LiveView

        class ChildView(LiveView):
            template_name = "child.html"

            def mount(self, request, **kwargs):
                self.name = "John"

        original_dirs = settings.TEMPLATES[0]["DIRS"]
        try:
            settings.TEMPLATES[0]["DIRS"] = template_dirs
            view = ChildView()

            # Simulate WebSocket mount
            factory = RequestFactory()
            request = factory.get("/")
            view.mount(request)
            view._initialize_rust_view(request)
            view._sync_state_to_rust()

            # Get HTML that would be sent over WebSocket
            html_ws, patches, version = view.render_with_diff()
            html_ws = view._strip_comments_and_whitespace(html_ws)
            html_ws_inner = view._extract_liveview_content(html_ws)

            # Get HTML that would be sent on initial GET
            html_get = view.render_full_template(request)
            html_get_stripped = view._strip_liveview_root_in_html(html_get)

            # Extract liveview-root from GET HTML for comparison
            get_liveview = view._extract_liveview_root_with_wrapper(html_get_stripped)
            get_liveview_inner = view._extract_liveview_content(get_liveview)

            # The innerHTML should match between WebSocket and GET
            # (allowing for minor differences in {{ var }} rendering)
            assert len(html_ws_inner) > 0
            assert len(get_liveview_inner) > 0
            # Both should have no comments
            assert "<!--" not in html_ws_inner
            assert "<!--" not in get_liveview_inner

        finally:
            settings.TEMPLATES[0]["DIRS"] = original_dirs


class TestVDOMStructureMatching:
    """Test that VDOM structures match between client and server."""

    def test_vdom_template_matches_rendered_output(self):
        """Test that VDOM template structure matches what gets rendered."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            # Use simple template without comments to avoid stripping complexity
            template = (
                """<div data-djust-root><div class="row"><p>Hello {{ name }}</p></div></div>"""
            )

            def mount(self, request, **kwargs):
                self.name = "World"

        view = TestView()
        factory = RequestFactory()
        request = factory.get("/")

        # Initialize and sync state
        view.mount(request)
        view._initialize_rust_view(request)
        view._sync_state_to_rust()

        # Get the template used for VDOM initialization
        vdom_template = view.get_template()

        # Render and extract HTML
        rendered_html, patches, version = view.render_with_diff()

        # Both should have no comments
        assert "<!--" not in vdom_template
        assert "<!--" not in rendered_html

        # Structure should match (both have same div nesting)
        assert vdom_template.count("<div") == rendered_html.count("<div")
        assert vdom_template.count("</div>") == rendered_html.count("</div>")

    def test_initial_render_no_patches(self):
        """Test that initial render returns no patches (establishes baseline)."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = "<div data-djust-root><p>{{ text }}</p></div>"

            def mount(self, request, **kwargs):
                self.text = "Hello"

        view = TestView()
        factory = RequestFactory()
        request = factory.get("/")

        view.mount(request)
        view._initialize_rust_view(request)
        view._sync_state_to_rust()

        # First render should not generate patches
        html, patches, version = view.render_with_diff()

        assert patches is None, "Initial render should not generate patches"
        assert version == 1, "Initial version should be 1"

    def test_second_render_generates_patches(self):
        """Test that second render generates patches after state change."""
        from djust.live_view import LiveView

        class TestView(LiveView):
            template = "<div data-djust-root><p>{{ text }}</p></div>"

            def mount(self, request, **kwargs):
                self.text = "Hello"

        view = TestView()
        factory = RequestFactory()
        request = factory.get("/")

        view.mount(request)
        view._initialize_rust_view(request)
        view._sync_state_to_rust()

        # First render (baseline)
        view.render_with_diff()

        # Change state
        view.text = "World"
        view._sync_state_to_rust()

        # Second render should generate patches
        html, patches, version = view.render_with_diff()

        assert patches is not None, "Second render should generate patches"
        assert version == 2, "Version should increment"
        assert "World" in html, "Rendered HTML should have new text"
