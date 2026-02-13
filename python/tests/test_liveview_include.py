"""
Tests for {% include %} tag support in LiveView and Component classes.

These tests verify that {% include %} works correctly when using
LiveView, Component, and LiveComponent classes (not just DjustTemplateBackend).

Related: GitHub Issue #77
"""

import pytest


@pytest.fixture
def template_dir(tmp_path):
    """Create a temporary template directory with test templates."""
    templates = tmp_path / "templates"
    templates.mkdir()

    # Create partial template
    (templates / "_header.html").write_text('<header class="site-header">{{ site_name }}</header>')

    # Create another partial
    (templates / "_footer.html").write_text("<footer>Copyright {{ year }}</footer>")

    return templates


@pytest.fixture
def patch_template_dirs(template_dir, settings):
    """Patch Django settings to use our temp template directory."""
    import copy
    from django.test.utils import override_settings
    from djust.utils import _get_template_dirs_cached

    # Build a full TEMPLATES override so Django's signal-based cache reset fires
    new_templates = copy.deepcopy(settings.TEMPLATES)
    new_templates[0]["DIRS"] = [str(template_dir)]

    with override_settings(TEMPLATES=new_templates):
        _get_template_dirs_cached.cache_clear()
        yield template_dir

    _get_template_dirs_cached.cache_clear()


class TestLiveViewInclude:
    """Tests for {% include %} in LiveView classes."""

    def test_liveview_with_include(self, patch_template_dirs):
        """Test that LiveView can render templates with {% include %}."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template = """
            <div dj-root>
                {% include "_header.html" %}
                <main>{{ content }}</main>
            </div>
            """

            def mount(self, request, **kwargs):
                self.site_name = "Test Site"
                self.content = "Hello World"

        view = MyView()
        view.mount(None)

        html = view.render()

        assert '<header class="site-header">Test Site</header>' in html
        assert "<main>Hello World</main>" in html

    def test_liveview_include_with_context(self, patch_template_dirs):
        """Test that included templates receive context variables."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template = """
            <div dj-root>
                {% include "_header.html" %}
                {% include "_footer.html" %}
            </div>
            """

            def mount(self, request, **kwargs):
                self.site_name = "My Site"
                self.year = 2024

        view = MyView()
        view.mount(None)

        html = view.render()

        assert "My Site" in html
        assert "2024" in html


class TestComponentInclude:
    """Tests for {% include %} in Component classes."""

    def test_component_with_include(self, patch_template_dirs):
        """Test that Component can render templates with {% include %}."""
        from djust.components.base import Component

        class Card(Component):
            template = """
            <div class="card">
                {% include "_header.html" %}
                <div class="card-body">{{ body }}</div>
            </div>
            """

            def get_context_data(self):
                return {"site_name": self.site_name, "body": self.body}

        card = Card(site_name="Card Header", body="Card content")
        html = card.render()

        assert '<header class="site-header">Card Header</header>' in html
        assert '<div class="card-body">Card content</div>' in html


class TestLiveComponentInclude:
    """Tests for {% include %} in LiveComponent classes."""

    def test_live_component_with_include(self, patch_template_dirs):
        """Test that LiveComponent can render templates with {% include %}."""
        from djust.components.base import LiveComponent

        class Widget(LiveComponent):
            template = """
            <div class="widget">
                {% include "_header.html" %}
                <p>{{ message }}</p>
            </div>
            """

            def mount(self, site_name="Default", message="Hello"):
                self.site_name = site_name
                self.message = message

            def get_context_data(self):
                return {"site_name": self.site_name, "message": self.message}

        widget = Widget(site_name="Widget Title", message="Widget content")
        html = widget.render()

        assert '<header class="site-header">Widget Title</header>' in html
        assert "<p>Widget content</p>" in html


class TestGetTemplateDirs:
    """Tests for the get_template_dirs utility function."""

    def test_returns_list(self):
        """Test that get_template_dirs returns a list."""
        from djust.utils import get_template_dirs

        result = get_template_dirs()

        assert isinstance(result, list)

    def test_contains_configured_dirs(self, settings):
        """Test that result contains TEMPLATES DIRS."""
        from djust.utils import get_template_dirs

        # Clear cache to pick up current settings
        from djust.utils import _get_template_dirs_cached

        _get_template_dirs_cached.cache_clear()

        result = get_template_dirs()

        # Should contain at least the dirs from settings
        for template_dir in settings.TEMPLATES[0].get("DIRS", []):
            assert str(template_dir) in result

    def test_caching_returns_same_result(self):
        """Test that repeated calls return cached result."""
        from djust.utils import get_template_dirs, _get_template_dirs_cached

        # Clear cache first
        _get_template_dirs_cached.cache_clear()

        result1 = get_template_dirs()
        result2 = get_template_dirs()

        # Should return equal lists (though not same object since we copy)
        assert result1 == result2

        # Cache should have been hit
        cache_info = _get_template_dirs_cached.cache_info()
        assert cache_info.hits >= 1

    def test_clear_template_dirs_cache(self):
        """Test that clear_template_dirs_cache clears the cache."""
        from djust.utils import (
            get_template_dirs,
            clear_template_dirs_cache,
            _get_template_dirs_cached,
        )

        # Ensure cache is populated
        get_template_dirs()

        # Clear it
        clear_template_dirs_cache()

        # Cache should be empty
        cache_info = _get_template_dirs_cached.cache_info()
        assert cache_info.currsize == 0


class TestUnsupportedTagWarning:
    """Tests for unsupported template tag warning behavior."""

    def test_unsupported_tag_renders_html_comment(self):
        """Test that unsupported tags render as HTML comments."""
        from djust.live_view import LiveView

        class MyView(LiveView):
            template = """
            <div dj-root>
                {% spaceless %}content{% endspaceless %}
            </div>
            """

        view = MyView()
        html = view.render()

        # Should contain HTML comments for the unsupported tags
        assert "<!-- djust: unsupported tag" in html
        assert "spaceless" in html
