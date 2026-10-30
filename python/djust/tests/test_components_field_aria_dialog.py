"""Form-field ARIA for combobox / rich_select, and {% modal native=True %}.

* ``combobox`` and ``rich_select`` could not be tied to a form field's help
  text or error: no ``aria-describedby``, no ``aria-invalid``. They now take
  ``describedby=`` / ``invalid=`` / ``error=`` / ``helper=``, and ``field=`` (a
  Django BoundField) fills name, label, value, error and help text from the
  field and wires the ARIA attributes itself.
* ``{% modal %}`` rendered a positioned ``<div>``, so it had no top layer, no
  inert page behind it and no native Escape. ``native=True`` renders a
  ``<dialog dj-dialog="open">`` that the client opens with ``showModal()``.

Each case renders through both engines (the Django tag and the Rust handler).
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


class _CityForm(forms.Form):
    city = forms.ChoiceField(choices=CHOICES, help_text="Where you work.")


def _bound(data: dict | None = None) -> forms.BoundField:
    form = _CityForm(data=data)
    if data is not None:
        form.is_valid()
    return form["city"]


def _attrs_of(html: str, marker: str) -> str:
    """The opening tag that contains ``marker``."""
    match = re.search(r"<[^>]*" + re.escape(marker) + r"[^>]*>", html)
    assert match, f"{marker!r} not in {html}"
    return match.group(0)


# ---------------------------------------------------------------------------
# combobox
# ---------------------------------------------------------------------------


class TestComboboxFieldAria:
    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_error_and_helper_are_described_and_invalid(self, engine):
        render = _django if engine == "django" else _rust
        out = render(
            '{% combobox name="city" options=opts error="Pick one." helper="Where you work." %}',
            {"opts": OPTIONS},
        )
        tag = _attrs_of(out, 'role="combobox"')
        assert 'aria-describedby="city-helper city-error"' in tag
        assert 'aria-invalid="true"' in tag
        assert '<span class="form-error-message" id="city-error">Pick one.</span>' in out
        assert '<span class="form-helper" id="city-helper">Where you work.</span>' in out

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_explicit_describedby_and_invalid(self, engine):
        render = _django if engine == "django" else _rust
        out = render(
            '{% combobox name="city" options=opts describedby="city-note" invalid=True %}',
            {"opts": OPTIONS},
        )
        tag = _attrs_of(out, 'role="combobox"')
        assert 'aria-describedby="city-note"' in tag
        assert 'aria-invalid="true"' in tag

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_plain_combobox_has_neither(self, engine):
        render = _django if engine == "django" else _rust
        tag = _attrs_of(
            render('{% combobox name="city" options=opts %}', {"opts": OPTIONS}), 'role="combobox"'
        )
        assert "aria-describedby" not in tag
        assert "aria-invalid" not in tag

    def test_bound_field_with_error_and_help_text(self):
        out = _django(
            "{% combobox field=f options=opts %}", {"f": _bound({"city": "zz"}), "opts": OPTIONS}
        )
        tag = _attrs_of(out, 'role="combobox"')
        # Ids follow the bound field's auto_id; the field name is on the
        # hidden input that carries the selected value.
        assert 'id="id_city"' in tag
        assert re.search(r'class="combobox-value"[^>]*name="city"', out)
        assert 'aria-describedby="id_city-helper id_city-error"' in tag
        assert 'aria-invalid="true"' in tag
        assert "Where you work." in out
        assert "Select a valid choice." in out
        assert '<label class="form-label" for="id_city">City</label>' in out

    def test_bound_field_without_errors_is_not_invalid(self):
        out = _django("{% combobox field=f options=opts %}", {"f": _bound(), "opts": OPTIONS})
        tag = _attrs_of(out, 'role="combobox"')
        assert 'aria-describedby="id_city-helper"' in tag
        assert "aria-invalid" not in tag

    def test_component_class(self):
        from djust.components.components.combobox import Combobox

        out = str(Combobox(name="city", options=OPTIONS, describedby="n1", invalid=True))
        tag = _attrs_of(out, 'role="combobox"')
        assert 'aria-describedby="n1"' in tag and 'aria-invalid="true"' in tag


# ---------------------------------------------------------------------------
# rich_select
# ---------------------------------------------------------------------------


class TestRichSelectFieldAria:
    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_error_and_helper_are_described_and_invalid(self, engine):
        render = _django if engine == "django" else _rust
        out = render(
            '{% rich_select name="city" options=opts error="Pick one." helper="Where you work." %}',
            {"opts": OPTIONS},
        )
        tag = _attrs_of(out, 'class="rich-select-trigger')
        assert 'aria-describedby="rs-city-helper rs-city-error"' in tag
        assert 'aria-invalid="true"' in tag
        assert 'id="rs-city-error"' in out and "Pick one." in out
        assert 'id="rs-city-helper"' in out and "Where you work." in out

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_explicit_describedby_and_invalid(self, engine):
        render = _django if engine == "django" else _rust
        out = render(
            '{% rich_select name="city" options=opts describedby="city-note" invalid=True %}',
            {"opts": OPTIONS},
        )
        tag = _attrs_of(out, 'class="rich-select-trigger')
        assert 'aria-describedby="city-note"' in tag
        assert 'aria-invalid="true"' in tag

    def test_bound_field_with_error_and_help_text(self):
        out = _django(
            "{% rich_select field=f options=opts %}", {"f": _bound({"city": "zz"}), "opts": OPTIONS}
        )
        tag = _attrs_of(out, 'class="rich-select-trigger')
        assert 'aria-describedby="id_city-helper id_city-error"' in tag
        assert 'aria-invalid="true"' in tag
        assert '<input type="hidden" name="city"' in out
        assert "Select a valid choice." in out

    def test_component_class(self):
        from djust.components.components.rich_select import RichSelect

        out = str(RichSelect(name="city", options=OPTIONS, describedby="n1", invalid=True))
        tag = _attrs_of(out, 'class="rich-select-trigger')
        assert 'aria-describedby="n1"' in tag and 'aria-invalid="true"' in tag


# ---------------------------------------------------------------------------
# {% modal native=True %}
# ---------------------------------------------------------------------------


class TestNativeModal:
    SRC = (
        '{% modal open=True native=True size="lg" title="Request access" id="req" '
        'close_event="close_req" %}<p>Body</p>{% endmodal %}'
    )

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_renders_a_dialog_opened_with_show_modal(self, engine):
        render = _django if engine == "django" else _rust
        out = render(self.SRC)
        dialog = re.search(r"<dialog[^>]*>", out).group(0)
        assert out.lstrip().startswith("<dialog")
        assert 'class="dj-modal dj-modal--lg dj-modal--native"' in dialog
        assert 'dj-dialog="open"' in dialog
        assert 'dj-dialog-close-event="close_req"' in dialog
        assert 'aria-labelledby="req-title"' in dialog
        assert 'id="req-title"' in out
        assert 'class="dj-modal__close" aria-label="Close" dj-click="close_req"' in out
        assert "<p>Body</p>" in out
        # The top layer replaces the backdrop div and the scrim.
        assert "dj-modal-backdrop" not in out and "dj-scrim" not in out
        assert_strict_csp_safe(out)

    def test_engines_agree(self):
        out = _both(self.SRC)
        assert out["django"] == out["rust"]

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_closed_renders_nothing(self, engine):
        render = _django if engine == "django" else _rust
        assert render("{% modal open=False native=True %}x{% endmodal %}") == ""

    @pytest.mark.parametrize("engine", ["django", "rust"])
    def test_default_is_unchanged(self, engine):
        render = _django if engine == "django" else _rust
        out = render('{% modal open=True title="T" %}x{% endmodal %}')
        assert out.startswith('<div class="dj-modal-backdrop">')
        assert "<dialog" not in out
