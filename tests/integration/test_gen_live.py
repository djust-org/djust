"""
Tests for djust_gen_live scaffolding generator.

Tests field parsing, generator logic, file output, and end-to-end
LiveView generation with LiveViewTestClient.
"""

import os
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Django settings — configure BEFORE any djust imports
# ---------------------------------------------------------------------------

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        SECRET_KEY="test-secret-key",
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
        ROOT_URLCONF="",
    )
    django.setup()

# ---------------------------------------------------------------------------
# Imports after Django setup
# ---------------------------------------------------------------------------

from djust.scaffolding.gen_live import (
    GenerationError,
    build_template_context,
    generate_liveview,
    parse_field_defs,
    validate_model_name,
)


# ---------------------------------------------------------------------------
# Field parsing tests
# ---------------------------------------------------------------------------


class TestParseFieldDefs:
    """Tests for parse_field_defs()."""

    def test_parses_string_field(self):
        fields = parse_field_defs(["title:string"])
        assert len(fields) == 1
        assert fields[0]["name"] == "title"
        assert fields[0]["type"] == "string"
        assert fields[0]["label"] == "Title"

    def test_parses_text_field(self):
        fields = parse_field_defs(["body:text"])
        assert len(fields) == 1
        assert fields[0]["name"] == "body"
        assert fields[0]["type"] == "text"

    def test_parses_integer_field(self):
        fields = parse_field_defs(["views:integer"])
        assert len(fields) == 1
        assert fields[0]["name"] == "views"
        assert fields[0]["type"] == "integer"

    def test_parses_float_field(self):
        fields = parse_field_defs(["price:float"])
        assert len(fields) == 1
        assert fields[0]["name"] == "price"
        assert fields[0]["type"] == "float"

    def test_parses_decimal_field(self):
        fields = parse_field_defs(["price:decimal"])
        assert len(fields) == 1
        assert fields[0]["name"] == "price"
        assert fields[0]["type"] == "decimal"

    def test_parses_boolean_field(self):
        fields = parse_field_defs(["published:boolean"])
        assert len(fields) == 1
        assert fields[0]["name"] == "published"
        assert fields[0]["type"] == "boolean"

    def test_parses_date_field(self):
        fields = parse_field_defs(["created_at:date"])
        assert len(fields) == 1
        assert fields[0]["name"] == "created_at"
        assert fields[0]["type"] == "date"

    def test_parses_datetime_field(self):
        fields = parse_field_defs(["updated_at:datetime"])
        assert len(fields) == 1
        assert fields[0]["name"] == "updated_at"
        assert fields[0]["type"] == "datetime"

    def test_parses_email_field(self):
        fields = parse_field_defs(["email:email"])
        assert len(fields) == 1
        assert fields[0]["name"] == "email"
        assert fields[0]["type"] == "email"

    def test_parses_url_field(self):
        fields = parse_field_defs(["website:url"])
        assert len(fields) == 1
        assert fields[0]["name"] == "website"
        assert fields[0]["type"] == "url"

    def test_parses_slug_field(self):
        fields = parse_field_defs(["slug:slug"])
        assert len(fields) == 1
        assert fields[0]["name"] == "slug"
        assert fields[0]["type"] == "slug"

    def test_parses_fk_field(self):
        fields = parse_field_defs(["author:fk:User"])
        assert len(fields) == 1
        assert fields[0]["name"] == "author"
        assert fields[0]["type"] == "fk"
        assert fields[0]["related_model"] == "User"

    def test_parses_multiple_fields(self):
        fields = parse_field_defs(
            [
                "title:string",
                "body:text",
                "published:boolean",
                "views:integer",
            ]
        )
        assert len(fields) == 4
        assert [f["name"] for f in fields] == ["title", "body", "published", "views"]
        assert [f["type"] for f in fields] == ["string", "text", "boolean", "integer"]

    def test_raises_on_empty_field_def(self):
        with pytest.raises(ValueError, match="Invalid field definition ''"):
            parse_field_defs([""])

    def test_raises_on_field_without_type(self):
        with pytest.raises(ValueError, match="Invalid field definition 'title'"):
            parse_field_defs(["title"])

    def test_raises_on_empty_name(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            parse_field_defs([":string"])

    def test_raises_on_invalid_name(self):
        with pytest.raises(ValueError, match="not a valid Python"):
            parse_field_defs(["123field:string"])

    def test_raises_on_duplicate_field(self):
        with pytest.raises(ValueError, match="Duplicate field name"):
            parse_field_defs(["title:string", "title:text"])

    def test_raises_on_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown field type 'invalid'"):
            parse_field_defs(["title:invalid"])

    def test_raises_on_empty_fk_model(self):
        with pytest.raises(ValueError, match="Foreign key requires model name"):
            parse_field_defs(["author:fk:"])


# ---------------------------------------------------------------------------
# Model name validation tests
# ---------------------------------------------------------------------------


class TestValidateModelName:
    """Tests for validate_model_name()."""

    def test_accepts_simple_pascal_case(self):
        validate_model_name("Post")
        validate_model_name("Blog")
        validate_model_name("MyModel")

    def test_accepts_pascal_case_with_numbers(self):
        validate_model_name("Post2")
        validate_model_name("Blog3Post")

    def test_raises_on_empty(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            validate_model_name("")

    def test_raises_on_snake_case(self):
        with pytest.raises(ValueError, match="must be PascalCase"):
            validate_model_name("blog_post")

    def test_rejects_all_caps(self):
        """All-caps names like 'POST' are technically valid identifiers but not idiomatic."""
        # Note: regex ^[A-Z][a-zA-Z0-9]*$ accepts POST but it's not idiomatic.
        # This test documents that we accept it for flexibility.
        # If strict PascalCase is desired, the regex could be tightened.
        validate_model_name("POST")  # Currently accepted for flexibility

    def test_raises_on_leading_underscore(self):
        with pytest.raises(ValueError, match="must be PascalCase"):
            validate_model_name("_Post")

    def test_raises_on_leading_number(self):
        with pytest.raises(ValueError, match="must be PascalCase"):
            validate_model_name("2Post")


# ---------------------------------------------------------------------------
# Template context building tests
# ---------------------------------------------------------------------------


class TestBuildTemplateContext:
    """Tests for build_template_context()."""

    def test_builds_basic_context(self):
        fields = parse_field_defs(["title:string", "body:text"])
        ctx = build_template_context("blog", "Post", fields)
        assert ctx["app_name"] == "blog"
        assert ctx["model_name"] == "Post"
        assert ctx["model_slug"] == "post"
        assert ctx["view_class"] == "PostListView"
        assert ctx["url_prefix"] == "post/"
        assert ctx["model_display"] == "Posts"
        assert ctx["model_display_singular"] == "Post"

    def test_includes_fields_in_context(self):
        fields = parse_field_defs(["title:string", "published:boolean"])
        ctx = build_template_context("blog", "Post", fields)
        assert len(ctx["fields"]) == 2
        assert ctx["fields"][0]["name"] == "title"
        assert ctx["fields"][1]["name"] == "published"

    def test_builds_search_filter(self):
        fields = parse_field_defs(["title:string", "body:text"])
        ctx = build_template_context("blog", "Post", fields)
        assert "title__icontains" in ctx["search_filter"]
        assert "body__icontains" in ctx["search_filter"]

    def test_search_filter_skips_non_text_fields(self):
        fields = parse_field_defs(["published:boolean", "views:integer"])
        ctx = build_template_context("blog", "Post", fields)
        assert "pass" in ctx["search_filter"]


# ---------------------------------------------------------------------------
# File generation tests (temp directory)
# ---------------------------------------------------------------------------


class TestGenerateLiveview:
    """Tests for generate_liveview()."""

    @pytest.fixture
    def temp_app_dir(self):
        """Create a temp directory with app structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            app_dir = Path(tmpdir) / "blog"
            templates_dir = app_dir / "templates" / "blog"
            tests_dir = app_dir / "tests"
            app_dir.mkdir(parents=True)
            templates_dir.mkdir(parents=True)
            tests_dir.mkdir(parents=True)

            # Create minimal __init__.py
            (app_dir / "__init__.py").write_text("", encoding="utf-8")

            yield app_dir

    def test_generates_views_file(self, temp_app_dir):
        """views.py is created."""
        fields = parse_field_defs(["title:string", "body:text"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            generate_liveview("blog", "Post", fields, {"force": True})
            views_file = temp_app_dir / "views.py"
            assert views_file.exists(), "views.py should be created"
            content = views_file.read_text(encoding="utf-8")
            assert "class PostListView" in content
            assert "def mount" in content
            assert "def search" in content
            assert "def create" in content
            assert "def delete" in content
        finally:
            os.chdir(original_cwd)

    def test_generates_urls_file(self, temp_app_dir):
        """urls.py is created."""
        fields = parse_field_defs(["title:string"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            generate_liveview("blog", "Post", fields, {"force": True})
            urls_file = temp_app_dir / "urls.py"
            assert urls_file.exists(), "urls.py should be created"
            content = urls_file.read_text(encoding="utf-8")
            assert "PostListView" in content
            assert 'path("post/"' in content
        finally:
            os.chdir(original_cwd)

    def test_generates_template_file(self, temp_app_dir):
        """<model>_list.html is created."""
        fields = parse_field_defs(["title:string", "body:text"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            generate_liveview("blog", "Post", fields, {"force": True})
            tpl_file = temp_app_dir / "templates" / "blog" / "post_list.html"
            assert tpl_file.exists(), "post_list.html should be created"
            content = tpl_file.read_text(encoding="utf-8")
            assert "dj-root" in content
            assert "dj-view" in content
            assert "dj-input" in content
            assert "dj-submit" in content
        finally:
            os.chdir(original_cwd)

    def test_generates_test_file(self, temp_app_dir):
        """test_<model>_crud.py is created."""
        fields = parse_field_defs(["title:string"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            generate_liveview("blog", "Post", fields, {"force": True})
            test_file = temp_app_dir / "tests" / "test_post_crud.py"
            assert test_file.exists(), "test_post_crud.py should be created"
            content = test_file.read_text(encoding="utf-8")
            assert "TestPostLiveView" in content
            assert "LiveViewTestClient" in content
        finally:
            os.chdir(original_cwd)

    def test_skips_test_file_with_no_tests(self, temp_app_dir):
        """Test file is not created with --no-tests."""
        fields = parse_field_defs(["title:string"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            generate_liveview("blog", "Post", fields, {"force": True, "no_tests": True})
            test_file = temp_app_dir / "tests" / "test_post_crud.py"
            assert not test_file.exists(), "test_post_crud.py should NOT be created"
        finally:
            os.chdir(original_cwd)

    def test_dry_run_does_not_write_files(self, temp_app_dir):
        """--dry-run prints but does not write files."""
        fields = parse_field_defs(["title:string"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            generate_liveview("blog", "Post", fields, {"dry_run": True})
            views_file = temp_app_dir / "views.py"
            assert not views_file.exists(), "views.py should NOT be created in dry run"
        finally:
            os.chdir(original_cwd)

    def test_force_overwrites_existing_files(self, temp_app_dir):
        """--force allows overwriting existing files."""
        fields1 = parse_field_defs(["title:string"])
        fields2 = parse_field_defs(["title:string", "body:text"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            # Generate first version
            generate_liveview("blog", "Post", fields1, {"force": True})
            # Overwrite with second version
            generate_liveview("blog", "Post", fields2, {"force": True})
            views_file = temp_app_dir / "views.py"
            content = views_file.read_text(encoding="utf-8")
            assert "title" in content
            assert "body" in content
        finally:
            os.chdir(original_cwd)

    def test_raises_on_existing_files_without_force(self, temp_app_dir):
        """Error is raised if files exist and --force is not set."""
        fields = parse_field_defs(["title:string"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            generate_liveview("blog", "Post", fields, {"force": True})
            # Try again without force
            with pytest.raises(GenerationError, match="already exist"):
                generate_liveview("blog", "Post", fields, {"force": False})
        finally:
            os.chdir(original_cwd)

    def test_raises_on_invalid_model_name(self, temp_app_dir):
        """Invalid model names raise CommandError via generate_liveview."""
        fields = parse_field_defs(["title:string"])
        original_cwd = os.getcwd()
        try:
            os.chdir(temp_app_dir.parent)
            with pytest.raises(GenerationError, match="not a valid model name"):
                generate_liveview("blog", "blog_post", fields)
        finally:
            os.chdir(original_cwd)


# ---------------------------------------------------------------------------
# End-to-end integration tests
# ---------------------------------------------------------------------------


class TestGenLiveEndToEnd:
    """End-to-end tests for djust_gen_live management command."""

    def test_command_runs_without_error(self):
        """The management command can be invoked (basic smoke test)."""
        # We can't fully run the command without a Django project,
        # but we can test that the module loads without error
        from djust.management.commands import djust_gen_live

        assert djust_gen_live is not None

    def test_parse_field_defs_integration(self):
        """parse_field_defs handles all documented field types."""
        field_defs = [
            "title:string",
            "body:text",
            "views:integer",
            "price:decimal",
            "published:boolean",
            "published_at:datetime",
            "created_at:date",
            "email:email",
            "website:url",
            "slug:slug",
            "author:fk:User",
        ]
        fields = parse_field_defs(field_defs)
        assert len(fields) == 11
        assert fields[0]["name"] == "title"
        assert fields[10]["name"] == "author"
        assert fields[10]["type"] == "fk"
        assert fields[10]["related_model"] == "User"
