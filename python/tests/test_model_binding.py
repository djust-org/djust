"""Tests for ModelBindingMixin (dj-model server-side support)."""

import os
import importlib.util

# Direct import to avoid pulling in Django/channels via djust.__init__
_spec = importlib.util.spec_from_file_location(
    "model_binding",
    os.path.join(os.path.dirname(__file__), "..", "djust", "mixins", "model_binding.py"),
)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
ModelBindingMixin = _mod.ModelBindingMixin
FORBIDDEN_MODEL_FIELDS = _mod.FORBIDDEN_MODEL_FIELDS


class FakeView(ModelBindingMixin):
    """Minimal view stub for testing."""

    search_query = ""
    count = 0
    price = 9.99
    active = True
    _private = "secret"
    template_name = "test.html"


class RestrictedView(ModelBindingMixin):
    """View with allowed_model_fields set."""

    name = ""
    email = ""
    role = "user"
    allowed_model_fields = {"name", "email"}


class TestModelBindingMixin:
    def setup_method(self):
        self.view = FakeView()

    def test_update_string_field(self):
        self.view.update_model(field="search_query", value="hello")
        assert self.view.search_query == "hello"

    def test_update_int_field_with_coercion(self):
        self.view.update_model(field="count", value="42")
        assert self.view.count == 42

    def test_update_float_field_with_coercion(self):
        self.view.update_model(field="price", value="19.99")
        assert self.view.price == 19.99

    def test_update_bool_field_with_coercion(self):
        self.view.update_model(field="active", value="false")
        assert self.view.active is False

    def test_update_bool_field_true(self):
        self.view.active = False
        self.view.update_model(field="active", value="true")
        assert self.view.active is True

    def test_block_private_field(self):
        self.view.update_model(field="_private", value="hacked")
        assert self.view._private == "secret"

    def test_block_forbidden_field(self):
        self.view.update_model(field="template_name", value="evil.html")
        assert self.view.template_name == "test.html"

    def test_block_nonexistent_field(self):
        self.view.update_model(field="nonexistent", value="x")
        assert not hasattr(self.view, "nonexistent")

    def test_empty_field_name(self):
        # Should not raise
        self.view.update_model(field="", value="x")

    def test_none_field_name(self):
        self.view.update_model(field=None, value="x")

    def test_invalid_coercion(self):
        self.view.update_model(field="count", value="not_a_number")
        assert self.view.count == 0  # Unchanged

    def test_allowed_model_fields_permits(self):
        view = RestrictedView()
        view.update_model(field="name", value="John")
        assert view.name == "John"

    def test_allowed_model_fields_blocks(self):
        view = RestrictedView()
        view.update_model(field="role", value="admin")
        assert view.role == "user"  # Unchanged

    def test_forbidden_fields_comprehensive(self):
        """All forbidden fields should be blocked."""
        for field in FORBIDDEN_MODEL_FIELDS:
            view = FakeView()
            if hasattr(view, field):
                original = getattr(view, field)
                view.update_model(field=field, value="hacked")
                assert getattr(view, field) == original
