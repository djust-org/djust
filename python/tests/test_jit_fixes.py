"""
Tests for JIT serialization fixes (PR #140).

Covers:
1. Codegen .all() with subtree generates list iteration
2. Deep dict serialization
3. @property in fallback serialization
4. Model before duck-typing in encoder
5. Rust→Python fallback when Rust returns incomplete keys
"""

import json
from unittest.mock import MagicMock, patch

from django.db import models as _django_models

from djust.optimization.codegen import (
    _build_path_tree,
    generate_serializer_code,
    compile_serializer,
)
from djust.serialization import DjangoJSONEncoder


# Create a single mock Model class at module level to avoid re-registration warnings.
# Django's app registry warns when the same (app_label, model_name) pair is registered twice.
_MockModelClass = type(
    "MockModel",
    (_django_models.Model,),
    {"__module__": "test", "Meta": type("Meta", (), {"app_label": "test"})},
)


# --- Fix 1: Codegen .all() with subtree generates list iteration ---


class TestCodegenAllWithSubtree:
    def test_path_tree_with_all_subtree(self):
        """_build_path_tree keeps 'all' as intermediate node with children."""
        tree = _build_path_tree(["tags.all.name", "tags.all.url"])
        assert tree == {"tags": {"all": {"name": {}, "url": {}}}}

    def test_codegen_all_with_subtree_generates_loop(self):
        """Generated code for tags.all.name should contain a for loop."""
        code = generate_serializer_code("Post", ["tags.all.name", "tags.all.url"])
        assert "for" in code
        assert ".all()" in code

    def test_compiled_all_serializer_iterates_m2m(self):
        """Compiled serializer iterates .all() and extracts nested attrs."""

        class Tag:
            def __init__(self, name, url):
                self.name = name
                self.url = url

        class TagManager:
            def __init__(self, tags):
                self._tags = tags

            def all(self):
                return self._tags

        class Post:
            def __init__(self):
                self.tags = TagManager([Tag("python", "/t/python"), Tag("django", "/t/django")])

        code = generate_serializer_code("Post", ["tags.all.name", "tags.all.url"])
        func_name = code.split("def ")[1].split("(")[0]
        func = compile_serializer(code, func_name)

        result = func(Post())
        assert "tags" in result
        assert "all" in result["tags"]
        items = result["tags"]["all"]
        assert len(items) == 2
        assert items[0]["name"] == "python"
        assert items[0]["url"] == "/t/python"
        assert items[1]["name"] == "django"
        assert items[1]["url"] == "/t/django"


# --- Fix 2: Deep dict serialization ---


class TestDeepDictSerialization:
    def setup_method(self):
        # DjangoJSONEncoder._depth is a class-level counter.  Reset it before
        # each test so that a crash in a previous test (which would leave it
        # non-zero despite the try/finally in default()) cannot affect later
        # assertions about serialized output.
        DjangoJSONEncoder._depth = 0

    def teardown_method(self):
        DjangoJSONEncoder._depth = 0

    def _make_model(self, **kwargs):
        """Create a mock Django Model instance."""
        obj = MagicMock()
        # Make isinstance(obj, models.Model) return True
        obj.__class__ = _MockModelClass
        obj.pk = 1
        obj.__str__ = MagicMock(return_value="MockModel")
        obj._meta = MagicMock()
        obj._meta.get_fields.return_value = []
        obj._state = MagicMock()
        obj._state.fields_cache = {}
        obj._prefetched_objects_cache = {}
        for k, v in kwargs.items():
            setattr(obj, k, v)
        return obj

    def test_deep_serialize_dict_with_model(self):
        """Dict containing a Model should serialize the Model to a dict."""
        model = self._make_model(title="Test")
        serialized = json.loads(json.dumps({"item": model}, cls=DjangoJSONEncoder))
        assert isinstance(serialized["item"], dict)
        assert serialized["item"]["id"] == 1, "id must be native int after PR #472"

    def test_serialize_model_includes_pk_key(self):
        """Model serialization includes 'pk' key alongside 'id' (#262, #472).

        Both 'id' and 'pk' are now the native type (PR #472 fix).
        'id' now matches 'pk' — no longer a string for backwards compat.
        """
        model = self._make_model(title="Test")
        encoder = DjangoJSONEncoder()
        result = encoder._serialize_model_safely(model)
        assert "pk" in result, "Serialized model must include 'pk' key"
        assert result["pk"] == 1, "'pk' must be the native primary key value"
        assert result["id"] == 1, "'id' must now be native type, matching pk (PR #472)"
        assert result["id"] == result["pk"], ".id and .pk must be identical"

    def test_serialize_model_id_is_native_type(self):
        """Primary .id serialization path returns native type (#472).

        The 'id' key must be the native pk type (not a string) so that
        template code using {% if model.id == var %} works with integer comparisons.
        PR #472 fixed this — model.id now returns native type, matching model.pk.
        """
        # Integer pk (most common case)
        model_int = self._make_model(title="Int PK")
        model_int.pk = 42
        encoder = DjangoJSONEncoder()
        result = encoder._serialize_model_safely(model_int)
        assert result["id"] == 42, "id must be native int, not string"
        assert isinstance(result["id"], int), "id must always be native type"

    def test_serialize_model_uuid_pk(self):
        """Model with UUID primary key serializes id as native UUID (#472)."""
        import uuid

        model = self._make_model(title="UUID PK")
        model.pk = uuid.UUID("12345678-1234-5678-1234-567812345678")
        encoder = DjangoJSONEncoder()
        result = encoder._serialize_model_safely(model)
        assert isinstance(result["id"], uuid.UUID), "id must be native UUID"
        assert result["id"] == uuid.UUID("12345678-1234-5678-1234-567812345678")
        # pk should also be the native UUID object
        assert isinstance(result["pk"], uuid.UUID)
        assert result["id"] == result["pk"]

    def test_serialize_model_uuid_pk_via_json_dumps(self):
        """UUID pk model serialized through json.dumps produces a string id in JSON output.

        _serialize_model_safely() returns id as a native UUID object.  When
        that dict is re-encoded by json.dumps, DjangoJSONEncoder.default()
        converts the UUID to a string.  This test exercises the full pipeline
        so both the direct and json.dumps code paths are covered.
        """
        import uuid

        model = self._make_model(title="UUID PK via dumps")
        model.pk = uuid.UUID("12345678-1234-5678-1234-567812345678")
        serialized = json.loads(json.dumps(model, cls=DjangoJSONEncoder))
        assert serialized["id"] == "12345678-1234-5678-1234-567812345678"
        assert serialized["pk"] == "12345678-1234-5678-1234-567812345678"

    def test_serialize_model_none_pk(self):
        """Model with pk=None (unsaved) serializes id as None, not 'None' (#408)."""
        model = self._make_model(title="Unsaved")
        model.pk = None
        encoder = DjangoJSONEncoder()
        result = encoder._serialize_model_safely(model)
        assert result["id"] is None, "id must be None for unsaved models, not the string 'None'"
        assert result["pk"] is None

    def test_deep_serialize_dict_nested(self):
        """Dict-in-dict containing Model is recursively serialized."""
        model = self._make_model(title="Nested")
        serialized = json.loads(json.dumps({"outer": {"inner": model}}, cls=DjangoJSONEncoder))
        assert isinstance(serialized["outer"]["inner"], dict)

    def test_deep_serialize_dict_primitives_passthrough(self):
        """Dict with only primitives passes through unchanged."""
        d = {"a": 1, "b": "hello", "c": True}
        serialized = json.loads(json.dumps(d, cls=DjangoJSONEncoder))
        assert serialized == d


# --- Fix 3: @property in fallback serialization ---


class _FakeModel(_django_models.Model):
    """Module-level model class to avoid re-registration warnings."""

    class Meta:
        app_label = "test"

    @property
    def url(self):
        return "/posts/1"

    @property
    def failing_prop(self):
        raise RuntimeError("boom")

    @property
    def list_prop(self):
        return [1, 2, 3]


class TestPropertySerialization:
    def test_encoder_includes_property_values(self):
        """Model with @property url → result['url'] present."""
        encoder = DjangoJSONEncoder()
        obj = _FakeModel.__new__(_FakeModel)
        obj.pk = 1
        obj.__dict__["id"] = 1

        result = {}
        encoder._add_property_values(obj, result)
        assert result["url"] == "/posts/1"

    def test_encoder_skips_failing_property(self):
        """@property that raises → silently skipped."""
        encoder = DjangoJSONEncoder()
        obj = _FakeModel.__new__(_FakeModel)

        result = {}
        encoder._add_property_values(obj, result)
        assert "failing_prop" not in result

    def test_encoder_skips_non_primitive_property(self):
        """@property returning list → not included (only primitives)."""
        encoder = DjangoJSONEncoder()
        obj = _FakeModel.__new__(_FakeModel)

        result = {}
        encoder._add_property_values(obj, result)
        assert "list_prop" not in result


# --- Fix 3b: @property cache avoids duplicate evaluation ---


class _CountingModel(_django_models.Model):
    """Module-level model class to avoid re-registration warnings."""

    class Meta:
        app_label = "test"

    _call_count = 0

    @property
    def expensive_prop(self):
        _CountingModel._call_count += 1
        return 42


class TestPropertyCache:
    def test_property_cache_populated_on_first_access(self):
        """First _add_property_values call populates _djust_prop_cache."""
        encoder = DjangoJSONEncoder()
        obj = _CountingModel.__new__(_CountingModel)
        obj.pk = 1
        obj.__dict__["id"] = 1

        result = {}
        encoder._add_property_values(obj, result)
        assert result["expensive_prop"] == 42
        assert hasattr(obj, "_djust_prop_cache")
        assert obj._djust_prop_cache["expensive_prop"] == 42

    def test_property_cache_avoids_reeval(self):
        """Second _add_property_values call reads from cache, not property."""
        encoder = DjangoJSONEncoder()
        obj = _CountingModel.__new__(_CountingModel)
        obj.pk = 1
        obj.__dict__["id"] = 1

        # Reset class-level counter before the test.  This is safe because
        # _call_count is only written by _CountingModel.expensive_prop and read
        # here; no other test touches this class attribute, so resetting it
        # directly avoids the need for a pytest fixture.
        _CountingModel._call_count = 0

        result1 = {}
        encoder._add_property_values(obj, result1)
        first_count = _CountingModel._call_count

        result2 = {}
        encoder._add_property_values(obj, result2)
        second_count = _CountingModel._call_count

        assert result1["expensive_prop"] == 42
        assert result2["expensive_prop"] == 42
        # Property should only be called once; second call uses cache
        assert second_count == first_count


# --- Fix 4: Model before duck-typing ---


class TestModelBeforeDuckTyping:
    def test_model_with_url_and_name_serialized_as_dict(self):
        """Model with both 'url' and 'name' attrs → serialized as dict, not string URL."""
        from django.db import models as django_models

        obj = MagicMock()
        obj.__class__ = type(
            "PostModel",
            (django_models.Model,),
            {"__module__": "test_duck", "Meta": type("Meta", (), {"app_label": "test_duck"})},
        )
        obj.pk = 42
        obj.url = "/posts/42"
        obj.name = "My Post"
        obj.__str__ = MagicMock(return_value="My Post")
        obj._meta = MagicMock()
        obj._meta.get_fields.return_value = []
        obj._state = MagicMock()
        obj._state.fields_cache = {}
        obj._prefetched_objects_cache = {}

        result = json.loads(json.dumps(obj, cls=DjangoJSONEncoder))
        # Should be a dict (model serialization), not a string URL
        assert isinstance(result, dict)
        assert result["id"] == 42, "id must be native int after PR #472"


# --- Fix 5: Rust→Python fallback ---


class TestRustFallback:
    @patch("djust.mixins.jit.extract_template_variables")
    @patch("djust.mixins.jit.JIT_AVAILABLE", True)
    def test_jit_queryset_falls_back_when_rust_incomplete(self, mock_extract):
        """When Rust serialize_queryset returns fewer keys, codegen fallback is used."""
        mock_extract.return_value = {"posts": ["title", "url"]}

        class MockPost:
            def __init__(self):
                self.title = "Hello"
                self.url = "/hello"

        qs = MagicMock()
        qs.model = MockPost
        qs.__iter__ = MagicMock(return_value=iter([MockPost()]))

        mock_rust_serialize = MagicMock(return_value=[{"title": "Hello"}])

        with patch.dict(
            "sys.modules", {"djust._rust": MagicMock(serialize_queryset=mock_rust_serialize)}
        ):
            with patch("djust.mixins.jit._get_model_hash", return_value="abc123"):
                with patch("djust.mixins.jit.generate_serializer_code") as mock_codegen:
                    with patch("djust.mixins.jit.compile_serializer") as mock_compile:
                        with patch(
                            "djust.mixins.jit.analyze_queryset_optimization", return_value=None
                        ):
                            mock_serializer = MagicMock(
                                return_value={"title": "Hello", "url": "/hello"}
                            )
                            mock_compile.return_value = mock_serializer
                            mock_codegen.return_value = "def test(obj): pass"

                            from djust.mixins.jit import JITMixin

                            mixin = JITMixin()
                            mixin._jit_serialize_queryset(
                                qs, "{{ posts.title }} {{ posts.url }}", "posts"
                            )

                            # Codegen fallback should have been called
                            mock_codegen.assert_called_once()
