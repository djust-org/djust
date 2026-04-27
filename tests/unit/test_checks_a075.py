"""Tests for ``djust.A075`` — sticky+lazy collision check (closes #1146).

A075 is a startup-time guard against ``{% live_render sticky=True
lazy=True %}`` in template files. The two kwargs are mutually exclusive
and already raise ``TemplateSyntaxError`` at tag-eval time (in
``live_tags.live_render``); this check surfaces the same condition
during ``manage.py check`` so the misuse is caught before any request
hits the server.

Mirrors the shape of the A070/A071 scanner: walks template directories
via ``_get_template_dirs`` + ``_iter_template_files``, scans each file
with a regex, strips ``{% verbatim %}`` regions first to avoid
false-positives on documentation pages.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Integration tests via ``check_templates``
# ---------------------------------------------------------------------------


class TestA075StickyLazyCollision:
    """A075 fires on real ``sticky=True lazy=True`` collisions."""

    def test_a075_fires_on_collision(self, tmp_path, settings):
        """Both kwargs truthy → one A075 warning per occurrence."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            "{% load live_tags %}\n"
            '<div>{% live_render "myapp.views.Foo" sticky=True lazy=True %}</div>\n'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 1
        # Message includes template path and line number.
        assert "bad.html" in a075[0].msg
        assert ":2" in a075[0].msg

    def test_a075_silent_on_sticky_only(self, tmp_path, settings):
        """``sticky=True`` alone is fine — no warning."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text(
            '{% load live_tags %}\n<div>{% live_render "myapp.views.Foo" sticky=True %}</div>\n'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 0

    def test_a075_silent_on_lazy_only(self, tmp_path, settings):
        """``lazy=True`` alone is fine — no warning."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "ok.html").write_text(
            '{% load live_tags %}\n<div>{% live_render "myapp.views.Foo" lazy=True %}</div>\n'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 0

    def test_a075_silent_on_no_live_render(self, tmp_path, settings):
        """A template with neither tag is silent."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "plain.html").write_text("<div>hello world</div>\n")
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 0

    def test_a075_does_not_fire_inside_verbatim(self, tmp_path, settings):
        """``{% verbatim %}`` regions are skipped — docs pages that show the
        anti-pattern as an example don't false-positive."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "docs.html").write_text(
            "{% load live_tags %}\n"
            "<h2>Don't do this:</h2>\n"
            "{% verbatim %}\n"
            '{% live_render "myapp.views.Foo" sticky=True lazy=True %}\n'
            "{% endverbatim %}\n"
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 0

    def test_a075_real_call_alongside_verbatim_example(self, tmp_path, settings):
        """A real collision in the same file as a verbatim example still
        fires — the verbatim suppression is per-region, not per-file."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "mixed.html").write_text(
            "{% load live_tags %}\n"
            "{% verbatim %}\n"
            '{% live_render "myapp.views.Foo" sticky=True lazy=True %}\n'  # docs example
            "{% endverbatim %}\n"
            '{% live_render "myapp.views.Bar" sticky=True lazy=True %}\n'  # real bug
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 1
        # The flagged line is the real call (line 5), not the verbatim one.
        assert ":5" in a075[0].msg

    def test_a075_suppressed_via_config(self, tmp_path, settings):
        """``DJUST_CONFIG['suppress_checks'] = ['A075']`` silences the
        warning even on real collisions."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "bad.html").write_text(
            '{% load live_tags %}\n{% live_render "myapp.views.Foo" sticky=True lazy=True %}\n'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]
        settings.DJUST_CONFIG = {"suppress_checks": ["A075"]}

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 0

    def test_a075_works_with_string_truthy_kwargs(self, tmp_path, settings):
        """Templates may use ``sticky="yes"`` / ``lazy="visible"`` rather
        than ``=True`` literals — both are truthy at render time and
        the check should flag the collision."""
        tpl_dir = tmp_path / "templates"
        tpl_dir.mkdir()
        (tpl_dir / "string_form.html").write_text(
            '{% load live_tags %}\n{% live_render "myapp.views.Foo" sticky=True lazy="visible" %}\n'
        )
        settings.TEMPLATES = [
            {
                "DIRS": [str(tpl_dir)],
                "BACKEND": "django.template.backends.django.DjangoTemplateBackend",
            }
        ]

        from djust.checks import check_templates

        errors = check_templates(None)
        a075 = [e for e in errors if e.id == "djust.A075"]
        assert len(a075) == 1
        assert "string_form.html" in a075[0].msg
