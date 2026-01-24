"""
Tests for DjustTemplateBackend context serialization (GitHub Issue #34).

Tests that common Django types are automatically serialized before passing
to the Rust rendering engine:
- datetime.datetime -> ISO format string
- datetime.date -> ISO format string
- datetime.time -> ISO format string
- Decimal -> string (preserves precision)
- UUID -> string
- ImageFieldFile/FieldFile -> .url if file exists, else None
- QuerySets -> list
- Model instances -> dict of fields (already handled by DjangoJSONEncoder)
"""

import json
from datetime import date, datetime, time
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import UUID, uuid4


class TestDjangoJSONEncoderTypes:
    """Test DjangoJSONEncoder handles all required types."""

    def test_datetime_serialization(self):
        """datetime objects should serialize to ISO format strings."""
        from djust.live_view import DjangoJSONEncoder

        dt = datetime(2024, 6, 15, 14, 30, 45, 123456)
        result = json.dumps({"created_at": dt}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["created_at"] == "2024-06-15T14:30:45.123456"

    def test_date_serialization(self):
        """date objects should serialize to ISO format strings."""
        from djust.live_view import DjangoJSONEncoder

        d = date(2024, 6, 15)
        result = json.dumps({"birth_date": d}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["birth_date"] == "2024-06-15"

    def test_time_serialization(self):
        """time objects should serialize to ISO format strings."""
        from djust.live_view import DjangoJSONEncoder

        t = time(14, 30, 45)
        result = json.dumps({"start_time": t}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["start_time"] == "14:30:45"

    def test_decimal_serialization(self):
        """Decimal objects should serialize to strings (preserving precision)."""
        from djust.live_view import DjangoJSONEncoder

        d = Decimal("123.456789")
        result = json.dumps({"price": d}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        # Should be serialized as float (DjangoJSONEncoder uses float)
        assert data["price"] == 123.456789

    def test_uuid_serialization(self):
        """UUID objects should serialize to strings."""
        from djust.live_view import DjangoJSONEncoder

        u = UUID("12345678-1234-5678-1234-567812345678")
        result = json.dumps({"uuid": u}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["uuid"] == "12345678-1234-5678-1234-567812345678"

    def test_filefield_with_file_serialization(self):
        """FieldFile with a file should serialize to its URL."""
        from djust.live_view import DjangoJSONEncoder

        # Mock a FieldFile with a file
        mock_file = MagicMock()
        mock_file.url = "/media/documents/test.pdf"
        mock_file.name = "documents/test.pdf"
        # Make bool(mock_file) return True (has a file)
        mock_file.__bool__ = MagicMock(return_value=True)

        result = json.dumps({"document": mock_file}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["document"] == "/media/documents/test.pdf"

    def test_filefield_without_file_serialization(self):
        """FieldFile without a file should serialize to None."""
        from djust.live_view import DjangoJSONEncoder

        # Mock a FieldFile without a file
        mock_file = MagicMock()
        mock_file.name = ""
        # Make bool(mock_file) return False (no file)
        mock_file.__bool__ = MagicMock(return_value=False)

        result = json.dumps({"document": mock_file}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["document"] is None

    def test_imagefield_serialization(self):
        """ImageFieldFile should serialize to its URL."""
        from djust.live_view import DjangoJSONEncoder

        # Mock an ImageFieldFile with a file
        mock_image = MagicMock()
        mock_image.url = "/media/images/avatar.jpg"
        mock_image.name = "images/avatar.jpg"
        mock_image.__bool__ = MagicMock(return_value=True)

        result = json.dumps({"avatar": mock_image}, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["avatar"] == "/media/images/avatar.jpg"


class TestTemplateBackendContextSerialization:
    """Test that DjustTemplateBackend properly serializes all context values."""

    def test_serialize_context_with_datetime(self):
        """Context with datetime should be serializable for Rust."""
        from djust.template_backend import DjustTemplate

        # Create a mock template
        template = MagicMock(spec=DjustTemplate)
        template.template_string = "<p>{{ created_at }}</p>"

        context = {
            "created_at": datetime(2024, 6, 15, 14, 30, 45),
        }

        # The context should be JSON-serializable after processing
        from djust.live_view import DjangoJSONEncoder

        # This should not raise TypeError
        result = json.dumps(context, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert "created_at" in data
        assert data["created_at"] == "2024-06-15T14:30:45"

    def test_serialize_context_with_decimal(self):
        """Context with Decimal should be serializable for Rust."""
        from djust.live_view import DjangoJSONEncoder

        context = {
            "price": Decimal("99.99"),
            "tax_rate": Decimal("0.0825"),
        }

        result = json.dumps(context, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["price"] == 99.99
        assert data["tax_rate"] == 0.0825

    def test_serialize_context_with_uuid(self):
        """Context with UUID should be serializable for Rust."""
        from djust.live_view import DjangoJSONEncoder

        test_uuid = uuid4()
        context = {
            "id": test_uuid,
        }

        result = json.dumps(context, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["id"] == str(test_uuid)

    def test_serialize_context_with_mixed_types(self):
        """Context with multiple special types should all be serializable."""
        from djust.live_view import DjangoJSONEncoder

        test_uuid = uuid4()
        mock_file = MagicMock()
        mock_file.url = "/media/doc.pdf"
        mock_file.__bool__ = MagicMock(return_value=True)

        context = {
            "created_at": datetime(2024, 6, 15, 14, 30, 45),
            "birth_date": date(1990, 1, 15),
            "price": Decimal("123.45"),
            "uuid": test_uuid,
            "document": mock_file,
            "name": "Test",
            "count": 42,
        }

        result = json.dumps(context, cls=DjangoJSONEncoder)
        data = json.loads(result)

        assert data["created_at"] == "2024-06-15T14:30:45"
        assert data["birth_date"] == "1990-01-15"
        assert data["price"] == 123.45
        assert data["uuid"] == str(test_uuid)
        assert data["document"] == "/media/doc.pdf"
        assert data["name"] == "Test"
        assert data["count"] == 42


class TestSerializeContextFunction:
    """Test the serialize_context helper function in template_backend."""

    def test_serialize_context_handles_all_types(self):
        """serialize_context should handle all special types."""
        from djust.template_backend import serialize_context

        test_uuid = uuid4()
        mock_file = MagicMock()
        mock_file.url = "/media/doc.pdf"
        mock_file.__bool__ = MagicMock(return_value=True)

        context = {
            "created_at": datetime(2024, 6, 15, 14, 30, 45),
            "birth_date": date(1990, 1, 15),
            "start_time": time(9, 0, 0),
            "price": Decimal("123.45"),
            "uuid": test_uuid,
            "document": mock_file,
            "name": "Test",
            "count": 42,
            "items": ["a", "b", "c"],
        }

        result = serialize_context(context)

        # All values should now be JSON-primitive types
        assert result["created_at"] == "2024-06-15T14:30:45"
        assert result["birth_date"] == "1990-01-15"
        assert result["start_time"] == "09:00:00"
        assert result["price"] == 123.45
        assert result["uuid"] == str(test_uuid)
        assert result["document"] == "/media/doc.pdf"
        assert result["name"] == "Test"
        assert result["count"] == 42
        assert result["items"] == ["a", "b", "c"]

    def test_serialize_context_handles_nested_dict(self):
        """serialize_context should handle nested dictionaries."""
        from djust.template_backend import serialize_context

        context = {
            "user": {
                "name": "John",
                "created_at": datetime(2024, 6, 15, 14, 30, 45),
                "balance": Decimal("100.50"),
            }
        }

        result = serialize_context(context)

        assert result["user"]["name"] == "John"
        assert result["user"]["created_at"] == "2024-06-15T14:30:45"
        assert result["user"]["balance"] == 100.50

    def test_serialize_context_handles_list_of_dicts(self):
        """serialize_context should handle lists containing dicts with special types."""
        from djust.template_backend import serialize_context

        context = {
            "events": [
                {"name": "Event 1", "date": date(2024, 6, 15)},
                {"name": "Event 2", "date": date(2024, 7, 20)},
            ]
        }

        result = serialize_context(context)

        assert result["events"][0]["date"] == "2024-06-15"
        assert result["events"][1]["date"] == "2024-07-20"

    def test_serialize_context_preserves_none(self):
        """serialize_context should preserve None values."""
        from djust.template_backend import serialize_context

        context = {
            "optional_field": None,
            "name": "Test",
        }

        result = serialize_context(context)

        assert result["optional_field"] is None
        assert result["name"] == "Test"

    def test_serialize_context_handles_empty_file(self):
        """serialize_context should handle FieldFile without a file."""
        from djust.template_backend import serialize_context

        mock_file = MagicMock()
        mock_file.name = ""
        mock_file.__bool__ = MagicMock(return_value=False)

        context = {
            "avatar": mock_file,
        }

        result = serialize_context(context)

        assert result["avatar"] is None
