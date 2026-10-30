"""Combobox options carry stable ids for ``aria-activedescendant``.

The WAI-ARIA combobox pattern moves keyboard focus through the options by
pointing the input's ``aria-activedescendant`` at the active option's id, so
every option needs a stable id: ``<prefix>-option-<n>``, where the prefix is
the same one the listbox (``<prefix>-listbox``) uses. Both engines render the
tag; ids stay unique when two forms render the same field.
"""

from __future__ import annotations

import re

import pytest
from django import forms

from djust.tests.test_components_csp_aria import OPTIONS, _django, _rust


class _F(forms.Form):
    city = forms.ChoiceField(choices=[("a", "Alpha"), ("b", "Beta")])


def _option_ids(html: str) -> list[str]:
    return re.findall(r'<div class="combobox-option[^"]*" id="([^"]+)"', html)


@pytest.mark.parametrize("engine", ["django", "rust"])
@pytest.mark.parametrize("multiple", [False, True])
def test_every_option_has_a_prefixed_id(engine, multiple):
    render = _django if engine == "django" else _rust
    out = render(
        '{% combobox name="city" options=opts multiple=m %}', {"opts": OPTIONS, "m": multiple}
    )
    assert _option_ids(out) == ["city-option-0", "city-option-1"]
    assert 'aria-controls="city-listbox"' in out and 'id="city-listbox"' in out


@pytest.mark.parametrize("engine", ["django", "rust"])
def test_id_kwarg_prefixes_the_option_ids(engine):
    render = _django if engine == "django" else _rust
    out = render('{% combobox name="city" id="run-city" options=opts %}', {"opts": OPTIONS})
    assert _option_ids(out) == ["run-city-option-0", "run-city-option-1"]


def test_option_ids_are_unique_across_prefixed_forms():
    a = _F(prefix="request")["city"]
    b = _F(prefix="run")["city"]
    out = _django(
        "{% combobox field=a options=opts %}{% combobox field=b options=opts %}",
        {"a": a, "b": b, "opts": OPTIONS},
    )
    ids = _option_ids(out)
    assert ids == [
        "id_request-city-option-0",
        "id_request-city-option-1",
        "id_run-city-option-0",
        "id_run-city-option-1",
    ]
    all_ids = re.findall(r'\sid="([^"]+)"', out)
    assert len(all_ids) == len(set(all_ids)), all_ids


def test_component_class_option_ids():
    from djust.components.components.combobox import Combobox

    out = str(Combobox(name="city", options=OPTIONS))
    assert _option_ids(out) == ["city-option-0", "city-option-1"]
