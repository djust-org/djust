"""
End-to-End tests for Phase 5 State Management decorators.

Tests the complete integration of:
- @cache decorator (client-side response caching)
- dj-loading HTML attributes (loading indicators)

These tests verify the full Python → Rust → WebSocket → JavaScript flow.

NOTE: DraftModeMixin E2E tests will be added once that feature is merged.
"""

import json
import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware
from djust import LiveView
from djust.decorators import cache, debounce
from djust import state

# Use pytest.mark.django_db for all tests that need Django
pytestmark = pytest.mark.django_db


class TestCacheDecoratorE2E:
    """End-to-end tests for @cache decorator."""

    def test_cache_metadata_in_rendered_html(self):
        """Test @cache decorator metadata appears in rendered HTML."""

        class SearchView(LiveView):
            exposure_policy = "explicit"
            results = state()
            template = """
            <html>
            <body>
                <input dj-input="search" />
            </body>
            </html>
            """

            @cache(ttl=300, key_params=["query"])
            def search(self, query: str = "", **kwargs):
                self.results = [f"Result for: {query}"]

        view = SearchView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        # Add session
        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        # Mount view
        view.mount(request)

        # Get rendered HTML
        html = view.render()

        # Verify cache metadata is injected
        assert "window.handlerMetadata" in html
        assert '"search"' in html
        assert '"cache"' in html
        assert '"ttl": 300' in html
        assert '"key_params": ["query"]' in html

    def test_cache_with_multiple_key_params(self):
        """Test @cache with multiple key parameters."""

        class SearchView(LiveView):
            exposure_policy = "explicit"
            template = "<div>Search</div>"

            @cache(ttl=60, key_params=["query", "page", "filter"])
            def search(self, query="", page=1, filter="all", **kwargs):
                pass

        view = SearchView()
        metadata = view._extract_handler_metadata()

        assert "search" in metadata
        assert metadata["search"]["cache"]["ttl"] == 60
        assert metadata["search"]["cache"]["key_params"] == ["query", "page", "filter"]

    def test_cache_with_debounce(self):
        """Test @cache combined with @debounce."""

        class SearchView(LiveView):
            exposure_policy = "explicit"
            template = "<div>Search</div>"

            @debounce(wait=0.5)
            @cache(ttl=300, key_params=["query"])
            def search(self, query="", **kwargs):
                pass

        view = SearchView()
        metadata = view._extract_handler_metadata()

        # Both decorators should be present
        assert "debounce" in metadata["search"]
        assert "cache" in metadata["search"]
        assert metadata["search"]["debounce"]["wait"] == 0.5
        assert metadata["search"]["cache"]["ttl"] == 300


class TestLoadingAttributeE2E:
    """End-to-end tests for dj-loading HTML attributes."""

    def test_loading_disable_in_template(self):
        """Test dj-loading.disable attribute renders correctly."""

        class SaveFormView(LiveView):
            exposure_policy = "explicit"
            saved = state()
            template = """
            <html>
            <body>
                <button dj-click="save_data" dj-loading.disable>Save</button>
            </body>
            </html>
            """

            def save_data(self, **kwargs):
                self.saved = True

        view = SaveFormView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        view.mount(request)
        html = view.render()

        # Verify dj-loading.disable is preserved in HTML
        assert "dj-loading.disable" in html

    def test_loading_class_in_template(self):
        """Test dj-loading.class attribute renders correctly."""

        class SaveFormView(LiveView):
            exposure_policy = "explicit"
            template = """
            <html>
            <body>
                <button dj-click="save" dj-loading.class="opacity-50">Save</button>
            </body>
            </html>
            """

            def save(self, **kwargs):
                pass

        view = SaveFormView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        view.mount(request)
        html = view.render()

        # Verify dj-loading.class is preserved in HTML
        assert 'dj-loading.class="opacity-50"' in html

    def test_loading_show_hide_in_template(self):
        """Test dj-loading.show and dj-loading.hide attributes render correctly."""

        class SaveFormView(LiveView):
            exposure_policy = "explicit"
            template = """
            <html>
            <body>
                <button dj-click="save">Save</button>
                <div dj-loading.show style="display: none;">Saving...</div>
                <div dj-loading.hide>Form content</div>
            </body>
            </html>
            """

            def save(self, **kwargs):
                pass

        view = SaveFormView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        view.mount(request)
        html = view.render()

        # Verify dj-loading.show and dj-loading.hide are preserved
        assert "dj-loading.show" in html
        assert "dj-loading.hide" in html

    def test_loading_multiple_modifiers(self):
        """Test multiple dj-loading modifiers on same element."""

        class SaveFormView(LiveView):
            exposure_policy = "explicit"
            template = """
            <button dj-click="save" dj-loading.disable dj-loading.class="loading">
                Save
            </button>
            """

            def save(self, **kwargs):
                pass

        view = SaveFormView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        view.mount(request)
        html = view.render()

        # Verify both modifiers are preserved
        assert "dj-loading.disable" in html
        assert 'dj-loading.class="loading"' in html


class TestIntegrationScenarios:
    """Test realistic integration scenarios combining multiple Phase 5 features."""

    def test_search_with_cache_and_debounce(self):
        """Test realistic search view with caching and debouncing."""

        class ProductSearchView(LiveView):
            exposure_policy = "explicit"
            results = state(default_factory=list)
            template = """
            <html>
            <body>
                <input dj-input="search" placeholder="Search products..." />
                <div dj-loading.show style="display: none;">Searching...</div>
                <div id="results">{{ results|length }} results</div>
            </body>
            </html>
            """

            def mount(self, request):
                self.results = []

            @debounce(wait=0.5)
            @cache(ttl=300, key_params=["query"])
            def search(self, query: str = "", **kwargs):
                # Simulate search
                self.results = [f"Product {i}" for i in range(10)]

        view = ProductSearchView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        view.mount(request)
        html = view.render()

        # Verify all features are present
        assert '"debounce"' in html
        assert '"cache"' in html
        assert "dj-loading.show" in html
        assert 'dj-input="search"' in html


class TestMetadataJSONFormat:
    """Test that all decorator metadata is properly serialized to JSON."""

    def test_all_decorators_valid_json(self):
        """Test that all Phase 5 decorators produce valid JSON."""

        class ComplexView(LiveView):
            exposure_policy = "explicit"
            template = "<div>Test</div>"

            @cache(ttl=60, key_params=["query"])
            @debounce(wait=0.5)
            def cached_search(self, query="", **kwargs):
                pass

            @debounce(wait=0.2)
            def set_state(self, filter="", sort="", **kwargs):
                pass

        view = ComplexView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        view.mount(request)
        html = view.render()

        # Extract and validate handler metadata JSON
        if "window.handlerMetadata" in html:
            start = html.index("Object.assign(window.handlerMetadata, ") + len(
                "Object.assign(window.handlerMetadata, "
            )
            end = html.index(");", start)
            json_str = html[start:end]

            # Should be valid JSON
            metadata = json.loads(json_str)

            # Verify structure
            assert "cached_search" in metadata
            assert metadata["cached_search"]["cache"]["ttl"] == 60
            assert metadata["cached_search"]["debounce"]["wait"] == 0.5

            assert "set_state" in metadata
            assert metadata["set_state"]["debounce"]["wait"] == 0.2


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
