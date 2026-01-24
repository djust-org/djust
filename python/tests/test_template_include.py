"""
Test for template {% include %} tag support (GitHub Issue #39).

This test verifies that {% include %} works correctly when using
the DjustTemplateBackend with the Rust rendering engine.

The issue is that when rendering includes, the template loader isn't being
passed properly to the Rust renderer.
"""

import os

import pytest

# Django must be set up before importing djust modules
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEBUG=True,
        DATABASES={},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": True,
            }
        ],
    )
    django.setup()


class TestTemplateInclude:
    """Tests for {% include %} tag support."""

    def test_basic_include(self, tmp_path):
        """
        Test that basic {% include 'partial.html' %} works.

        This is the core issue from GitHub #39 - when using include,
        the template loader is not configured and we get:
        "Template loader not configured. Cannot include template: partial.html"
        """
        from djust.template_backend import DjustTemplateBackend

        # Create template directory structure
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Create the partial template
        (templates_dir / "_partial.html").write_text("<span>Hello from partial</span>")

        # Create the main template that includes the partial
        main_template = "Before{% include '_partial.html' %}After"

        # Initialize the backend with our temp template directory
        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [str(templates_dir)],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        # Create template from string
        template = backend.from_string(main_template)

        # This should work but currently fails with:
        # "Template loader not configured. Cannot include template: _partial.html"
        result = template.render({})

        assert "Before" in result
        assert "Hello from partial" in result
        assert "After" in result

    def test_include_with_context_variable(self, tmp_path):
        """
        Test that {% include %} can access context variables.
        """
        from djust.template_backend import DjustTemplateBackend

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Partial uses a variable from context
        (templates_dir / "_greeting.html").write_text("<p>Hello, {{ name }}!</p>")

        main_template = "{% include '_greeting.html' %}"

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [str(templates_dir)],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string(main_template)
        result = template.render({"name": "World"})

        assert "Hello, World!" in result

    def test_include_with_with_clause(self, tmp_path):
        """
        Test that {% include 'partial.html' with var=value %} works.

        From the issue example:
        {% for post in posts %}
            {% include "blog/_post_card.html" with post=post %}
        {% endfor %}
        """
        from djust.template_backend import DjustTemplateBackend

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Create subdirectory for organized templates
        (templates_dir / "blog").mkdir()
        (templates_dir / "blog" / "_post_card.html").write_text(
            "<article><h3>{{ post.title }}</h3></article>"
        )

        main_template = """{% for item in posts %}{% include "blog/_post_card.html" with post=item %}{% endfor %}"""

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [str(templates_dir)],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string(main_template)
        result = template.render(
            {
                "posts": [
                    {"title": "First Post"},
                    {"title": "Second Post"},
                ]
            }
        )

        assert "First Post" in result
        assert "Second Post" in result

    def test_include_with_only_keyword(self, tmp_path):
        """
        Test that {% include 'partial.html' with var=value only %} works.

        The 'only' keyword should prevent parent context from being passed.
        """
        from djust.template_backend import DjustTemplateBackend

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Partial that tries to access both passed and parent context
        (templates_dir / "_limited.html").write_text("{{ passed }}|{{ parent }}")

        main_template = "{% include '_limited.html' with passed=value only %}"

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [str(templates_dir)],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string(main_template)
        result = template.render(
            {
                "value": "PASSED",
                "parent": "SHOULD_NOT_APPEAR",
            }
        )

        # With 'only', parent should not be accessible
        assert "PASSED" in result
        assert "SHOULD_NOT_APPEAR" not in result

    def test_include_file_loaded_via_get_template(self, tmp_path):
        """
        Test that includes work when the main template is loaded via get_template().

        This tests the path from get_template() -> render().
        """
        from djust.template_backend import DjustTemplateBackend

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        # Partial
        (templates_dir / "_footer.html").write_text("<footer>Copyright 2024</footer>")

        # Main template file
        (templates_dir / "page.html").write_text("<body>Content{% include '_footer.html' %}</body>")

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [str(templates_dir)],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        # Load template by name (this is how Django views work)
        template = backend.get_template("page.html")
        result = template.render({})

        assert "<body>Content" in result
        assert "Copyright 2024" in result


class TestTemplateIncludeErrorCases:
    """Test error handling for include edge cases."""

    def test_include_missing_template_error(self, tmp_path):
        """
        Test that missing included template gives a clear error.
        """
        from djust.template_backend import DjustTemplateBackend

        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()

        main_template = "{% include 'nonexistent.html' %}"

        backend = DjustTemplateBackend(
            {
                "NAME": "djust",
                "DIRS": [str(templates_dir)],
                "APP_DIRS": False,
                "OPTIONS": {},
            }
        )

        template = backend.from_string(main_template)

        with pytest.raises(Exception) as exc_info:
            template.render({})

        # Should give a meaningful error about the template not being found
        error_msg = str(exc_info.value)
        assert "nonexistent.html" in error_msg
        # Should NOT say "Template loader not configured"
        assert "loader not configured" not in error_msg.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
