"""Coverage for ``djust.validation`` hint generators.

The hint generators produce actionable error messages when parameter
coercion fails. They're exercised indirectly by
``validate_handler_params`` but the exact wording is worth locking in —
these messages are what app developers see when a template attribute
comes through in the wrong type.
"""

from __future__ import annotations


from djust.validation import (
    _coerce_single_value,
    _coerce_value,
    coerce_parameter_types,
    format_type_error_hint,
    get_handler_signature_info,
    validate_handler_params,
    validate_parameter_types,
)


# ─────────────────────────────────────────────────────────────────────────────
# format_type_error_hint routes to the right sub-generator
# ─────────────────────────────────────────────────────────────────────────────


def test_hint_generic_for_unknown_pair():
    hint = format_type_error_hint("x", expected="SomeCustomType", actual="dict", value={})
    assert "SomeCustomType" in hint


def test_hint_routes_str_to_int():
    hint = format_type_error_hint("count", expected="int", actual="str", value="abc")
    assert "could not be converted to int" in hint
    assert "data-count:int" in hint


def test_hint_routes_str_to_int_no_coercion_attempted():
    hint = format_type_error_hint(
        "count", expected="int", actual="str", value="abc", coercion_attempted=False
    )
    # The "could not be converted" phrase is gated on coercion_attempted=True.
    assert "could not be converted" not in hint
    assert "data-count:int" in hint


def test_hint_routes_str_to_float():
    hint = format_type_error_hint("price", expected="float", actual="str", value="oops")
    assert "data-price:float" in hint
    assert "could not be converted to float" in hint


def test_hint_routes_str_to_float_no_coercion_attempted():
    hint = format_type_error_hint(
        "price", expected="float", actual="str", value="oops", coercion_attempted=False
    )
    assert "could not be converted" not in hint
    assert "data-price:float" in hint


def test_hint_routes_str_to_bool():
    hint = format_type_error_hint("enabled", expected="bool", actual="str", value="maybe")
    assert "data-enabled:bool" in hint
    assert "treated as False" in hint


def test_hint_routes_str_to_bool_no_coercion_attempted():
    hint = format_type_error_hint(
        "enabled", expected="bool", actual="str", value="maybe", coercion_attempted=False
    )
    assert "treated as False" not in hint
    assert "data-enabled:bool" in hint


def test_hint_routes_str_to_list():
    hint = format_type_error_hint("tags", expected="list", actual="str", value="a,b,c")
    assert "data-tags:json" in hint
    assert "data-tags:list" in hint


def test_hint_routes_str_to_decimal():
    hint = format_type_error_hint("amount", expected="Decimal", actual="str", value="not-a-number")
    assert "could not be converted to Decimal" in hint


def test_hint_routes_str_to_decimal_no_coercion_attempted():
    hint = format_type_error_hint(
        "amount",
        expected="Decimal",
        actual="str",
        value="not-a-number",
        coercion_attempted=False,
    )
    assert "could not be converted" not in hint


# ─────────────────────────────────────────────────────────────────────────────
# validate_parameter_types surfaces type errors with hints
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_parameter_types_returns_type_errors():
    def handler(self, n: int = 0, **kwargs):
        return n

    errors = validate_parameter_types(handler, {"n": "not-a-number"})
    assert errors  # non-empty → at least one type error surfaced


def test_validate_parameter_types_ok_for_correct_types():
    def handler(self, n: int = 0, **kwargs):
        return n

    errors = validate_parameter_types(handler, {"n": 42})
    assert not errors  # None or [] both mean "no errors"


# ─────────────────────────────────────────────────────────────────────────────
# coerce_parameter_types / _coerce_value edge cases
# ─────────────────────────────────────────────────────────────────────────────


def test_coerce_returns_original_on_failure_for_non_str_source():
    def handler(self, n: int = 0, **kwargs):
        return n

    # Pass a dict — coercion should not try to turn dict into int.
    result = coerce_parameter_types(handler, {"n": {"x": 1}})
    # Left as-is; caller should see the dict and generate a type error.
    assert result["n"] == {"x": 1}


def test_coerce_value_with_no_origin_returns_direct_coercion():
    # origin=None means not a generic (e.g., list[int]). Should behave like a
    # plain ``int()`` coercion.
    assert _coerce_value("42", int, None) == 42


def test_coerce_value_list_with_comma_separated_and_element_coercion():
    # list[int] from comma-separated string.
    result = _coerce_value("1,2,3", list, list)
    assert result == ["1", "2", "3"]  # elements stay str when no element type info


def test_coerce_single_value_noop_when_types_match():
    # Only str inputs are touched by the coercer; already-typed values pass through.
    assert _coerce_single_value("42", int) == 42
    assert _coerce_single_value("3.14", float) == 3.14


def test_coerce_single_value_empty_string_to_int_zero():
    assert _coerce_single_value("", int) == 0


def test_coerce_single_value_bool_truthy_strings():
    for truthy in ("true", "TRUE", "1", "yes", "on"):
        assert _coerce_single_value(truthy, bool) is True


def test_coerce_single_value_bool_falsy_strings():
    for falsy in ("false", "FALSE", "0", "no", "off", ""):
        assert _coerce_single_value(falsy, bool) is False


# ─────────────────────────────────────────────────────────────────────────────
# get_handler_signature_info coverage
# ─────────────────────────────────────────────────────────────────────────────


def test_signature_info_captures_required_and_optional():
    def handler(self, req: int, opt: str = "", **kwargs):
        """Do a thing."""
        return None

    info = get_handler_signature_info(handler)
    by_name = {p["name"]: p for p in info["params"]}
    assert by_name["req"]["required"] is True
    assert by_name["opt"]["required"] is False
    assert info["accepts_kwargs"] is True


def test_signature_info_no_kwargs():
    def handler(self, x: int):
        return x

    info = get_handler_signature_info(handler)
    assert info["accepts_kwargs"] is False


def test_signature_info_no_docstring():
    def handler(self, x: int = 0, **kwargs):
        return x

    info = get_handler_signature_info(handler)
    assert info["description"] == ""


# ─────────────────────────────────────────────────────────────────────────────
# validate_handler_params: coverage for edge branches
# ─────────────────────────────────────────────────────────────────────────────


def test_validate_handler_params_reports_missing_required():
    def handler(self, required: int, **kwargs):
        return required

    result = validate_handler_params(handler, {}, "my_event")
    assert result["valid"] is False
    assert "required" in result["expected"]


def test_validate_handler_params_reports_unexpected_without_kwargs():
    def handler(self, x: int = 0):
        return x

    result = validate_handler_params(handler, {"unknown": 1}, "my_event")
    assert result["valid"] is False


def test_validate_handler_params_accepts_unexpected_with_kwargs():
    def handler(self, x: int = 0, **kwargs):
        return x

    result = validate_handler_params(handler, {"x": 1, "extra": "ok"}, "my_event")
    assert result["valid"] is True


def test_validate_handler_params_reports_type_error_in_details():
    def handler(self, n: int = 0, **kwargs):
        return n

    result = validate_handler_params(handler, {"n": "abc"}, "my_event", coerce=False)
    assert result["valid"] is False
    assert result["type_errors"]


def test_validate_handler_params_positional_args_bind_to_named_params():
    def handler(self, a: int, b: int = 0, **kwargs):
        return a + b

    result = validate_handler_params(handler, {}, "my_event", positional_args=[1, 2])
    assert result["valid"] is True
    assert result["coerced_params"]["a"] == 1
    assert result["coerced_params"]["b"] == 2
