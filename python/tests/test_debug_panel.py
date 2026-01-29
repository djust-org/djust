"""
Tests for Developer Debug Panel (Phase 3 of DEVELOPER_TOOLING_PLAN.md)

Tests cover:
1. get_debug_info() method extracts handlers and variables correctly
2. Debug info injection in DEBUG mode
3. CSS injection in DEBUG mode
4. No injection when DEBUG=False
"""

import json
from unittest.mock import patch

import pytest

from djust import LiveView
from djust.decorators import event_handler, debounce, cache


class TestGetDebugInfo:
    """Test LiveView.get_debug_info() method"""

    def test_basic_handler_extraction(self):
        """Test that event handlers are extracted with correct metadata"""

        class MyView(LiveView):
            template_name = "test.html"
            count = 0

            @event_handler
            def increment(self, amount: int = 1):
                """Increment the counter"""
                self.count += amount

            @event_handler
            def decrement(self):
                """Decrement the counter"""
                self.count -= 1

        view = MyView()
        debug_info = view.get_debug_info()

        # Check structure
        assert "view_class" in debug_info
        assert "handlers" in debug_info
        assert "variables" in debug_info
        assert "template" in debug_info

        # Check view class name
        assert debug_info["view_class"] == "MyView"

        # Check template
        assert debug_info["template"] == "test.html"

        # Check handlers
        assert "increment" in debug_info["handlers"]
        assert "decrement" in debug_info["handlers"]

        # Check increment handler details
        increment = debug_info["handlers"]["increment"]
        assert increment["name"] == "increment"
        assert increment["description"] == "Increment the counter"
        assert len(increment["params"]) == 1
        assert increment["params"][0]["name"] == "amount"
        assert increment["params"][0]["type"] == "int"
        assert increment["params"][0]["required"] is False
        assert increment["params"][0]["default"] == "1"

        # Check decrement handler details
        decrement = debug_info["handlers"]["decrement"]
        assert decrement["name"] == "decrement"
        assert decrement["description"] == "Decrement the counter"
        assert len(decrement["params"]) == 0

    def test_handler_with_decorators(self):
        """Test that decorator metadata is included"""

        class MyView(LiveView):
            template_name = "test.html"

            @debounce(wait=0.5)
            @event_handler
            def search(self, query: str = ""):
                """Search for items"""
                pass

            @cache(ttl=300)
            @event_handler
            def fetch_data(self, id: int = 0):
                """Fetch data with caching"""
                pass

        view = MyView()
        debug_info = view.get_debug_info()

        # Check search has debounce decorator
        search = debug_info["handlers"]["search"]
        assert "debounce" in search["decorators"]
        assert search["decorators"]["debounce"]["wait"] == 0.5

        # Check fetch_data has cache decorator
        fetch = debug_info["handlers"]["fetch_data"]
        assert "cache" in fetch["decorators"]
        assert fetch["decorators"]["cache"]["ttl"] == 300

    def test_variable_extraction(self):
        """Test that public variables are extracted"""

        class MyView(LiveView):
            template_name = "test.html"
            count = 42
            message = "Hello World"
            items = [1, 2, 3]

            @event_handler
            def increment(self):
                pass

        view = MyView()
        debug_info = view.get_debug_info()

        # Check variables
        variables = debug_info["variables"]
        assert "count" in variables
        assert "message" in variables
        assert "items" in variables

        # Check variable details
        assert variables["count"]["name"] == "count"
        assert variables["count"]["type"] == "int"
        assert variables["count"]["value"] == "42"

        assert variables["message"]["name"] == "message"
        assert variables["message"]["type"] == "str"
        assert variables["message"]["value"] == "'Hello World'"

    def test_private_attributes_excluded(self):
        """Test that private attributes are not included"""

        class MyView(LiveView):
            template_name = "test.html"
            count = 0
            _private = "secret"

            def _private_method(self):
                pass

            @event_handler
            def public_method(self):
                pass

        view = MyView()
        debug_info = view.get_debug_info()

        # Private variable should not be in variables
        assert "_private" not in debug_info["variables"]

        # Private method should not be in handlers
        assert "_private_method" not in debug_info["handlers"]

        # Public method should be in handlers
        assert "public_method" in debug_info["handlers"]

    def test_long_value_truncation(self):
        """Test that long variable values are truncated"""

        class MyView(LiveView):
            template_name = "test.html"
            long_string = "x" * 200  # 200 character string

        view = MyView()
        debug_info = view.get_debug_info()

        # Check that value is truncated to 100 chars + "..."
        value = debug_info["variables"]["long_string"]["value"]
        assert len(value) <= 104  # 100 chars + "..." + quotes
        assert value.endswith("...")

    def test_no_template_name(self):
        """Test when template_name is not set"""

        class MyView(LiveView):
            template_string = "<div>Hello</div>"

            @event_handler
            def handler(self):
                pass

        view = MyView()
        debug_info = view.get_debug_info()

        # Template should be None when not set
        assert debug_info["template"] is None


class TestDebugInfoInjection:
    """Test debug info injection in HTML"""

    @patch("django.conf.settings")
    def test_debug_info_injected_when_debug_true(self, mock_settings):
        """Test that debug info is injected when DEBUG=True"""
        mock_settings.DEBUG = True
        mock_settings.STATIC_URL = "/static/"

        class MyView(LiveView):
            template_string = """
            <html>
            <head><title>Test</title></head>
            <body><h1>Test</h1></body>
            </html>
            """
            count = 0

            @event_handler
            def increment(self):
                """Increment counter"""
                self.count += 1

        view = MyView()

        # Test _inject_client_script directly
        test_html = """
        <html>
        <head><title>Test</title></head>
        <body><h1>Test</h1></body>
        </html>
        """
        html = view._inject_client_script(test_html)

        # Check that debug info script is present
        assert "window.DJUST_DEBUG_INFO" in html

        # Check that debug CSS link is present
        assert (
            '<link rel="stylesheet" href="/static/djust/debug-panel.css" data-turbo-track="reload">'
            in html
        )

        # Extract and parse debug info
        import re

        match = re.search(r"window\.DJUST_DEBUG_INFO = ({.*?});", html, re.DOTALL)
        assert match is not None

        debug_info_json = match.group(1)
        debug_info = json.loads(debug_info_json)

        # Verify structure
        assert "view_class" in debug_info
        assert "handlers" in debug_info
        assert "variables" in debug_info
        assert debug_info["view_class"] == "MyView"
        assert "increment" in debug_info["handlers"]

    @patch("django.conf.settings")
    def test_debug_info_not_injected_when_debug_false(self, mock_settings):
        """Test that debug info is NOT injected when DEBUG=False"""
        mock_settings.DEBUG = False
        mock_settings.STATIC_URL = "/static/"

        class MyView(LiveView):
            template_string = """
            <html>
            <head><title>Test</title></head>
            <body><h1>Test</h1></body>
            </html>
            """
            count = 0

            @event_handler
            def increment(self):
                self.count += 1

        view = MyView()

        # Test _inject_client_script directly
        test_html = """
        <html>
        <head><title>Test</title></head>
        <body><h1>Test</h1></body>
        </html>
        """
        html = view._inject_client_script(test_html)

        # Check that debug info is NOT SET (the string will appear in client.js code,
        # but it should not be set as a variable)
        assert "window.DJUST_DEBUG_INFO = {" not in html

        # Check that debug CSS link is NOT present
        assert "debug-panel.css" not in html

    @patch("django.conf.settings")
    def test_debug_css_injected_in_head(self, mock_settings):
        """Test that debug CSS is injected in <head> tag"""
        mock_settings.DEBUG = True
        mock_settings.STATIC_URL = "/static/"

        class MyView(LiveView):
            template_string = """
            <html>
            <head>
                <title>Test</title>
                <link rel="stylesheet" href="/static/other.css">
            </head>
            <body><h1>Test</h1></body>
            </html>
            """

        view = MyView()

        # Test _inject_client_script directly
        test_html = """
        <html>
        <head>
            <title>Test</title>
            <link rel="stylesheet" href="/static/other.css">
        </head>
        <body><h1>Test</h1></body>
        </html>
        """
        html = view._inject_client_script(test_html)

        # Check that debug CSS is in <head>
        head_end = html.find("</head>")
        debug_css_pos = html.find("debug-panel.css")

        assert debug_css_pos != -1
        assert debug_css_pos < head_end  # CSS should be before </head>


class TestDebugPanelMetadata:
    """Test that debug panel receives correct metadata"""

    @patch("django.conf.settings")
    def test_handler_params_in_debug_info(self, mock_settings):
        """Test that handler parameters are correctly serialized"""
        mock_settings.DEBUG = True
        mock_settings.STATIC_URL = "/static/"

        class MyView(LiveView):
            template_string = "<div>Test</div>"

            @event_handler
            def search(self, query: str = "", limit: int = 10, **kwargs):
                """Search with query and limit"""
                pass

        view = MyView()

        # Test _inject_client_script directly
        test_html = "<div>Test</div>"
        html = view._inject_client_script(test_html)

        # Extract debug info
        import re

        match = re.search(r"window\.DJUST_DEBUG_INFO = ({.*?});", html, re.DOTALL)
        debug_info = json.loads(match.group(1))

        # Check search handler
        search = debug_info["handlers"]["search"]
        assert search["description"] == "Search with query and limit"
        assert search["accepts_kwargs"] is True
        assert len(search["params"]) == 2

        # Check query parameter
        query_param = search["params"][0]
        assert query_param["name"] == "query"
        assert query_param["type"] == "str"
        assert query_param["required"] is False
        assert query_param["default"] == ""

        # Check limit parameter
        limit_param = search["params"][1]
        assert limit_param["name"] == "limit"
        assert limit_param["type"] == "int"
        assert limit_param["required"] is False
        assert limit_param["default"] == "10"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
