"""The ``djust.components.ui`` simple components each have one renderer (#4002).

Fifteen of the ``ui/*_simple.py`` classes (``Alert`` ... ``Tooltip``) had three renderers: a
PyO3 class from the ``djust_components`` crate (``RustAlert`` ...), an inline ``template`` the
constructor fell back to when the crate refused a value, and a ``_render_custom`` f-string per
CSS framework that no render reached. Slice 1 kept one, ``_render_custom``, a Python port of the
crate's markup, and removed the crate.

Slice 3 (option C, the ui ``*Component`` markup wins): the eight simple classes with a twin in
``djust.components.ui`` -- ``Alert``, ``Badge``, ``Button``, ``Card``, ``Dropdown``, ``Modal``,
``Progress``, ``Spinner`` -- map their arguments onto the twin's and return its markup.

``fixtures/ui_simple_golden_4002.json`` pins every render. The twin classes' entries were
recaptured from the twins (``slice1_html`` keeps the markup slice 1 drew, for the per-class
summary in ``docs/website/guides/migrating-to-2.0.md``). The rest were captured from the crate
before it was removed and must render the same bytes; those with a ``fix`` key differ from
``rust_html`` on purpose:

* ``Tooltip`` -- the crate wrote no space before ``title=`` (``data-bs-trigger="hover"title=``).
* ``Range`` / ``Switch`` / ``TextArea`` -- an explicit ``id`` reached the base class, never the
  crate, which used ``name`` for the element's ``id`` and the label's ``for``.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import warnings

import pytest
from django.utils.safestring import mark_safe

from djust.config import config

FIXTURE = Path(__file__).parent / "fixtures" / "ui_simple_golden_4002.json"
CASES: list[dict[str, Any]] = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]

MODULES = {
    "Alert": "alert",
    "Avatar": "avatar",
    "Badge": "badge",
    "Button": "button",
    "Card": "card",
    "Divider": "divider",
    "Dropdown": "dropdown",
    "Icon": "icon",
    "Modal": "modal",
    "Progress": "progress",
    "Range": "range",
    "Spinner": "spinner",
    "Switch": "switch",
    "TextArea": "textarea",
    "Toast": "toast",
    "Tooltip": "tooltip",
}


#: The simple classes that render through a ui ``*Component`` twin (slice 3), and the twin.
TWINS = {
    "Alert": "AlertComponent",
    "Badge": "BadgeComponent",
    "Button": "ButtonComponent",
    "Card": "CardComponent",
    "Dropdown": "DropdownComponent",
    "Modal": "ModalComponent",
    "Progress": "ProgressComponent",
    "Spinner": "SpinnerComponent",
}

#: Simple classes with no twin in ``djust.components.ui``: left drawing their own markup.
NO_UI_TWIN = {
    "Accordion",
    "Avatar",
    "Breadcrumb",
    "ButtonGroup",
    "Checkbox",
    "Divider",
    "Icon",
    "Input",
    "ListGroup",
    "NavBar",
    "Offcanvas",
    "Pagination",
    "Radio",
    "Range",
    "Select",
    "Switch",
    "Table",
    "Tabs",
    "TextArea",
    "Toast",
    "Tooltip",
}


def _value(raw: Any) -> Any:
    return mark_safe(raw["safe"]) if isinstance(raw, dict) else raw


def _render(case: dict[str, Any]) -> str:
    module = importlib.import_module(f"djust.components.ui.{MODULES[case['cls']]}_simple")
    cls = getattr(module, case["cls"])
    before = config.get("csp_strict", False)
    config.set("csp_strict", case["csp_strict"])
    try:
        with warnings.catch_warnings():
            # The deprecated arguments some cases pass (#4002 slice 3) warn; their own test.
            warnings.simplefilter("ignore", DeprecationWarning)
            return str(cls(**{k: _value(v) for k, v in case["kwargs"].items()}).render())
    finally:
        config.set("csp_strict", before)


def _case_id(case: dict[str, Any]) -> str:
    return f"{case['cls']}-{CASES.index(case)}"


@pytest.mark.parametrize("case", CASES, ids=[_case_id(c) for c in CASES])
def test_render_matches_the_golden(case: dict[str, Any]) -> None:
    assert _render(case) == case["html"]


def test_every_class_is_covered() -> None:
    assert {c["cls"] for c in CASES} == set(MODULES)
    assert len(CASES) >= 290


def test_only_the_twin_classes_changed_markup_in_slice_3() -> None:
    assert {c["cls"] for c in CASES if "slice1_html" in c} == set(TWINS) - {"Dropdown"}


def test_the_intentional_differences_are_only_the_documented_fixes() -> None:
    fixed = [c for c in CASES if "fix" in c]
    assert {c["cls"] for c in fixed} == {"Tooltip", "Range", "Switch", "TextArea"}
    for case in fixed:
        if case["cls"] == "Tooltip":
            assert case["html"].replace('" title="', '"title="') == case["rust_html"]
        else:
            explicit, name = case["kwargs"]["id"], case["kwargs"]["name"]
            rust = case["rust_html"]
            for attr in ("id", "for"):
                rust = rust.replace(f'{attr}="{name}"', f'{attr}="{explicit}"')
            assert case["html"] == rust


def test_the_crate_classes_are_gone() -> None:
    import djust._rust as native

    for name in set(MODULES) - {"Dropdown"}:
        assert not hasattr(native, f"Rust{name}"), name


@pytest.mark.parametrize("name", sorted(MODULES))
def test_one_renderer(name: str) -> None:
    """No crate class, no inline template: ``_render_custom`` is the class's only renderer."""
    module = importlib.import_module(f"djust.components.ui.{MODULES[name]}_simple")
    cls = getattr(module, name)
    assert cls._rust_impl_class is None
    assert cls.template is None
    assert "_render_custom" in vars(cls)
    for legacy in ("_render_bootstrap", "_render_tailwind", "_render_plain"):
        assert not hasattr(cls, legacy), (name, legacy)


def test_every_ui_simple_class_is_a_twin_or_listed_without_one() -> None:
    import djust.components.ui as ui

    simple = {
        name
        for name in ui.__all__
        if getattr(getattr(ui, name), "__module__", "").endswith("_simple")
    }
    assert simple == set(TWINS) | NO_UI_TWIN
    for name, twin in TWINS.items():
        assert hasattr(ui, twin), twin


@pytest.mark.parametrize("name", sorted(TWINS))
def test_twin_class_renders_through_its_component(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever the twin draws is what the simple class returns: one renderer."""
    import djust.components.ui as ui

    twin = getattr(ui, TWINS[name])
    monkeypatch.setattr(twin, "render", lambda self: f"<twin {self.component_id}>")
    case = next(c for c in CASES if c["cls"] == name)
    assert _render(case).startswith("<twin ")


@pytest.mark.parametrize("framework", ["tailwind", "plain"])
def test_twin_class_follows_the_css_framework(framework: str) -> None:
    from djust.components.ui import Alert, AlertComponent

    before = config.get("css_framework", "bootstrap5")
    config.set("css_framework", framework)
    try:
        expected = AlertComponent(component_id="alert", message="Hi", dismissible=False).render()
        assert Alert("Hi").render() == expected
    finally:
        config.set("css_framework", before)


DEPRECATED_ARGUMENTS = [
    ("Alert", {"text": "x", "variant": "primary"}, "variant outside"),
    ("Badge", {"text": "x", "size": "lg"}, "size"),
    ("Card", {"body": "x", "variant": "outlined"}, "variant"),
    ("Modal", {"body": "x", "centered": True}, "centered"),
    ("Modal", {"body": "x", "dismissable": False}, "dismissable"),
    ("Spinner", {"sr_text": "Wait"}, "sr_text"),
]


@pytest.mark.parametrize(
    "name,kwargs,needle", DEPRECATED_ARGUMENTS, ids=[f"{n}-{k}" for n, _, k in DEPRECATED_ARGUMENTS]
)
def test_an_argument_with_no_twin_equivalent_warns(
    name: str, kwargs: dict[str, Any], needle: str
) -> None:
    from djust.components import _alias_deprecation

    _alias_deprecation._WARNED.clear()
    module = importlib.import_module(f"djust.components.ui.{MODULES[name]}_simple")
    with pytest.warns(DeprecationWarning, match=f"{name}'s .*{needle}"):
        getattr(module, name)(**kwargs).render()
    _alias_deprecation._WARNED.clear()


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("Alert", {"text": "x", "variant": "danger", "dismissable": True}),
        ("Badge", {"text": "x", "variant": "info", "pill": True}),
        ("Button", {"text": "x", "size": "lg", "outline": True, "disabled": True}),
        ("Card", {"body": "x", "header": "h", "footer": "f"}),
        ("Modal", {"body": "x", "title": "t", "show": True, "size": "lg"}),
        ("Progress", {"value": 3, "label": "l", "striped": True, "height": "2px"}),
        ("Spinner", {"size": "sm", "animation": "grow"}),
        ("Dropdown", {"label": "M", "items": [{"label": "A", "action": "go"}]}),
        ("Dropdown", {"label": "M", "items": [{"label": "A", "url": "/a", "disabled": True}]}),
    ],
)
def test_an_argument_with_a_twin_equivalent_does_not_warn(
    name: str, kwargs: dict[str, Any]
) -> None:
    from djust.components import _alias_deprecation

    _alias_deprecation._WARNED.clear()
    module = importlib.import_module(f"djust.components.ui.{MODULES[name]}_simple")
    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        getattr(module, name)(**kwargs).render()
