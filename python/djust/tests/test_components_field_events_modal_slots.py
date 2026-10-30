"""combobox / rich_select validate their field; modal heading level and description slot.

* Picking a combobox or rich_select option sent ``validate_field`` without the
  field's name (other inputs send it as ``field``), so per-field validation
  never ran for it. Options now carry ``data-field`` when the component is a
  form field (``field=``) or sends ``validate_field``.
* The combobox always wired a ``<name>_search`` handler, which ordinary Django
  forms do not have. Search is now opt-in (``search_event=``); without it the
  client filters the static options.
* ``{% modal %}``'s title was always an ``<h3>``, and its description plain
  text. ``heading_level=`` sets the heading, and
  ``{% modal_description %}...{% endmodal_description %}`` holds template
  markup that ``aria-describedby`` names.
"""

from __future__ import annotations

import re

import pytest
from django import forms

from djust.tests.test_components_csp_aria import (
    OPTIONS,
    _both,
    _django,
    _rust,
    assert_strict_csp_safe,
)


class _F(forms.Form):
    city = forms.ChoiceField(choices=[("a", "Alpha"), ("b", "Beta")])


def _options(html: str) -> list[str]:
    return re.findall(r"<div class=\"(?:combobox|rich-select)-option[^>]*>", html)


class TestOptionsCarryTheFieldName:
    @pytest.mark.parametrize("engine", ["django", "rust"])
    @pytest.mark.parametrize("tag", ["combobox", "rich_select"])
    def test_validate_field_options_carry_the_field(self, engine, tag):
        render = _django if engine == "django" else _rust
        out = render(
            "{% " + tag + ' name="city" options=opts event="validate_field" %}', {"opts": OPTIONS}
        )
        opts = _options(out)
        assert len(opts) == 2
        for opt in opts:
            assert 'dj-click="validate_field"' in opt
            assert 'data-field="city"' in opt

    @pytest.mark.parametrize("tag", ["combobox", "rich_select"])
    def test_bound_field_options_carry_the_html_name(self, tag):
        f = _F(prefix="run")["city"]
        out = _django(
            "{% " + tag + ' field=f options=opts event="validate_field" %}',
            {"f": f, "opts": OPTIONS},
        )
        for opt in _options(out):
            assert 'data-field="run-city"' in opt

    @pytest.mark.parametrize("engine", ["django", "rust"])
    @pytest.mark.parametrize("tag", ["combobox", "rich_select"])
    def test_other_events_unchanged(self, engine, tag):
        render = _django if engine == "django" else _rust
        out = render(
            "{% " + tag + ' name="city" options=opts event="pick_city" %}', {"opts": OPTIONS}
        )
        for opt in _options(out):
            assert "data-field" not in opt

    def test_multiple_mode_options_carry_the_field(self):
        out = _django(
            '{% combobox name="tags" options=opts multiple=True event="validate_field" %}',
            {"opts": OPTIONS},
        )
        for opt in _options(out):
            assert 'data-field="tags"' in opt


class TestComboboxSearchIsOptIn:
    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_no_search_handler_by_default(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% combobox name="city" options=opts %}', {"opts": OPTIONS})
        assert "dj-input" not in out
        assert "city_search" not in out
        assert re.search(r'class="combobox"[^>]*data-filter="client"', out), out

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_search_event_wires_the_server_search(self, engine):
        render = _django if engine == "django" else _rust
        out = render(
            '{% combobox name="city" options=opts search_event="find_city" %}', {"opts": OPTIONS}
        )
        assert 'dj-input="find_city"' in out
        assert 'data-filter="client"' not in out


class TestComboboxClassSearch:
    def test_class_filters_client_side_without_a_search_event(self):
        from djust.components.components.combobox import Combobox

        out = str(Combobox(name="city", options=OPTIONS))
        assert "dj-input" not in out and "city_search" not in out
        assert 'data-filter="client"' in out
        searched = str(Combobox(name="city", options=OPTIONS, search_event="find_city"))
        assert 'dj-input="find_city"' in searched


class TestModalHeadingLevel:
    @pytest.mark.parametrize("native", [False, True])
    def test_heading_level(self, native):
        src = (
            '{% modal open=True native=n title="Request" id="req" heading_level=2 %}x{% endmodal %}'
        )
        for out in _both(src, {"n": native}).values():
            assert '<h2 class="dj-modal__title" id="req-title">Request</h2>' in out
            assert "<h3" not in out

    def test_out_of_range_falls_back_to_h3(self):
        out = _django('{% modal open=True title="T" heading_level=9 %}x{% endmodal %}')
        assert '<h3 class="dj-modal__title"' in out


class TestModalDescriptionSlot:
    SRC = (
        '{% modal open=True native=n title="Send request" id="req" %}'
        "{% modal_description %}Approvers in <strong>{{ team }}</strong> are notified.{% endmodal_description %}"
        '<textarea name="note"></textarea>'
        "{% endmodal %}"
    )

    @pytest.mark.parametrize("native", [False, True])
    def test_slot_markup_is_described(self, native):
        for engine, out in _both(self.SRC, {"n": native, "team": "Ops"}).items():
            assert 'aria-describedby="req-desc"' in out, engine
            assert re.search(
                r'<div class="dj-modal__description" id="req-desc">Approvers in <strong>Ops</strong> are notified\.</div>',
                out,
            ), (engine, out)
            assert "data-dj-modal-description" not in out
            assert_strict_csp_safe(out)

    def test_slot_values_are_escaped(self):
        out = _django(self.SRC, {"n": True, "team": "<script>x</script>"})
        assert "<script>x" not in out and "&lt;script&gt;" in out

    def test_slot_and_describedby_combine(self):
        src = (
            '{% modal open=True title="T" id="m" describedby="extra" %}'
            "{% modal_description %}Hi{% endmodal_description %}{% endmodal %}"
        )
        for out in _both(src).values():
            assert 'aria-describedby="m-desc extra"' in out

    def test_without_a_slot_nothing_is_described(self):
        for out in _both('{% modal open=True title="T" %}x{% endmodal %}').values():
            assert "aria-describedby" not in out
