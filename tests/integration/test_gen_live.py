"""
Tests for ``manage.py djust_gen_live`` — model-to-LiveView scaffolding generator.

Tests cover:
- Field definition parsing (string, text, integer, float, decimal, boolean,
  date, datetime, email, url, slug, fk:Model)
- Model name validation (PascalCase requirement)
- Template context building
- File generation (views.py, urls.py, templates, tests)
- Command options (--dry-run, --force, --no-tests, --api)
- Edge cases (no fields, only booleans, FK fields, etc.)
- Generated output validity (Django template syntax, live_session routing, Q objects)
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from djust.scaffolding.gen_live import (
    build_create_body,
    build_update_body,
    generate_liveview,
    get_search_filter,
    parse_field_defs,
    validate_model_name,
)


class TestParseFieldDefs(TestCase):
    """Test parse_field_defs() for all supported field types."""

    def test_string_field(self):
        result = parse_field_defs(["title:string"])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "title")
        self.assertEqual(result[0]["type"], "string")

    def test_text_field(self):
        result = parse_field_defs(["body:text"])
        self.assertEqual(result[0]["type"], "text")

    def test_integer_field(self):
        result = parse_field_defs(["count:integer"])
        self.assertEqual(result[0]["type"], "integer")

    def test_float_field(self):
        result = parse_field_defs(["price:float"])
        self.assertEqual(result[0]["type"], "float")

    def test_decimal_field(self):
        result = parse_field_defs(["amount:decimal"])
        self.assertEqual(result[0]["type"], "decimal")

    def test_boolean_field(self):
        result = parse_field_defs(["active:boolean"])
        self.assertEqual(result[0]["type"], "boolean")

    def test_date_field(self):
        result = parse_field_defs(["birth_date:date"])
        self.assertEqual(result[0]["type"], "date")

    def test_datetime_field(self):
        result = parse_field_defs(["created_at:datetime"])
        self.assertEqual(result[0]["type"], "datetime")

    def test_email_field(self):
        result = parse_field_defs(["contact:email"])
        self.assertEqual(result[0]["type"], "email")

    def test_url_field(self):
        result = parse_field_defs(["website:url"])
        self.assertEqual(result[0]["type"], "url")

    def test_slug_field(self):
        result = parse_field_defs(["slug:slug"])
        self.assertEqual(result[0]["type"], "slug")

    def test_fk_field(self):
        result = parse_field_defs(["author:fk:User"])
        self.assertEqual(result[0]["name"], "author")
        self.assertEqual(result[0]["type"], "fk")
        self.assertEqual(result[0]["related_model"], "User")

    def test_multiple_fields(self):
        result = parse_field_defs(["title:string", "body:text", "count:integer"])
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["name"], "title")
        self.assertEqual(result[1]["name"], "body")
        self.assertEqual(result[2]["name"], "count")

    def test_empty_list(self):
        result = parse_field_defs([])
        self.assertEqual(result, [])

    def test_error_field_without_type(self):
        with self.assertRaises(ValueError) as ctx:
            parse_field_defs(["title"])
        self.assertIn("name:type", str(ctx.exception))

    def test_error_empty_field_name(self):
        with self.assertRaises(ValueError) as ctx:
            parse_field_defs([":string"])
        self.assertIn("empty", str(ctx.exception).lower())

    def test_error_invalid_field_name_starts_with_number(self):
        with self.assertRaises(ValueError) as ctx:
            parse_field_defs(["1title:string"])
        self.assertIn("identifier", str(ctx.exception).lower())

    def test_error_duplicate_field_name(self):
        with self.assertRaises(ValueError) as ctx:
            parse_field_defs(["title:string", "title:text"])
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_error_unknown_field_type(self):
        with self.assertRaises(ValueError) as ctx:
            parse_field_defs(["title:blob"])
        self.assertIn("unknown", str(ctx.exception).lower())

    def test_error_fk_without_model(self):
        with self.assertRaises(ValueError) as ctx:
            parse_field_defs(["author:fk"])
        self.assertIn("model", str(ctx.exception).lower())


class TestValidateModelName(TestCase):
    """Test validate_model_name() PascalCase validation."""

    def test_valid_pascalcase(self):
        validate_model_name("Post")
        validate_model_name("BlogPost")
        validate_model_name("Post2")

    def test_reject_empty(self):
        with self.assertRaises(ValueError):
            validate_model_name("")

    def test_reject_snake_case(self):
        with self.assertRaises(ValueError):
            validate_model_name("blog_post")

    def test_reject_leading_underscore(self):
        with self.assertRaises(ValueError):
            validate_model_name("_Post")

    def test_reject_leading_number(self):
        with self.assertRaises(ValueError):
            validate_model_name("2Post")

    def test_reject_lowercase_start(self):
        with self.assertRaises(ValueError):
            validate_model_name("post")


class TestGetSearchFilter(TestCase):
    """Test get_search_filter() generates Q-object OR logic for text fields."""

    def test_text_fields_use_q_objects(self):
        fields = [
            {"name": "title", "type": "string"},
            {"name": "body", "type": "text"},
        ]
        result = get_search_filter(fields)
        self.assertIn("Q(title__icontains=self.search_query)", result)
        self.assertIn("Q(body__icontains=self.search_query)", result)
        self.assertIn("|", result)

    def test_no_text_fields_produces_pass(self):
        fields = [
            {"name": "count", "type": "integer"},
            {"name": "active", "type": "boolean"},
        ]
        result = get_search_filter(fields)
        self.assertIn("pass", result)

    def test_single_text_field_no_pipe(self):
        fields = [{"name": "title", "type": "string"}]
        result = get_search_filter(fields)
        self.assertIn("Q(title__icontains=self.search_query)", result)
        self.assertNotIn("|", result)

    def test_email_and_url_are_searchable(self):
        fields = [
            {"name": "contact", "type": "email"},
            {"name": "website", "type": "url"},
        ]
        result = get_search_filter(fields)
        self.assertIn("Q(contact__icontains=self.search_query)", result)
        self.assertIn("Q(website__icontains=self.search_query)", result)

    def test_slug_is_searchable(self):
        fields = [{"name": "slug", "type": "slug"}]
        result = get_search_filter(fields)
        self.assertIn("Q(slug__icontains=self.search_query)", result)


class TestBuildCreateBody(TestCase):
    """Test build_create_body() produces correct model creation code."""

    def test_creates_with_model_name(self):
        fields = [
            {"name": "title", "type": "string"},
            {"name": "body", "type": "text"},
        ]
        result = build_create_body(fields, "Post")
        self.assertIn("Post.objects.create(", result)
        self.assertNotIn("None.objects.create(", result)

    def test_fk_field_uses_id_suffix(self):
        fields = [
            {"name": "title", "type": "string"},
            {"name": "author", "type": "fk", "related_model": "User"},
        ]
        result = build_create_body(fields, "Post")
        self.assertIn("author_id=", result)

    def test_boolean_field_uses_bool_conversion(self):
        fields = [
            {"name": "title", "type": "string"},
            {"name": "active", "type": "boolean"},
        ]
        result = build_create_body(fields, "Post")
        self.assertIn("active=", result)

    def test_no_fields_creates_bare(self):
        result = build_create_body([], "Post")
        self.assertIn("Post.objects.create()", result)


class TestBuildUpdateBody(TestCase):
    """Test build_update_body() produces correct model update code."""

    def test_updates_with_model_name(self):
        fields = [
            {"name": "title", "type": "string"},
        ]
        result = build_update_body(fields, "Post")
        self.assertIn("Post.objects.get(pk=item_id)", result)

    def test_fk_field_uses_id_suffix(self):
        fields = [
            {"name": "author", "type": "fk", "related_model": "User"},
        ]
        result = build_update_body(fields, "Post")
        self.assertIn("author_id", result)


class TestGenerateLiveview(TestCase):
    """Test generate_liveview() file generation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a fake Django app directory
        self.app_dir = os.path.join(self.tmpdir, "blog")
        os.makedirs(self.app_dir)
        # Create __init__.py so it looks like a package
        with open(os.path.join(self.app_dir, "__init__.py"), "w") as f:
            f.write("")
        # Create templates dir
        os.makedirs(os.path.join(self.app_dir, "templates", "blog"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_generates_views_py(self):
        fields = parse_field_defs(["title:string", "body:text"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        views_path = os.path.join(self.app_dir, "views.py")
        self.assertTrue(os.path.exists(views_path))
        content = Path(views_path).read_text()
        self.assertIn("class PostListView(LiveView):", content)
        self.assertIn("def mount(self, request, **kwargs):", content)
        self.assertIn("@event_handler()", content)
        self.assertIn("def search(self,", content)
        self.assertIn("def show(self,", content)
        self.assertIn("def create(self,", content)
        self.assertIn("def delete(self,", content)
        self.assertIn("def update(self,", content)

    def test_generates_urls_py_with_live_session(self):
        fields = parse_field_defs(["title:string"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        urls_path = os.path.join(self.app_dir, "urls.py")
        self.assertTrue(os.path.exists(urls_path))
        content = Path(urls_path).read_text()
        self.assertIn("live_session(", content)
        self.assertIn("from djust.routing import live_session", content)
        # Ensure path() is inside live_session(), not standalone
        self.assertIn("*live_session(", content)

    def test_generates_template_with_django_syntax(self):
        fields = parse_field_defs(["title:string", "active:boolean"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        tpl_path = os.path.join(self.app_dir, "templates", "blog", "post_list.html")
        self.assertTrue(os.path.exists(tpl_path))
        content = Path(tpl_path).read_text()
        self.assertIn("dj-root", content)
        self.assertIn("dj-view=", content)
        self.assertIn("dj-input=", content)
        self.assertIn("dj-click=", content)
        self.assertIn("dj-submit=", content)
        # Must NOT contain Handlebars syntax
        self.assertNotIn("{{#if", content)
        self.assertNotIn("{{/if}}", content)
        # Must use Django template syntax for conditionals
        self.assertIn("{% if", content)
        self.assertIn("{% endif %}", content)

    def test_generates_test_file(self):
        fields = parse_field_defs(["title:string"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        test_path = os.path.join(self.app_dir, "tests.py")
        self.assertTrue(os.path.exists(test_path))
        content = Path(test_path).read_text()
        self.assertIn("PostListView", content)

    def test_no_tests_flag_skips_test_file(self):
        fields = parse_field_defs(["title:string"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={"no_tests": True},
        )
        test_path = os.path.join(self.app_dir, "tests.py")
        self.assertFalse(os.path.exists(test_path))

    def test_dry_run_writes_nothing(self):
        fields = parse_field_defs(["title:string"])
        result = generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={"dry_run": True},
        )
        views_path = os.path.join(self.app_dir, "views.py")
        urls_path = os.path.join(self.app_dir, "urls.py")
        self.assertFalse(os.path.exists(views_path))
        self.assertFalse(os.path.exists(urls_path))
        # Should return the files that would be created
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_force_overwrites_existing(self):
        views_path = os.path.join(self.app_dir, "views.py")
        Path(views_path).write_text("# existing content")
        fields = parse_field_defs(["title:string"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={"force": True},
        )
        content = Path(views_path).read_text()
        self.assertIn("PostListView", content)
        self.assertNotIn("# existing content", content)

    def test_error_files_exist_without_force(self):
        views_path = os.path.join(self.app_dir, "views.py")
        Path(views_path).write_text("# existing content")
        fields = parse_field_defs(["title:string"])
        with self.assertRaises(FileExistsError):
            generate_liveview(
                app_name="blog",
                model_name="Post",
                fields=fields,
                base_dir=self.tmpdir,
                options={},
            )

    def test_error_invalid_model_name(self):
        fields = parse_field_defs(["title:string"])
        with self.assertRaises(ValueError):
            generate_liveview(
                app_name="blog",
                model_name="blog_post",
                fields=fields,
                base_dir=self.tmpdir,
                options={},
            )

    def test_error_app_directory_does_not_exist(self):
        fields = parse_field_defs(["title:string"])
        with self.assertRaises(FileNotFoundError):
            generate_liveview(
                app_name="nonexistent_app",
                model_name="Post",
                fields=fields,
                base_dir=self.tmpdir,
                options={},
            )

    def test_api_mode_generates_json_view(self):
        fields = parse_field_defs(["title:string", "body:text"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={"api": True},
        )
        views_path = os.path.join(self.app_dir, "views.py")
        content = Path(views_path).read_text()
        self.assertIn("render_json", content)
        # API mode should not generate HTML template
        tpl_path = os.path.join(self.app_dir, "templates", "blog", "post_list.html")
        self.assertFalse(os.path.exists(tpl_path))

    def test_no_fields_generates_minimal_view(self):
        fields = parse_field_defs([])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        views_path = os.path.join(self.app_dir, "views.py")
        content = Path(views_path).read_text()
        self.assertIn("class PostListView(LiveView):", content)


class TestGeneratedTemplateValidity(TestCase):
    """Verify generated code uses correct Django/djust patterns."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.app_dir = os.path.join(self.tmpdir, "blog")
        os.makedirs(self.app_dir)
        with open(os.path.join(self.app_dir, "__init__.py"), "w") as f:
            f.write("")
        os.makedirs(os.path.join(self.app_dir, "templates", "blog"), exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_template_uses_django_if_not_handlebars(self):
        fields = parse_field_defs(["title:string", "active:boolean"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        tpl_path = os.path.join(self.app_dir, "templates", "blog", "post_list.html")
        content = Path(tpl_path).read_text()
        # No Handlebars
        self.assertNotIn("{{#if", content)
        self.assertNotIn("{{/if}}", content)
        self.assertNotIn("{{else}}", content)
        # Django syntax present
        self.assertIn("{% if", content)

    def test_views_use_q_objects_for_search(self):
        fields = parse_field_defs(["title:string", "body:text"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        views_path = os.path.join(self.app_dir, "views.py")
        content = Path(views_path).read_text()
        self.assertIn("from django.db.models import Q", content)
        self.assertIn("Q(title__icontains=self.search_query)", content)
        self.assertIn("|", content)

    def test_urls_use_live_session(self):
        fields = parse_field_defs(["title:string"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        urls_path = os.path.join(self.app_dir, "urls.py")
        content = Path(urls_path).read_text()
        self.assertIn("live_session(", content)
        self.assertIn("from djust.routing import live_session", content)

    def test_boolean_show_uses_django_if(self):
        """Bug #2 fix: boolean display must use {% if %} not {{#if}}."""
        fields = parse_field_defs(["active:boolean"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        tpl_path = os.path.join(self.app_dir, "templates", "blog", "post_list.html")
        content = Path(tpl_path).read_text()
        # Boolean display should use Django template tags
        self.assertIn("{% if selected.active %}", content)
        self.assertNotIn("{{#if selected.active}}", content)

    def test_model_name_not_none_in_create(self):
        """Bug #3 fix: model_name must not be None in generated create body."""
        fields = parse_field_defs(["title:string"])
        generate_liveview(
            app_name="blog",
            model_name="Post",
            fields=fields,
            base_dir=self.tmpdir,
            options={},
        )
        views_path = os.path.join(self.app_dir, "views.py")
        content = Path(views_path).read_text()
        self.assertIn("Post.objects.create(", content)
        self.assertNotIn("None.objects.create(", content)


class TestGenLiveEndToEnd(TestCase):
    """End-to-end validation of the gen_live module."""

    def test_module_imports_without_error(self):
        """Module loads cleanly."""
        from importlib import import_module

        mod = import_module("djust.scaffolding.gen_live")
        self.assertTrue(hasattr(mod, "generate_liveview"))
        mod2 = import_module("djust.scaffolding.gen_live_templates")
        self.assertTrue(hasattr(mod2, "__name__"))

    def test_parse_all_documented_types(self):
        all_types = [
            "title:string",
            "body:text",
            "count:integer",
            "price:float",
            "amount:decimal",
            "active:boolean",
            "birth_date:date",
            "created_at:datetime",
            "contact:email",
            "website:url",
            "slug:slug",
            "author:fk:User",
        ]
        result = parse_field_defs(all_types)
        self.assertEqual(len(result), 12)
        types = [f["type"] for f in result]
        self.assertIn("fk", types)
        fk_field = [f for f in result if f["type"] == "fk"][0]
        self.assertEqual(fk_field["related_model"], "User")

    def test_generated_template_no_handlebars(self):
        """Ensure template output contains valid Django template syntax."""
        tmpdir = tempfile.mkdtemp()
        try:
            app_dir = os.path.join(tmpdir, "blog")
            os.makedirs(app_dir)
            with open(os.path.join(app_dir, "__init__.py"), "w") as f:
                f.write("")
            os.makedirs(os.path.join(app_dir, "templates", "blog"), exist_ok=True)
            fields = parse_field_defs(["title:string", "active:boolean"])
            generate_liveview(
                app_name="blog",
                model_name="Post",
                fields=fields,
                base_dir=tmpdir,
                options={},
            )
            tpl_path = os.path.join(app_dir, "templates", "blog", "post_list.html")
            content = Path(tpl_path).read_text()
            # Validate no Handlebars syntax anywhere
            self.assertFalse(
                re.search(r"\{\{#if\b", content),
                "Found Handlebars {{#if in generated template",
            )
            self.assertFalse(
                re.search(r"\{\{/if\}\}", content),
                "Found Handlebars {{/if}} in generated template",
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
