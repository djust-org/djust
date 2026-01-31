"""
Tests for djust decorators and state management.

Tests decorator metadata attachment, metadata extraction from LiveView,
and metadata injection in rendered HTML.
"""

import json
import pytest
from djust import LiveView
from djust.decorators import (
    event_handler,
    event,
    debounce,
    throttle,
    optimistic,
    cache,
    client_state,
)


class TestEventHandlerDecorator:
    """Test enhanced @event_handler decorator with signature introspection."""

    def test_event_handler_extracts_signature(self):
        """Test that decorator extracts parameter information"""

        @event_handler
        def my_handler(self, value: str = "", count: int = 0):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert len(metadata["params"]) == 2
        assert metadata["params"][0]["name"] == "value"
        assert metadata["params"][0]["type"] == "str"
        assert metadata["params"][0]["required"] is False
        assert metadata["params"][1]["name"] == "count"
        assert metadata["params"][1]["type"] == "int"

    def test_event_handler_with_required_params(self):
        """Test required vs optional parameter detection"""

        @event_handler
        def my_handler(self, required: str, optional: str = "default"):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert "required" in metadata["required"]
        assert "optional" in metadata["optional"]

    def test_event_handler_with_description(self):
        """Test explicit description parameter"""

        @event_handler(description="Custom description")
        def my_handler(self, value: str):
            """Original docstring"""
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["description"] == "Custom description"

    def test_event_handler_uses_docstring(self):
        """Test that docstring is used if no explicit description"""

        @event_handler
        def my_handler(self, value: str):
            """Handler docstring"""
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["description"] == "Handler docstring"

    def test_event_handler_with_kwargs(self):
        """Test handler with **kwargs"""

        @event_handler
        def my_handler(self, value: str = "", **kwargs):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["accepts_kwargs"] is True

    def test_event_handler_without_kwargs(self):
        """Test handler without **kwargs"""

        @event_handler
        def my_handler(self, value: str = ""):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["accepts_kwargs"] is False

    def test_event_handler_no_parentheses(self):
        """Test @event_handler without parentheses"""

        @event_handler
        def my_handler(self, value: str):
            """Test handler"""
            pass

        assert hasattr(my_handler, "_djust_decorators")
        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["description"] == "Test handler"

    def test_event_handler_with_parentheses(self):
        """Test @event_handler() with parentheses"""

        @event_handler()
        def my_handler(self, value: str):
            """Test handler"""
            pass

        assert hasattr(my_handler, "_djust_decorators")

    def test_event_alias(self):
        """Test @event alias works but emits deprecation warning"""

        with pytest.warns(DeprecationWarning, match="@event is deprecated"):

            @event
            def my_handler(self, value: str):
                """Test handler"""
                pass

        assert hasattr(my_handler, "_djust_decorators")
        assert "event_handler" in my_handler._djust_decorators

    def test_event_handler_metadata_structure(self):
        """Test complete metadata structure"""

        @event_handler
        def search(self, value: str = "", count: int = 0, **kwargs):
            """Search items"""
            pass

        metadata = search._djust_decorators["event_handler"]

        # Check all required keys exist
        assert "params" in metadata
        assert "param_names" in metadata
        assert "description" in metadata
        assert "accepts_kwargs" in metadata
        assert "required" in metadata
        assert "optional" in metadata

        # Check values
        assert metadata["param_names"] == ["value", "count"]
        assert metadata["description"] == "Search items"
        assert metadata["accepts_kwargs"] is True
        assert metadata["required"] == []
        assert set(metadata["optional"]) == {"value", "count"}

    def test_event_handler_with_multiple_decorators(self):
        """Test @event_handler combines with other decorators"""

        @event_handler
        @debounce(wait=0.5)
        @cache(ttl=60)
        def my_handler(self, value: str = ""):
            """Combined handler"""
            pass

        assert hasattr(my_handler, "_djust_decorators")
        assert "event_handler" in my_handler._djust_decorators
        assert "debounce" in my_handler._djust_decorators
        assert "cache" in my_handler._djust_decorators

    def test_event_handler_explicit_params(self):
        """Test explicit params parameter"""

        @event_handler(params=["custom", "params"])
        def my_handler(self, value: str = ""):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["param_names"] == ["custom", "params"]

    def test_event_handler_coerce_types_default(self):
        """Test that coerce_types defaults to True"""

        @event_handler
        def my_handler(self, count: int = 0, **kwargs):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert "coerce_types" in metadata
        assert metadata["coerce_types"] is True

    def test_event_handler_coerce_types_explicit_true(self):
        """Test coerce_types=True is stored in metadata"""

        @event_handler(coerce_types=True)
        def my_handler(self, count: int = 0, **kwargs):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["coerce_types"] is True

    def test_event_handler_coerce_types_false(self):
        """Test coerce_types=False disables type coercion"""

        @event_handler(coerce_types=False)
        def my_handler(self, count: int = 0, **kwargs):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["coerce_types"] is False

    def test_event_handler_coerce_types_with_description(self):
        """Test coerce_types works with other parameters"""

        @event_handler(description="Test handler", coerce_types=False)
        def my_handler(self, value: str = "", **kwargs):
            pass

        metadata = my_handler._djust_decorators["event_handler"]
        assert metadata["description"] == "Test handler"
        assert metadata["coerce_types"] is False


class TestDecoratorMetadata:
    """Test decorator metadata attachment."""

    def test_debounce_metadata(self):
        """Test @debounce attaches correct metadata."""

        @debounce(wait=0.5)
        def handler(self, **kwargs):
            pass

        assert hasattr(handler, "_djust_decorators")
        assert "debounce" in handler._djust_decorators
        assert handler._djust_decorators["debounce"] == {"wait": 0.5, "max_wait": None}

    def test_debounce_with_max_wait(self):
        """Test @debounce with max_wait parameter."""

        @debounce(wait=0.5, max_wait=2.0)
        def handler(self, **kwargs):
            pass

        assert handler._djust_decorators["debounce"] == {"wait": 0.5, "max_wait": 2.0}

    def test_throttle_metadata(self):
        """Test @throttle attaches correct metadata."""

        @throttle(interval=0.1, leading=True, trailing=False)
        def handler(self, **kwargs):
            pass

        assert hasattr(handler, "_djust_decorators")
        assert "throttle" in handler._djust_decorators
        assert handler._djust_decorators["throttle"] == {
            "interval": 0.1,
            "leading": True,
            "trailing": False,
        }

    def test_throttle_defaults(self):
        """Test @throttle default values."""

        @throttle(interval=0.2)
        def handler(self, **kwargs):
            pass

        assert handler._djust_decorators["throttle"] == {
            "interval": 0.2,
            "leading": True,  # Default
            "trailing": True,  # Default
        }

    def test_optimistic_metadata(self):
        """Test @optimistic attaches correct metadata."""

        @optimistic
        def handler(self, **kwargs):
            pass

        assert hasattr(handler, "_djust_decorators")
        assert "optimistic" in handler._djust_decorators
        assert handler._djust_decorators["optimistic"] is True

    def test_cache_metadata(self):
        """Test @cache attaches correct metadata."""

        @cache(ttl=60, key_params=["query"])
        def handler(self, **kwargs):
            pass

        assert hasattr(handler, "_djust_decorators")
        assert "cache" in handler._djust_decorators
        assert handler._djust_decorators["cache"] == {"ttl": 60, "key_params": ["query"]}

    def test_cache_defaults(self):
        """Test @cache default values."""

        @cache(ttl=120)
        def handler(self, **kwargs):
            pass

        assert handler._djust_decorators["cache"] == {
            "ttl": 120,
            "key_params": [],  # Default empty list
        }

    def test_client_state_metadata(self):
        """Test @client_state attaches correct metadata."""

        @client_state(keys=["filter", "sort"])
        def handler(self, **kwargs):
            pass

        assert hasattr(handler, "_djust_decorators")
        assert "client_state" in handler._djust_decorators
        assert handler._djust_decorators["client_state"] == {"keys": ["filter", "sort"]}

    def test_multiple_decorators(self):
        """Test multiple decorators on same handler."""

        @debounce(wait=0.5)
        @optimistic
        @cache(ttl=60, key_params=["query"])
        def handler(self, **kwargs):
            pass

        assert hasattr(handler, "_djust_decorators")
        assert "debounce" in handler._djust_decorators
        assert "optimistic" in handler._djust_decorators
        assert "cache" in handler._djust_decorators

        # Verify all metadata is correct
        assert handler._djust_decorators["debounce"]["wait"] == 0.5
        assert handler._djust_decorators["optimistic"] is True
        assert handler._djust_decorators["cache"]["ttl"] == 60

    def test_decorator_composition_order(self):
        """Test decorator composition works regardless of order."""

        @cache(ttl=60)
        @optimistic
        @debounce(wait=0.5)
        def handler1(self, **kwargs):
            pass

        @debounce(wait=0.5)
        @optimistic
        @cache(ttl=60)
        def handler2(self, **kwargs):
            pass

        # Both should have all three decorators
        assert "debounce" in handler1._djust_decorators
        assert "optimistic" in handler1._djust_decorators
        assert "cache" in handler1._djust_decorators

        assert "debounce" in handler2._djust_decorators
        assert "optimistic" in handler2._djust_decorators
        assert "cache" in handler2._djust_decorators


class TestMetadataExtraction:
    """Test metadata extraction from LiveView."""

    def test_extract_handler_metadata(self):
        """Test _extract_handler_metadata() method."""

        class TestView(LiveView):
            template = "<div>Test</div>"

            @debounce(wait=0.5)
            def search(self, query: str = "", **kwargs):
                pass

            @optimistic
            def increment(self, **kwargs):
                pass

        view = TestView()
        metadata = view._extract_handler_metadata()

        assert "search" in metadata
        assert metadata["search"]["debounce"] == {"wait": 0.5, "max_wait": None}

        assert "increment" in metadata
        assert metadata["increment"]["optimistic"] is True

    def test_extract_multiple_decorators(self):
        """Test extraction of handlers with multiple decorators."""

        class TestView(LiveView):
            template = "<div>Test</div>"

            @debounce(wait=0.5)
            @optimistic
            @cache(ttl=60, key_params=["query"])
            def search(self, query: str = "", **kwargs):
                pass

        view = TestView()
        metadata = view._extract_handler_metadata()

        assert "search" in metadata
        assert "debounce" in metadata["search"]
        assert "optimistic" in metadata["search"]
        assert "cache" in metadata["search"]

    def test_extract_no_decorators(self):
        """Test extraction when no decorators present."""

        class TestView(LiveView):
            template = "<div>Test</div>"

            def plain_handler(self, **kwargs):
                pass

        view = TestView()
        metadata = view._extract_handler_metadata()

        # Plain handler should not appear in metadata
        assert "plain_handler" not in metadata

    def test_extract_ignores_private_methods(self):
        """Test extraction ignores private methods."""

        class TestView(LiveView):
            template = "<div>Test</div>"

            @debounce(wait=0.5)
            def _private_method(self, **kwargs):
                pass

        view = TestView()
        metadata = view._extract_handler_metadata()

        # Private method should be ignored
        assert "_private_method" not in metadata

    def test_extract_mixed_handlers(self):
        """Test extraction with mix of decorated and undecorated handlers."""

        class TestView(LiveView):
            template = "<div>Test</div>"

            @debounce(wait=0.5)
            def search(self, **kwargs):
                pass

            def plain_handler(self, **kwargs):
                pass

            @optimistic
            def increment(self, **kwargs):
                pass

        view = TestView()
        metadata = view._extract_handler_metadata()

        # Only decorated handlers should appear
        assert "search" in metadata
        assert "increment" in metadata
        assert "plain_handler" not in metadata

    def test_extract_empty_view(self):
        """Test extraction from view with no handlers."""

        class TestView(LiveView):
            template = "<div>Test</div>"

        view = TestView()
        metadata = view._extract_handler_metadata()

        # Should return empty dict
        assert metadata == {}

    def test_metadata_caching(self):
        """Test that metadata extraction is cached."""

        class TestView(LiveView):
            template = "<div>Test</div>"

            @debounce(wait=0.5)
            def search(self, **kwargs):
                pass

        view = TestView()

        # First call should extract and cache
        metadata1 = view._extract_handler_metadata()
        assert "search" in metadata1

        # Second call should return cached version (same object)
        metadata2 = view._extract_handler_metadata()
        assert metadata2 is metadata1  # Same object reference

        # Verify the cache is stored
        assert view._handler_metadata is not None
        assert view._handler_metadata is metadata1


class TestMetadataInjection:
    """Test metadata injection in rendered HTML."""

    def test_inject_metadata_basic(self):
        """Test _inject_handler_metadata() injects script tag."""

        class TestView(LiveView):
            template = "<html><body><div>Test</div></body></html>"

            @debounce(wait=0.5)
            def search(self, **kwargs):
                pass

        view = TestView()
        html = "<html><body><div>Test</div></body></html>"
        injected = view._inject_handler_metadata(html)

        # Should contain script tag
        assert "<script>" in injected
        assert "window.handlerMetadata" in injected
        assert '"search"' in injected
        assert '"debounce"' in injected

        # Should be injected before </body>
        assert injected.index("<script>") < injected.index("</body>")

    def test_inject_no_metadata(self):
        """Test injection when no metadata exists."""

        class TestView(LiveView):
            template = "<html><body><div>Test</div></body></html>"

            def plain_handler(self, **kwargs):
                pass

        view = TestView()
        html = "<html><body><div>Test</div></body></html>"
        injected = view._inject_handler_metadata(html)

        # Should not inject script if no metadata
        assert "<script>" not in injected
        assert injected == html

    def test_inject_before_body(self):
        """Test injection before </body> tag."""

        class TestView(LiveView):
            template = "<html><body>Content</body></html>"

            @optimistic
            def handler(self, **kwargs):
                pass

        view = TestView()
        html = "<html><body>Content</body></html>"
        injected = view._inject_handler_metadata(html)

        # Script should be before </body>
        assert injected.index("<script>") < injected.index("</body>")
        assert "</body>" in injected

    def test_inject_before_html_fallback(self):
        """Test injection before </html> if no </body>."""

        class TestView(LiveView):
            template = "<html><div>Content</div></html>"

            @optimistic
            def handler(self, **kwargs):
                pass

        view = TestView()
        html = "<html><div>Content</div></html>"
        injected = view._inject_handler_metadata(html)

        # Script should be before </html>
        assert injected.index("<script>") < injected.index("</html>")

    def test_inject_append_fallback(self):
        """Test injection appends to end if no closing tags."""

        class TestView(LiveView):
            template = "<div>Content</div>"

            @optimistic
            def handler(self, **kwargs):
                pass

        view = TestView()
        html = "<div>Content</div>"
        injected = view._inject_handler_metadata(html)

        # Script should be at end
        assert injected.endswith("</script>")

    def test_inject_valid_json(self):
        """Test injected JSON is valid."""

        class TestView(LiveView):
            template = "<html><body>Test</body></html>"

            @debounce(wait=0.5, max_wait=2.0)
            @optimistic
            @cache(ttl=60, key_params=["query", "page"])
            def search(self, **kwargs):
                pass

        view = TestView()
        html = "<html><body>Test</body></html>"
        injected = view._inject_handler_metadata(html)

        # Extract JSON from script tag
        start = injected.index("Object.assign(window.handlerMetadata, ") + len(
            "Object.assign(window.handlerMetadata, "
        )
        end = injected.index(");", start)
        json_str = injected[start:end]

        # Should be valid JSON
        metadata = json.loads(json_str)

        # Verify structure
        assert "search" in metadata
        assert metadata["search"]["debounce"] == {"wait": 0.5, "max_wait": 2.0}
        assert metadata["search"]["optimistic"] is True
        assert metadata["search"]["cache"] == {"ttl": 60, "key_params": ["query", "page"]}


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
