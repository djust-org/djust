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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
