"""
Regression tests for serialization hardening (#628, #626, #612).

- #628: form.cleaned_data Python types (date, Decimal, UUID) serialized to null
- #626: set() not JSON-serializable as public state
- #612: dict state deserialized as list after Rust state sync
"""

import json
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

import pytest

from djust.serialization import DjangoJSONEncoder, normalize_django_value


class TestFormCleanedDataTypes:
    """#628: form.cleaned_data Python types must not serialize to null."""

    def test_date_serializes_to_iso_string(self):
        """datetime.date should serialize to ISO format string, not null."""
        d = date(2024, 1, 15)
        result = json.loads(json.dumps(d, cls=DjangoJSONEncoder))
        assert result == "2024-01-15"

    def test_datetime_serializes_to_iso_string(self):
        """datetime.datetime should serialize to ISO format string."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        result = json.loads(json.dumps(dt, cls=DjangoJSONEncoder))
        assert result == "2024-01-15T10:30:00"

    def test_time_serializes_to_iso_string(self):
        """datetime.time should serialize to ISO format string."""
        t = time(14, 30, 45)
        result = json.loads(json.dumps(t, cls=DjangoJSONEncoder))
        assert result == "14:30:45"

    def test_decimal_serializes_to_number(self):
        """Decimal should serialize to a float, not null."""
        d = Decimal("3.14")
        result = json.loads(json.dumps(d, cls=DjangoJSONEncoder))
        assert result == pytest.approx(3.14)

    def test_decimal_zero(self):
        """Decimal('0') should serialize to 0.0, not null."""
        d = Decimal("0")
        result = json.loads(json.dumps(d, cls=DjangoJSONEncoder))
        assert result == 0.0

    def test_uuid_serializes_to_string(self):
        """UUID should serialize to its string representation, not null."""
        u = UUID("12345678-1234-5678-1234-567812345678")
        result = json.loads(json.dumps(u, cls=DjangoJSONEncoder))
        assert result == "12345678-1234-5678-1234-567812345678"

    def test_cleaned_data_dict_with_mixed_types(self):
        """Simulate form.cleaned_data with various Python types."""
        cleaned_data = {
            "birth_date": date(1990, 5, 20),
            "amount": Decimal("99.99"),
            "user_id": UUID("abcdef01-2345-6789-abcd-ef0123456789"),
            "name": "Alice",
            "count": 42,
        }
        result = json.loads(json.dumps(cleaned_data, cls=DjangoJSONEncoder))
        assert result["birth_date"] == "1990-05-20"
        assert result["amount"] == pytest.approx(99.99)
        assert result["user_id"] == "abcdef01-2345-6789-abcd-ef0123456789"
        assert result["name"] == "Alice"
        assert result["count"] == 42

    def test_normalize_date(self):
        """normalize_django_value should handle date."""
        assert normalize_django_value(date(2024, 1, 15)) == "2024-01-15"

    def test_normalize_decimal(self):
        """normalize_django_value should handle Decimal."""
        assert normalize_django_value(Decimal("3.14")) == pytest.approx(3.14)

    def test_normalize_uuid(self):
        """normalize_django_value should handle UUID."""
        u = UUID("12345678-1234-5678-1234-567812345678")
        assert normalize_django_value(u) == "12345678-1234-5678-1234-567812345678"


class TestSetSerialization:
    """#626: set() must be JSON-serializable as public state."""

    def test_set_serializes_to_sorted_list(self):
        """set should serialize to a sorted list, not crash."""
        s = {3, 1, 2}
        result = json.loads(json.dumps(s, cls=DjangoJSONEncoder))
        assert result == [1, 2, 3]

    def test_frozenset_serializes_to_sorted_list(self):
        """frozenset should serialize to a sorted list."""
        fs = frozenset({3, 1, 2})
        result = json.loads(json.dumps(fs, cls=DjangoJSONEncoder))
        assert result == [1, 2, 3]

    def test_empty_set_serializes_to_empty_list(self):
        """Empty set should serialize to empty list."""
        result = json.loads(json.dumps(set(), cls=DjangoJSONEncoder))
        assert result == []

    def test_set_of_strings(self):
        """Set of strings should serialize to sorted list."""
        s = {"banana", "apple", "cherry"}
        result = json.loads(json.dumps(s, cls=DjangoJSONEncoder))
        assert result == ["apple", "banana", "cherry"]

    def test_set_in_dict(self):
        """set nested in a dict should serialize correctly."""
        data = {"selected_ids": {10, 20, 30}, "name": "test"}
        result = json.loads(json.dumps(data, cls=DjangoJSONEncoder))
        assert result["selected_ids"] == [10, 20, 30]
        assert result["name"] == "test"

    def test_normalize_set(self):
        """normalize_django_value should handle set → sorted list."""
        assert normalize_django_value({3, 1, 2}) == [1, 2, 3]

    def test_normalize_frozenset(self):
        """normalize_django_value should handle frozenset → sorted list."""
        assert normalize_django_value(frozenset({3, 1, 2})) == [1, 2, 3]

    def test_normalize_empty_set(self):
        """normalize_django_value should handle empty set."""
        assert normalize_django_value(set()) == []

    def test_normalize_set_with_unsortable_elements(self):
        """set with mixed types that can't be sorted should still serialize."""
        # This tests the fallback to list() when sorted() fails
        # We can't easily create a set with unsortable elements that's also
        # deterministic, so test that the result is a list with same elements
        s = {1, "a"}  # int and str can't be sorted together in Python 3
        result = normalize_django_value(s)
        assert isinstance(result, list)
        assert set(result) == {1, "a"}

    def test_set_with_unsortable_elements_encoder(self):
        """DjangoJSONEncoder should handle sets with unsortable elements."""
        s = {1, "a"}
        result = json.loads(json.dumps(s, cls=DjangoJSONEncoder))
        assert isinstance(result, list)
        assert set(result) == {1, "a"}

    def test_normalize_nested_set_in_dict(self):
        """normalize_django_value should handle set nested in dict."""
        data = {"tags": {"python", "django"}, "count": 2}
        result = normalize_django_value(data)
        assert result["tags"] == ["django", "python"]
        assert result["count"] == 2


def _rust_available():
    """Check if Rust extensions are available."""
    try:
        from djust._rust import RustLiveView  # noqa: F401

        return True
    except ImportError:
        return False


class TestDictRoundTrip:
    """#612: dict state must survive round-trip through Rust state sync."""

    def test_dict_survives_json_roundtrip(self):
        """Dict should survive JSON serialization round-trip."""
        original = {"key1": "value1", "key2": 42, "nested": {"a": True}}
        serialized = json.dumps(original, cls=DjangoJSONEncoder)
        result = json.loads(serialized)
        assert isinstance(result, dict)
        assert result == original

    def test_normalize_preserves_dict(self):
        """normalize_django_value should preserve dict type."""
        original = {"key1": "value1", "key2": 42}
        result = normalize_django_value(original)
        assert isinstance(result, dict)
        assert result == original

    def test_normalize_preserves_nested_dict(self):
        """normalize_django_value should preserve nested dicts."""
        original = {"outer": {"inner": {"deep": "value"}}}
        result = normalize_django_value(original)
        assert isinstance(result, dict)
        assert isinstance(result["outer"], dict)
        assert isinstance(result["outer"]["inner"], dict)
        assert result["outer"]["inner"]["deep"] == "value"

    def test_normalize_empty_dict(self):
        """Empty dict should remain an empty dict."""
        assert normalize_django_value({}) == {}

    def test_normalize_dict_with_complex_values(self):
        """Dict with various value types should normalize correctly."""
        original = {
            "name": "test",
            "count": 0,
            "active": True,
            "tags": ["a", "b"],
            "meta": {"created": date(2024, 1, 1)},
        }
        result = normalize_django_value(original)
        assert isinstance(result, dict)
        assert result["name"] == "test"
        assert result["count"] == 0
        assert result["active"] is True
        assert result["tags"] == ["a", "b"]
        assert isinstance(result["meta"], dict)
        assert result["meta"]["created"] == "2024-01-01"

    @pytest.mark.skipif(
        not _rust_available(),
        reason="Rust extensions not built",
    )
    def test_dict_survives_rust_msgpack_roundtrip(self):
        """Dict must survive MessagePack round-trip through Rust (#612).

        This is the core regression test: serialize a dict-containing state
        to msgpack via Rust, deserialize it back, and verify dicts are still
        dicts (not lists).
        """
        from djust._rust import RustLiveView

        view = RustLiveView("<div>{{ data.name }}</div>", [])
        state = {
            "data": {"name": "test", "count": 42, "nested": {"a": True}},
            "items": [1, 2, 3],
            "simple": "hello",
        }
        view.update_state(state)

        # Serialize to msgpack and back
        msgpack_bytes = view.serialize_msgpack()
        restored = RustLiveView.deserialize_msgpack(msgpack_bytes)

        # Get the state back — render and check it didn't corrupt
        restored.set_template_dirs([])
        restored.update_state({})  # no-op update to access existing state
        # The real test: render with the restored state and verify output
        html = restored.render()
        assert "test" in html
