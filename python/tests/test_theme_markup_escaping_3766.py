"""``djust_theming`` builds no markup out of an unescaped value (#3766, review of #3775).

``theme_icon`` was the reported case. Its sweep found the same class where the guard of the
first fix could not see it, because the value never sat inside ``mark_safe(<f-string>)``:

* ``theme_form`` (``templatetags/theme_form_tags.py``) appended ``f'<label for="{field_id}" ...'``
  lines (``field_id`` is ``BoundField.id_for_label``, built from the FIELD NAME, the form
  ``prefix`` and ``auto_id``) and the configured ``css_prefix`` to a list, then
  ``mark_safe("\\n".join(lines))``. A form whose field is named ``a" onfocus="alert(1)" x="``
  printed ``<label for="id_a" onfocus="alert(1)" x="" class="theme-label">``. Fields are normally
  developer-set, but form builders and dynamic forms exist.
* ``theme_form_errors`` printed ``css_prefix`` the same way.
* ``context_processors._render_theme_switcher`` (the ``{{ theme_switcher }}`` chunk, marked safe by
  ``LazyThemeHTML``) printed ``preset["name"]`` / ``preset["display_name"]`` into
  ``<option value="...">``.
* ``theme_components._passthrough_attrs`` joined ``f'{name}="{escape(value)}"'`` parts: ``name`` is
  validated by ``_ATTR_NAME_RE`` first, so it was inert, but it was an unescaped value in a string
  built under ``mark_safe``. Now ``format_html`` like the rest, with the same output.

What each class proves:

* ``TestHostileFormValues`` drives the real tags through BOTH engines and reads the FINAL tags with
  ``html.parser`` (the attributes the browser sees), never the string or the sanitiser's verdict.
* ``TestBenignFormOutputIsUnchanged`` compares 40 renderings (config prefix x layout x form) to
  ``fixtures/theme_form_golden_3766.json``, captured from the code BEFORE the change. The switcher
  golden is JSON too (``theme_switcher_inline_golden_3766.json``): the markup has trailing
  whitespace, which the repo's whitespace hooks would strip from a ``.html`` file.
* ``TestThemeSwitcherChunk`` is the same pair for the inline switcher.

Refs #3766, #3775, #1825, #1468.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django import forms
from django.template import engines
from django.test import override_settings

pytestmark = pytest.mark.theming

FIXTURES = Path(__file__).parent / "fixtures"

#: Each breaks out of a double-quoted attribute and adds an event handler.
BREAKOUTS = {
    "onfocus": 'a" onfocus="alert(1)" x="',
    "onload": '1" onload="alert(1)',
    "tag-close": 'a"><script>alert(1)</script><i x="',
    "single-quote": "a' onfocus='alert(1)",
    "entity": "a&quot; onfocus=&quot;alert(1)",
    "newline": 'a"\nonfocus="alert(1)',
}


@pytest.fixture(scope="module", params=["django", "djust"])
def engine(request):
    if request.param == "django":
        return engines["django"]
    from djust.template import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def _tags(html: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = _Tags()
    parser.feed(html)
    parser.close()
    return parser.tags


def _breakout_findings(html: str) -> list[str]:
    """Anything a breakout would have added: an ``on*`` attribute, a ``script``/extra tag."""
    found = []
    for tag, attrs in _tags(html):
        found += [f"{tag}[{name}]" for name in attrs if name.startswith("on") or name == "x"]
        if tag == "script":
            found.append("script")
    return found


def _form(field_name: str = "name", **kwargs) -> forms.Form:
    class Plain(forms.Form):
        pass

    form = Plain(**kwargs)
    form.fields[field_name] = forms.CharField(label="Label", help_text="Help")
    return form


def _render(engine, source: str, context: dict) -> str:
    return engine.from_string("{% load theme_form_tags %}" + source).render(context)


def _theme_form(engine, form, **config) -> str:
    with override_settings(LIVEVIEW_CONFIG={"theme": config}):
        return _render(engine, "{% theme_form form %}", {"form": form})


@pytest.fixture
def validator_off(monkeypatch):
    """Let a hostile ``css_prefix`` reach the tags: ``get_theme_config()`` refuses one (#3779).

    The loader is the first line of defence; the ``format_html`` in ``theme_form`` is the second.
    The tests that use this fixture pin the second line, which a refusing loader would otherwise
    hide (they would pass because the render raised, not because the prefix was escaped).
    """
    monkeypatch.setattr("djust.theming._config._validate_css_prefix", lambda value: value)


class TestHostileFormValues:
    @pytest.mark.parametrize("label", sorted(BREAKOUTS))
    def test_a_field_name_cannot_break_out_of_label_for_or_the_help_id(self, engine, label):
        html = _theme_form(engine, _form(BREAKOUTS[label]))
        assert _breakout_findings(html) == [], html
        # The label and the help text are there, each carrying exactly the attributes it prints.
        labels = [attrs for tag, attrs in _tags(html) if tag == "label"]
        assert len(labels) == 1 and set(labels[0]) == {"for", "class"}, labels
        helps = [attrs for tag, attrs in _tags(html) if tag == "div" and "help" in attrs["class"]]
        assert len(helps) == 1 and set(helps[0]) == {"class", "id"}, helps

    @pytest.mark.parametrize("label", sorted(BREAKOUTS))
    def test_a_form_prefix_cannot_either(self, engine, label):
        html = _theme_form(engine, _form("name", prefix=BREAKOUTS[label]))
        assert _breakout_findings(html) == [], html

    @pytest.mark.parametrize("label", sorted(BREAKOUTS))
    def test_a_custom_auto_id_cannot_either(self, engine, label):
        html = _theme_form(engine, _form("name", auto_id=BREAKOUTS[label] + "%s"))
        assert _breakout_findings(html) == [], html

    @pytest.mark.parametrize("label", sorted(BREAKOUTS))
    def test_a_configured_css_prefix_cannot_break_out_of_a_class(
        self, engine, label, validator_off
    ):
        form = _form("name")
        html = _theme_form(engine, form, css_prefix=BREAKOUTS[label])
        assert _breakout_findings(html) == [], html

    @pytest.mark.parametrize("label", sorted(BREAKOUTS))
    def test_every_line_that_prints_the_css_prefix_escapes_it(self, engine, label, validator_off):
        """The legend (a fieldset widget), the field-error span and the form-level alert too."""

        class Mixed(forms.Form):
            plan = forms.ChoiceField(choices=[("a", "A")], widget=forms.RadioSelect)
            name = forms.CharField(help_text="h")

        form = Mixed(data={})
        form.add_error(None, "Bad")
        html = _theme_form(engine, form, css_prefix=BREAKOUTS[label])
        names = [tag for tag, _ in _tags(html)]
        assert "legend" in names and names.count("span") >= 2 and "ul" in names, html
        assert _breakout_findings(html) == [], html

    @pytest.mark.parametrize("label", sorted(BREAKOUTS))
    def test_form_errors_print_the_css_prefix_escaped_too(self, engine, label, validator_off):
        form = _form("name", data={"name": "x"})
        assert form.is_valid()
        form.add_error(None, "Bad")
        with override_settings(LIVEVIEW_CONFIG={"theme": {"css_prefix": BREAKOUTS[label]}}):
            html = _render(engine, "{% theme_form_errors form %}", {"form": form})
        assert "Bad" in html
        assert _breakout_findings(html) == [], html

    def test_the_reported_field_name_verbatim(self, engine):
        html = _theme_form(engine, _form('a" onfocus="alert(1)" x="'))
        assert "onfocus=" not in html.replace("&quot; onfocus=&quot;", "")
        assert _breakout_findings(html) == [], html

    def test_label_text_and_errors_stay_escaped_text(self, engine):
        class F(forms.Form):
            f = forms.CharField(label='<img src=x onerror="alert(1)">', help_text="<b>h</b>")

        form = F(data={"f": ""})
        html = _theme_form(engine, form)
        assert [t for t, _ in _tags(html) if t in ("img", "b")] == [], html


class TestBenignFormOutputIsUnchanged:
    GOLDEN = json.loads((FIXTURES / "theme_form_golden_3766.json").read_text())

    class Plain(forms.Form):
        name = forms.CharField(help_text="Your name")
        email = forms.EmailField(label="Mail & more")
        flag = forms.BooleanField(required=False, label="")
        secret = forms.CharField(widget=forms.HiddenInput, required=False)

    class Radio(forms.Form):
        plan = forms.ChoiceField(choices=[("a", "Alpha")], widget=forms.RadioSelect)

    @classmethod
    def _cases(cls):
        bad = cls.Plain(data={"name": "", "email": "x"})
        bad.add_error(None, "Form <b>wide</b> error")
        return {
            "plain": cls.Plain(),
            "bound-errors": bad,
            "radio-fieldset": cls.Radio(),
            "prefixed": cls.Plain(prefix="p"),
            "no-auto-id": cls.Plain(auto_id=False),
        }

    def test_the_golden_names_every_combination(self):
        cases = self._cases()
        expected = {
            f"{prefix}|{layout}|{label}"
            for prefix in ("", "dj-")
            for layout in ("stacked", "horizontal", "inline", "bogus")
            for label in cases
        }
        assert set(self.GOLDEN) == expected

    @pytest.mark.parametrize("prefix", ["", "dj-"])
    @pytest.mark.parametrize("layout", ["stacked", "horizontal", "inline", "bogus"])
    @pytest.mark.parametrize(
        "label", ["plain", "bound-errors", "radio-fieldset", "prefixed", "no-auto-id"]
    )
    def test_byte_for_byte_what_the_code_before_the_change_printed(self, prefix, layout, label):
        form = self._cases()[label]
        with override_settings(LIVEVIEW_CONFIG={"theme": {"css_prefix": prefix}}):
            html = (
                engines["django"]
                .from_string(
                    "{% load theme_form_tags %}{% theme_form form layout=layout %}"
                    "|{% theme_form_errors form %}"
                )
                .render({"form": form, "layout": layout})
            )
        assert html == self.GOLDEN[f"{prefix}|{layout}|{label}"]

    def test_the_djust_engine_prints_what_the_django_engine_prints(self):
        from djust.template import DjustTemplateBackend

        rust = DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})
        form = self._cases()["bound-errors"]
        source = "{% load theme_form_tags %}{% theme_form form %}|{% theme_form_errors form %}"
        with override_settings(LIVEVIEW_CONFIG={"theme": {"css_prefix": "dj-"}}):
            assert (
                rust.from_string(source).render({"form": form})
                == self.GOLDEN["dj-|stacked|bound-errors"]
            )


class TestThemeSwitcherChunk:
    PRESETS = [
        {"name": "default", "display_name": "Default", "is_active": True},
        {"name": "ocean", "display_name": "Ocean", "is_active": False},
        {"name": "rose", "display_name": "Rose Stone", "is_active": False},
    ]

    @staticmethod
    def _state():
        from djust.theming.manager import ThemeState

        return ThemeState(
            theme="material",
            preset="default",
            mode="light",
            resolved_mode="light",
            pack=None,
            layout="",
        )

    def _render(self, presets):
        from djust.theming.context_processors import _render_theme_switcher

        return _render_theme_switcher(self._state(), presets)

    def test_byte_for_byte_what_the_code_before_the_change_printed(self):
        golden = json.loads((FIXTURES / "theme_switcher_inline_golden_3766.json").read_text())[
            "html"
        ]
        assert self._render(self.PRESETS) == golden

    @pytest.mark.parametrize("label", sorted(BREAKOUTS))
    @pytest.mark.parametrize("field", ["name", "display_name"])
    def test_a_preset_name_cannot_break_out_of_the_option(self, label, field):
        presets = [dict(p) for p in self.PRESETS]
        presets[1][field] = BREAKOUTS[label]
        html = self._render(presets)
        # The switcher prints its own constant ``onclick`` handlers; a breakout adds to them.
        golden = json.loads((FIXTURES / "theme_switcher_inline_golden_3766.json").read_text())[
            "html"
        ]
        assert _breakout_findings(html) == _breakout_findings(golden), html
        options = [attrs for tag, attrs in _tags(html) if tag == "option"]
        assert len(options) == len(presets)
        assert all(set(attrs) <= {"value", "selected"} for attrs in options), options

    def test_the_built_in_preset_with_an_apostrophe_keeps_its_bytes(self):
        """``Synthwave '84`` is a built-in name: ``escape`` alone would print ``&#x27;``."""
        from djust.theming.presets import list_presets

        names = [p["display_name"] for p in list_presets() if "'" in p["display_name"]]
        assert names, "no built-in preset name holds an apostrophe any more: drop this test"
        presets = [{"name": "x", "display_name": names[0], "is_active": False}]
        assert f">{names[0]}</option>" in self._render(presets)

    def test_an_ampersand_in_a_display_name_is_printed_as_text(self):
        presets = [dict(p) for p in self.PRESETS]
        presets[2]["display_name"] = "Rose & <Stone>"
        assert "Rose &amp; &lt;Stone&gt;" in self._render(presets)


class TestPassthroughAttrsUnchanged:
    """``_passthrough_attrs`` now builds each ``name="value"`` with ``format_html``; same output."""

    def test_prints_what_the_f_string_printed(self):
        from django.utils.safestring import SafeData, mark_safe

        from djust.theming.templatetags.theme_components import _passthrough_attrs

        out = _passthrough_attrs(
            {
                "title": mark_safe('<b>"x"</b>'),
                "data_k": "a&b",
                "disabled": True,
                "aria_label": "L",
                "hidden_attr": None,
                "off": False,
                "count": 3,
            },
            skip=(),
        )
        assert out == (
            'title="&lt;b&gt;&quot;x&quot;&lt;/b&gt;" data-k="a&amp;b" disabled aria-label="L" count="3"'
        )
        assert isinstance(out, SafeData)
