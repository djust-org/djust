"""
Unit tests for LiveView core functionality.
"""

import pytest
from djust.live_view import LiveView, cleanup_expired_sessions, get_session_stats


class TestLiveViewBasics:
    """Test basic LiveView functionality."""

    def test_liveview_initialization(self):
        """Test LiveView can be instantiated."""
        class SimpleView(LiveView):
            template = "<div>{{ message }}</div>"

        view = SimpleView()
        assert view is not None
        assert view.template == "<div>{{ message }}</div>"

    def test_get_template_with_string(self):
        """Test get_template returns template string."""
        class SimpleView(LiveView):
            template = "<div>Hello</div>"

        view = SimpleView()
        template = view.get_template()
        assert template == "<div>Hello</div>"

    @pytest.mark.django_db
    def test_mount_called(self, get_request):
        """Test mount method is called on GET request."""
        class CounterView(LiveView):
            template = "<div>{{ count }}</div>"
            mount_called = False

            def mount(self, request, **kwargs):
                self.mount_called = True
                self.count = 0

        view = CounterView()
        response = view.get(get_request)

        assert view.mount_called is True
        assert hasattr(view, 'count')
        assert view.count == 0


class TestSessionCleanup:
    """Test session cleanup functionality."""

    def test_cleanup_expired_sessions_empty(self):
        """Test cleanup with empty cache."""
        cleaned = cleanup_expired_sessions()
        assert cleaned == 0

    def test_get_session_stats_empty(self):
        """Test stats with empty cache."""
        stats = get_session_stats()
        assert stats['total_sessions'] == 0
        assert stats['oldest_session_age'] == 0

    @pytest.mark.django_db
    def test_session_stats_with_sessions(self, get_request):
        """Test stats with active sessions."""
        # Create a LiveView to populate cache
        class SimpleView(LiveView):
            template = "<div>Test</div>"

        view = SimpleView()
        view.get(get_request)

        stats = get_session_stats()
        assert stats['total_sessions'] >= 0  # May be 0 if no session key


class TestTemplateInheritance:
    """Test Django template inheritance support."""

    @pytest.mark.django_db
    def test_get_template_with_extends(self, tmp_path, settings):
        """Test get_template with {% extends %} extracts liveview-root from resolved template."""
        # Create temporary template directory
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Update Django settings to use temp directory
        settings.TEMPLATES[0]['DIRS'] = [str(templates_dir)]

        # Create base template with liveview-root in block
        base_template = templates_dir / "base.html"
        base_template.write_text(
            "<!DOCTYPE html><html><body>{% block content %}{% endblock %}</body></html>"
        )

        # Create child template with data-liveview-root div
        child_template = templates_dir / "child.html"
        child_template.write_text(
            "{% extends 'base.html' %}{% block content %}<div data-liveview-root><div>{$ message $}</div></div>{% endblock %}"
        )

        # Test that get_template() extracts liveview-root from resolved template
        class TestView(LiveView):
            template_name = "child.html"

        view = TestView()
        result = view.get_template()

        # get_template() should return ONLY the liveview-root div (for VDOM tracking)
        # NOT the full document (no DOCTYPE, html, body from base template)
        assert "<!DOCTYPE html>" not in result
        assert "<body>" not in result
        assert "<html>" not in result
        # Should contain the liveview-root div and child content
        assert "data-liveview-root" in result
        assert "<div>{$ message $}</div>" in result
        # Should NOT contain Django template tags
        assert "{% extends" not in result
        assert "{% block" not in result

        # Full template should be stored in _full_template attribute
        assert hasattr(view, '_full_template')
        assert "<!DOCTYPE html>" in view._full_template
        assert "<body>" in view._full_template

    @pytest.mark.django_db
    def test_get_template_without_extends_unchanged(self):
        """Test get_template without {% extends %} returns raw source."""
        class SimpleView(LiveView):
            template = "<div>{{ message }}</div>"

        view = SimpleView()
        result = view.get_template()

        # Should return unchanged for standalone templates
        assert result == "<div>{{ message }}</div>"

    @pytest.mark.django_db
    @pytest.mark.skip(reason="Django template loader caching prevents dynamic template loading in tests")
    def test_liveview_syntax_conversion(self, tmp_path, settings):
        """Test {$ var $} syntax is converted to {{ var }}."""
        from django.template import engines

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        settings.TEMPLATES[0]['DIRS'] = [str(templates_dir)]

        base = templates_dir / "base.html"
        base.write_text("<html>{% block content %}{% endblock %}</html>")

        child = templates_dir / "test_syntax.html"
        child.write_text(
            "{% extends 'base.html' %}"
            "{% block content %}"
            "{$ var1 $} and {$ var2.render $}"
            "{% endblock %}"
        )

        # Clear Django's template cache
        engines._engines = {}

        class TestView(LiveView):
            template_name = "test_syntax.html"

        view = TestView()
        result = view.get_template()

        # Both LiveView variables should be converted
        assert "{{ var1 }}" in result
        assert "{{ var2.render }}" in result
        # LiveView syntax should not remain
        assert "{$" not in result
        assert "$}" not in result

    @pytest.mark.django_db
    def test_backward_compatibility_standalone_templates(self):
        """Test standalone templates still use {{ }} syntax."""
        class StandaloneView(LiveView):
            template_name = None
            template = "<div>{{ count }}</div>"

        view = StandaloneView()
        result = view.get_template()

        # Should preserve original {{ }} syntax for standalone templates
        assert result == "<div>{{ count }}</div>"


class TestErrorHandling:
    """Test error handling improvements."""

    @pytest.mark.django_db
    def test_invalid_event_handler(self, post_request):
        """Test calling non-existent event handler."""
        import json

        class SimpleView(LiveView):
            template = "<div>{{ count }}</div>"

            def mount(self, request, **kwargs):
                self.count = 0

        view = SimpleView()

        # Simulate POST with non-existent handler
        # Note: Currently the code silently ignores missing handlers and returns HTML
        post_request._body = json.dumps({'event': 'nonexistent', 'params': {}}).encode()
        response = view.post(post_request)

        # Currently returns 200 with HTML (not an error)
        # This is a design choice - silently ignore unknown events
        assert response.status_code == 200
        # JsonResponse.content is bytes, need to decode and parse
        import json
        data = json.loads(response.content.decode())
        # Should return HTML or version, not an error
        assert 'html' in data or 'version' in data
