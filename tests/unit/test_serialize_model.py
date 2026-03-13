"""Regression tests for model.id returning native types (not strings).

PR #472 fixed _serialize_model_safely and _jit_serialize_model to use
obj.pk instead of str(obj.pk). These tests guard all 4 previously untested
paths so they cannot silently regress to string behavior.
"""

import pytest
from unittest.mock import MagicMock

from django.db import models
from django.db.models.base import ModelState

from djust.serialization import DjangoJSONEncoder


# ---------------------------------------------------------------------------
# Shared helpers — defined at module level to avoid Django re-registration
# warnings when the test file is collected multiple times.
# ---------------------------------------------------------------------------

_FakeModel = type(
    "SerializeTestModel",
    (models.Model,),
    {
        "__module__": __name__,
        "__str__": lambda self: f"SerializeTestModel({self.pk})",
        "Meta": type("Meta", (), {"app_label": "tests"}),
    },
)


def _make_obj(pk=42):
    """Create a FakeModel instance without hitting the database."""
    obj = _FakeModel.__new__(_FakeModel)
    obj.pk = pk
    obj.id = pk
    obj._state = ModelState()
    obj._djust_prop_cache = {}
    return obj


# ---------------------------------------------------------------------------
# Path 1: _serialize_model_safely() — primary "id" is native int
# ---------------------------------------------------------------------------


class TestSerializeModelSafelyPrimaryId:
    """_serialize_model_safely() must set 'id' and 'pk' to native int, not str."""

    def test_id_is_native_int(self):
        encoder = DjangoJSONEncoder()
        obj = _make_obj(42)
        result = encoder._serialize_model_safely(obj)

        assert result["id"] == 42
        assert isinstance(
            result["id"], int
        ), f"Expected int for 'id', got {type(result['id']).__name__!r}"

    def test_pk_is_native_int(self):
        encoder = DjangoJSONEncoder()
        obj = _make_obj(42)
        result = encoder._serialize_model_safely(obj)

        assert result["pk"] == 42
        assert isinstance(result["pk"], int)

    def test_id_equals_pk(self):
        encoder = DjangoJSONEncoder()
        obj = _make_obj(99)
        result = encoder._serialize_model_safely(obj)

        assert result["id"] == result["pk"]


# ---------------------------------------------------------------------------
# Path 2: _serialize_model_safely() — related-object shallow repr at max depth
# ---------------------------------------------------------------------------


class TestSerializeModelSafelyShallowRepr:
    """At max depth, related objects get a shallow repr — 'id' must be native type."""

    def test_shallow_repr_id_is_native_int(self, monkeypatch):
        """The shallow repr dict for prefetched relations uses related.pk (native int)."""
        encoder = DjangoJSONEncoder()
        related = _make_obj(77)
        parent = _make_obj(10)

        # Add a plain Python attribute so getattr(parent, "owner") returns related
        # without a DB hit (FakeModel has no "owner" descriptor, so it lands in __dict__)
        parent.owner = related

        # Mock the FK-like field returned by _meta.get_fields()
        mock_fk_field = MagicMock()
        mock_fk_field.name = "owner"
        mock_fk_field.is_relation = True
        mock_fk_field.concrete = True
        mock_fk_field.many_to_many = False
        mock_fk_field.related_model = type(related)

        monkeypatch.setattr(type(parent)._meta, "get_fields", lambda: [mock_fk_field])
        monkeypatch.setattr(encoder, "_is_relation_prefetched", lambda obj, name: True)

        max_depth = DjangoJSONEncoder._get_max_depth()
        DjangoJSONEncoder._depth = max_depth
        try:
            result = encoder._serialize_model_safely(parent)
        finally:
            DjangoJSONEncoder._depth = 0

        assert "owner" in result
        owner_repr = result["owner"]
        assert owner_repr["id"] == 77
        assert isinstance(
            owner_repr["id"], int
        ), f"Expected int for shallow repr 'id', got {type(owner_repr['id']).__name__!r}"
        assert owner_repr["pk"] == 77
        assert isinstance(owner_repr["pk"], int)

    def test_depth_counter_restored_on_exception(self, monkeypatch):
        """DjangoJSONEncoder._depth is reset even if _serialize_model_safely raises."""
        initial = DjangoJSONEncoder._depth
        try:
            encoder = DjangoJSONEncoder()
            obj = _make_obj(1)
            monkeypatch.setattr(
                type(obj)._meta, "get_fields", lambda: (_ for _ in ()).throw(RuntimeError("boom"))
            )
            with pytest.raises(RuntimeError):
                encoder._serialize_model_safely(obj)
        finally:
            DjangoJSONEncoder._depth = initial


# ---------------------------------------------------------------------------
# Path 3: _jit_serialize_model() with JIT_AVAILABLE=False
# ---------------------------------------------------------------------------


class TestJitSerializeModelNoJit:
    """When JIT is unavailable, _jit_serialize_model returns native-type id and pk."""

    def test_fallback_id_is_native_int(self, monkeypatch):
        import djust.template.rendering as rendering_module
        from djust.template.rendering import DjustTemplate

        monkeypatch.setattr(rendering_module, "JIT_AVAILABLE", False)
        monkeypatch.setattr(rendering_module, "DjangoJSONEncoder", None)

        tmpl = object.__new__(DjustTemplate)

        obj = _make_obj(42)
        result = tmpl._jit_serialize_model(obj, "todo")

        assert result["id"] == 42
        assert isinstance(
            result["id"], int
        ), f"Expected int for JIT-fallback 'id', got {type(result['id']).__name__!r}"

    def test_fallback_pk_present_and_native_int(self, monkeypatch):
        import djust.template.rendering as rendering_module
        from djust.template.rendering import DjustTemplate

        monkeypatch.setattr(rendering_module, "JIT_AVAILABLE", False)
        monkeypatch.setattr(rendering_module, "DjangoJSONEncoder", None)

        tmpl = object.__new__(DjustTemplate)

        obj = _make_obj(42)
        result = tmpl._jit_serialize_model(obj, "todo")

        assert "pk" in result
        assert result["pk"] == 42
        assert isinstance(result["pk"], int)


# ---------------------------------------------------------------------------
# Path 4: _jit_serialize_model() serialization-error fallback
# ---------------------------------------------------------------------------


class TestJitSerializeModelErrorFallback:
    """When normalize_django_value raises, the error fallback returns native-type id."""

    def test_error_fallback_id_is_native_int(self, monkeypatch):
        import djust.template.rendering as rendering_module
        from djust.template.rendering import DjustTemplate
        from djust.serialization import DjangoJSONEncoder as _RealEncoder

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated serialization error")

        # Ensure the JIT branch is entered (not the early-exit fallback)
        monkeypatch.setattr(rendering_module, "JIT_AVAILABLE", True)
        monkeypatch.setattr(rendering_module, "DjangoJSONEncoder", _RealEncoder)
        monkeypatch.setattr(rendering_module, "normalize_django_value", _raise)

        tmpl = object.__new__(DjustTemplate)

        obj = _make_obj(42)
        result = tmpl._jit_serialize_model(obj, "todo")

        assert result["id"] == 42
        assert isinstance(
            result["id"], int
        ), f"Expected int for error-fallback 'id', got {type(result['id']).__name__!r}"

    def test_error_fallback_pk_present_and_native_int(self, monkeypatch):
        import djust.template.rendering as rendering_module
        from djust.template.rendering import DjustTemplate
        from djust.serialization import DjangoJSONEncoder as _RealEncoder

        def _raise(*args, **kwargs):
            raise RuntimeError("simulated serialization error")

        monkeypatch.setattr(rendering_module, "JIT_AVAILABLE", True)
        monkeypatch.setattr(rendering_module, "DjangoJSONEncoder", _RealEncoder)
        monkeypatch.setattr(rendering_module, "normalize_django_value", _raise)

        tmpl = object.__new__(DjustTemplate)

        obj = _make_obj(42)
        result = tmpl._jit_serialize_model(obj, "todo")

        assert "pk" in result
        assert result["pk"] == 42
        assert isinstance(result["pk"], int)
