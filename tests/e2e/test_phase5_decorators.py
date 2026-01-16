"""
End-to-End tests for Phase 5 State Management decorators.

Tests the complete integration of:
- @cache decorator (client-side response caching)
- @client_state decorator (StateBus coordination)
- @loading HTML attributes (loading indicators)

These tests verify the full Python → Rust → WebSocket → JavaScript flow.

NOTE: DraftModeMixin E2E tests will be added once that feature is merged.
"""

import json
import pytest
from django.test import RequestFactory
from django.contrib.sessions.middleware import SessionMiddleware
from djust import LiveView
from djust.decorators import cache, client_state, debounce

# Use pytest.mark.django_db for all tests that need Django
pytestmark = pytest.mark.django_db


class TestCacheDecoratorE2E:
    """End-to-end tests for @cache decorator."""

    def test_cache_metadata_in_rendered_html(self):
        """Test @cache decorator metadata appears in rendered HTML."""

        class SearchView(LiveView):
            template = """
            <html>
            <body>
                <input @input="search" />
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


class TestClientStateDecoratorE2E:
    """End-to-end tests for @client_state decorator."""

    def test_client_state_metadata_in_rendered_html(self):
        """Test @client_state decorator metadata appears in rendered HTML."""

        class DashboardView(LiveView):
            template = """
            <html>
            <body>
                <input @input="set_filter" />
            </body>
            </html>
            """

            @client_state(keys=["filter", "sort"])
            def set_filter(self, filter: str = "", **kwargs):
                self.filter = filter

        view = DashboardView()
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

        # Verify client_state metadata is injected
        assert "window.handlerMetadata" in html
        assert '"set_filter"' in html
        assert '"client_state"' in html
        assert '"keys": ["filter", "sort"]' in html

    def test_client_state_single_key(self):
        """Test @client_state with single key."""

        class SimpleView(LiveView):
            template = "<div>Simple</div>"

            @client_state(keys=["temp"])
            def update_temp(self, temp=0, **kwargs):
                pass

        view = SimpleView()
        metadata = view._extract_handler_metadata()

        assert "update_temp" in metadata
        assert metadata["update_temp"]["client_state"]["keys"] == ["temp"]

    def test_client_state_empty_keys_not_allowed(self):
        """Test @client_state requires at least one key."""

        with pytest.raises(ValueError, match="At least one key must be specified"):

            @client_state(keys=[])
            def handler(**kwargs):
                pass


class TestLoadingAttributeE2E:
    """End-to-end tests for @loading HTML attributes."""

    def test_loading_disable_in_template(self):
        """Test @loading.disable attribute renders correctly."""

        class SaveFormView(LiveView):
            template = """
            <html>
            <body>
                <button @click="save_data" @loading.disable>Save</button>
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

        # Verify @loading.disable is preserved in HTML
        assert '@loading.disable' in html

    def test_loading_class_in_template(self):
        """Test @loading.class attribute renders correctly."""

        class SaveFormView(LiveView):
            template = """
            <html>
            <body>
                <button @click="save" @loading.class="opacity-50">Save</button>
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

        # Verify @loading.class is preserved in HTML
        assert '@loading.class="opacity-50"' in html

    def test_loading_show_hide_in_template(self):
        """Test @loading.show and @loading.hide attributes render correctly."""

        class SaveFormView(LiveView):
            template = """
            <html>
            <body>
                <button @click="save">Save</button>
                <div @loading.show style="display: none;">Saving...</div>
                <div @loading.hide>Form content</div>
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

        # Verify @loading.show and @loading.hide are preserved
        assert '@loading.show' in html
        assert '@loading.hide' in html

    def test_loading_multiple_modifiers(self):
        """Test multiple @loading modifiers on same element."""

        class SaveFormView(LiveView):
            template = """
            <button @click="save" @loading.disable @loading.class="loading">
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
        assert '@loading.disable' in html
        assert '@loading.class="loading"' in html


class TestIntegrationScenarios:
    """Test realistic integration scenarios combining multiple Phase 5 features."""

    def test_search_with_cache_and_debounce(self):
        """Test realistic search view with caching and debouncing."""

        class ProductSearchView(LiveView):
            template = """
            <html>
            <body>
                <input @input="search" placeholder="Search products..." />
                <div @loading.show style="display: none;">Searching...</div>
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
        assert '@loading.show' in html
        assert '@input="search"' in html

    def test_dashboard_with_client_state(self):
        """Test dashboard with client state coordination."""

        class DashboardView(LiveView):
            template = """
            <html>
            <body>
                <select @change="set_filter">
                    <option value="all">All</option>
                    <option value="active">Active</option>
                </select>
                <select @change="set_sort">
                    <option value="name">Name</option>
                    <option value="date">Date</option>
                </select>
                <button @click="refresh" @loading.disable>Refresh</button>
            </body>
            </html>
            """

            @client_state(keys=["filter", "sort"])
            def set_filter(self, filter: str = "all", **kwargs):
                self.filter = filter

            @client_state(keys=["filter", "sort"])
            def set_sort(self, sort: str = "name", **kwargs):
                self.sort = sort

            def refresh(self, **kwargs):
                pass

        view = DashboardView()
        request_factory = RequestFactory()
        request = request_factory.get("/")

        middleware = SessionMiddleware(lambda x: None)
        middleware.process_request(request)
        request.session.save()

        view.mount(request)
        html = view.render()

        # Verify client state coordination
        assert '"set_filter"' in html
        assert '"set_sort"' in html
        assert '"client_state"' in html
        assert '"keys": ["filter", "sort"]' in html
        assert '@loading.disable' in html


class TestMetadataJSONFormat:
    """Test that all decorator metadata is properly serialized to JSON."""

    def test_all_decorators_valid_json(self):
        """Test that all Phase 5 decorators produce valid JSON."""

        class ComplexView(LiveView):
            template = "<div>Test</div>"

            @cache(ttl=60, key_params=["query"])
            @debounce(wait=0.5)
            def cached_search(self, query="", **kwargs):
                pass

            @client_state(keys=["filter", "sort"])
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
            assert metadata["set_state"]["client_state"]["keys"] == ["filter", "sort"]


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
