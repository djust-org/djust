"""
Tests for djust security utilities.

These tests verify the security utilities work correctly to prevent:
- Prototype pollution attacks (safe_setattr)
- Log injection attacks (sanitize_for_log)
- Information disclosure (create_safe_error_response)
"""

import pytest
import logging


class TestAttributeGuard:
    """Tests for the attribute_guard module."""

    def test_is_safe_attribute_name_allows_normal_names(self):
        """Normal attribute names should be allowed."""
        from djust.security import is_safe_attribute_name

        assert is_safe_attribute_name("count") is True
        assert is_safe_attribute_name("user_name") is True
        assert is_safe_attribute_name("item123") is True
        assert is_safe_attribute_name("CamelCase") is True

    def test_is_safe_attribute_name_blocks_dunder_attributes(self):
        """Dunder attributes should be blocked."""
        from djust.security import is_safe_attribute_name

        assert is_safe_attribute_name("__class__") is False
        assert is_safe_attribute_name("__proto__") is False
        assert is_safe_attribute_name("__init__") is False
        assert is_safe_attribute_name("__dict__") is False
        assert is_safe_attribute_name("__module__") is False

    def test_is_safe_attribute_name_blocks_private_by_default(self):
        """Single underscore (private) attributes should be blocked by default."""
        from djust.security import is_safe_attribute_name

        assert is_safe_attribute_name("_private") is False
        assert is_safe_attribute_name("_internal_state") is False

    def test_is_safe_attribute_name_allows_private_when_enabled(self):
        """Private attributes can be allowed with allow_private=True."""
        from djust.security import is_safe_attribute_name

        assert is_safe_attribute_name("_private", allow_private=True) is True
        assert is_safe_attribute_name("_internal_state", allow_private=True) is True
        # But dunders should still be blocked
        assert is_safe_attribute_name("__class__", allow_private=True) is False

    def test_is_safe_attribute_name_blocks_dangerous_attributes(self):
        """Known dangerous attributes should be blocked."""
        from djust.security import is_safe_attribute_name, DANGEROUS_ATTRIBUTES

        for attr in DANGEROUS_ATTRIBUTES:
            assert is_safe_attribute_name(attr) is False, f"{attr} should be blocked"

    def test_is_safe_attribute_name_blocks_prototype_pollution_vectors(self):
        """Prototype pollution vectors should be blocked."""
        from djust.security import is_safe_attribute_name

        assert is_safe_attribute_name("prototype") is False
        assert is_safe_attribute_name("constructor") is False

    def test_is_safe_attribute_name_blocks_special_characters(self):
        """Attribute names with special characters should be blocked."""
        from djust.security import is_safe_attribute_name

        assert is_safe_attribute_name("foo.bar") is False
        assert is_safe_attribute_name("foo[bar]") is False
        assert is_safe_attribute_name("foo;bar") is False
        assert is_safe_attribute_name("foo\nbar") is False
        assert is_safe_attribute_name("") is False

    def test_is_safe_attribute_name_blocks_non_strings(self):
        """Non-string attribute names should be blocked."""
        from djust.security import is_safe_attribute_name

        assert is_safe_attribute_name(123) is False
        assert is_safe_attribute_name(None) is False
        assert is_safe_attribute_name(["list"]) is False

    def test_safe_setattr_sets_normal_attributes(self):
        """safe_setattr should set normal attributes."""
        from djust.security import safe_setattr

        class TestObj:
            pass

        obj = TestObj()
        result = safe_setattr(obj, "count", 5)

        assert result is True
        assert obj.count == 5

    def test_safe_setattr_blocks_dangerous_attributes(self):
        """safe_setattr should block dangerous attributes."""
        from djust.security import safe_setattr

        class TestObj:
            pass

        obj = TestObj()
        original_class = obj.__class__

        result = safe_setattr(obj, "__class__", object)

        assert result is False
        assert obj.__class__ is original_class  # Unchanged

    def test_safe_setattr_blocks_proto(self):
        """safe_setattr should block __proto__ (JavaScript prototype pollution)."""
        from djust.security import safe_setattr

        class TestObj:
            pass

        obj = TestObj()
        result = safe_setattr(obj, "__proto__", {"evil": True})

        assert result is False

    def test_safe_setattr_raises_when_requested(self):
        """safe_setattr should raise when raise_on_blocked=True."""
        from djust.security import safe_setattr, AttributeSecurityError

        class TestObj:
            pass

        obj = TestObj()

        with pytest.raises(AttributeSecurityError) as exc_info:
            safe_setattr(obj, "__class__", object, raise_on_blocked=True)

        assert "__class__" in str(exc_info.value)

    def test_safe_setattr_handles_read_only_properties(self):
        """safe_setattr should handle read-only properties gracefully."""
        from djust.security import safe_setattr

        class TestObj:
            @property
            def readonly(self):
                return "constant"

        obj = TestObj()
        result = safe_setattr(obj, "readonly", "new_value")

        assert result is False  # Should fail gracefully
        assert obj.readonly == "constant"  # Unchanged


class TestLogSanitizer:
    """Tests for the log_sanitizer module."""

    def test_sanitize_for_log_handles_normal_text(self):
        """Normal text should pass through unchanged."""
        from djust.security import sanitize_for_log

        assert sanitize_for_log("hello world") == "hello world"
        assert sanitize_for_log("user@example.com") == "user@example.com"

    def test_sanitize_for_log_strips_ansi_escape_sequences(self):
        """ANSI escape sequences should be stripped."""
        from djust.security import sanitize_for_log

        # Red text escape sequence
        result = sanitize_for_log("\x1b[31mred text\x1b[0m")
        assert "\x1b" not in result
        assert "red text" in result

    def test_sanitize_for_log_strips_control_characters(self):
        """Control characters should be stripped."""
        from djust.security import sanitize_for_log

        result = sanitize_for_log("hello\x00world")
        assert "\x00" not in result

        result = sanitize_for_log("test\x07bell")
        assert "\x07" not in result

    def test_sanitize_for_log_replaces_newlines(self):
        """Newlines should be replaced to prevent log injection."""
        from djust.security import sanitize_for_log

        result = sanitize_for_log("line1\nline2\r\nline3")
        assert "\n" not in result
        assert "\r" not in result
        assert "line1" in result
        assert "line2" in result
        assert "line3" in result

    def test_sanitize_for_log_truncates_long_strings(self):
        """Long strings should be truncated."""
        from djust.security import sanitize_for_log, MAX_LOG_LENGTH

        long_string = "a" * 1000
        result = sanitize_for_log(long_string)

        assert len(result) <= MAX_LOG_LENGTH
        assert "truncated" in result

    def test_sanitize_for_log_handles_none(self):
        """None should be handled gracefully."""
        from djust.security import sanitize_for_log

        assert sanitize_for_log(None) == "[None]"

    def test_sanitize_for_log_handles_bytes(self):
        """Bytes should be decoded safely."""
        from djust.security import sanitize_for_log

        result = sanitize_for_log(b"hello world")
        assert result == "hello world"

        # Invalid UTF-8 should be handled
        result = sanitize_for_log(b"\xff\xfe")
        assert result  # Should not raise

    def test_sanitize_for_log_handles_objects(self):
        """Objects should be stringified."""
        from djust.security import sanitize_for_log

        result = sanitize_for_log(12345)
        assert result == "12345"

        result = sanitize_for_log(["list", "items"])
        assert "list" in result

    def test_sanitize_dict_for_log_redacts_sensitive_keys(self):
        """Sensitive keys like password should be redacted."""
        from djust.security.log_sanitizer import sanitize_dict_for_log

        data = {
            "username": "bob",
            "password": "secret123",
            "api_key": "sk-123456",
        }
        result = sanitize_dict_for_log(data)

        assert result["username"] == "bob"
        assert result["password"] == "[REDACTED]"
        assert result["api_key"] == "[REDACTED]"


class TestErrorHandling:
    """Tests for the error_handling module."""

    def test_create_safe_error_response_production_mode(self):
        """In production (DEBUG=False), response should be generic."""
        from djust.security import create_safe_error_response

        exc = ValueError("sensitive database details here")
        response = create_safe_error_response(exc, debug_mode=False)

        assert response["type"] == "error"
        assert "sensitive" not in response["error"]
        assert "database" not in response["error"]
        assert "traceback" not in response
        assert "params" not in response

    def test_create_safe_error_response_debug_mode(self):
        """In debug mode, response should include details."""
        from djust.security import create_safe_error_response

        exc = ValueError("test error message")
        response = create_safe_error_response(exc, debug_mode=True)

        assert response["type"] == "error"
        assert "ValueError" in response["error"]
        assert "test error message" in response["error"]
        assert "traceback" in response
        # Still should NOT include params
        assert "params" not in response

    def test_create_safe_error_response_includes_event_name_in_debug(self):
        """Event name should be included in debug mode."""
        from djust.security import create_safe_error_response

        exc = ValueError("test")
        response = create_safe_error_response(
            exc,
            event_name="click",
            debug_mode=True,
        )

        assert response["event"] == "click"

    def test_create_safe_error_response_excludes_event_name_in_production(self):
        """Event name should NOT be included in production."""
        from djust.security import create_safe_error_response

        exc = ValueError("test")
        response = create_safe_error_response(
            exc,
            event_name="click",
            debug_mode=False,
        )

        assert "event" not in response

    def test_create_safe_error_response_never_includes_params(self):
        """Params should NEVER be included (even in debug mode)."""
        from djust.security import create_safe_error_response

        exc = ValueError("test")

        # Debug mode
        response = create_safe_error_response(exc, debug_mode=True)
        assert "params" not in response

        # Production mode
        response = create_safe_error_response(exc, debug_mode=False)
        assert "params" not in response

    def test_create_safe_error_response_uses_error_type_messages(self):
        """Different error types should use appropriate messages."""
        from djust.security import create_safe_error_response

        exc = ValueError("test")

        mount_response = create_safe_error_response(
            exc, error_type="mount", debug_mode=False
        )
        assert "refresh" in mount_response["error"].lower()

        event_response = create_safe_error_response(
            exc, error_type="event", debug_mode=False
        )
        assert "error occurred" in event_response["error"].lower()

    def test_safe_error_message_debug_mode(self):
        """safe_error_message should include exception details in debug mode."""
        from djust.security import safe_error_message

        exc = ValueError("test error")
        message = safe_error_message(exc, debug_mode=True)

        assert "ValueError" in message
        assert "test error" in message

    def test_safe_error_message_production_mode(self):
        """safe_error_message should be generic in production mode."""
        from djust.security import safe_error_message

        exc = ValueError("sensitive details")
        message = safe_error_message(exc, debug_mode=False)

        assert "sensitive" not in message
        assert "ValueError" not in message

    def test_handle_exception_returns_safe_response(self):
        """handle_exception should return a safe response dict."""
        from djust.security import handle_exception
        import logging

        logger = logging.getLogger("test")
        exc = ValueError("test error")

        # With mock Django settings in production mode
        response = handle_exception(
            exc,
            error_type="event",
            event_name="click",
            logger=logger,
        )

        assert response["type"] == "error"
        assert "error" in response

    def test_handle_exception_with_context(self):
        """handle_exception should accept context parameters."""
        from djust.security import handle_exception
        import logging

        logger = logging.getLogger("test")
        exc = RuntimeError("test")

        response = handle_exception(
            exc,
            error_type="mount",
            view_class="TestView",
            event_name="mount",
            logger=logger,
            log_message="Custom log message",
        )

        assert response["type"] == "error"


class TestSecurityIntegration:
    """Integration tests for security utilities working together."""

    def test_safe_setattr_state_restoration_scenario(self):
        """Test safe_setattr in a state restoration scenario."""
        from djust.security import safe_setattr

        class ViewState:
            pass

        view = ViewState()

        # Simulate state restoration from session
        saved_state = {
            "count": 5,
            "search_query": "test",
            "__class__": object,  # Attack attempt
            "__proto__": {"evil": True},  # Attack attempt
            "prototype": {"bad": True},  # Attack attempt
        }

        for key, value in saved_state.items():
            safe_setattr(view, key, value)

        # Good values should be set
        assert view.count == 5
        assert view.search_query == "test"

        # Dangerous attributes should NOT be set
        assert view.__class__ is ViewState
        assert not hasattr(view, "__proto__")
        assert not hasattr(view, "prototype")

    def test_combined_logging_and_error_handling(self):
        """Test sanitize_for_log and create_safe_error_response together."""
        from djust.security import sanitize_for_log, create_safe_error_response

        # Simulate malicious user input with log injection
        malicious_input = "test\n[CRITICAL] Fake log entry\x1b[31m"

        # Sanitize for logging
        safe_log = sanitize_for_log(malicious_input)
        assert "\n" not in safe_log
        assert "\x1b" not in safe_log
        assert "[CRITICAL]" in safe_log  # Content preserved, just sanitized

        # Create error response
        exc = ValueError(f"Invalid input: {malicious_input}")
        response = create_safe_error_response(exc, debug_mode=False)

        # Production response should be generic
        assert "Invalid input" not in response["error"]
        assert "[CRITICAL]" not in response["error"]
