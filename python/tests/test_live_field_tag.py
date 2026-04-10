"""
Tests for the standalone {% live_field %} template tag.

Covers: text input, textarea, select with choices, password, custom attrs,
default CSS class fallback, CSS class from djust config, and the view-based
backward-compatible dispatch.
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from django.template import Template, Context
from django.utils.safestring import mark_safe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def render_tag(tag_content: str, context_dict: dict | None = None) -> str:
    """Render a template snippet that loads live_tags and uses the given tag."""
    tpl = Template("{% load live_tags %}" + tag_content)
    ctx = Context(context_dict or {})
    return tpl.render(ctx).strip()


class FakeView:
    """Minimal stand-in for a FormMixin view used by the view-based live_field."""

    def __init__(self, html="<input>"):
        self._html = html

    def as_live_field(self, field_name, **kwargs):
        return mark_safe(self._html)


# ---------------------------------------------------------------------------
# Text input
# ---------------------------------------------------------------------------


class TestTextInput:
    def test_basic_text_input(self):
        html = render_tag(
            '{% live_field "text" handler="set_name" value="Alice" placeholder="Name..." %}'
        )
        assert "<input " in html
        assert 'type="text"' in html
        assert 'dj-input="set_name"' in html
        assert 'value="Alice"' in html
        assert 'placeholder="Name..."' in html

    def test_text_input_escapes_value(self):
        html = render_tag(
            '{% live_field "text" handler="h" value=val %}',
            {"val": "<script>alert(1)</script>"},
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_text_input_empty_value_omits_attr(self):
        html = render_tag('{% live_field "text" handler="h" %}')
        # No value="" attribute when value is empty/unset
        assert "value=" not in html


# ---------------------------------------------------------------------------
# Password
# ---------------------------------------------------------------------------


class TestPasswordInput:
    def test_password_type(self):
        html = render_tag('{% live_field "password" handler="set_pw" placeholder="Enter..." %}')
        assert 'type="password"' in html
        assert 'dj-input="set_pw"' in html
        assert 'placeholder="Enter..."' in html


# ---------------------------------------------------------------------------
# Textarea
# ---------------------------------------------------------------------------


class TestTextarea:
    def test_textarea_rendering(self):
        html = render_tag('{% live_field "textarea" handler="set_body" value="Hello" rows="4" %}')
        assert "<textarea " in html
        assert "</textarea>" in html
        assert 'dj-input="set_body"' in html
        assert 'rows="4"' in html
        assert ">Hello</textarea>" in html

    def test_textarea_escapes_value(self):
        html = render_tag(
            '{% live_field "textarea" handler="h" value=val %}',
            {"val": "<b>bold</b>"},
        )
        assert "<b>" not in html
        assert "&lt;b&gt;bold&lt;/b&gt;" in html

    def test_textarea_empty_value(self):
        html = render_tag('{% live_field "textarea" handler="h" %}')
        assert "></textarea>" in html


# ---------------------------------------------------------------------------
# Select
# ---------------------------------------------------------------------------


class TestSelect:
    def test_select_with_choices(self):
        choices = [("GENERAL", "General"), ("PHONE", "Phone Call")]
        html = render_tag(
            '{% live_field "select" handler="set_type" value=val choices=choices %}',
            {"val": "GENERAL", "choices": choices},
        )
        assert "<select " in html
        assert "</select>" in html
        assert 'dj-change="set_type"' in html
        assert 'value="GENERAL" selected' in html
        assert ">General</option>" in html
        assert 'value="PHONE"' in html
        assert ">Phone Call</option>" in html
        # PHONE should NOT be selected
        assert 'value="PHONE" selected' not in html

    def test_select_escapes_labels(self):
        choices = [("x", '<img src=x onerror="alert(1)">')]
        html = render_tag(
            '{% live_field "select" handler="h" choices=choices %}',
            {"choices": choices},
        )
        assert "<img" not in html
        assert "&lt;img" in html

    def test_select_no_choices(self):
        html = render_tag('{% live_field "select" handler="h" %}')
        assert "<select " in html
        assert "</select>" in html


# ---------------------------------------------------------------------------
# Extra kwargs pass-through
# ---------------------------------------------------------------------------


class TestExtraAttrs:
    def test_custom_id(self):
        html = render_tag('{% live_field "text" handler="h" id="my-field" %}')
        assert 'id="my-field"' in html

    def test_required_attr(self):
        html = render_tag('{% live_field "text" handler="h" required="" %}')
        assert 'required=""' in html

    def test_multiple_custom_attrs(self):
        html = render_tag(
            '{% live_field "email" handler="h" id="email-input" autocomplete="off" %}'
        )
        assert 'id="email-input"' in html
        assert 'autocomplete="off"' in html


# ---------------------------------------------------------------------------
# CSS class
# ---------------------------------------------------------------------------


class TestCSSClass:
    def test_default_css_class_fallback(self):
        """When djust config is unavailable, falls back to 'form-input'."""
        # We mock the import to simulate config not being available
        import djust.templatetags.live_tags as mod

        original = mod._get_field_css_class

        def _mock():
            return "form-input"

        mod._get_field_css_class = _mock
        try:
            html = render_tag('{% live_field "text" handler="h" %}')
            assert 'class="form-input"' in html
        finally:
            mod._get_field_css_class = original

    def test_css_class_from_config(self):
        """When djust config returns a class, it is used."""
        import djust.templatetags.live_tags as mod

        original = mod._get_field_css_class

        def _mock():
            return "form-control"

        mod._get_field_css_class = _mock
        try:
            html = render_tag('{% live_field "text" handler="h" %}')
            assert 'class="form-control"' in html
        finally:
            mod._get_field_css_class = original

    def test_css_class_on_textarea(self):
        html = render_tag('{% live_field "textarea" handler="h" %}')
        assert 'class="' in html

    def test_css_class_on_select(self):
        html = render_tag('{% live_field "select" handler="h" %}')
        assert 'class="' in html


# ---------------------------------------------------------------------------
# View-based backward compatibility
# ---------------------------------------------------------------------------


class TestViewBasedDispatch:
    def test_view_based_still_works(self):
        """The (view, field_name) form should still dispatch to as_live_field()."""
        view = FakeView(html='<input type="email" name="email">')
        html = render_tag(
            "{% live_field view 'email' %}",
            {"view": view},
        )
        assert 'type="email"' in html
        assert 'name="email"' in html

    def test_view_without_method(self):
        html = render_tag(
            "{% live_field obj 'field' %}",
            {"obj": object()},
        )
        assert "ERROR" in html


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_no_handler(self):
        """Field without handler should still render, just no dj-input."""
        html = render_tag('{% live_field "text" value="x" %}')
        assert "<input " in html
        assert "dj-input" not in html

    def test_none_value_treated_as_empty(self):
        html = render_tag(
            '{% live_field "text" handler="h" value=val %}',
            {"val": None},
        )
        assert "value=" not in html

    def test_select_none_value(self):
        choices = [("a", "A")]
        html = render_tag(
            '{% live_field "select" handler="h" value=val choices=choices %}',
            {"val": None, "choices": choices},
        )
        # None won't match "a", so no selected
        assert "selected" not in html
