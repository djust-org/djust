"""
Tests for LiveForm — standalone form validation system.
"""

import pytest
import sys
import os

# Add parent to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import forms module directly to avoid Django dependency chain
import importlib.util
_forms_path = os.path.join(os.path.dirname(__file__), "..", "djust", "forms.py")

# Mock django modules that forms.py imports at top level
from unittest.mock import MagicMock
for mod in ["django", "django.forms", "django.core", "django.core.exceptions", "django.db", "django.db.models"]:
    if mod not in sys.modules:
        sys.modules[mod] = MagicMock()

spec = importlib.util.spec_from_file_location("djust_forms", _forms_path)
djust_forms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(djust_forms)

LiveForm = djust_forms.LiveForm
_BUILTIN_VALIDATORS = djust_forms._BUILTIN_VALIDATORS


# =============================================================================
# Basic construction
# =============================================================================


class TestLiveFormConstruction:
    def test_empty_form(self):
        form = LiveForm({})
        assert form.data == {}
        assert form.errors == {}
        assert form.valid is True

    def test_fields_initialised_to_empty_string(self):
        form = LiveForm({"name": {"required": True}, "email": {}})
        assert form.data == {"name": "", "email": ""}

    def test_initial_values(self):
        form = LiveForm({"name": {}}, initial={"name": "Alice"})
        assert form.data["name"] == "Alice"

    def test_repr(self):
        form = LiveForm({"x": {}})
        assert "LiveForm" in repr(form)


# =============================================================================
# Built-in validators
# =============================================================================


class TestBuiltinValidators:
    def test_required_empty(self):
        form = LiveForm({"name": {"required": True}})
        err = form.validate_field("name")
        assert err is not None
        assert "required" in err.lower()

    def test_required_whitespace(self):
        form = LiveForm({"name": {"required": True}})
        form.set_values({"name": "   "})
        assert form.validate_field("name") is not None

    def test_required_valid(self):
        form = LiveForm({"name": {"required": True}})
        assert form.validate_field("name", "Alice") is None

    def test_min_length(self):
        form = LiveForm({"name": {"min_length": 3}})
        assert form.validate_field("name", "ab") is not None
        assert form.validate_field("name", "abc") is None

    def test_max_length(self):
        form = LiveForm({"bio": {"max_length": 5}})
        assert form.validate_field("bio", "hello!") is not None
        assert form.validate_field("bio", "hello") is None

    def test_pattern(self):
        form = LiveForm({"code": {"pattern": r"^\d{3}$"}})
        assert form.validate_field("code", "12") is not None
        assert form.validate_field("code", "123") is None

    def test_email(self):
        form = LiveForm({"email": {"email": True}})
        assert form.validate_field("email", "bad") is not None
        assert form.validate_field("email", "a@b.com") is None

    def test_url(self):
        form = LiveForm({"site": {"url": True}})
        assert form.validate_field("site", "not-a-url") is not None
        assert form.validate_field("site", "https://example.com") is None

    def test_min(self):
        form = LiveForm({"age": {"min": 18}})
        assert form.validate_field("age", "10") is not None
        assert form.validate_field("age", "18") is None

    def test_max(self):
        form = LiveForm({"score": {"max": 100}})
        assert form.validate_field("score", "101") is not None
        assert form.validate_field("score", "100") is None

    def test_choices(self):
        form = LiveForm({"role": {"choices": ["admin", "user"]}})
        assert form.validate_field("role", "hacker") is not None
        assert form.validate_field("role", "admin") is None

    def test_email_empty_not_required(self):
        """Non-required email field should accept empty value."""
        form = LiveForm({"email": {"email": True}})
        assert form.validate_field("email", "") is None


# =============================================================================
# Custom validators
# =============================================================================


class TestCustomValidators:
    def test_custom_validator(self):
        form = LiveForm({
            "name": {
                "validators": [lambda v: "No spam" if v and "spam" in v.lower() else None]
            }
        })
        assert form.validate_field("name", "Buy spam now") is not None
        assert "spam" in form.errors["name"].lower()
        assert form.validate_field("name", "Hello") is None

    def test_multiple_custom_validators(self):
        form = LiveForm({
            "val": {
                "validators": [
                    lambda v: "too short" if v and len(v) < 2 else None,
                    lambda v: "no digits" if v and v.isdigit() else None,
                ]
            }
        })
        assert form.validate_field("val", "a") is not None
        assert "short" in form.errors["val"]


# =============================================================================
# Form-level operations
# =============================================================================


class TestFormOperations:
    def test_validate_all_valid(self):
        form = LiveForm({
            "name": {"required": True},
            "email": {"email": True},
        }, initial={"name": "Alice", "email": "a@b.com"})
        assert form.validate_all() is True
        assert form.valid is True

    def test_validate_all_invalid(self):
        form = LiveForm({
            "name": {"required": True},
            "email": {"required": True, "email": True},
        })
        assert form.validate_all() is False
        assert "name" in form.errors
        assert "email" in form.errors

    def test_reset(self):
        form = LiveForm({"name": {"required": True}}, initial={"name": "Alice"})
        form.validate_field("name", "")
        assert form.errors
        form.reset()
        assert form.errors == {}
        assert form.data["name"] == ""

    def test_set_values(self):
        form = LiveForm({"a": {}, "b": {}})
        form.set_values({"a": "1", "b": "2"})
        assert form.data == {"a": "1", "b": "2"}

    def test_valid_property_checks_required(self):
        """valid should be False if required fields haven't been filled, even without errors."""
        form = LiveForm({"name": {"required": True}})
        assert form.valid is False

    def test_valid_after_filling_required(self):
        form = LiveForm({"name": {"required": True}})
        form.validate_field("name", "Alice")
        assert form.valid is True

    def test_unknown_field_validate(self):
        form = LiveForm({"name": {}})
        assert form.validate_field("nonexistent", "x") is None

    def test_data_returns_copy(self):
        form = LiveForm({"a": {}})
        d = form.data
        d["a"] = "mutated"
        assert form.data["a"] == ""


# =============================================================================
# Combined rules
# =============================================================================


class TestCombinedRules:
    def test_required_and_min_length(self):
        form = LiveForm({"name": {"required": True, "min_length": 3}})
        # Empty triggers required
        err = form.validate_field("name", "")
        assert "required" in err.lower()
        # Short triggers min_length
        err = form.validate_field("name", "ab")
        assert "at least" in err.lower()
        # Valid
        assert form.validate_field("name", "abc") is None

    def test_email_and_required(self):
        form = LiveForm({"email": {"required": True, "email": True}})
        assert form.validate_field("email", "") is not None
        assert form.validate_field("email", "bad") is not None
        assert form.validate_field("email", "a@b.com") is None


# =============================================================================
# Integration: LiveForm with LiveView pattern
# =============================================================================


class TestLiveFormIntegration:
    """Simulate the LiveView usage pattern."""

    def test_contact_form_workflow(self):
        form = LiveForm({
            "name": {"required": True, "min_length": 2},
            "email": {"required": True, "email": True},
            "message": {"required": True, "min_length": 10, "max_length": 500},
        })

        # User fills name
        form.validate_field("name", "Al")
        assert form.errors == {}

        # User fills bad email
        form.validate_field("email", "not-email")
        assert "email" in form.errors

        # User fixes email
        form.validate_field("email", "al@example.com")
        assert "email" not in form.errors

        # User fills short message
        form.validate_field("message", "Hi")
        assert "message" in form.errors

        # User fills good message
        form.validate_field("message", "Hello, this is a proper message.")
        assert form.errors == {}
        assert form.valid is True

        # Submit
        assert form.validate_all() is True
        assert form.data == {
            "name": "Al",
            "email": "al@example.com",
            "message": "Hello, this is a proper message.",
        }

        # Reset
        form.reset()
        assert form.valid is False
        assert form.data == {"name": "", "email": "", "message": ""}
