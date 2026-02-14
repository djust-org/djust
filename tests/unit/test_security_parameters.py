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
from decimal import Decimal
from uuid import UUID

from djust.security.attribute_guard import (
    AttributeSecurityError,
    DANGEROUS_ATTRIBUTES,
    is_safe_attribute_name,
    safe_setattr,
)
from djust.validation import (
    coerce_parameter_types,
    validate_handler_params,
    validate_parameter_types,
)


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
# Type coercion abuse tests
# ============================================================================


class TestTypeCoercionSQLInjection:
    """Verify type coercion rejects SQL injection payloads."""

    def test_sql_injection_in_int_param(self):
        """SQL injection string fails int coercion, kept as original string."""

        def handler(self, item_id: int = 0, **kwargs):
            pass

        result = coerce_parameter_types(handler, {"item_id": "1; DROP TABLE users;--"})
        # Coercion fails, value stays as string
        assert isinstance(result["item_id"], str)
        assert "DROP TABLE" in result["item_id"]

    def test_sql_injection_caught_by_type_validation(self):
        """After failed coercion, type validation catches the mismatch."""

        def handler(self, item_id: int = 0, **kwargs):
            pass

        params = coerce_parameter_types(handler, {"item_id": "1 OR 1=1"})
        errors = validate_parameter_types(handler, params)
        assert errors is not None
        assert errors[0]["param"] == "item_id"
        assert errors[0]["expected"] == "int"
        assert errors[0]["actual"] == "str"

    def test_sql_union_injection_in_uuid(self):
        """SQL UNION injection in UUID param fails coercion."""

        def handler(self, ref: UUID, **kwargs):
            pass

        result = coerce_parameter_types(handler, {"ref": "' UNION SELECT password FROM users--"})
        # UUID coercion fails, value stays as string
        assert isinstance(result["ref"], str)


class TestTypeCoercionCommandInjection:
    """Verify command injection payloads are handled safely in type coercion."""

    def test_command_injection_in_int_param(self):
        """Shell command in int param fails coercion."""

        def handler(self, count: int = 0, **kwargs):
            pass

        result = coerce_parameter_types(handler, {"count": "$(rm -rf /)"})
        assert isinstance(result["count"], str)  # Not coerced

    def test_pipe_injection_in_string_param(self):
        """String params preserve the value (strings are valid strings)."""

        def handler(self, query: str = "", **kwargs):
            pass

        result = coerce_parameter_types(handler, {"query": "test | cat /etc/passwd"})
        # String coercion succeeds (it is a string), but the app should
        # handle this safely via Django ORM parameterized queries
        assert result["query"] == "test | cat /etc/passwd"

    def test_newline_injection_in_string_param(self):
        """Newline characters in string params are preserved for the handler."""

        def handler(self, value: str = "", **kwargs):
            pass

        result = coerce_parameter_types(handler, {"value": "line1\r\nline2"})
        assert result["value"] == "line1\r\nline2"


class TestTypeCoercionOverflow:
    """Verify numeric overflow and edge cases in type coercion."""

    def test_extremely_large_int(self):
        """Very large integers are valid in Python, coercion should succeed."""

        def handler(self, count: int = 0, **kwargs):
            pass

        result = coerce_parameter_types(handler, {"count": "9" * 1000})
        assert isinstance(result["count"], int)

    def test_float_nan(self):
        """NaN string coerces to float NaN."""

        def handler(self, value: float = 0.0, **kwargs):
            pass

        import math

        result = coerce_parameter_types(handler, {"value": "nan"})
        assert isinstance(result["value"], float)
        assert math.isnan(result["value"])

    def test_float_inf(self):
        """Infinity string coerces to float inf."""

        def handler(self, value: float = 0.0, **kwargs):
            pass

        import math

        result = coerce_parameter_types(handler, {"value": "inf"})
        assert isinstance(result["value"], float)
        assert math.isinf(result["value"])

    def test_decimal_extreme_precision(self):
        """Extremely precise Decimal values are handled."""

        def handler(self, price: Decimal = Decimal("0"), **kwargs):
            pass

        result = coerce_parameter_types(handler, {"price": "0." + "1" * 100})
        assert isinstance(result["price"], Decimal)

    def test_empty_string_int_coercion(self):
        """Empty string coerces to 0 for int type."""

        def handler(self, count: int = 0, **kwargs):
            pass

        result = coerce_parameter_types(handler, {"count": ""})
        assert result["count"] == 0

    def test_empty_string_float_coercion(self):
        """Empty string coerces to 0.0 for float type."""

        def handler(self, value: float = 0.0, **kwargs):
            pass

        result = coerce_parameter_types(handler, {"value": ""})
        assert result["value"] == 0.0


# ============================================================================
# Positional argument override attacks
# ============================================================================


class TestPositionalArgOverride:
    """Verify positional args don't enable parameter confusion attacks."""

    def test_positional_args_mapped_correctly(self):
        """Positional args map to handler params in order."""

        def handler(self, item_id: int = 0, action: str = "", **kwargs):
            pass

        result = validate_handler_params(handler, {}, "test", positional_args=[42, "delete"])
        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 42
        assert result["coerced_params"]["action"] == "delete"

    def test_positional_args_override_data_attrs(self):
        """Positional args override conflicting data-* attributes."""

        def handler(self, item_id: int = 0, **kwargs):
            pass

        # Attacker sends data-item-id="999" but inline handler has id=1
        result = validate_handler_params(handler, {"item_id": "999"}, "test", positional_args=[1])
        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 1  # Positional wins

    def test_excess_positional_args_ignored(self):
        """Extra positional args beyond handler params are safely ignored."""

        def handler(self, item_id: int = 0, **kwargs):
            pass

        result = validate_handler_params(
            handler, {}, "test", positional_args=[42, "extra1", "extra2"]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 42
        assert "extra1" not in result["coerced_params"]

    def test_no_positional_args_normal_flow(self):
        """Without positional args, data-* params work normally."""

        def handler(self, value: str = "", **kwargs):
            pass

        result = validate_handler_params(handler, {"value": "hello"}, "test", positional_args=[])
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

        result = validate_handler_params(handler, {"value": "ok", "__class__": "object"}, "test")
        assert result["valid"] is False
        assert "unexpected parameters" in result["error"]

    def test_kwargs_handler_accepts_extra_params(self):
        """Handler with **kwargs accepts extra params (by design)."""

        def handler(self, value: str = "", **kwargs):
            pass

        result = validate_handler_params(handler, {"value": "ok", "extra_key": "data"}, "test")
        assert result["valid"] is True

    def test_missing_required_params_rejected(self):
        """Missing required params are caught."""

        def handler(self, required_id: int):
            pass

        result = validate_handler_params(handler, {}, "test")
        assert result["valid"] is False
        assert "missing required parameters" in result["error"]

    def test_type_mismatch_detected(self):
        """Type validation catches incorrect param types after coercion fails."""

        def handler(self, count: int = 0, **kwargs):
            pass

        result = validate_handler_params(handler, {"count": "not-a-number"}, "test")
        assert result["valid"] is False
        assert result["type_errors"] is not None


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
