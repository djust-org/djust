"""
Tests for the Tag Handler Registry.

Tests the Rust registry, Python handlers, and integration between them.
"""

import pytest
from unittest.mock import patch


class TestTagHandlerBase:
    """Tests for the TagHandler base class."""

    def test_tag_handler_resolve_string_literal_single_quotes(self):
        """String literals with single quotes are resolved."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        result = handler._resolve_arg("'hello'", {})
        assert result == "hello"

    def test_tag_handler_resolve_string_literal_double_quotes(self):
        """String literals with double quotes are resolved."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        result = handler._resolve_arg('"world"', {})
        assert result == "world"

    def test_tag_handler_resolve_integer_literal(self):
        """Integer literals are resolved."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        result = handler._resolve_arg("42", {})
        assert result == 42

    def test_tag_handler_resolve_negative_integer(self):
        """Negative integers are resolved."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        result = handler._resolve_arg("-5", {})
        assert result == -5

    def test_tag_handler_resolve_simple_variable(self):
        """Simple context variables are resolved."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        context = {"name": "Django"}
        result = handler._resolve_arg("name", context)
        assert result == "Django"

    def test_tag_handler_resolve_nested_variable(self):
        """Nested context variables are resolved."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        context = {"user": {"name": "Alice"}}
        result = handler._resolve_arg("user.name", context)
        assert result == "Alice"

    def test_tag_handler_resolve_named_param(self):
        """Named parameters are resolved."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        context = {"pk": 42}
        result = handler._resolve_arg("id=pk", context)
        assert result == ("id", 42)

    def test_tag_handler_resolve_missing_variable(self):
        """Missing variables return original string."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        result = handler._resolve_arg("missing", {})
        assert result == "missing"

    def test_tag_handler_render_not_implemented(self):
        """Base TagHandler.render() raises NotImplementedError."""
        from djust.template_tags import TagHandler

        handler = TagHandler()
        with pytest.raises(NotImplementedError):
            handler.render([], {})


class TestUrlTagHandler:
    """Tests for the URL tag handler."""

    def test_url_handler_simple(self):
        """{% url 'home' %} resolves correctly."""
        from djust.template_tags.url import UrlTagHandler

        handler = UrlTagHandler()

        with patch("django.urls.reverse", return_value="/"):
            result = handler.render(["'home'"], {})
            assert result == "/"

    def test_url_handler_with_arg(self):
        """{% url 'post_detail' post.id %} resolves with args."""
        from djust.template_tags.url import UrlTagHandler

        handler = UrlTagHandler()

        # Rust already resolves post.id to the actual value
        with patch("django.urls.reverse", return_value="/posts/42/") as mock_reverse:
            result = handler.render(["'post_detail'", "42"], {})
            assert result == "/posts/42/"
            mock_reverse.assert_called_once_with("post_detail", args=[42])

    def test_url_handler_with_kwarg(self):
        """{% url 'user_profile' username='alice' %} resolves with kwargs."""
        from djust.template_tags.url import UrlTagHandler

        handler = UrlTagHandler()

        with patch("django.urls.reverse", return_value="/users/alice/") as mock_reverse:
            result = handler.render(["'user_profile'", "username='alice'"], {})
            assert result == "/users/alice/"
            mock_reverse.assert_called_once_with("user_profile", kwargs={"username": "alice"})

    def test_url_handler_no_reverse_match(self):
        """NoReverseMatch returns empty string."""
        from django.urls import NoReverseMatch
        from djust.template_tags.url import UrlTagHandler

        handler = UrlTagHandler()

        with patch("django.urls.reverse", side_effect=NoReverseMatch("not found")):
            result = handler.render(["'nonexistent'"], {})
            assert result == ""

    def test_url_handler_empty_args(self):
        """Empty args returns empty string."""
        from djust.template_tags.url import UrlTagHandler

        handler = UrlTagHandler()
        result = handler.render([], {})
        assert result == ""


class TestStaticTagHandler:
    """Tests for the static tag handler."""

    def test_static_handler_simple(self):
        """{% static 'css/style.css' %} resolves correctly."""
        from djust.template_tags.static import StaticTagHandler

        handler = StaticTagHandler()

        with patch("django.templatetags.static.static", return_value="/static/css/style.css"):
            result = handler.render(["'css/style.css'"], {})
            assert result == "/static/css/style.css"

    def test_static_handler_with_variable(self):
        """{% static image_path %} resolves with variable."""
        from djust.template_tags.static import StaticTagHandler

        handler = StaticTagHandler()

        # Rust already resolves image_path to the actual value
        with patch("django.templatetags.static.static", return_value="/static/images/logo.png"):
            result = handler.render(["images/logo.png"], {})
            assert result == "/static/images/logo.png"

    @pytest.mark.skip(reason="Fallback behavior requires module reload - tested manually")
    def test_static_handler_fallback_to_settings(self):
        """Falls back to STATIC_URL if django.templatetags.static not available."""
        # This test would require reloading the static module after patching
        # sys.modules, which is complex. The fallback behavior is tested manually.

    def test_static_handler_empty_args(self):
        """Empty args returns empty string."""
        from djust.template_tags.static import StaticTagHandler

        handler = StaticTagHandler()
        result = handler.render([], {})
        assert result == ""


class TestRegistryIntegration:
    """Integration tests for the registry system."""

    @pytest.fixture(autouse=True)
    def setup_registry(self):
        """Clear registry before each test."""
        try:
            from djust._rust import clear_tag_handlers

            clear_tag_handlers()
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_register_and_check(self):
        """Handler can be registered and checked."""
        from djust._rust import register_tag_handler, has_tag_handler

        class TestHandler:
            def render(self, args, context):
                return "test"

        register_tag_handler("test", TestHandler())
        assert has_tag_handler("test")
        assert not has_tag_handler("nonexistent")

    def test_register_via_decorator(self):
        """@register decorator registers handler."""
        from djust.template_tags import register, TagHandler
        from djust._rust import has_tag_handler, clear_tag_handlers

        clear_tag_handlers()

        @register("custom")
        class CustomHandler(TagHandler):
            def render(self, args, context):
                return "custom result"

        assert has_tag_handler("custom")

    def test_get_registered_tags(self):
        """Can list all registered tags."""
        from djust._rust import register_tag_handler, get_registered_tags, clear_tag_handlers

        clear_tag_handlers()

        class Handler:
            def render(self, args, context):
                return ""

        register_tag_handler("tag1", Handler())
        register_tag_handler("tag2", Handler())

        tags = get_registered_tags()
        assert "tag1" in tags
        assert "tag2" in tags

    def test_unregister_handler(self):
        """Handler can be unregistered."""
        from djust._rust import (
            register_tag_handler,
            unregister_tag_handler,
            has_tag_handler,
            clear_tag_handlers,
        )

        clear_tag_handlers()

        class Handler:
            def render(self, args, context):
                return ""

        register_tag_handler("temp", Handler())
        assert has_tag_handler("temp")

        result = unregister_tag_handler("temp")
        assert result is True
        assert not has_tag_handler("temp")

        # Unregistering non-existent handler returns False
        result = unregister_tag_handler("nonexistent")
        assert result is False

    def test_handler_requires_render_method(self):
        """Handler must have render method."""
        from djust._rust import register_tag_handler

        class InvalidHandler:
            pass

        with pytest.raises(TypeError):
            register_tag_handler("invalid", InvalidHandler())


class TestRenderIntegration:
    """Tests for rendering templates with custom tags."""

    @pytest.fixture(autouse=True)
    def setup_registry(self):
        """Clear and setup registry before each test."""
        try:
            from djust._rust import clear_tag_handlers

            clear_tag_handlers()
        except ImportError:
            pytest.skip("Rust extension not available")

    def test_render_custom_tag(self):
        """Custom tag is rendered via Python handler."""
        from djust._rust import render_template, register_tag_handler

        class GreetHandler:
            def render(self, args, context):
                name = args[0].strip("'\"") if args else "World"
                return f"Hello, {name}!"

        register_tag_handler("greet", GreetHandler())

        result = render_template("{% greet 'Django' %}", {})
        assert result == "Hello, Django!"

    def test_render_custom_tag_with_context(self):
        """Custom tag can access template context."""
        from djust._rust import render_template, register_tag_handler

        class EchoHandler:
            def render(self, args, context):
                # First arg is already resolved by Rust
                return str(args[0]) if args else ""

        register_tag_handler("echo", EchoHandler())

        result = render_template("{% echo name %}", {"name": "Alice"})
        assert result == "Alice"

    def test_render_custom_tag_in_loop(self):
        """Custom tag works inside for loops."""
        from djust._rust import render_template, register_tag_handler

        class LinkHandler:
            def render(self, args, context):
                # Rust resolves item.id to the actual value
                return f"/items/{args[0]}/"

        register_tag_handler("link", LinkHandler())

        template = "{% for item in items %}{% link item.id %}{% endfor %}"
        context = {
            "items": [{"id": 1}, {"id": 2}, {"id": 3}],
        }
        result = render_template(template, context)
        assert "/items/1/" in result
        assert "/items/2/" in result
        assert "/items/3/" in result

    def test_unknown_tag_without_handler_renders_warning_comment(self):
        """Unknown tags without handlers render as HTML comments with warning."""
        from djust._rust import render_template, clear_tag_handlers

        clear_tag_handlers()

        result = render_template("before {% unknown_tag %} after", {})
        # Unknown tags now render as HTML comments for debugging
        assert "before" in result
        assert "after" in result
        assert "<!-- djust: unsupported tag" in result
        assert "unknown_tag" in result

    def test_handler_exception_returns_error(self):
        """Handler exceptions are caught and reported."""
        from djust._rust import render_template, register_tag_handler

        class BrokenHandler:
            def render(self, args, context):
                raise ValueError("Something went wrong")

        register_tag_handler("broken", BrokenHandler())

        with pytest.raises(Exception) as exc_info:
            render_template("{% broken %}", {})

        assert "Something went wrong" in str(exc_info.value)


class TestVariableExtraction:
    """Tests for variable extraction from CustomTag nodes."""

    def test_extract_variables_from_custom_tag(self):
        """Variables in custom tags are extracted."""
        from djust._rust import extract_template_variables, register_tag_handler

        # Register a handler so the tag is recognized
        class DummyHandler:
            def render(self, args, context):
                return ""

        register_tag_handler("custom", DummyHandler())

        template = "{% custom post.title user.name %}"
        variables = extract_template_variables(template)

        assert "post" in variables
        assert "user" in variables
        assert "title" in variables.get("post", [])
        assert "name" in variables.get("user", [])

    def test_extract_variables_ignores_string_literals(self):
        """String literals in custom tags are not extracted as variables."""
        from djust._rust import extract_template_variables, register_tag_handler

        class DummyHandler:
            def render(self, args, context):
                return ""

        register_tag_handler("tag", DummyHandler())

        template = "{% tag 'literal' post.id %}"
        variables = extract_template_variables(template)

        # 'literal' should not appear
        assert "literal" not in variables
        # But post.id should
        assert "post" in variables


class TestPwaTagHandlers:
    """Tests for the PWA tag handlers."""

    def test_pwa_manifest_handler_renders_theme_color(self):
        """{% djust_pwa_manifest %} renders theme-color meta tag."""
        from djust.template_tags.pwa import PwaManifestHandler

        handler = PwaManifestHandler()
        result = handler.render([], {})
        assert "theme-color" in result
        assert "<meta" in result

    def test_pwa_manifest_handler_renders_manifest_link(self):
        """{% djust_pwa_manifest %} renders manifest link tag."""
        from djust.template_tags.pwa import PwaManifestHandler

        handler = PwaManifestHandler()
        result = handler.render([], {})
        assert '<link rel="manifest"' in result

    def test_pwa_manifest_handler_with_name(self):
        """{% djust_pwa_manifest name="Test" %} passes name kwarg."""
        from djust.template_tags.pwa import PwaManifestHandler

        handler = PwaManifestHandler()
        result = handler.render(['name="Test App"'], {})
        assert "Test App" in result

    def test_sw_register_handler_renders_script(self):
        """{% djust_sw_register %} renders service worker script."""
        from djust.template_tags.pwa import SwRegisterHandler

        handler = SwRegisterHandler()
        result = handler.render([], {})
        assert "serviceWorker" in result
        assert "<script>" in result

    def test_offline_indicator_handler_renders_indicator(self):
        """{% djust_offline_indicator %} renders indicator HTML."""
        from djust.template_tags.pwa import OfflineIndicatorHandler

        handler = OfflineIndicatorHandler()
        result = handler.render([], {})
        assert "djust-offline-indicator" in result
        assert "djust-indicator-dot" in result

    def test_pwa_head_handler_renders_full_head(self):
        """{% djust_pwa_head %} renders manifest, SW script, and meta tags."""
        from djust.template_tags.pwa import PwaHeadHandler

        handler = PwaHeadHandler()
        result = handler.render([], {})
        assert "theme-color" in result
        assert "serviceWorker" in result
        assert "manifest.json" in result

    def test_pwa_head_handler_with_kwargs(self):
        """{% djust_pwa_head name="X" theme_color="#fff" %} passes kwargs."""
        from djust.template_tags.pwa import PwaHeadHandler

        handler = PwaHeadHandler()
        result = handler.render(['name="My PWA"', 'theme_color="#ff0000"'], {})
        assert "My PWA" in result
        assert "#ff0000" in result


class TestTemplatetagHandler:
    """Tests for the templatetag handler."""

    def test_openblock(self):
        """{% templatetag openblock %} renders '{%'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["openblock"], {}) == "{%"

    def test_closeblock(self):
        """{% templatetag closeblock %} renders '%}'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["closeblock"], {}) == "%}"

    def test_openvariable(self):
        """{% templatetag openvariable %} renders '{{'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["openvariable"], {}) == "{{"

    def test_closevariable(self):
        """{% templatetag closevariable %} renders '}}'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["closevariable"], {}) == "}}"

    def test_openbrace(self):
        """{% templatetag openbrace %} renders '{'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["openbrace"], {}) == "{"

    def test_closebrace(self):
        """{% templatetag closebrace %} renders '}'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["closebrace"], {}) == "}"

    def test_opencomment(self):
        """{% templatetag opencomment %} renders '{#'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["opencomment"], {}) == "{#"

    def test_closecomment(self):
        """{% templatetag closecomment %} renders '#}'."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["closecomment"], {}) == "#}"

    def test_unknown_keyword(self):
        """Unknown keyword returns empty string."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["badkeyword"], {}) == ""

    def test_no_args(self):
        """No arguments returns empty string."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render([], {}) == ""

    def test_quoted_keyword(self):
        """Quoted keywords are handled (quotes stripped)."""
        from djust.template_tags.templatetag import TemplatetagHandler

        handler = TemplatetagHandler()
        assert handler.render(["'openblock'"], {}) == "{%"
        assert handler.render(['"closeblock"'], {}) == "%}"
