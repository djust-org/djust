"""combobox / rich_select as form fields, and {% modal %} role/description/close.

* A combobox's visible input was free text named after the field, so a form
  submitted whatever was typed, not the chosen option's value. It now submits
  the selected value from a hidden input named after the field; the visible
  input carries no name. Ids follow the bound field's ``auto_id`` (form prefix
  included) like Django widgets, so two forms rendering the same field name
  on one page no longer collide.
* ``{% modal %}`` could not be an ``alertdialog``, had no ``aria-describedby``
  and always rendered a close button.

Each case renders through both engines where a template value can carry it.
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

CHOICES = [("a", "Alpha"), ("b", "Beta")]


class _RunForm(forms.Form):
    city = forms.ChoiceField(choices=CHOICES)


def _tag(html: str, marker: str) -> str:
    match = re.search(r"<[^>]*" + re.escape(marker) + r"[^>]*>", html)
    assert match, f"{marker!r} not in {html}"
    return match.group(0)


class TestComboboxSubmitsTheValue:
    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_hidden_input_carries_the_selected_value(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% combobox name="city" options=opts value="b" %}', {"opts": OPTIONS})
        hidden = _tag(out, 'class="combobox-value"')
        assert 'type="hidden"' in hidden and 'name="city"' in hidden and 'value="b"' in hidden
        visible = _tag(out, 'role="combobox"')
        assert "name=" not in visible
        assert 'value="Beta"' in visible

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_free_text_is_opt_in(self, engine):
        render = _django if engine == "django" else _rust
        plain = render('{% combobox name="city" options=opts %}', {"opts": OPTIONS})
        assert "data-allow-free-text" not in plain
        free = render(
            '{% combobox name="city" options=opts allow_free_text=True %}', {"opts": OPTIONS}
        )
        assert "data-allow-free-text" in _tag(free, 'class="combobox"')

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_legacy_ids_without_a_field(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% combobox name="city" label="City" options=opts %}', {"opts": OPTIONS})
        assert 'id="city-input"' in out and 'for="city-input"' in out
        assert 'id="city-listbox"' in out

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_id_kwarg_sets_the_ids(self, engine):
        render = _django if engine == "django" else _rust
        out = render(
            '{% combobox name="city" id="run-city" label="City" options=opts %}', {"opts": OPTIONS}
        )
        visible = _tag(out, 'role="combobox"')
        assert 'id="run-city"' in visible and 'aria-controls="run-city-listbox"' in visible
        assert 'for="run-city"' in out and 'id="run-city-listbox"' in out

    def test_bound_field_ids_follow_auto_id_and_prefix(self):
        a = _RunForm(prefix="request")["city"]
        b = _RunForm(prefix="run")["city"]
        out = _django(
            "{% combobox field=a options=opts %}{% combobox field=b options=opts %}",
            {"a": a, "b": b, "opts": OPTIONS},
        )
        ids = re.findall(r'\sid="([^"]+)"', out)
        assert len(ids) == len(set(ids)), ids
        assert 'id="id_request-city"' in out and 'for="id_request-city"' in out
        assert 'id="id_run-city"' in out
        assert re.search(r'class="combobox-value"[^>]*name="request-city"', out), out
        assert re.search(r'class="combobox-value"[^>]*name="run-city"', out), out

    def test_bound_field_submitted_value_round_trips(self):
        form = _RunForm(data={"city": "b"})
        out = _django("{% combobox field=f options=opts %}", {"f": form["city"], "opts": OPTIONS})
        assert re.search(r'class="combobox-value"[^>]*name="city"[^>]*value="b"', out), out
        assert 'value="Beta"' in _tag(out, 'role="combobox"')

    def test_multiple_mode_visible_input_is_not_submitted(self):
        out = _django(
            '{% combobox name="tags" options=opts multiple=True selected=sel %}',
            {"opts": OPTIONS, "sel": ["a"]},
        )
        assert "name=" not in _tag(out, 'role="combobox"')
        assert 'name="tags[]" value="a"' in out

    def test_component_class_submits_the_value(self):
        from djust.components.components.combobox import Combobox

        out = str(Combobox(name="city", options=OPTIONS, value="a"))
        assert re.search(r'class="combobox-value"[^>]*name="city"[^>]*value="a"', out), out
        assert "name=" not in _tag(out, 'role="combobox"')
        assert 'value="Alpha"' in _tag(out, 'role="combobox"')

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_still_strict_csp_safe(self, engine):
        render = _django if engine == "django" else _rust
        assert_strict_csp_safe(render('{% combobox name="city" options=opts %}', {"opts": OPTIONS}))


class TestRichSelectIds:
    def test_bound_field_ids_follow_auto_id_and_prefix(self):
        a = _RunForm(prefix="request")["city"]
        b = _RunForm(prefix="run")["city"]
        out = _django(
            "{% rich_select field=a options=opts %}{% rich_select field=b options=opts %}",
            {"a": a, "b": b, "opts": OPTIONS},
        )
        ids = re.findall(r'\sid="([^"]+)"', out)
        assert len(ids) == len(set(ids)), ids
        assert 'id="id_request-city"' in out and 'id="id_run-city"' in out
        assert 'name="request-city"' in out and 'name="run-city"' in out


class TestModalRoleDescriptionClose:
    @pytest.mark.parametrize("native", [False, True])
    def test_alertdialog_role(self, native):
        src = '{% modal open=True native=n role="alertdialog" title="Delete?" id="del" %}x{% endmodal %}'
        for out in _both(src, {"n": native}).values():
            assert 'role="alertdialog"' in out
            assert 'role="dialog"' not in out

    def test_unknown_role_falls_back_to_dialog(self):
        out = _django('{% modal open=True role="banner" title="T" %}x{% endmodal %}')
        assert 'role="dialog"' in out and "banner" not in out

    @pytest.mark.parametrize("native", [False, True])
    def test_description_is_rendered_and_described(self, native):
        src = '{% modal open=True native=n title="Delete?" id="del" description="Gone for good." %}x{% endmodal %}'
        for out in _both(src, {"n": native}).values():
            assert '<p class="dj-modal__description" id="del-desc">Gone for good.</p>' in out
            assert 'aria-describedby="del-desc"' in out

    @pytest.mark.parametrize("native", [False, True])
    def test_explicit_describedby(self, native):
        src = '{% modal open=True native=n title="T" describedby="note-1 note-2" %}x{% endmodal %}'
        for out in _both(src, {"n": native}).values():
            assert 'aria-describedby="note-1 note-2"' in out

    @pytest.mark.parametrize("native", [False, True])
    def test_close_button_can_be_omitted(self, native):
        src = '{% modal open=True native=n title="T" close_button=False %}x{% endmodal %}'
        for out in _both(src, {"n": native}).values():
            assert "dj-modal__close" not in out
            assert 'class="dj-modal__title"' in out

    @pytest.mark.parametrize("native", [False, True])
    def test_defaults_unchanged(self, native):
        src = '{% modal open=True native=n title="T" %}x{% endmodal %}'
        for out in _both(src, {"n": native}).values():
            assert "dj-modal__close" in out
            assert "aria-describedby" not in out
            assert "dj-modal__description" not in out
            if native:
                assert "role=" not in _tag(out, "dj-modal--native")
            else:
                assert 'role="dialog"' in out
