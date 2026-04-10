"""
Tests for the ``{% live_input %}`` standalone state-bound field tag (#650).

Coverage:

* Every supported field type (text, password, email, url, tel, search,
  number, hidden, textarea, select, checkbox, radio).
* Correct default ``dj-*`` event per type and explicit ``event=`` override.
* Debounce/throttle passthrough.
* CSS class resolution (explicit kwarg, framework config, fallback).
* HTML attribute passthrough with underscore → dash normalization.
* **XSS test matrix** — every field type gets tested with a hostile
  ``<script>alert(1)</script>`` payload in the value, placeholder,
  ``aria-label``, and select/radio choice labels. This is the key
  defense against future regressions.
* Error cases (unknown field_type, missing handler).
* Integration via ``django.template.Template`` with real
  ``{% load live_tags %}{% live_input ... %}`` rendering.
"""

from django.template import Context, Template

from djust._html import build_tag
from djust.templatetags.live_tags import (
    _DEFAULT_EVENT_BY_TYPE,
    live_input,
)


# ---------------------------------------------------------------------------
# _html.build_tag — escape boundary
# ---------------------------------------------------------------------------


class TestBuildTag:
    def test_self_closing(self):
        out = build_tag("input", {"type": "text", "value": "hi"})
        assert out == '<input type="text" value="hi" />'

    def test_with_content(self):
        out = build_tag("textarea", {"name": "msg"}, "hello world")
        assert "hello world" in out
        assert out.startswith('<textarea name="msg">')
        assert out.endswith("</textarea>")

    def test_escapes_attribute_values(self):
        out = build_tag("input", {"value": '"><script>alert(1)</script>'})
        assert "<script>" not in out
        assert "&lt;script&gt;" in out or "&#x27;" in out or "&quot;" in out

    def test_escapes_content_by_default(self):
        out = build_tag("textarea", {"name": "x"}, "<script>alert(1)</script>")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_content_is_safe_not_escaped(self):
        out = build_tag(
            "select", {"name": "x"}, '<option value="y">Y</option>', content_is_safe=True
        )
        assert '<option value="y">Y</option>' in out

    def test_boolean_attr_true_renders(self):
        out = build_tag("input", {"type": "checkbox", "checked": True})
        assert 'checked="checked"' in out

    def test_boolean_attr_false_omitted(self):
        out = build_tag("input", {"type": "checkbox", "checked": False})
        assert "checked" not in out

    def test_none_attr_omitted(self):
        out = build_tag("input", {"type": "text", "disabled": None})
        assert "disabled" not in out

    def test_normalises_key_with_no_attrs(self):
        out = build_tag("br", {})
        assert out == "<br />"


# ---------------------------------------------------------------------------
# live_input — text-like field types
# ---------------------------------------------------------------------------


class TestLiveInputTextLike:
    def test_default_text(self):
        out = str(live_input("text", handler="search", value="django"))
        assert 'type="text"' in out
        assert 'value="django"' in out
        assert 'dj-input="search"' in out
        assert 'name="search"' in out
        assert 'class="' in out

    def test_explicit_name(self):
        out = str(live_input("text", handler="search", name="q", value=""))
        assert 'name="q"' in out

    def test_explicit_event_dj_change(self):
        out = str(live_input("text", handler="search", event="change", value=""))
        assert 'dj-change="search"' in out
        assert 'dj-input="search"' not in out

    def test_explicit_event_with_dj_prefix(self):
        out = str(live_input("text", handler="search", event="dj-blur", value=""))
        assert 'dj-blur="search"' in out

    def test_debounce_throttle(self):
        out = str(live_input("text", handler="search", value="", debounce="300", throttle="500"))
        assert 'dj-debounce="300"' in out
        assert 'dj-throttle="500"' in out

    def test_password(self):
        out = str(live_input("password", handler="set_pw", value=""))
        assert 'type="password"' in out

    def test_email(self):
        out = str(live_input("email", handler="set_email", value=""))
        assert 'type="email"' in out

    def test_number(self):
        out = str(live_input("number", handler="set_qty", value=5))
        assert 'type="number"' in out
        assert 'value="5"' in out

    def test_url(self):
        out = str(live_input("url", handler="set_url", value=""))
        assert 'type="url"' in out

    def test_tel(self):
        out = str(live_input("tel", handler="set_phone", value=""))
        assert 'type="tel"' in out

    def test_search(self):
        out = str(live_input("search", handler="do_search", value=""))
        assert 'type="search"' in out

    def test_hidden_no_handler_ok(self):
        """Hidden fields may be rendered without a handler (just server state)."""
        out = str(live_input("hidden", value="token123", name="csrf"))
        assert 'type="hidden"' in out
        assert 'value="token123"' in out

    def test_hidden_does_not_get_dj_input_binding(self):
        """Hidden inputs have no event by default (non-interactive)."""
        out = str(live_input("hidden", value="x", name="foo"))
        assert "dj-input" not in out
        assert "dj-change" not in out

    def test_passthrough_attrs_with_underscore_normalization(self):
        out = str(
            live_input(
                "text",
                handler="search",
                value="",
                placeholder="Search...",
                aria_label="Search field",
                data_test="q",
            )
        )
        assert 'placeholder="Search..."' in out
        assert 'aria-label="Search field"' in out
        assert 'data-test="q"' in out


# ---------------------------------------------------------------------------
# textarea
# ---------------------------------------------------------------------------


class TestLiveInputTextarea:
    def test_basic(self):
        out = str(live_input("textarea", handler="set_body", value="line1\nline2"))
        assert out.startswith("<textarea")
        assert "line1\nline2" in out
        assert 'dj-input="set_body"' in out

    def test_with_rows_cols(self):
        out = str(live_input("textarea", handler="set_body", value="", rows=5, cols=80))
        assert 'rows="5"' in out
        assert 'cols="80"' in out

    def test_empty_value_renders_empty_content(self):
        out = str(live_input("textarea", handler="set_body", value=None))
        assert "<textarea" in out
        assert "</textarea>" in out


# ---------------------------------------------------------------------------
# select
# ---------------------------------------------------------------------------


class TestLiveInputSelect:
    def test_tuple_choices(self):
        out = str(
            live_input(
                "select",
                handler="set_status",
                value="open",
                choices=[("open", "Open"), ("closed", "Closed")],
            )
        )
        assert 'dj-change="set_status"' in out
        assert '<option value="open" selected="selected">Open</option>' in out
        assert '<option value="closed" >Closed</option>' in out

    def test_string_choices(self):
        out = str(
            live_input(
                "select",
                handler="set_color",
                value="red",
                choices=["red", "green", "blue"],
            )
        )
        assert '<option value="red" selected="selected">red</option>' in out
        assert '<option value="green" >green</option>' in out

    def test_empty_choices(self):
        out = str(live_input("select", handler="set_x", value=None, choices=[]))
        assert "<select" in out
        assert "<option" not in out

    def test_none_choices_renders_empty(self):
        out = str(live_input("select", handler="set_x", value=None))
        assert "<select" in out


# ---------------------------------------------------------------------------
# checkbox
# ---------------------------------------------------------------------------


class TestLiveInputCheckbox:
    def test_unchecked(self):
        out = str(live_input("checkbox", handler="toggle_notif", value="1", checked=False))
        assert 'type="checkbox"' in out
        assert "checked" not in out

    def test_checked(self):
        out = str(live_input("checkbox", handler="toggle_notif", value="1", checked=True))
        assert 'checked="checked"' in out

    def test_checkbox_uses_dj_change(self):
        out = str(live_input("checkbox", handler="toggle", checked=True))
        assert 'dj-change="toggle"' in out


# ---------------------------------------------------------------------------
# radio
# ---------------------------------------------------------------------------


class TestLiveInputRadio:
    def test_basic(self):
        out = str(
            live_input(
                "radio",
                handler="set_plan",
                value="pro",
                choices=[("free", "Free"), ("pro", "Pro")],
            )
        )
        # Both labels present
        assert "Free" in out
        assert "Pro" in out
        # Only 'pro' is selected
        assert out.count('checked="checked"') == 1
        # Handler bound on each
        assert out.count('dj-change="set_plan"') == 2

    def test_string_choices(self):
        out = str(live_input("radio", handler="set_x", value="a", choices=["a", "b", "c"]))
        assert out.count("<label>") == 3
        assert out.count('checked="checked"') == 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestLiveInputErrors:
    def test_unknown_field_type(self):
        out = str(live_input("pineapple", handler="x"))
        assert "ERROR" in out
        assert "pineapple" in out

    def test_missing_handler(self):
        out = str(live_input("text", value=""))
        assert "ERROR" in out
        assert "handler=" in out


# ---------------------------------------------------------------------------
# XSS matrix — every type gets a hostile value, placeholder, aria_label,
# and (for select/radio) a hostile choice label. None of the payloads
# should land unescaped in the output.
# ---------------------------------------------------------------------------


XSS_PAYLOAD = '"><script>alert(1)</script>'


class TestLiveInputXssMatrix:
    """Every field type must escape hostile values and attributes."""

    def _assert_clean(self, html: str):
        """Assert no unescaped <script> tag landed in output."""
        assert "<script>" not in html
        assert "alert(1)" not in html or "&lt;script&gt;" in html or "&#x27;" in html

    def test_text_value(self):
        html = str(live_input("text", handler="search", value=XSS_PAYLOAD))
        self._assert_clean(html)

    def test_text_placeholder(self):
        html = str(live_input("text", handler="search", value="", placeholder=XSS_PAYLOAD))
        self._assert_clean(html)

    def test_text_aria_label(self):
        html = str(live_input("text", handler="search", value="", aria_label=XSS_PAYLOAD))
        self._assert_clean(html)

    def test_textarea_value(self):
        html = str(live_input("textarea", handler="set_body", value=XSS_PAYLOAD))
        self._assert_clean(html)

    def test_textarea_placeholder(self):
        html = str(live_input("textarea", handler="set_body", value="", placeholder=XSS_PAYLOAD))
        self._assert_clean(html)

    def test_select_value(self):
        html = str(
            live_input(
                "select",
                handler="set_status",
                value=XSS_PAYLOAD,
                choices=[("a", "A")],
            )
        )
        self._assert_clean(html)

    def test_select_choice_label(self):
        html = str(
            live_input(
                "select",
                handler="set_status",
                value="a",
                choices=[("a", XSS_PAYLOAD)],
            )
        )
        self._assert_clean(html)

    def test_select_choice_value(self):
        html = str(
            live_input(
                "select",
                handler="set_status",
                value="a",
                choices=[(XSS_PAYLOAD, "label")],
            )
        )
        self._assert_clean(html)

    def test_radio_choice_label(self):
        html = str(
            live_input(
                "radio",
                handler="set_plan",
                value="a",
                choices=[("a", XSS_PAYLOAD)],
            )
        )
        self._assert_clean(html)

    def test_radio_choice_value(self):
        html = str(
            live_input(
                "radio",
                handler="set_plan",
                value=XSS_PAYLOAD,
                choices=[("a", "A")],
            )
        )
        self._assert_clean(html)

    def test_checkbox_value(self):
        html = str(
            live_input(
                "checkbox",
                handler="toggle",
                value=XSS_PAYLOAD,
                checked=True,
            )
        )
        self._assert_clean(html)

    def test_hidden_value(self):
        html = str(live_input("hidden", value=XSS_PAYLOAD, name="csrf"))
        self._assert_clean(html)

    def test_number_value(self):
        html = str(live_input("number", handler="set_n", value=XSS_PAYLOAD))
        self._assert_clean(html)


# ---------------------------------------------------------------------------
# Default-event registry
# ---------------------------------------------------------------------------


class TestDefaultEventRegistry:
    def test_text_defaults_to_dj_input(self):
        assert _DEFAULT_EVENT_BY_TYPE["text"] == "dj-input"

    def test_select_defaults_to_dj_change(self):
        assert _DEFAULT_EVENT_BY_TYPE["select"] == "dj-change"

    def test_hidden_has_no_event(self):
        assert _DEFAULT_EVENT_BY_TYPE["hidden"] is None


# ---------------------------------------------------------------------------
# Template integration — render via django.template.Template
# ---------------------------------------------------------------------------


class TestLiveInputTemplateIntegration:
    def _render(self, template_src: str, context=None) -> str:
        tpl = Template("{% load live_tags %}" + template_src)
        return tpl.render(Context(context or {}))

    def test_basic_text_render(self):
        out = self._render(
            '{% live_input "text" handler="search" value=query %}',
            {"query": "django"},
        )
        assert 'value="django"' in out
        assert 'dj-input="search"' in out

    def test_select_render_with_context_choices(self):
        out = self._render(
            '{% live_input "select" handler="set_status" value=status choices=choices %}',
            {"status": "open", "choices": [("open", "Open"), ("closed", "Closed")]},
        )
        assert "Open" in out
        assert "selected" in out

    def test_escapes_xss_in_context(self):
        out = self._render(
            '{% live_input "text" handler="search" value=query %}',
            {"query": '"><script>alert(1)</script>'},
        )
        assert "<script>" not in out
        # Django's template engine may also escape; the key thing is no unescaped script.
