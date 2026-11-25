"""The component argument aliases still work and warn once (#4207)."""

import warnings

import pytest

from djust.components import _alias_deprecation
from djust.components.layout.tabs import TabItem, TabsComponent as Tabs
from djust.components.templatetags import djust_components as tags
from djust.components.templatetags import _forms
from djust.components.ui.dropdown import DropdownComponent as Dropdown, DropdownItem


@pytest.fixture(autouse=True)
def _fresh():
    _alias_deprecation._WARNED.clear()
    yield
    _alias_deprecation._WARNED.clear()


def _deprecations(fn):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = fn()
    return out, [str(w.message) for w in caught if issubclass(w.category, DeprecationWarning)]


def test_slider_min_max_warn_and_still_apply():
    out, msgs = _deprecations(lambda: _forms.slider("x", min=2, max=9))
    assert 'min="2"' in str(out) and 'max="9"' in str(out)
    assert any("min" in m and "min_val" in m and "3.0.0" in m for m in msgs)
    assert any("max_val" in m for m in msgs)


def test_current_names_do_not_warn():
    _, msgs = _deprecations(lambda: _forms.slider("x", min_val=2, max_val=9))
    assert msgs == []


def test_date_picker_range_warns():
    _, msgs = _deprecations(lambda: tags.date_picker(range=True))
    assert any("is_range" in m for m in msgs)


def test_split_button_open_warns():
    _, msgs = _deprecations(lambda: tags.split_button(open=True))
    assert any("is_open" in m for m in msgs)


def test_dj_button_danger_warns_and_destructive_does_not():
    out, msgs = _deprecations(lambda: tags.dj_button(label="x", variant="danger"))
    assert "btn-destructive" in str(out)
    assert any("destructive" in m for m in msgs)
    _, msgs = _deprecations(lambda: tags.dj_button(label="x", variant="destructive"))
    assert msgs == []


def test_warns_once_per_process():
    _, first = _deprecations(lambda: tags.dj_button(label="x", variant="danger"))
    _, second = _deprecations(lambda: tags.dj_button(label="x", variant="danger"))
    assert first and second == []


def test_dict_items_warn_and_objects_do_not():
    def dict_items():
        d = Dropdown()
        d.mount(items=[{"label": "A", "event": "a"}])
        t = Tabs()
        t.mount(tabs=[{"id": "a", "label": "A"}])

    _, msgs = _deprecations(dict_items)
    assert any("DropdownItem" in m for m in msgs) and any("TabItem" in m for m in msgs)

    def objects():
        d = Dropdown()
        d.mount(items=[DropdownItem(text="A", action="a")])
        t = Tabs()
        t.mount(tabs=[TabItem(id="a", label="A")])

    _alias_deprecation._WARNED.clear()
    _, msgs = _deprecations(objects)
    assert msgs == []


@pytest.mark.parametrize("preset", ["danger-confirm", "danger-sm"])
def test_built_in_presets_do_not_trigger_the_alias_warning(preset):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        out = tags.dj_button(label="x", preset=preset, event="go")
    assert "btn-destructive" in str(out)
