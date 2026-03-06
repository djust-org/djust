"""Security tests for Component rendering to prevent XSS vulnerabilities."""

from django.utils.safestring import SafeString

from djust.components.base import LiveComponent


class SecurityTestComponent(LiveComponent):
    """Component for security testing (not a test class)."""

    template = '<div class="test">{{ content }}</div>'

    def mount(self, content="", **kwargs):
        """Initialize component state."""
        self.content = content

    def get_context_data(self):
        """Return template context."""
        return {"content": self.content}


class TestComponentRenderSecurity:
    """Test that Component.render() properly escapes malicious input."""

    def test_component_escapes_malicious_component_id(self):
        """Component.render() must escape component_id to prevent XSS."""
        component = SecurityTestComponent(content="Safe content")
        # Inject malicious component_id
        component.component_id = '<script>alert("XSS")</script>'

        html = component.render()

        # Should not contain raw script tag
        assert "<script>" not in html
        assert 'alert("XSS")' not in html
        # Should be escaped
        assert "&lt;script&gt;" in html or "data-component-id=" in html

    def test_component_escapes_malicious_content(self):
        """Component content with XSS attempts should be escaped by template engine."""
        malicious_content = '<script>alert("XSS")</script>'
        component = SecurityTestComponent(content=malicious_content)

        html = component.render()

        # Template engine should escape this
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_component_escapes_various_xss_vectors(self):
        """Test various XSS attack vectors are properly escaped."""
        xss_vectors = [
            ("<img src=x onerror=alert(1)>", "<img", "&lt;"),
            ("<svg/onload=alert(1)>", "<svg", "&lt;"),
            ('<iframe src="javascript:alert(1)">', "<iframe", "&lt;"),
            ('"><script>alert(1)</script>', "<script>", "&lt;"),
            ("' onclick='alert(1)'", "onclick='alert", "&#x27;"),  # Single quotes escaped as &#x27;
        ]

        for vector, dangerous_part, escape_marker in xss_vectors:
            component = SecurityTestComponent(content=vector)
            html = component.render()

            # Should not contain unescaped dangerous HTML tags/attributes
            assert dangerous_part not in html, f"XSS vector not escaped: {vector}"
            # Should contain escape markers
            assert escape_marker in html, f"Missing escape marker {escape_marker} in: {html}"

    def test_component_preserves_safe_html(self):
        """When content is explicitly marked safe in template, it should be preserved."""

        # Create a component with a template that uses |safe filter
        class SafeHtmlComponent(LiveComponent):
            template = '<div class="test">{{ content|safe }}</div>'

            def mount(self, content="", **kwargs):
                self.content = content

            def get_context_data(self):
                return {"content": self.content}

        from django.utils.safestring import mark_safe

        safe_html = mark_safe("<strong>Bold Text</strong>")
        component = SafeHtmlComponent(content=safe_html)

        html = component.render()

        # Safe HTML should be preserved when using |safe filter
        assert "<strong>Bold Text</strong>" in html

    def test_component_id_with_special_characters(self):
        """Component IDs with special characters should be safely escaped."""
        special_chars = [
            '"double-quotes"',
            "'single-quotes'",
            "&ampersand&",
            "<less-than>",
            ">greater-than<",
        ]

        for special in special_chars:
            component = SecurityTestComponent(content="Safe")
            component.component_id = special
            html = component.render()

            # Check that the HTML is valid and special chars are escaped
            assert "data-component-id=" in html
            # Raw special characters should not appear in attribute value
            if "<" in special or ">" in special:
                assert special not in html

    def test_component_double_escaping_prevention(self):
        """Ensure we don't double-escape already escaped content."""
        # Content with HTML entities
        content_with_entities = "R&D Department"
        component = SecurityTestComponent(content=content_with_entities)

        html = component.render()

        # Should contain the entity R&D (template will escape & to &amp;)
        assert "R&amp;D" in html

    def test_component_render_returns_safestring(self):
        """Component.render() should return a SafeString to prevent double-escaping."""
        component = SecurityTestComponent(content="Test")
        html = component.render()

        # Should return SafeString so Django doesn't escape it again
        assert isinstance(html, SafeString)


class TestFormatHTMLUsage:
    """Test proper usage of format_html() for security."""

    def test_format_html_escapes_unsafe_values(self):
        """Verify format_html() properly escapes values."""
        from django.utils.html import format_html

        unsafe_value = "<script>alert(1)</script>"
        result = format_html("<div>{}</div>", unsafe_value)

        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_format_html_preserves_safe_values(self):
        """Verify format_html() preserves explicitly safe values."""
        from django.utils.html import format_html
        from django.utils.safestring import mark_safe

        safe_html = mark_safe("<strong>Bold</strong>")
        result = format_html("<div>{}</div>", safe_html)

        assert "<strong>Bold</strong>" in result

    def test_format_html_better_than_mark_safe_fstring(self):
        """Demonstrate why format_html() is safer than mark_safe(f"...")."""
        from django.utils.html import format_html
        from django.utils.safestring import mark_safe

        unsafe_value = "<script>alert(1)</script>"

        # UNSAFE pattern (what we're fixing)
        unsafe_result = mark_safe(f"<div>{unsafe_value}</div>")
        assert "<script>" in unsafe_result  # VULNERABILITY!

        # SAFE pattern (what we should use)
        safe_result = format_html("<div>{}</div>", unsafe_value)
        assert "<script>" not in safe_result  # PROTECTED!
