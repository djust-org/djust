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

from typing import Optional

import types

from djust.validation import (
    validate_handler_params,
    get_handler_signature_info,
)


# Test handlers for validation
def _bound(function):
    """Bind a module- or test-local function the way dispatch binds a view method."""
    return types.MethodType(function, object())


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
        assert "required argument is missing" in result["error"]
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
        assert "undeclared argument" in result["error"]
        assert set(result["expected"]) == {"value"}

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
        assert "required argument is missing" in result["error"]
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
        assert "undeclared argument" in result["error"]


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
        assert "default" not in info["params"][0]  # a server default is never published
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

        # Find the optional param
        optional_param = next(p for p in info["params"] if p["name"] == "optional")
        assert optional_param["required"] is False
        assert "default" not in optional_param

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

        info = get_handler_signature_info(handler_with_union, for_declaration=True)
        param = info["params"][0]
        assert param["name"] == "value"
        assert param["type"] == "str | None"
        assert param["required"] is False

    def test_signature_info_optional_type(self):
        """Test signature info for handler with typing.Optional (same as X | None)."""

        def handler_with_optional(self, value: Optional[int] = None, **kwargs):
            pass

        info = get_handler_signature_info(handler_with_optional, for_declaration=True)
        param = info["params"][0]
        assert param["name"] == "value"
        assert param["type"] == "int | None"

    def test_signature_info_multi_union_type(self):
        """Test signature info for handler with multi-type PEP 604 union."""

        def handler_with_multi_union(self, value: str | int | None = None, **kwargs):
            pass

        info = get_handler_signature_info(handler_with_multi_union, for_declaration=True)
        param = info["params"][0]
        assert param["name"] == "value"
        assert param["type"] == "str | int | None"

    def test_signature_info_any_type(self):
        """Test signature info for handler with typing.Any."""
        from typing import Any

        def handler_with_any(self, value: Any = None, **kwargs):
            pass

        info = get_handler_signature_info(handler_with_any, for_declaration=True)
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
        assert "requires" in result["error"]

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
        assert "undeclared argument" in result["error"]
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
        assert "requires" in result["error"]

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
        assert "requires" in result["error"]

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


class TestCoercionIntegration:
    """Integration tests for coercion with validation"""

    def test_validation_with_coercion_succeeds(self):
        """Test that validation passes after coercion"""

        def handler(self, item_id: int, quantity: int = 1):
            pass

        # String values that would fail without coercion
        result = validate_handler_params(
            _bound(handler), {"item_id": "123", "quantity": "5"}, "update_item"
        )
        assert result["valid"] is True
        assert result["coerced_params"]["item_id"] == 123
        assert result["coerced_params"]["quantity"] == 5

    def test_validation_fails_for_unconvertible_values(self):
        """Test that validation fails when coercion can't convert"""

        def handler(self, count: int):
            pass

        result = validate_handler_params(_bound(handler), {"count": "not_an_int"}, "test_event")
        assert result["valid"] is False
        assert "requires" in result["error"]

    def test_coercion_can_be_disabled(self):
        """Test that coercion can be disabled"""

        def handler(self, count: int):
            pass

        result = validate_handler_params(
            _bound(handler), {"count": "42"}, "test_event", coerce=False
        )
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


class TestPositionalArgsMapping:
    """Test positional arguments mapping from inline handler syntax.

    Tests the feature that allows @click="handler('value')" to pass
    positional arguments that are mapped to named parameters.
    """

    def test_single_positional_arg_maps_to_first_param(self):
        """Test that a single positional arg maps to the first parameter."""

        def handler(self, value: str, **kwargs):
            pass

        result = validate_handler_params(_bound(handler), {}, "handler", positional_args=["hello"])
        assert result["valid"] is True
        assert result["coerced_params"]["value"] == "hello"

    def test_multiple_positional_args_map_in_order(self):
        """Test that multiple positional args map to parameters in order."""

        def handler(self, name: str, count: int, enabled: bool = False, **kwargs):
            pass

        result = validate_handler_params(
            _bound(handler), {}, "handler", positional_args=["Alice", 42, True]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["name"] == "Alice"
        assert result["coerced_params"]["count"] == 42
        assert result["coerced_params"]["enabled"] is True

    def test_positional_args_take_precedence_over_data_attrs(self):
        """A value supplied both positionally and by name is refused, not chosen between."""

        def handler(self, value: str, **kwargs):
            pass

        # data-value="from_attr" but positional arg says "from_handler"
        result = validate_handler_params(
            _bound(handler), {"value": "from_attr"}, "handler", positional_args=["from_handler"]
        )
        assert result["valid"] is False

    def test_positional_args_with_type_coercion(self):
        """Test that positional args are coerced to expected types."""

        def handler(self, count: int, price: float, **kwargs):
            pass

        # Note: In practice, parseEventHandler already parses to typed values
        # but if strings are passed, coercion should still work
        result = validate_handler_params(
            _bound(handler), {}, "handler", positional_args=[42, 19.99]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["count"] == 42
        assert result["coerced_params"]["price"] == 19.99

    def test_empty_positional_args_list(self):
        """Test that empty positional args list works correctly."""

        def handler(self, value: str = "default", **kwargs):
            pass

        result = validate_handler_params(_bound(handler), {}, "handler", positional_args=[])
        assert result["valid"] is True
        # Default value is used since nothing was provided
        assert result["coerced_params"] == {}

    def test_positional_args_with_required_params(self):
        """Test that positional args satisfy required parameters."""

        def handler(self, required_value: str):
            """Handler with required parameter and no **kwargs."""
            pass

        # Without positional args, validation fails
        result = validate_handler_params(_bound(handler), {}, "handler")
        assert result["valid"] is False
        assert "required argument is missing" in result["error"]

        # With positional args, validation passes
        result = validate_handler_params(
            _bound(handler), {}, "handler", positional_args=["provided"]
        )
        assert result["valid"] is True
        assert result["coerced_params"]["required_value"] == "provided"

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
            _bound(handler),
            {"item_id": "123", "confirm": "true"},
            "handler",
            positional_args=["delete"],
        )
        assert result["valid"] is True
        assert result["coerced_params"]["action"] == "delete"
        assert result["coerced_params"]["item_id"] == 123  # Coerced from string
        assert result["coerced_params"]["confirm"] is True
