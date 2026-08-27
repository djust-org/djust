"""Tests for normalize_django_value() in djust.serialization."""

import json
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
    """Decimal -> the Decimal itself, exactly (#2239).

    The output goes into the template context, where the Rust renderer carries a
    Decimal as ``Value::Decimal`` and renders it exactly as Django does. A float
    would lose every digit past ~15 significant ones; a string would stop
    ``|floatformat`` rounding and make ``{% if p > 10 %}`` compare lexically.
    """

    def test_decimal_is_carried_through_unconverted(self):
        result = normalize_django_value(Decimal("3.14"))
        assert result == Decimal("3.14")
        assert isinstance(result, Decimal)

    def test_decimal_zero(self):
        result = normalize_django_value(Decimal("0"))
        assert result == Decimal("0")
        assert isinstance(result, Decimal)

    def test_a_decimal_past_float_precision_keeps_every_digit(self):
        """The bug #2239 names: 29 significant digits do not fit in a double."""
        huge = Decimal("12345678901234567890.123456789")
        assert normalize_django_value(huge) == huge
        assert str(normalize_django_value(huge)) == "12345678901234567890.123456789"

    def test_state_roundtrip_converts_to_float(self):
        """The one boundary that cannot take the Decimal — see
        ``decimal_for_state_roundtrip``. Django's session serializer runs
        ``json.dumps`` with no encoder, and a string restored into view state
        would be a string in the template."""
        result = normalize_django_value(Decimal("3.14"), state_roundtrip=True)
        assert result == 3.14
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
        assert result == {"price": Decimal("9.99")}
        assert isinstance(result["price"], Decimal)

    def test_dict_with_decimal_under_state_roundtrip(self):
        """The flag reaches nested values, not just the top-level one (#2239)."""
        result = normalize_django_value({"price": Decimal("9.99")}, state_roundtrip=True)
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
        assert result == [Decimal("1.5"), Decimal("2.5")]
        assert all(isinstance(x, Decimal) for x in result)

    def test_list_with_decimals_under_state_roundtrip(self):
        result = normalize_django_value([Decimal("1.5"), Decimal("2.5")], state_roundtrip=True)
        assert all(isinstance(x, float) for x in result)

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
        assert result == {"id": 99, "pk": 99, "__str__": "fake"}

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


class TestParityWithJSONRoundtrip:
    """``normalize_django_value`` is the encoder's PRE-PASS, so encoding its
    output must equal encoding the input:

        json.dumps(normalize_django_value(x), cls=Enc) == json.dumps(x, cls=Enc)

    The invariant used to be stated as raw equality —
    ``normalize_django_value(x) == json.loads(json.dumps(x, cls=Enc))`` — which
    held only while both converters flattened ``Decimal`` to the same float.
    #2239 split them ON PURPOSE, because they have different destinations: the
    normalizer feeds the template context (a ``Decimal`` renders exactly there),
    the encoder feeds the wire (where only the digit STRING is lossless). Raw
    equality can no longer hold, and asserting it would be asserting the bug.

    Composition is the stronger property anyway: it says the pre-pass is a
    no-op with respect to the encoded bytes, which is exactly the licence every
    caller relies on when it skips the ``json.loads(json.dumps(...))`` round
    trip. It also still covers ``Decimal``, which a documented exception would
    have carved out.

    timedelta and Promise are intentionally excluded -- they are enhancements
    that DjangoJSONEncoder does not support (would raise TypeError).
    """

    @staticmethod
    def _encoded(value):
        return json.loads(json.dumps(value, cls=DjangoJSONEncoder))

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            False,
            0,
            42,
            -7,
            3.14,
            "",
            "hello world",
            Decimal("9.99"),
            Decimal("0"),
            Decimal("-123.456"),
            UUID("12345678-1234-5678-1234-567812345678"),
            datetime(2024, 6, 15, 12, 30, 45),
            date(2024, 6, 15),
            time(8, 0, 0),
            time(23, 59, 59),
        ],
        ids=[
            "None",
            "True",
            "False",
            "zero",
            "int",
            "neg_int",
            "float",
            "empty_str",
            "str",
            "Decimal",
            "Decimal_zero",
            "Decimal_neg",
            "UUID",
            "datetime",
            "date",
            "time_morning",
            "time_night",
        ],
    )
    def test_scalar_parity(self, value):
        assert self._encoded(normalize_django_value(value)) == self._encoded(value)

    def test_dict_parity(self):
        value = {"name": "test", "price": Decimal("19.95"), "active": True}
        assert self._encoded(normalize_django_value(value)) == self._encoded(value)

    def test_list_parity(self):
        value = [Decimal("1.1"), UUID("abcdefab-cdef-abcd-efab-cdefabcdefab"), 42]
        assert self._encoded(normalize_django_value(value)) == self._encoded(value)

    def test_nested_structure_parity(self):
        value = {
            "items": [
                {
                    "id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                    "price": Decimal("5.00"),
                    "created": datetime(2024, 1, 1, 0, 0, 0),
                },
                {
                    "id": UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
                    "price": Decimal("10.00"),
                    "created": date(2024, 6, 15),
                },
            ],
            "count": 2,
            "label": "batch",
        }
        assert self._encoded(normalize_django_value(value)) == self._encoded(value)

    def test_tuple_becomes_list_parity(self):
        """tuple -> list matches JSON roundtrip (JSON has no tuple type)."""
        value = (Decimal("1.0"), "a", 3)
        assert self._encoded(normalize_django_value(value)) == self._encoded(value)

    def test_the_composition_would_catch_a_divergence(self):
        """Gate-off for the invariant itself (#1468).

        If the two converters could disagree about the encoded bytes and this
        class still passed, it would be pinning nothing. Feed the comparison a
        DIFFERENT value and it must fail.
        """
        assert self._encoded(normalize_django_value(Decimal("1.1"))) != self._encoded(
            Decimal("1.2")
        )


class TestStrictSerializationMode:
    """Test strict_serialization config behavior."""

    def test_non_serializable_logs_warning_by_default(self, caplog):
        """Non-serializable objects emit warning in default mode."""
        import logging
        from djust.config import config

        class NonSerializable:
            def __str__(self):
                return "NonSerializable instance"

        # Default mode (strict_serialization=False)
        assert config.get("strict_serialization", False) is False

        with caplog.at_level(logging.WARNING):
            result = normalize_django_value(NonSerializable())

        assert isinstance(result, str)
        assert any("non-serializable value" in rec.message.lower() for rec in caplog.records)

    def test_strict_mode_raises_type_error(self):
        """In strict mode, non-serializable objects raise TypeError."""
        from djust.config import config

        class NonSerializable:
            def __str__(self):
                return "NonSerializable instance"

        # Enable strict mode
        config.set("strict_serialization", True)

        try:
            with pytest.raises(TypeError) as exc_info:
                normalize_django_value(NonSerializable())

            assert "non-serializable value" in str(exc_info.value).lower()
            assert "NonSerializable" in str(exc_info.value)
        finally:
            # Reset config
            config.set("strict_serialization", False)

    def test_strict_mode_message_includes_guidance(self):
        """Error message includes helpful guidance."""
        from djust.config import config

        class ServiceClient:
            def __str__(self):
                return "ServiceClient instance"

        config.set("strict_serialization", True)

        try:
            with pytest.raises(TypeError) as exc_info:
                normalize_django_value(ServiceClient())

            msg = str(exc_info.value)
            assert "ServiceClient" in msg
            assert "self._" in msg or "re-initialize" in msg
        finally:
            config.set("strict_serialization", False)
