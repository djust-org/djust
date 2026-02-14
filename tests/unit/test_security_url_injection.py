"""
Security tests for URL injection attacks in djust navigation.

Tests verify that the NavigationMixin and URL handling code properly
handles malicious URL inputs, including:
- Path traversal via live_redirect / live_patch
- CRLF injection in URL parameters
- JavaScript URI scheme injection
- Open redirect via live_redirect
- Log injection via URL parameters
"""

from djust.mixins.navigation import NavigationMixin
from djust.security.event_guard import is_safe_event_name
from djust.security.log_sanitizer import sanitize_for_log, sanitize_dict_for_log


# ============================================================================
# NavigationMixin URL injection tests
# ============================================================================


class TestLiveRedirectInjection:
    """Verify live_redirect handles malicious paths safely.

    Note: live_redirect queues navigation commands that are sent to the
    client. The server-side code stores the path as-is; client-side JS
    and Django URL routing provide the actual security boundary. These
    tests document expected behavior and ensure no server-side crashes.
    """

    def _make_nav_mixin(self):
        mixin = NavigationMixin()
        mixin._init_navigation()
        return mixin

    def test_normal_redirect(self):
        """Standard redirect path is queued correctly."""
        nav = self._make_nav_mixin()
        nav.live_redirect("/items/42/")
        commands = nav._drain_navigation()
        assert len(commands) == 1
        assert commands[0]["type"] == "live_redirect"
        assert commands[0]["path"] == "/items/42/"

    def test_path_traversal_in_redirect(self):
        """Path traversal in redirect path is stored as-is (URL routing handles security)."""
        nav = self._make_nav_mixin()
        nav.live_redirect("/../../../etc/passwd")
        commands = nav._drain_navigation()
        assert len(commands) == 1
        # The path is stored verbatim; Django URL resolver won't match this

    def test_javascript_uri_in_redirect(self):
        """JavaScript URI scheme in redirect path is stored as-is.

        The client-side navigation code uses history.pushState/replaceState
        which only accepts same-origin URLs, so javascript: URIs are blocked
        by the browser.
        """
        nav = self._make_nav_mixin()
        nav.live_redirect("javascript:alert(1)")
        commands = nav._drain_navigation()
        assert len(commands) == 1
        assert commands[0]["path"] == "javascript:alert(1)"

    def test_data_uri_in_redirect(self):
        """Data URI scheme in redirect path."""
        nav = self._make_nav_mixin()
        nav.live_redirect("data:text/html,<script>alert(1)</script>")
        commands = nav._drain_navigation()
        assert len(commands) == 1

    def test_external_url_in_redirect(self):
        """External URL in redirect (open redirect vector).

        history.pushState blocks cross-origin URLs, so this is safe
        in the browser. The server stores it without validation.
        """
        nav = self._make_nav_mixin()
        nav.live_redirect("https://evil.com/phishing")
        commands = nav._drain_navigation()
        assert len(commands) == 1

    def test_redirect_with_replace(self):
        """Replace mode queues correctly."""
        nav = self._make_nav_mixin()
        nav.live_redirect("/new-page/", replace=True)
        commands = nav._drain_navigation()
        assert commands[0]["replace"] is True


class TestLivePatchInjection:
    """Verify live_patch handles malicious parameters safely."""

    def _make_nav_mixin(self):
        mixin = NavigationMixin()
        mixin._init_navigation()
        return mixin

    def test_normal_patch(self):
        """Standard patch with params works correctly."""
        nav = self._make_nav_mixin()
        nav.live_patch(params={"page": 2, "sort": "name"})
        commands = nav._drain_navigation()
        assert len(commands) == 1
        assert commands[0]["type"] == "live_patch"
        assert commands[0]["params"]["page"] == 2

    def test_crlf_injection_in_params(self):
        """CRLF characters in param values are preserved (URL encoding is client's job)."""
        nav = self._make_nav_mixin()
        nav.live_patch(params={"query": "test\r\nSet-Cookie: evil=1"})
        commands = nav._drain_navigation()
        assert commands[0]["params"]["query"] == "test\r\nSet-Cookie: evil=1"

    def test_html_injection_in_params(self):
        """HTML tags in param values are stored as-is."""
        nav = self._make_nav_mixin()
        nav.live_patch(params={"search": '<img src=x onerror="alert(1)">'})
        commands = nav._drain_navigation()
        assert "<img" in commands[0]["params"]["search"]

    def test_extremely_long_param_value(self):
        """Very long parameter values don't crash the navigation system."""
        nav = self._make_nav_mixin()
        nav.live_patch(params={"q": "A" * 100_000})
        commands = nav._drain_navigation()
        assert len(commands[0]["params"]["q"]) == 100_000

    def test_path_override_in_patch(self):
        """live_patch with custom path queues correctly."""
        nav = self._make_nav_mixin()
        nav.live_patch(path="/search/", params={"q": "test"})
        commands = nav._drain_navigation()
        assert commands[0]["path"] == "/search/"

    def test_null_byte_in_param(self):
        """Null bytes in parameter values are preserved."""
        nav = self._make_nav_mixin()
        nav.live_patch(params={"q": "test\x00evil"})
        commands = nav._drain_navigation()
        assert "\x00" in commands[0]["params"]["q"]

    def test_drain_clears_commands(self):
        """Draining navigation commands clears the queue."""
        nav = self._make_nav_mixin()
        nav.live_patch(params={"page": 1})
        nav.live_patch(params={"page": 2})
        commands = nav._drain_navigation()
        assert len(commands) == 2
        assert nav._drain_navigation() == []


class TestHandleParamsInjection:
    """Verify handle_params processes URL parameters safely."""

    def test_default_handle_params_is_noop(self):
        """Base handle_params does nothing (safe by default)."""
        mixin = NavigationMixin()
        mixin._init_navigation()
        # Should not raise
        mixin.handle_params({"__class__": "evil", "query": "test"}, "/search/?q=test")

    def test_handle_params_receives_raw_params(self):
        """handle_params receives the raw params dict for the view to process."""

        class TestNav(NavigationMixin):
            received_params = None

            def handle_params(self, params, uri):
                self.received_params = params

        nav = TestNav()
        nav._init_navigation()
        nav.handle_params({"category": "books", "page": "2"}, "/items/?category=books&page=2")
        assert nav.received_params == {"category": "books", "page": "2"}


# ============================================================================
# Log injection via event names and parameters
# ============================================================================


class TestLogInjectionPrevention:
    """Verify sanitize_for_log prevents log injection attacks."""

    def test_newline_injection_stripped(self):
        """Newlines in logged values are replaced to prevent log line injection."""
        result = sanitize_for_log("normal\n[CRITICAL] Fake log entry")
        assert "\n" not in result
        assert "CRITICAL" in result  # Content preserved, but on same line

    def test_ansi_escape_stripped(self):
        """ANSI escape sequences are removed to prevent terminal manipulation."""
        result = sanitize_for_log("\x1b[31mRED TEXT\x1b[0m")
        assert "\x1b" not in result
        assert "RED TEXT" in result

    def test_control_characters_stripped(self):
        """Control characters (ASCII 0-31) are removed."""
        result = sanitize_for_log("test\x00\x01\x02\x03value")
        assert "\x00" not in result
        assert "\x01" not in result

    def test_truncation_prevents_log_flooding(self):
        """Very long strings are truncated to prevent log file flooding."""
        long_string = "A" * 10_000
        result = sanitize_for_log(long_string)
        assert len(result) <= 500 + len("...[truncated at 500 chars]")

    def test_none_value_handled(self):
        """None values produce safe placeholder string."""
        assert sanitize_for_log(None) == "[None]"

    def test_bytes_value_handled(self):
        """Bytes values are decoded safely."""
        result = sanitize_for_log(b"binary \xff data")
        assert isinstance(result, str)

    def test_carriage_return_stripped(self):
        """Carriage returns are replaced (prevents overwriting log lines)."""
        result = sanitize_for_log("visible\roverwritten")
        assert "\r" not in result


class TestLogSanitizerDict:
    """Verify sanitize_dict_for_log redacts sensitive keys."""

    def test_password_redacted(self):
        result = sanitize_dict_for_log({"username": "admin", "password": "s3cret"})
        assert result["password"] == "[REDACTED]"
        assert result["username"] == "admin"

    def test_token_redacted(self):
        result = sanitize_dict_for_log({"token": "abc123", "action": "login"})
        assert result["token"] == "[REDACTED]"

    def test_csrf_redacted(self):
        result = sanitize_dict_for_log({"csrf": "token123", "event": "submit"})
        assert result["csrf"] == "[REDACTED]"

    def test_nested_dict_sanitized(self):
        result = sanitize_dict_for_log({"data": {"password": "secret", "name": "test"}})
        assert result["data"]["password"] == "[REDACTED]"
        assert result["data"]["name"] == "test"

    def test_long_list_truncated(self):
        """Lists with >10 items are truncated."""
        result = sanitize_dict_for_log({"items": list(range(20))})
        assert len(result["items"]) == 11  # 10 items + "...and 10 more"

    def test_custom_sensitive_keys(self):
        """Custom sensitive key sets work."""
        result = sanitize_dict_for_log(
            {"api_key": "abc", "internal_id": "123"},
            sensitive_keys={"internal_id"},
        )
        assert result["internal_id"] == "[REDACTED]"
        assert result["api_key"] == "abc"  # Not in custom set


# ============================================================================
# Event name injection via URL-triggered events
# ============================================================================


class TestEventNameInjectionViaURL:
    """Verify event names derived from URL parameters are validated."""

    def test_dunder_event_blocked(self):
        """Dunder method names are blocked as event names."""
        assert is_safe_event_name("__init__") is False
        assert is_safe_event_name("__class__") is False
        assert is_safe_event_name("__import__") is False

    def test_private_method_blocked(self):
        """Private method names are blocked as event names."""
        assert is_safe_event_name("_private_handler") is False

    def test_dot_notation_blocked(self):
        """Dot-separated names (attribute traversal) are blocked."""
        assert is_safe_event_name("os.system") is False
        assert is_safe_event_name("subprocess.call") is False

    def test_camel_case_blocked(self):
        """CamelCase names are blocked (only lowercase + underscore allowed)."""
        assert is_safe_event_name("DeleteUser") is False

    def test_numeric_start_blocked(self):
        """Names starting with a digit are blocked."""
        assert is_safe_event_name("1drop_table") is False

    def test_empty_string_blocked(self):
        assert is_safe_event_name("") is False

    def test_space_injection_blocked(self):
        """Names with spaces are blocked."""
        assert is_safe_event_name("search; rm -rf /") is False

    def test_valid_event_names_pass(self):
        """Standard event names pass validation."""
        assert is_safe_event_name("search") is True
        assert is_safe_event_name("update_item") is True
        assert is_safe_event_name("toggle_todo") is True
        assert is_safe_event_name("a") is True
        assert is_safe_event_name("page2") is True
