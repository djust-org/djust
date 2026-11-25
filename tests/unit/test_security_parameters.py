"""
Security tests for event handler parameter injection attacks.

Tests verify that djust properly sanitizes, validates, and rejects
malicious parameters sent through WebSocket event messages. Covers:
- Attribute injection via prototype pollution patterns
- Type coercion abuse (SQL injection, command injection via strings)
- Positional argument override attacks
- Oversized parameter payloads
- Parameter name injection (dunder, private attribute access)
"""

import pytest
from typing import Any

from djust.security.attribute_guard import (
    AttributeSecurityError,
    DANGEROUS_ATTRIBUTES,
    is_safe_attribute_name,
    safe_setattr,
)
from djust.validation import validate_handler_params


def _bound(function):
    """``function`` (written with a ``self``) as the bound method of a throwaway view."""

    class _View:
        pass

    _View.handler = function
    return _View().handler


# ============================================================================
# Attribute injection (prototype pollution) tests
# ============================================================================


class TestPrototypePollutionBlocked:
    """Verify safe_setattr blocks prototype pollution and dunder attribute attacks."""

    def test_dunder_class_blocked(self):
        """Attacker sends __class__ as param key to modify object type."""

        class Target:
            pass

        obj = Target()
        result = safe_setattr(obj, "__class__", object)
        assert result is False
        assert type(obj) is Target

    def test_dunder_proto_blocked(self):
        """JavaScript-style __proto__ pollution attempt."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "__proto__", {"polluted": True}) is False

    def test_dunder_globals_blocked(self):
        """Attempt to access __globals__ to leak server environment."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "__globals__", {}) is False

    def test_dunder_builtins_blocked(self):
        """Attempt to overwrite __builtins__ for code execution."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "__builtins__", {}) is False

    def test_dunder_code_blocked(self):
        """Attempt to replace __code__ on a function."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "__code__", None) is False

    def test_dunder_import_blocked(self):
        """Attempt to overwrite __import__ for supply chain attack."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "__import__", lambda x: None) is False

    @pytest.mark.parametrize("attr", sorted(DANGEROUS_ATTRIBUTES))
    def test_all_dangerous_attributes_blocked(self, attr):
        """Every attribute in the DANGEROUS_ATTRIBUTES set must be blocked."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, attr, "malicious") is False

    def test_raise_on_blocked_mode(self):
        """Verify raise_on_blocked=True raises AttributeSecurityError."""

        class Target:
            pass

        obj = Target()
        with pytest.raises(AttributeSecurityError, match="__class__"):
            safe_setattr(obj, "__class__", object, raise_on_blocked=True)

    def test_private_attribute_blocked_by_default(self):
        """Single-underscore private attributes are blocked by default."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "_internal_state", "hacked") is False

    def test_private_attribute_allowed_when_opted_in(self):
        """Private attributes can be allowed with explicit allow_private=True."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "_internal_state", "ok", allow_private=True) is True
        assert obj._internal_state == "ok"

    def test_safe_attribute_succeeds(self):
        """Normal attribute names pass through safely."""

        class Target:
            pass

        obj = Target()
        assert safe_setattr(obj, "count", 42) is True
        assert obj.count == 42

    def test_additional_blocked_attributes(self):
        """Custom blocked attributes are respected."""

        class Target:
            pass

        obj = Target()
        blocked = {"admin_mode", "debug_flag"}
        assert safe_setattr(obj, "admin_mode", True, additional_blocked=blocked) is False


class TestAttributeNameValidation:
    """Verify is_safe_attribute_name blocks malformed names."""

    def test_empty_string_blocked(self):
        assert is_safe_attribute_name("") is False

    def test_non_string_blocked(self):
        assert is_safe_attribute_name(123) is False
        assert is_safe_attribute_name(None) is False

    def test_special_characters_blocked(self):
        """Attribute names with dots, dashes, spaces, etc. are rejected."""
        assert is_safe_attribute_name("foo.bar") is False
        assert is_safe_attribute_name("foo-bar") is False
        assert is_safe_attribute_name("foo bar") is False
        assert is_safe_attribute_name("foo\nbar") is False
        assert is_safe_attribute_name("foo\x00bar") is False

    def test_numeric_start_blocked(self):
        """Attribute names starting with a digit are invalid Python identifiers."""
        assert is_safe_attribute_name("0day") is False
        assert is_safe_attribute_name("123abc") is False

    def test_arbitrary_dunder_blocked(self):
        """Any __name__ pattern is blocked, not just the known list."""
        assert is_safe_attribute_name("__custom_exploit__") is False
        assert is_safe_attribute_name("__anything__") is False

    def test_unicode_attribute_names(self):
        """Non-ASCII characters in attribute names are blocked."""
        assert is_safe_attribute_name("\u0441ount") is False  # Cyrillic 'с'
        assert is_safe_attribute_name("co\u200bunt") is False  # Zero-width space


# ============================================================================
# Positional argument override attacks
# ============================================================================


class TestPositionalArgOverride:
    """Verify positional args don't enable parameter confusion attacks."""

    def test_positional_args_mapped_correctly(self):
        """Positional args map to handler params in order."""

        def handler(self, item_id: int = 0, action: str = "", **kwargs: Any):
            pass

        result = validate_handler_params(
            _bound(handler), {}, "test", positional_args=[42, "delete"]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 42
        assert result["coerced_params"]["action"] == "delete"

    def test_a_value_given_positionally_and_by_name_is_refused(self):
        """Strict (#4205): a conflicting named value never silently loses to the positional one."""

        def handler(self, item_id: int = 0, **kwargs: Any):
            pass

        result = validate_handler_params(
            _bound(handler), {"item_id": "999"}, "test", positional_args=[1]
        )
        assert result["valid"] is False

    def test_excess_positional_args_are_refused(self):
        """Strict (#4205): arguments beyond the signature are an error, not dropped."""

        def handler(self, item_id: int = 0):
            pass

        result = validate_handler_params(
            _bound(handler), {}, "test", positional_args=[42, "extra1", "extra2"]
        )
        assert result["valid"] is False

    def test_no_positional_args_normal_flow(self):
        """Without positional args, declared named params work normally."""

        def handler(self, value: str = "", **kwargs: Any):
            pass

        result = validate_handler_params(
            _bound(handler), {"value": "hello"}, "test", positional_args=[]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["value"] == "hello"


# ============================================================================
# Handler validation security tests
# ============================================================================


class TestHandlerValidationSecurity:
    """Verify handler validation prevents injection via unexpected parameters."""

    def test_unexpected_params_rejected_without_kwargs(self):
        """Handler without **kwargs rejects extra parameters."""

        def handler(self, value: str = ""):
            pass

        result = validate_handler_params(
            _bound(handler), {"value": "ok", "__class__": "object"}, "test"
        )
        assert result["valid"] is False
        assert "undeclared argument" in result["error"]

    def test_kwargs_handler_accepts_extra_params(self):
        """Handler with **kwargs accepts extra params (by design)."""

        def handler(self, value: str = "", **kwargs: Any):
            pass

        result = validate_handler_params(
            _bound(handler), {"value": "ok", "extra_key": "data"}, "test"
        )
        assert result["valid"] is True

    def test_missing_required_params_rejected(self):
        """Missing required params are caught."""

        def handler(self, required_id: int):
            pass

        result = validate_handler_params(_bound(handler), {}, "test")
        assert result["valid"] is False
        assert "required argument is missing" in result["error"]

    def test_type_mismatch_detected(self):
        """Type validation catches incorrect param types after coercion fails."""

        def handler(self, count: int = 0, **kwargs: Any):
            pass

        result = validate_handler_params(_bound(handler), {"count": "not-a-number"}, "test")
        assert result["valid"] is False


# ============================================================================
# Malicious mount parameter tests
# ============================================================================


class TestMaliciousMountParams:
    """Verify safe_setattr usage blocks attacks through mount/state params."""

    def test_batch_param_injection(self):
        """Simulate batch parameter application with mixed safe and dangerous keys."""

        class ViewState:
            count = 0
            name = "default"

        state = ViewState()
        params = {
            "count": 5,
            "name": "user_input",
            "__class__": object,
            "__dict__": {},
            "_private": "hack",
            "constructor": "evil",
        }

        applied = {}
        for key, value in params.items():
            applied[key] = safe_setattr(state, key, value)

        # Safe params applied
        assert applied["count"] is True
        assert applied["name"] is True
        assert state.count == 5
        assert state.name == "user_input"

        # Dangerous params blocked
        assert applied["__class__"] is False
        assert applied["__dict__"] is False
        assert applied["_private"] is False
        assert applied["constructor"] is False

    def test_state_restoration_with_injection(self):
        """Simulate deserializing tampered state from a malicious client."""

        class ViewState:
            items = []

        state = ViewState()
        original_reduce = state.__reduce__  # All objects have __reduce__
        tampered_state = {
            "items": [1, 2, 3],
            "__reduce__": "os.system('rm -rf /')",
            "__getattr__": lambda self, name: None,
        }

        results = {}
        for key, value in tampered_state.items():
            results[key] = safe_setattr(state, key, value)

        assert state.items == [1, 2, 3]
        assert results["__reduce__"] is False  # Blocked by safe_setattr
        assert results["__getattr__"] is False  # Blocked by safe_setattr
        assert state.__reduce__ == original_reduce  # Not modified
