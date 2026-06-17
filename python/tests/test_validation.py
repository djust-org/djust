"""
Tests for event handler parameter validation.

Tests cover:
- Missing required parameters
- Unexpected parameters
- Type validation
- **kwargs handling
- Automatic type coercion
- Edge cases
"""

from decimal import Decimal
from typing import List, Optional, Union
from uuid import UUID

import pytest
from djust.validation import (
    validate_handler_params,
    validate_parameter_types,
    get_handler_signature_info,
    coerce_parameter_types,
)


# Test handlers for validation
class MockView:
    """Mock view class for testing event handlers"""

    def handler_required_param(self, value: str):
        """Handler with required parameter"""
        pass

    def handler_optional_param(self, value: str = ""):
        """Handler with optional parameter"""
        pass

    def handler_mixed_params(self, required: str, optional: int = 0):
        """Handler with mixed required and optional parameters"""
        pass

    def handler_with_kwargs(self, value: str = "", **kwargs):
        """Handler accepting **kwargs"""
        pass

    def handler_typed(self, count: int, name: str = "default"):
        """Handler with type hints"""
        pass

    def handler_no_params(self):
        """Handler with no parameters"""
        pass


class TestValidateHandlerParams:
    """Test validate_handler_params function"""

    def test_missing_required_parameter(self):
        """Test that missing required parameters are caught"""
        view = MockView()
        result = validate_handler_params(view.handler_required_param, {}, "test_event")
        assert result["valid"] is False
        assert "missing required parameters" in result["error"]
        assert "value" in result["error"]
        assert result["expected"] == ["value"]
        assert result["provided"] == []

    def test_valid_required_parameter(self):
        """Test that providing required parameter passes validation"""
        view = MockView()
        result = validate_handler_params(
            view.handler_required_param, {"value": "test"}, "test_event"
        )
        assert result["valid"] is True
        assert result["error"] is None

    def test_optional_parameter_omitted(self):
        """Test that optional parameters can be omitted"""
        view = MockView()
        result = validate_handler_params(view.handler_optional_param, {}, "test_event")
        assert result["valid"] is True
        assert result["error"] is None

    def test_optional_parameter_provided(self):
        """Test that optional parameters can be provided"""
        view = MockView()
        result = validate_handler_params(
            view.handler_optional_param, {"value": "test"}, "test_event"
        )
        assert result["valid"] is True
        assert result["error"] is None

    def test_unexpected_parameter_without_kwargs(self):
        """Test that unexpected parameters are rejected when no **kwargs"""
        view = MockView()
        result = validate_handler_params(
            view.handler_optional_param, {"value": "test", "unexpected": "bad"}, "test_event"
        )
        assert result["valid"] is False
        assert "unexpected parameters" in result["error"]
        assert "unexpected" in result["error"]
        assert set(result["expected"]) == {"value"}
        assert set(result["provided"]) == {"value", "unexpected"}

    def test_unexpected_parameter_with_kwargs(self):
        """Test that unexpected parameters are accepted with **kwargs"""
        view = MockView()
        result = validate_handler_params(
            view.handler_with_kwargs,
            {"value": "test", "any": "param", "works": "here"},
            "test_event",
        )
        assert result["valid"] is True
        assert result["error"] is None

    def test_mixed_params_all_provided(self):
        """Test mixed required and optional parameters all provided"""
        view = MockView()
        result = validate_handler_params(
            view.handler_mixed_params, {"required": "test", "optional": 42}, "test_event"
        )
        assert result["valid"] is True
        assert result["error"] is None

    def test_mixed_params_only_required(self):
        """Test mixed parameters with only required provided"""
        view = MockView()
        result = validate_handler_params(
            view.handler_mixed_params, {"required": "test"}, "test_event"
        )
        assert result["valid"] is True
        assert result["error"] is None

    def test_mixed_params_missing_required(self):
        """Test mixed parameters with required missing"""
        view = MockView()
        result = validate_handler_params(view.handler_mixed_params, {"optional": 42}, "test_event")
        assert result["valid"] is False
        assert "missing required parameters" in result["error"]
        assert "required" in result["error"]

    def test_no_params_handler_empty_params(self):
        """Test handler with no parameters receives empty params"""
        view = MockView()
        result = validate_handler_params(view.handler_no_params, {}, "test_event")
        assert result["valid"] is True
        assert result["error"] is None

    def test_no_params_handler_with_params(self):
        """Test handler with no parameters rejects parameters"""
        view = MockView()
        result = validate_handler_params(
            view.handler_no_params, {"unexpected": "value"}, "test_event"
        )
        assert result["valid"] is False
        assert "unexpected parameters" in result["error"]


class TestValidateParameterTypes:
    """Test validate_parameter_types function"""

    def test_type_validation_correct_types(self):
        """Test that correct types pass validation"""
        view = MockView()
        errors = validate_parameter_types(view.handler_typed, {"count": 42, "name": "test"})
        assert errors is None or len(errors) == 0

    def test_type_validation_wrong_type(self):
        """Test that wrong types are caught"""
        view = MockView()
        errors = validate_parameter_types(
            view.handler_typed, {"count": "not_an_int", "name": "test"}
        )
        assert errors is not None
        assert len(errors) == 1
        assert errors[0]["param"] == "count"
        assert errors[0]["expected"] == "int"
        assert errors[0]["actual"] == "str"

    def test_type_validation_multiple_wrong_types(self):
        """Test that multiple type errors are caught"""
        view = MockView()
        errors = validate_parameter_types(view.handler_typed, {"count": "not_an_int", "name": 123})
        assert errors is not None
        assert len(errors) == 2
        param_names = {err["param"] for err in errors}
        assert param_names == {"count", "name"}

    def test_type_validation_no_type_hints(self):
        """Test that handlers without type hints skip validation"""

        def handler_no_hints(self, value):
            pass

        errors = validate_parameter_types(handler_no_hints, {"value": "anything"})
        # Should return None or empty list when no type hints
        assert errors is None or len(errors) == 0


class TestGetHandlerSignatureInfo:
    """Test get_handler_signature_info function"""

    def test_signature_info_basic(self):
        """Test extracting basic signature information"""
        view = MockView()
        info = get_handler_signature_info(view.handler_optional_param)

        assert len(info["params"]) == 1
        assert info["params"][0]["name"] == "value"
        assert info["params"][0]["type"] == "str"
        assert info["params"][0]["required"] is False
        assert info["params"][0]["default"] == ""
        assert info["description"] == "Handler with optional parameter"
        assert info["accepts_kwargs"] is False

    def test_signature_info_with_kwargs(self):
        """Test signature info for handler with **kwargs"""
        view = MockView()
        info = get_handler_signature_info(view.handler_with_kwargs)

        assert len(info["params"]) == 1  # **kwargs not included in params
        assert info["accepts_kwargs"] is True

    def test_signature_info_required_params(self):
        """Test signature info correctly identifies required parameters"""
        view = MockView()
        info = get_handler_signature_info(view.handler_mixed_params)

        assert len(info["params"]) == 2
        # Find the required param
        required_param = next(p for p in info["params"] if p["name"] == "required")
        assert required_param["required"] is True
        assert required_param["default"] is None

        # Find the optional param
        optional_param = next(p for p in info["params"] if p["name"] == "optional")
        assert optional_param["required"] is False
        assert optional_param["default"] == "0"

    def test_signature_info_no_params(self):
        """Test signature info for handler with no parameters"""
        view = MockView()
        info = get_handler_signature_info(view.handler_no_params)

        assert len(info["params"]) == 0
        assert info["accepts_kwargs"] is False
        assert info["description"] == "Handler with no parameters"

    def test_signature_info_union_type(self):
        """Test signature info for handler with PEP 604 union type (str | None).

        Regression test for #899: UnionType has no __name__.
        """

        def handler_with_union(self, value: str | None = None, **kwargs):
            pass

        info = get_handler_signature_info(handler_with_union)
        param = info["params"][0]
        assert param["name"] == "value"
        assert param["type"] == "str | None"
        assert param["required"] is False

    def test_signature_info_optional_type(self):
        """Test signature info for handler with typing.Optional (same as X | None)."""
        from typing import Optional

        def handler_with_optional(self, value: Optional[int] = None, **kwargs):
            pass

        info = get_handler_signature_info(handler_with_optional)
        param = info["params"][0]
        assert param["name"] == "value"
        assert param["type"] == "int | None"

    def test_signature_info_multi_union_type(self):
        """Test signature info for handler with multi-type PEP 604 union."""

        def handler_with_multi_union(self, value: str | int | None = None, **kwargs):
            pass

        info = get_handler_signature_info(handler_with_multi_union)
        param = info["params"][0]
        assert param["name"] == "value"
        assert param["type"] == "str | int | None"

    def test_signature_info_any_type(self):
        """Test signature info for handler with typing.Any."""
        from typing import Any

        def handler_with_any(self, value: Any = None, **kwargs):
            pass

        info = get_handler_signature_info(handler_with_any)
        param = info["params"][0]
        assert param["name"] == "value"
        assert param["type"] == "Any"


class TestValidationIntegration:
    """Integration tests combining validation functions"""

    def test_full_validation_flow_success(self):
        """Test complete validation flow for valid call"""
        view = MockView()

        # Get signature info
        info = get_handler_signature_info(view.handler_mixed_params)
        assert len(info["params"]) == 2

        # Validate parameters
        result = validate_handler_params(
            view.handler_mixed_params, {"required": "test", "optional": 42}, "my_event"
        )
        assert result["valid"] is True

    def test_full_validation_flow_type_error(self):
        """Test complete validation flow with type error"""
        view = MockView()

        # Validate parameters with wrong type
        result = validate_handler_params(
            view.handler_typed, {"count": "not_an_int", "name": "test"}, "my_event"
        )
        assert result["valid"] is False
        assert "wrong parameter types" in result["error"]
        assert result["type_errors"] is not None
        assert len(result["type_errors"]) == 1

    def test_real_world_scenario_search_handler(self):
        """Test validation for realistic search handler"""

        class SearchView:
            def search(self, value: str = ""):
                """Search with debouncing (without **kwargs)"""
                pass

        view = SearchView()

        # Common mistake: using 'query' instead of 'value'
        result = validate_handler_params(view.search, {"query": "test"}, "search")
        assert result["valid"] is False
        assert "unexpected parameters" in result["error"]
        assert "query" in result["error"]
        assert "value" in result["expected"]

        # Correct usage
        result = validate_handler_params(view.search, {"value": "test"}, "search")
        assert result["valid"] is True

    def test_real_world_scenario_update_quantity(self):
        """Test validation for realistic update handler with type coercion"""

        class ItemView:
            def update_quantity(self, item_id: int, quantity: int = 1):
                """Update item quantity"""
                pass

        view = ItemView()

        # With automatic coercion, string "123" is coerced to int 123
        result = validate_handler_params(
            view.update_quantity, {"item_id": "123", "quantity": 5}, "update_quantity"
        )
        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 123  # Coerced from "123"
        assert result["coerced_params"]["quantity"] == 5

        # Invalid string that can't be coerced to int
        result = validate_handler_params(
            view.update_quantity, {"item_id": "not_an_int", "quantity": 5}, "update_quantity"
        )
        assert result["valid"] is False
        assert "wrong parameter types" in result["error"]
        assert result["type_errors"][0]["param"] == "item_id"

        # Integer values work directly
        result = validate_handler_params(
            view.update_quantity, {"item_id": 123, "quantity": 5}, "update_quantity"
        )
        assert result["valid"] is True


class TestEdgeCases:
    """Test edge cases and boundary conditions"""

    def test_empty_params_dict(self):
        """Test validation with empty parameters dict"""
        view = MockView()
        result = validate_handler_params(view.handler_optional_param, {}, "test_event")
        assert result["valid"] is True

    def test_none_parameter_value(self):
        """Test that None parameter values are caught by type validation"""
        view = MockView()
        result = validate_handler_params(view.handler_optional_param, {"value": None}, "test_event")
        # None fails type validation for str parameter
        assert result["valid"] is False
        assert "wrong parameter types" in result["error"]

    def test_extra_params_with_kwargs(self):
        """Test that many extra params work with **kwargs"""
        view = MockView()
        many_params = {f"param_{i}": f"value_{i}" for i in range(100)}
        many_params["value"] = "test"

        result = validate_handler_params(view.handler_with_kwargs, many_params, "test_event")
        assert result["valid"] is True

    def test_validation_details_structure(self):
        """Test that validation result has correct structure"""
        view = MockView()
        result = validate_handler_params(view.handler_required_param, {}, "test_event")

        # Verify structure
        assert "valid" in result
        assert "error" in result
        assert "expected" in result
        assert "provided" in result
        assert "type_errors" in result

        assert isinstance(result["valid"], bool)
        assert isinstance(result["expected"], list)
        assert isinstance(result["provided"], list)


class TestCoerceParameterTypes:
    """Test coerce_parameter_types function"""

    def test_coerce_string_to_int(self):
        """Test that string is coerced to int"""

        def handler(self, count: int):
            pass

        result = coerce_parameter_types(handler, {"count": "42"})
        assert result["count"] == 42
        assert isinstance(result["count"], int)

    def test_coerce_negative_string_to_int(self):
        """Test that negative number strings are coerced correctly"""

        def handler(self, offset: int):
            pass

        result = coerce_parameter_types(handler, {"offset": "-123"})
        assert result["offset"] == -123
        assert isinstance(result["offset"], int)

    def test_coerce_string_to_float(self):
        """Test that string is coerced to float"""

        def handler(self, price: float):
            pass

        result = coerce_parameter_types(handler, {"price": "3.14"})
        assert result["price"] == 3.14
        assert isinstance(result["price"], float)

    def test_coerce_negative_string_to_float(self):
        """Test that negative float strings are coerced correctly"""

        def handler(self, temperature: float):
            pass

        result = coerce_parameter_types(handler, {"temperature": "-273.15"})
        assert result["temperature"] == -273.15
        assert isinstance(result["temperature"], float)

    def test_coerce_float_special_values(self):
        """Test that float special values (inf, nan) are handled.

        Note: These are valid Python float values but may be unexpected
        in some contexts. The coercion allows them since they are valid floats.
        """
        import math

        def handler(self, value: float):
            pass

        # Infinity
        result = coerce_parameter_types(handler, {"value": "inf"})
        assert math.isinf(result["value"])
        assert result["value"] > 0

        # Negative infinity
        result = coerce_parameter_types(handler, {"value": "-inf"})
        assert math.isinf(result["value"])
        assert result["value"] < 0

        # NaN
        result = coerce_parameter_types(handler, {"value": "nan"})
        assert math.isnan(result["value"])

    def test_coerce_string_to_bool_true_values(self):
        """Test that various truthy strings are coerced to True"""

        def handler(self, enabled: bool):
            pass

        for value in ["true", "True", "TRUE", "1", "yes", "on"]:
            result = coerce_parameter_types(handler, {"enabled": value})
            assert result["enabled"] is True, f"Expected True for '{value}'"

    def test_coerce_string_to_bool_false_values(self):
        """Test that other strings are coerced to False"""

        def handler(self, enabled: bool):
            pass

        for value in ["false", "False", "0", "no", "off"]:
            result = coerce_parameter_types(handler, {"enabled": value})
            assert result["enabled"] is False, f"Expected False for '{value}'"

    def test_coerce_empty_string_to_bool(self):
        """Test that empty string explicitly coerces to False for bool"""

        def handler(self, enabled: bool):
            pass

        result = coerce_parameter_types(handler, {"enabled": ""})
        assert result["enabled"] is False
        assert isinstance(result["enabled"], bool)

    def test_coerce_string_to_decimal(self):
        """Test that string is coerced to Decimal"""

        def handler(self, amount: Decimal):
            pass

        result = coerce_parameter_types(handler, {"amount": "123.45"})
        assert result["amount"] == Decimal("123.45")
        assert isinstance(result["amount"], Decimal)

    def test_coerce_string_to_uuid(self):
        """Test that string is coerced to UUID"""

        def handler(self, id: UUID):
            pass

        uuid_str = "550e8400-e29b-41d4-a716-446655440000"
        result = coerce_parameter_types(handler, {"id": uuid_str})
        assert result["id"] == UUID(uuid_str)
        assert isinstance(result["id"], UUID)

    def test_coerce_string_to_list(self):
        """Test that comma-separated string is coerced to list"""

        def handler(self, tags: list):
            pass

        result = coerce_parameter_types(handler, {"tags": "a,b,c"})
        assert result["tags"] == ["a", "b", "c"]

    def test_coerce_string_to_typed_list(self):
        """Test that comma-separated string is coerced to typed List[int]"""

        def handler(self, ids: List[int]):
            pass

        result = coerce_parameter_types(handler, {"ids": "1,2,3"})
        assert result["ids"] == [1, 2, 3]
        assert all(isinstance(x, int) for x in result["ids"])

    def test_coerce_empty_string_to_int(self):
        """Test that empty string is coerced to 0 for int"""

        def handler(self, count: int):
            pass

        result = coerce_parameter_types(handler, {"count": ""})
        assert result["count"] == 0

    def test_coerce_empty_string_to_list(self):
        """Test that empty string is coerced to empty list"""

        def handler(self, tags: list):
            pass

        result = coerce_parameter_types(handler, {"tags": ""})
        assert result["tags"] == []

    def test_coerce_preserves_non_string_values(self):
        """Test that non-string values are preserved"""

        def handler(self, count: int):
            pass

        result = coerce_parameter_types(handler, {"count": 42})
        assert result["count"] == 42

    def test_coerce_skips_params_without_type_hints(self):
        """Test that params without type hints are preserved as-is"""

        def handler(self, value):
            pass

        result = coerce_parameter_types(handler, {"value": "test"})
        assert result["value"] == "test"

    def test_coerce_handles_optional_types(self):
        """Test that Optional[int] is handled correctly"""

        def handler(self, count: Optional[int] = None):
            pass

        result = coerce_parameter_types(handler, {"count": "42"})
        assert result["count"] == 42

    def test_coerce_union_uses_first_non_none_type(self):
        """Test that Union types coerce to the first non-None type.

        For Union[int, str], coercion will try int first. This is documented
        behavior - if you need different behavior, use a specific type.
        """

        def handler(self, value: Union[int, str] = 0):
            pass

        # "42" can be coerced to int, so it becomes int
        result = coerce_parameter_types(handler, {"value": "42"})
        assert result["value"] == 42
        assert isinstance(result["value"], int)

        # "hello" can't be coerced to int, so it stays as string
        result = coerce_parameter_types(handler, {"value": "hello"})
        assert result["value"] == "hello"
        assert isinstance(result["value"], str)

    def test_coerce_invalid_int_keeps_original(self):
        """Test that invalid int conversion keeps original value"""

        def handler(self, count: int):
            pass

        result = coerce_parameter_types(handler, {"count": "not_an_int"})
        # Should keep original since coercion failed
        assert result["count"] == "not_an_int"

    def test_coerce_invalid_uuid_keeps_original(self):
        """Test that invalid UUID conversion keeps original value"""

        def handler(self, id: UUID):
            pass

        result = coerce_parameter_types(handler, {"id": "not-a-uuid"})
        assert result["id"] == "not-a-uuid"


class TestCoercionIntegration:
    """Integration tests for coercion with validation"""

    def test_validation_with_coercion_succeeds(self):
        """Test that validation passes after coercion"""

        def handler(self, item_id: int, quantity: int = 1):
            pass

        # String values that would fail without coercion
        result = validate_handler_params(
            handler, {"item_id": "123", "quantity": "5"}, "update_item"
        )
        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 123
        assert result["coerced_params"]["quantity"] == 5

    def test_validation_fails_for_unconvertible_values(self):
        """Test that validation fails when coercion can't convert"""

        def handler(self, count: int):
            pass

        result = validate_handler_params(handler, {"count": "not_an_int"}, "test_event")
        assert result["valid"] is False
        assert "wrong parameter types" in result["error"]

    def test_coercion_can_be_disabled(self):
        """Test that coercion can be disabled"""

        def handler(self, count: int):
            pass

        result = validate_handler_params(handler, {"count": "42"}, "test_event", coerce=False)
        assert result["valid"] is False  # String "42" doesn't match int

    def test_real_world_toggle_sender_scenario(self):
        """Test the Mail Manager toggle_sender scenario that caused 11 handler fixes"""

        class InboxView:
            def toggle_sender(self, sender_id: int = 0, **kwargs):
                """Toggle sender expansion"""
                pass

        view = InboxView()

        # This is exactly what the template sends: string from data-sender-id
        result = validate_handler_params(view.toggle_sender, {"sender_id": "123"}, "toggle_sender")

        assert result["valid"] is True
        assert result["coerced_params"]["sender_id"] == 123
        assert isinstance(result["coerced_params"]["sender_id"], int)

    def test_real_world_update_quantity_scenario(self):
        """Test realistic e-commerce update quantity handler"""

        class CartView:
            def update_quantity(self, item_id: int, quantity: int = 1, **kwargs):
                """Update item quantity in cart"""
                pass

        view = CartView()

        # Template sends: data-item-id="456" data-quantity="3"
        result = validate_handler_params(
            view.update_quantity,
            {"item_id": "456", "quantity": "3"},
            "update_quantity",
        )

        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 456
        assert result["coerced_params"]["quantity"] == 3

    def test_real_world_filter_with_bool(self):
        """Test filter handler with boolean parameter"""

        class ListView:
            def toggle_filter(self, enabled: bool = False, category: str = "all", **kwargs):
                """Toggle filter visibility"""
                pass

        view = ListView()

        # Template sends: data-enabled="true" data-category="electronics"
        result = validate_handler_params(
            view.toggle_filter,
            {"enabled": "true", "category": "electronics"},
            "toggle_filter",
        )

        assert result["valid"] is True
        assert result["coerced_params"]["enabled"] is True
        assert result["coerced_params"]["category"] == "electronics"


class TestFormatTypeErrorHint:
    """Tests for the format_type_error_hint function and helpers."""

    def test_string_to_int_hint_includes_template_syntax(self):
        """Test that int hint includes typed attribute syntax."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="sender_id",
            expected="int",
            actual="str",
            value="abc",
            coercion_attempted=True,
        )

        assert "data-sender-id:int" in hint
        assert "could not be converted to int" in hint
        assert "Quick fixes" in hint

    def test_string_to_float_hint(self):
        """Test that float hint includes appropriate guidance."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="price",
            expected="float",
            actual="str",
            value="invalid",
            coercion_attempted=True,
        )

        assert "data-price:float" in hint
        assert "could not be converted to float" in hint

    def test_string_to_bool_hint(self):
        """Test that bool hint includes valid true values."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="enabled",
            expected="bool",
            actual="str",
            value="maybe",
            coercion_attempted=True,
        )

        assert "data-enabled:bool" in hint
        assert "true" in hint.lower()
        assert "yesno" in hint

    def test_string_to_list_hint(self):
        """Test that list hint includes JSON and comma-separated options."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="tags",
            expected="list",
            actual="str",
            value="not a list",
            coercion_attempted=True,
        )

        assert "data-tags:json" in hint
        assert "data-tags:list" in hint
        assert "comma-separated" in hint.lower()

    def test_string_to_decimal_hint(self):
        """Test that Decimal hint provides appropriate guidance."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="amount",
            expected="Decimal",
            actual="str",
            value="$100",
            coercion_attempted=True,
        )

        assert "data-amount" in hint
        assert "Decimal" in hint or "decimal" in hint

    def test_generic_hint_for_unknown_types(self):
        """Test that unknown type combinations get a generic hint."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="custom",
            expected="CustomType",
            actual="dict",
            value={},
            coercion_attempted=True,
        )

        assert "CustomType" in hint
        assert "Hint:" in hint

    def test_hint_without_coercion_attempted(self):
        """Test hint when coercion was not attempted."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="count",
            expected="int",
            actual="str",
            value="42",
            coercion_attempted=False,
        )

        # Should still provide quick fixes but not mention failed conversion
        assert "Quick fixes" in hint
        assert "could not be converted" not in hint

    def test_param_name_kebab_conversion(self):
        """Test that snake_case params are converted to kebab-case in hints."""
        from djust.validation import format_type_error_hint

        hint = format_type_error_hint(
            param="user_profile_id",
            expected="int",
            actual="str",
            value="abc",
            coercion_attempted=True,
        )

        assert "data-user-profile-id:int" in hint


class TestPositionalArgsMapping:
    """Test positional arguments mapping from inline handler syntax.

    Tests the feature that allows @click="handler('value')" to pass
    positional arguments that are mapped to named parameters.
    """

    def test_single_positional_arg_maps_to_first_param(self):
        """Test that a single positional arg maps to the first parameter."""

        def handler(self, value: str, **kwargs):
            pass

        result = validate_handler_params(handler, {}, "handler", positional_args=["hello"])
        assert result["valid"] is True
        assert result["coerced_params"]["value"] == "hello"

    def test_multiple_positional_args_map_in_order(self):
        """Test that multiple positional args map to parameters in order."""

        def handler(self, name: str, count: int, enabled: bool = False, **kwargs):
            pass

        result = validate_handler_params(
            handler, {}, "handler", positional_args=["Alice", 42, True]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["name"] == "Alice"
        assert result["coerced_params"]["count"] == 42
        assert result["coerced_params"]["enabled"] is True

    def test_positional_args_take_precedence_over_data_attrs(self):
        """Test that positional args override data-* attribute values."""

        def handler(self, value: str, **kwargs):
            pass

        # data-value="from_attr" but positional arg says "from_handler"
        result = validate_handler_params(
            handler, {"value": "from_attr"}, "handler", positional_args=["from_handler"]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["value"] == "from_handler"

    def test_extra_positional_args_are_ignored(self):
        """Test that extra positional args beyond parameters are ignored."""

        def handler(self, value: str, **kwargs):
            pass

        result = validate_handler_params(
            handler, {}, "handler", positional_args=["first", "second", "third"]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["value"] == "first"
        assert "second" not in result["coerced_params"]
        assert "third" not in result["coerced_params"]

    def test_positional_args_with_type_coercion(self):
        """Test that positional args are coerced to expected types."""

        def handler(self, count: int, price: float, **kwargs):
            pass

        # Note: In practice, parseEventHandler already parses to typed values
        # but if strings are passed, coercion should still work
        result = validate_handler_params(handler, {}, "handler", positional_args=[42, 19.99])
        assert result["valid"] is True
        assert result["coerced_params"]["count"] == 42
        assert result["coerced_params"]["price"] == 19.99

    def test_empty_positional_args_list(self):
        """Test that empty positional args list works correctly."""

        def handler(self, value: str = "default", **kwargs):
            pass

        result = validate_handler_params(handler, {}, "handler", positional_args=[])
        assert result["valid"] is True
        # Default value is used since nothing was provided
        assert result["coerced_params"] == {}

    def test_positional_args_with_required_params(self):
        """Test that positional args satisfy required parameters."""

        def handler(self, required_value: str):
            """Handler with required parameter and no **kwargs."""
            pass

        # Without positional args, validation fails
        result = validate_handler_params(handler, {}, "handler")
        assert result["valid"] is False
        assert "missing required parameters" in result["error"]

        # With positional args, validation passes
        result = validate_handler_params(handler, {}, "handler", positional_args=["provided"])
        assert result["valid"] is True
        assert result["coerced_params"]["required_value"] == "provided"

    def test_positional_args_none_uses_default(self):
        """Test that None positional_args behaves like empty list."""

        def handler(self, value: str = "default", **kwargs):
            pass

        result = validate_handler_params(handler, {}, "handler", positional_args=None)
        assert result["valid"] is True

    def test_real_world_set_period_scenario(self):
        """Test the set_period('month') scenario from Issue #62."""

        class DashboardView:
            def set_period(self, value: str, **kwargs):
                """Set the time period filter."""
                pass

        view = DashboardView()

        # @click="set_period('month')" should map 'month' to value parameter
        result = validate_handler_params(
            view.set_period, {}, "set_period", positional_args=["month"]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["value"] == "month"

        # Different period values
        for period in ["day", "week", "month", "year"]:
            result = validate_handler_params(
                view.set_period, {}, "set_period", positional_args=[period]
            )
            assert result["valid"] is True
            assert result["coerced_params"]["value"] == period

    def test_real_world_select_tab_scenario(self):
        """Test tab selection with integer index."""

        class TabView:
            def select_tab(self, index: int, **kwargs):
                """Select a tab by index."""
                pass

        view = TabView()

        # @click="select_tab(2)" should map 2 to index parameter
        result = validate_handler_params(view.select_tab, {}, "select_tab", positional_args=[2])
        assert result["valid"] is True
        assert result["coerced_params"]["index"] == 2
        assert isinstance(result["coerced_params"]["index"], int)

    def test_positional_args_combined_with_data_attrs(self):
        """Test combining positional args with data-* attributes."""

        def handler(self, action: str, item_id: int = 0, confirm: bool = False, **kwargs):
            pass

        # @click="handler('delete')" with data-item-id="123" data-confirm="true"
        result = validate_handler_params(
            handler,
            {"item_id": "123", "confirm": "true"},
            "handler",
            positional_args=["delete"],
        )
        assert result["valid"] is True
        assert result["coerced_params"]["action"] == "delete"
        assert result["coerced_params"]["item_id"] == 123  # Coerced from string
        assert result["coerced_params"]["confirm"] is True


class TestCoercionSecurityEdgeCases:
    """Security regression tests for type-coercion edge cases (issue #1820).

    These PIN the audited-safe behavior of ``validate_handler_params`` against
    malformed / adversarial inputs. The coercion contract is:

      - ``str -> int``  : ``int()`` (base-10). Malformed strings RAISE in
        ``int()``; coercion keeps the original string, type validation then
        rejects the event (``valid is False``) — the handler is NOT invoked
        (see ``websocket.py`` ~1275 / 2943 / 3080 / 3192). It does NOT
        silently truncate ``"999 OR 1=1"`` to ``999``.
      - ``str -> bool`` : ALLOWLIST — ``value.lower() in
        ("true", "1", "yes", "on")``. This is NOT ``bool(non_empty_string)``,
        so adversarial strings such as ``"true; DROP TABLE"`` and the
        falsy-but-non-empty ``"false"`` / ``"0"`` coerce to ``False``. There
        is no truthiness logic-bypass.
      - ``str -> float``: ``float()``. Malformed strings are rejected (as for
        ``int``). Note: ``"inf"`` / ``"-inf"`` / ``"nan"`` ARE accepted because
        they are valid Python floats — this is an intentional, documented
        contract (see ``test_coerce_float_special_values`` and
        ``test_float_inf_nan_pass_as_valid_floats_known_contract`` below).

    NOTE: these are CHARACTERIZATION tests — they pin the *current, audited*
    behavior. No behavior change ships with #1820; the audit concluded the
    coercion paths are already safe (malformed input is rejected, bool uses an
    allowlist), so these tests are not "gate-off-able" against a code change.
    They guard against future regressions that would weaken the contract.
    """

    # --- str -> int (issue #1820 case 1 + 4) -------------------------------

    def test_sql_injection_string_to_int_is_rejected_not_truncated(self):
        """``page="999 OR 1=1"`` (page: int) must be REJECTED, not truncated.

        Confirms djust mirrors Python ``int()`` semantics: ``int("999 OR 1=1")``
        raises ``ValueError``, so the event is rejected rather than silently
        coerced to ``999`` (which would bypass an integer-comparison guard).
        """

        def handler(self, page: int = 0, **kwargs):
            pass

        result = validate_handler_params(handler, {"page": "999 OR 1=1"}, "paginate")
        assert result["valid"] is False
        # Original string preserved (NOT truncated to int 999).
        assert result["coerced_params"]["page"] == "999 OR 1=1"
        assert not isinstance(result["coerced_params"]["page"], int)
        # Type error names the offending param.
        assert any(e["param"] == "page" for e in result["type_errors"])

    def test_hex_string_to_int_is_rejected(self):
        """``id="0x41"`` / ``id="0x41414141"`` (id: int) must be REJECTED.

        ``int()`` is base-10 only, so hex literals raise and are rejected
        rather than coerced (Python's ``int("0x41")`` raises ValueError).
        """

        def handler(self, id: int = 0, **kwargs):
            pass

        for hexstr in ("0x41", "0x41414141"):
            result = validate_handler_params(handler, {"id": hexstr}, "load")
            assert result["valid"] is False, f"{hexstr!r} should be rejected"
            assert result["coerced_params"]["id"] == hexstr  # not coerced

    def test_well_formed_int_still_coerces(self):
        """Guard the happy path: a legitimate numeric string still coerces."""

        def handler(self, page: int = 0, **kwargs):
            pass

        result = validate_handler_params(handler, {"page": "42"}, "paginate")
        assert result["valid"] is True
        assert result["coerced_params"]["page"] == 42

    # --- str -> bool (issue #1820 case 2 — the designated dangerous one) ---

    def test_bool_coercion_uses_allowlist_not_truthiness(self):
        """``active="true; DROP TABLE"`` (active: bool) must coerce to False.

        The dangerous failure mode would be ``bool(non_empty_string)``, under
        which ANY non-empty string (including ``"false"`` and ``"0"``) is
        ``True`` — a real logic bypass. djust uses an ALLOWLIST instead, so an
        adversarial non-allowlisted string is ``False``.
        """

        def handler(self, active: bool = False, **kwargs):
            pass

        result = validate_handler_params(
            handler, {"active": "true; DROP TABLE"}, "toggle"
        )
        # bool coercion never raises -> always a valid bool -> valid is True.
        assert result["valid"] is True
        assert result["coerced_params"]["active"] is False

    def test_bool_falsy_strings_are_false_not_true(self):
        """The crux: ``"false"`` / ``"0"`` / ``"no"`` / ``"off"`` -> False.

        If bool coercion were ``bool(non_empty_string)`` these would all be
        ``True`` (a logic bypass). The allowlist makes them ``False``.
        """

        def handler(self, active: bool = False, **kwargs):
            pass

        for falsy in ("false", "False", "FALSE", "0", "no", "off", "anything", "2"):
            result = validate_handler_params(handler, {"active": falsy}, "toggle")
            assert result["valid"] is True
            assert result["coerced_params"]["active"] is False, (
                f"{falsy!r} must coerce to False (allowlist), not True"
            )

    def test_bool_truthy_allowlist_values_are_true(self):
        """Only the documented allowlist values coerce to True."""

        def handler(self, active: bool = False, **kwargs):
            pass

        for truthy in ("true", "True", "TRUE", "1", "yes", "YES", "on", "ON"):
            result = validate_handler_params(handler, {"active": truthy}, "toggle")
            assert result["valid"] is True
            assert result["coerced_params"]["active"] is True, (
                f"{truthy!r} must coerce to True"
            )

    # --- str -> float (issue #1820 case 3) ---------------------------------

    def test_malformed_float_string_is_rejected(self):
        """``amount="3.14 OR 1=1"`` (amount: float) must be REJECTED."""

        def handler(self, amount: float = 0.0, **kwargs):
            pass

        result = validate_handler_params(handler, {"amount": "3.14 OR 1=1"}, "pay")
        assert result["valid"] is False
        assert result["coerced_params"]["amount"] == "3.14 OR 1=1"  # not coerced

    def test_float_overflow_to_inf_known_contract(self):
        """``amount="1e309"`` overflows to ``inf`` and is accepted.

        CHARACTERIZATION: ``float("1e309")`` is ``inf``, a valid float, so it
        passes type validation. This is an intentional contract — handlers
        that perform bound checks or arithmetic on a coerced ``float`` should
        guard against non-finite values themselves (``math.isfinite``).
        """
        import math

        def handler(self, amount: float = 0.0, **kwargs):
            pass

        result = validate_handler_params(handler, {"amount": "1e309"}, "pay")
        assert result["valid"] is True
        assert math.isinf(result["coerced_params"]["amount"])

    def test_float_inf_nan_pass_as_valid_floats_known_contract(self):
        """``amount="inf"`` / ``"nan"`` (amount: float) are accepted.

        CHARACTERIZATION of the documented contract (mirrors
        ``test_coerce_float_special_values``): ``inf`` / ``-inf`` / ``nan`` are
        valid Python floats and so pass validation. Documented here at the
        handler-validation layer (not just the raw-coerce layer) so the
        security implication — non-finite floats reach handlers and downstream
        comparisons (``nan > x`` is always False) must be guarded by the
        application — is pinned and discoverable.
        """
        import math

        def handler(self, amount: float = 0.0, **kwargs):
            pass

        r_inf = validate_handler_params(handler, {"amount": "inf"}, "pay")
        assert r_inf["valid"] is True
        assert math.isinf(r_inf["coerced_params"]["amount"])

        r_nan = validate_handler_params(handler, {"amount": "nan"}, "pay")
        assert r_nan["valid"] is True
        assert math.isnan(r_nan["coerced_params"]["amount"])

    # --- list / typed-list adversarial inputs ------------------------------

    def test_typed_list_with_malformed_element_is_not_partially_coerced(self):
        """``ids="1,2,OR 1=1"`` (ids: List[int]): NO partial coercion.

        EMPIRICALLY VERIFIED CONTRACT (issue #1820 audit): a malformed element
        makes the *whole* typed-list coercion raise inside ``_coerce_value``
        (``int("OR 1=1")`` -> ValueError), which ``coerce_parameter_types``
        catches and recovers from by KEEPING THE ORIGINAL STRING. Crucially:

          - The handler does NOT receive a partially-coerced ``[1, 2]`` (no
            silent truncation of the malformed tail).
          - The injected text never becomes a list element.

        ``validate_parameter_types`` intentionally SKIPS subscripted generics
        like ``List[int]`` (its guard is ``isinstance(expected_type, type)``),
        so the raw string reaches the handler rather than being flagged as a
        type error. A handler typed ``ids: List[int]`` must therefore treat its
        input defensively — but the value it gets is the unmodified original
        string, never adversarial list contents. This test pins that contract;
        if a future change starts partially coercing, it fails.
        """

        def handler(self, ids: List[int] = None, **kwargs):
            pass

        result = validate_handler_params(handler, {"ids": "1,2,OR 1=1"}, "bulk")
        coerced = result["coerced_params"]["ids"]
        # Original string preserved verbatim — NOT a list, NOT partially coerced.
        assert coerced == "1,2,OR 1=1"
        assert not isinstance(coerced, list)
        # Sanity: a well-formed typed list DOES coerce fully.
        ok = validate_handler_params(handler, {"ids": "1,2,3"}, "bulk")
        assert ok["coerced_params"]["ids"] == [1, 2, 3]

    # --- coercion-disabled posture is at least as strict ------------------

    def test_strict_posture_via_coerce_false_rejects_all_strings(self):
        """``coerce=False`` is the strictest posture: every str fails int/bool.

        This is the existing knob that approximates the issue's suggested
        ``@strict_types`` for callers that want zero coercion — a string
        ``"42"`` for an ``int`` param is rejected outright.
        """

        def handler(self, page: int = 0, active: bool = False, **kwargs):
            pass

        result = validate_handler_params(
            handler, {"page": "42", "active": "true"}, "evt", coerce=False
        )
        assert result["valid"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
