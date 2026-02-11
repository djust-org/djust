"""Tests for normalize_django_value() in djust.serialization."""

from datetime import datetime, date, time, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from djust.serialization import normalize_django_value, DjangoJSONEncoder


# Override the autouse conftest fixture that requires Rust extension
@pytest.fixture(autouse=True)
def cleanup_session_cache():
    yield


class TestPrimitivePassthrough:
    """JSON-native primitives pass through unchanged."""

    def test_none(self):
        assert normalize_django_value(None) is None

    def test_true(self):
        assert normalize_django_value(True) is True

    def test_false(self):
        assert normalize_django_value(False) is False

    def test_int(self):
        assert normalize_django_value(42) == 42

    def test_float(self):
        result = normalize_django_value(3.14)
        assert result == 3.14
        assert isinstance(result, float)

    def test_string(self):
        assert normalize_django_value("hello") == "hello"

    def test_empty_string(self):
        assert normalize_django_value("") == ""

    def test_zero(self):
        assert normalize_django_value(0) == 0

    def test_negative_int(self):
        assert normalize_django_value(-5) == -5


class TestDecimal:
    """Decimal -> float."""

    def test_decimal_to_float(self):
        result = normalize_django_value(Decimal("3.14"))
        assert result == 3.14
        assert isinstance(result, float)

    def test_decimal_zero(self):
        result = normalize_django_value(Decimal("0"))
        assert result == 0.0
        assert isinstance(result, float)


class TestUUID:
    """UUID -> str."""

    def test_uuid_to_str(self):
        u = UUID("12345678-1234-5678-1234-567812345678")
        result = normalize_django_value(u)
        assert result == "12345678-1234-5678-1234-567812345678"
        assert isinstance(result, str)


class TestDateTimeTypes:
    """datetime, date, time -> isoformat strings."""

    def test_datetime_isoformat(self):
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = normalize_django_value(dt)
        assert result == "2024-01-15T10:30:00"
        assert isinstance(result, str)

    def test_date_isoformat(self):
        d = date(2024, 1, 15)
        result = normalize_django_value(d)
        assert result == "2024-01-15"
        assert isinstance(result, str)

    def test_time_isoformat(self):
        t = time(10, 30, 0)
        result = normalize_django_value(t)
        assert result == "10:30:00"
        assert isinstance(result, str)

    def test_timedelta_iso_string(self):
        td = timedelta(days=1, hours=2, minutes=30)
        result = normalize_django_value(td)
        # Django's duration_iso_string produces ISO-8601 format
        assert isinstance(result, str)
        assert "P" in result  # ISO-8601 duration starts with P


class TestDictRecursion:
    """dict values are recursed."""

    def test_simple_dict(self):
        result = normalize_django_value({"a": 1, "b": "hello"})
        assert result == {"a": 1, "b": "hello"}

    def test_dict_with_decimal(self):
        result = normalize_django_value({"price": Decimal("9.99")})
        assert result == {"price": 9.99}
        assert isinstance(result["price"], float)

    def test_dict_with_uuid(self):
        u = UUID("12345678-1234-5678-1234-567812345678")
        result = normalize_django_value({"id": u})
        assert result == {"id": "12345678-1234-5678-1234-567812345678"}

    def test_empty_dict(self):
        assert normalize_django_value({}) == {}


class TestListTupleRecursion:
    """list/tuple are recursed; tuple becomes list."""

    def test_list_passthrough(self):
        result = normalize_django_value([1, 2, 3])
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_tuple_becomes_list(self):
        result = normalize_django_value((1, 2, 3))
        assert result == [1, 2, 3]
        assert isinstance(result, list)

    def test_list_with_decimals(self):
        result = normalize_django_value([Decimal("1.5"), Decimal("2.5")])
        assert result == [1.5, 2.5]

    def test_empty_list(self):
        assert normalize_django_value([]) == []

    def test_empty_tuple(self):
        result = normalize_django_value(())
        assert result == []
        assert isinstance(result, list)


class TestNestedStructures:
    """Nested structures (dict containing list of dicts) are recursed correctly."""

    def test_dict_containing_list_of_dicts(self):
        value = {
            "items": [
                {"id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"), "price": Decimal("5.00")},
                {"id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"), "price": Decimal("10.00")},
            ]
        }
        result = normalize_django_value(value)
        assert result == {
            "items": [
                {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "price": 5.0},
                {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "price": 10.0},
            ]
        }

    def test_deeply_nested_dict(self):
        value = {"a": {"b": {"c": {"d": Decimal("1.0")}}}}
        result = normalize_django_value(value)
        assert result == {"a": {"b": {"c": {"d": 1.0}}}}


class TestMaxRecursionDepth:
    """Max recursion depth is respected for Django models."""

    def test_deeply_nested_plain_dict_does_not_crash(self):
        """Plain dicts recurse without depth limit (no model serialization)."""
        # Build a deeply nested dict (50 levels)
        value = "leaf"
        for i in range(50):
            value = {"level": value}
        # Should not crash -- plain dict recursion has no depth limit
        result = normalize_django_value(value)
        assert isinstance(result, dict)

    def test_depth_parameter_is_propagated(self):
        """Calling with _depth at max returns minimal model repr."""
        from django.db import models as dj_models

        # Create a mock model-like object
        class FakeModel(dj_models.Model):
            class Meta:
                app_label = "tests"

            def __str__(self):
                return "fake"

        obj = FakeModel.__new__(FakeModel)
        obj.pk = 99

        # _depth at or above max_depth should produce minimal output
        max_depth = DjangoJSONEncoder._get_max_depth()
        result = normalize_django_value(obj, _depth=max_depth)
        assert result == {"id": "99", "pk": 99, "__str__": "fake"}

    def test_depth_counter_is_reset_after_call(self):
        """DjangoJSONEncoder._depth is properly reset even after errors."""
        initial_depth = DjangoJSONEncoder._depth
        # Normalize a simple value
        normalize_django_value({"key": Decimal("1.0")})
        assert DjangoJSONEncoder._depth == initial_depth


class TestCallable:
    """callable -> None."""

    def test_function_returns_none(self):
        def my_func():
            return 42

        assert normalize_django_value(my_func) is None

    def test_lambda_returns_none(self):
        assert normalize_django_value(lambda: 42) is None

    def test_builtin_returns_none(self):
        # len is callable
        assert normalize_django_value(len) is None


class TestUnknownType:
    """Unknown types -> str()."""

    def test_custom_object_to_str(self):
        class MyCustom:
            def __str__(self):
                return "custom_value"

        result = normalize_django_value(MyCustom())
        assert result == "custom_value"
        assert isinstance(result, str)

    def test_bytes_to_str(self):
        # bytes is not JSON-native, falls through to str()
        result = normalize_django_value(b"hello")
        assert result == "b'hello'"
        assert isinstance(result, str)
