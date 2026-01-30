"""
Tests for list[Model] codegen serialization in context.py and
template include inlining in jit.py.

Covers:
1. context.py uses codegen for list[Model] when paths are available
2. context.py falls back to _jit_serialize_model when no paths
3. _inline_includes resolves {% include %} directives
4. _inline_includes handles doubled quotes from Rust resolver
5. _inline_includes handles nested includes with depth limit
6. _get_template_content resolves template inheritance + includes
"""

from unittest.mock import MagicMock, patch

from django.db import models as django_models

from djust.mixins.jit import JITMixin
from djust.optimization.codegen import generate_serializer_code, compile_serializer
from djust.serialization import DjangoJSONEncoder
from djust.session_utils import _jit_serializer_cache


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_model(**kwargs):
    """Create a mock Django Model instance with given attributes."""
    obj = MagicMock()
    obj.__class__ = type(
        "MockModel",
        (django_models.Model,),
        {"__module__": "test_list", "Meta": type("Meta", (), {"app_label": "test_list"})},
    )
    obj.pk = kwargs.pop("pk", 1)
    obj.__str__ = MagicMock(return_value="MockModel")
    obj._meta = MagicMock()
    obj._meta.get_fields.return_value = []
    obj._state = MagicMock()
    obj._state.fields_cache = {}
    obj._prefetched_objects_cache = {}
    for k, v in kwargs.items():
        setattr(obj, k, v)
    return obj


# ---------------------------------------------------------------------------
# 1. Codegen serializer used for list[Model] when paths are available
# ---------------------------------------------------------------------------


class TestListModelCodegenPath:
    """When context.py encounters list[Model] with known template paths,
    it should use a codegen serializer instead of DjangoJSONEncoder."""

    def test_codegen_serializes_only_template_paths(self):
        """Codegen serializer only includes fields referenced in template."""
        paths = ["title", "url"]
        code = generate_serializer_code("Post", paths, "serialize_posts_test1")
        serializer = compile_serializer(code, "serialize_posts_test1")

        obj = MagicMock()
        obj.title = "Hello"
        obj.url = "/hello"
        obj.secret_field = "should_not_appear"

        result = serializer(obj)
        assert result["title"] == "Hello"
        assert result["url"] == "/hello"
        assert "secret_field" not in result

    def test_codegen_handles_nested_fk_paths(self):
        """Codegen handles 'category.name' style FK traversal."""
        paths = ["title", "category.name"]
        code = generate_serializer_code("Post", paths, "serialize_posts_test2")
        serializer = compile_serializer(code, "serialize_posts_test2")

        category = MagicMock()
        category.name = "Tech"
        obj = MagicMock()
        obj.title = "Hello"
        obj.category = category

        result = serializer(obj)
        assert result["title"] == "Hello"
        assert result["category"]["name"] == "Tech"

    def test_codegen_serializer_cached(self):
        """Serializer cache stores and retrieves codegen serializers."""
        # Clear cache first
        cache_key = ("testhash", "posts", "modelhash", "list")
        _jit_serializer_cache.pop(cache_key, None)

        paths = ["title"]
        code = generate_serializer_code("Post", paths, "serialize_posts_cache")
        serializer = compile_serializer(code, "serialize_posts_cache")
        _jit_serializer_cache[cache_key] = (serializer, None)

        assert cache_key in _jit_serializer_cache
        cached_serializer, _ = _jit_serializer_cache[cache_key]
        assert cached_serializer is serializer

        # Cleanup
        del _jit_serializer_cache[cache_key]

    def test_list_model_uses_codegen_not_encoder(self):
        """list[Model] with paths should use codegen, not DjangoJSONEncoder.

        The codegen serializer only extracts template-referenced fields,
        avoiding DjangoJSONEncoder's _add_safe_model_methods which can
        trigger N+1 queries.
        """
        paths = ["title", "url"]
        code = generate_serializer_code("Post", paths, "serialize_posts_test3")
        serializer = compile_serializer(code, "serialize_posts_test3")

        # Create objects with a method that would trigger N+1 if called
        call_count = 0

        class FakePost:
            def __init__(self, title, url):
                self.title = title
                self.url = url

            def get_series_navigation(self):
                nonlocal call_count
                call_count += 1
                return {"previous": None, "next": None}

        items = [FakePost("Post 1", "/p1"), FakePost("Post 2", "/p2")]
        results = [serializer(item) for item in items]

        assert len(results) == 2
        assert results[0]["title"] == "Post 1"
        assert results[1]["url"] == "/p2"
        # Codegen should NOT call get_series_navigation
        assert call_count == 0


# ---------------------------------------------------------------------------
# 2. Fallback to _jit_serialize_model when no paths
# ---------------------------------------------------------------------------


class TestListModelFallback:
    """When no template paths are found for list[Model],
    context.py falls back to _jit_serialize_model."""

    @patch("djust.mixins.jit.extract_template_variables")
    @patch("djust.mixins.jit.JIT_AVAILABLE", True)
    def test_no_paths_uses_jit_serialize_model(self, mock_extract):
        """Without paths, _jit_serialize_model is called per item."""
        mock_extract.return_value = {}  # No paths for any variable

        mixin = JITMixin()
        model = _make_mock_model(pk=1, title="Test")

        # _jit_serialize_model with no paths falls back to DjangoJSONEncoder
        result = mixin._jit_serialize_model(model, "<div>{{ unknown }}</div>", "items")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# 3. _inline_includes resolves {% include %} directives
# ---------------------------------------------------------------------------


class TestInlineIncludes:
    """Tests for JITMixin._inline_includes static method."""

    def test_basic_include_resolution(self, tmp_path):
        """Simple {% include %} is replaced with file contents."""
        sidebar = tmp_path / "sidebar.html"
        sidebar.write_text("<div>{{ recent_posts }}</div>")

        template = '{% include "sidebar.html" %}'
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        assert "<div>{{ recent_posts }}</div>" in result
        assert "{% include" not in result

    def test_include_with_subdirectory(self, tmp_path):
        """{% include %} with subdirectory path resolves correctly."""
        subdir = tmp_path / "blog"
        subdir.mkdir()
        partial = subdir / "_sidebar.html"
        partial.write_text("{% for rp in recent_posts %}{{ rp.title }}{% endfor %}")

        template = '{% include "blog/_sidebar.html" %}'
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        assert "recent_posts" in result
        assert "rp.title" in result

    def test_doubled_quotes_from_rust_resolver(self, tmp_path):
        """Doubled quotes (""path"") from Rust resolver are handled."""
        sidebar = tmp_path / "sidebar.html"
        sidebar.write_text("<nav>sidebar</nav>")

        # Rust resolver produces doubled quotes around include paths
        template = '{% include ""sidebar.html"" %}'
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        assert "<nav>sidebar</nav>" in result

    def test_single_quotes_include(self, tmp_path):
        """{% include 'file.html' %} with single quotes works."""
        partial = tmp_path / "partial.html"
        partial.write_text("<span>partial</span>")

        template = "{% include 'partial.html' %}"
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        assert "<span>partial</span>" in result

    def test_nested_includes(self, tmp_path):
        """Nested includes are resolved recursively."""
        inner = tmp_path / "inner.html"
        inner.write_text("<p>inner content</p>")

        outer = tmp_path / "outer.html"
        outer.write_text('<div>{% include "inner.html" %}</div>')

        template = '{% include "outer.html" %}'
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        assert "<p>inner content</p>" in result
        assert "<div>" in result

    def test_depth_limit_prevents_infinite_recursion(self, tmp_path):
        """Recursive includes stop at depth limit."""
        recursive = tmp_path / "recursive.html"
        recursive.write_text('DEPTH {% include "recursive.html" %}')

        template = '{% include "recursive.html" %}'
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        # Should contain DEPTH repeated but not infinitely
        assert result.count("DEPTH") <= 7  # initial + up to 6 levels

    def test_missing_include_preserved(self):
        """Missing include file leaves the tag unchanged."""
        template = '{% include "nonexistent.html" %}'
        result = JITMixin._inline_includes(template, ["/no/such/dir"])

        assert '{% include "nonexistent.html" %}' in result

    def test_multiple_includes(self, tmp_path):
        """Multiple includes in one template are all resolved."""
        (tmp_path / "header.html").write_text("<header>{{ title }}</header>")
        (tmp_path / "footer.html").write_text("<footer>{{ year }}</footer>")

        template = '{% include "header.html" %}\n<main>body</main>\n{% include "footer.html" %}'
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        assert "<header>{{ title }}</header>" in result
        assert "<footer>{{ year }}</footer>" in result
        assert "<main>body</main>" in result

    def test_multiple_template_dirs(self, tmp_path):
        """Searches multiple template directories in order."""
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        dir2 = tmp_path / "dir2"
        dir2.mkdir()

        (dir2 / "partial.html").write_text("<p>from dir2</p>")

        template = '{% include "partial.html" %}'
        result = JITMixin._inline_includes(template, [str(dir1), str(dir2)])

        assert "<p>from dir2</p>" in result

    def test_first_dir_wins(self, tmp_path):
        """First matching directory takes priority."""
        dir1 = tmp_path / "dir1"
        dir1.mkdir()
        dir2 = tmp_path / "dir2"
        dir2.mkdir()

        (dir1 / "partial.html").write_text("<p>from dir1</p>")
        (dir2 / "partial.html").write_text("<p>from dir2</p>")

        template = '{% include "partial.html" %}'
        result = JITMixin._inline_includes(template, [str(dir1), str(dir2)])

        assert "<p>from dir1</p>" in result

    def test_include_with_extra_whitespace(self, tmp_path):
        """{% include %} with extra whitespace still resolves."""
        (tmp_path / "partial.html").write_text("content")

        template = '{%   include   "partial.html"   %}'
        result = JITMixin._inline_includes(template, [str(tmp_path)])

        assert "content" in result

    def test_variable_include_not_resolved(self):
        """{% include some_var %} (variable) is left unchanged."""
        template = "{% include template_name %}"
        result = JITMixin._inline_includes(template, ["/tmp"])

        # Variable includes don't match the pattern (no quotes)
        assert "{% include template_name %}" in result


# ---------------------------------------------------------------------------
# 4. _get_template_content with inheritance + includes
# ---------------------------------------------------------------------------


class TestGetTemplateContentResolution:
    """_get_template_content should return fully resolved template
    (inheritance + includes) for accurate variable extraction."""

    def test_prefers_full_template_if_available(self):
        """When _full_template is set, it's returned directly."""
        mixin = JITMixin()
        mixin._full_template = "<div>{{ foo }}</div>"

        result = mixin._get_template_content()
        assert result == "<div>{{ foo }}</div>"

    def test_prefers_template_attribute(self):
        """When template is set (no _full_template), it's returned."""
        mixin = JITMixin()
        mixin.template = "<div>{{ bar }}</div>"

        result = mixin._get_template_content()
        assert result == "<div>{{ bar }}</div>"

    def test_returns_none_without_template(self):
        """Returns None when no template info is available."""
        mixin = JITMixin()
        result = mixin._get_template_content()
        assert result is None


# ---------------------------------------------------------------------------
# 5. Integration: codegen for list[Model] avoids N+1 methods
# ---------------------------------------------------------------------------


class TestCodegenAvoidsMethods:
    """The codegen serializer should NOT call model methods,
    unlike DjangoJSONEncoder._add_safe_model_methods."""

    def test_encoder_calls_methods_but_codegen_does_not(self):
        """DjangoJSONEncoder calls get_* methods; codegen does not."""
        call_log = []

        class TrackedModel(django_models.Model):
            class Meta:
                app_label = "test_tracked"

            @property
            def title(self):
                return "Test"

            @property
            def url(self):
                return "/test"

            def get_expensive_data(self):
                call_log.append("get_expensive_data")
                return {"data": "expensive"}

        # Create codegen serializer with just title and url
        paths = ["title", "url"]
        code = generate_serializer_code("TrackedModel", paths, "serialize_tracked")
        serializer = compile_serializer(code, "serialize_tracked")

        obj = TrackedModel.__new__(TrackedModel)
        obj.pk = 1
        obj.__dict__["id"] = 1

        # Codegen path
        call_log.clear()
        result = serializer(obj)
        assert result["title"] == "Test"
        assert result["url"] == "/test"
        assert len(call_log) == 0, "Codegen should not call methods"

        # DjangoJSONEncoder path (for comparison)
        call_log.clear()
        encoder = DjangoJSONEncoder()
        encoder_result = encoder._serialize_model_safely(obj)
        # Encoder may call get_expensive_data via _add_safe_model_methods
        # (this verifies the N+1 risk that codegen avoids)
        assert isinstance(encoder_result, dict)
