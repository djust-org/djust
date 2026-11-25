"""The ``djust.components.ui`` simple components draw what the Rust crate drew (#4002).

Fifteen of the ``ui/*_simple.py`` classes (``Alert`` ... ``Tooltip``) had three renderers: a
PyO3 class from the ``djust_components`` crate (``RustAlert`` ...), an inline ``template`` the
constructor fell back to when the crate refused a value, and a ``_render_custom`` f-string per
CSS framework that no render reached. #4002 keeps one: ``_render_custom``, a Python port of the
crate's markup, and removes the crate.

``fixtures/ui_simple_golden_4002.json`` holds 284 renders captured from the crate before it was
removed (every class, every enumerated value of every parameter, hostile and marked-safe text,
and ``csp_strict`` on and off for the two classes that write a size). Each must render the same
bytes now. The entries with a ``fix`` key differ from ``rust_html`` on purpose:

* ``Tooltip`` -- the crate wrote no space before ``title=`` (``data-bs-trigger="hover"title=``).
* ``Range`` / ``Switch`` / ``TextArea`` -- an explicit ``id`` reached the base class, never the
  crate, which used ``name`` for the element's ``id`` and the label's ``for``.

``Modal`` never reached its crate class (its required ``id`` is the base class's own argument),
so its goldens are what its template drew.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

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


def _value(raw: Any) -> Any:
    return mark_safe(raw["safe"]) if isinstance(raw, dict) else raw


def _render(case: dict[str, Any]) -> str:
    module = importlib.import_module(f"djust.components.ui.{MODULES[case['cls']]}_simple")
    cls = getattr(module, case["cls"])
    before = config.get("csp_strict", False)
    config.set("csp_strict", case["csp_strict"])
    try:
        return str(cls(**{k: _value(v) for k, v in case["kwargs"].items()}).render())
    finally:
        config.set("csp_strict", before)


def _case_id(case: dict[str, Any]) -> str:
    return f"{case['cls']}-{CASES.index(case)}"


@pytest.mark.parametrize("case", CASES, ids=[_case_id(c) for c in CASES])
def test_render_matches_the_golden(case: dict[str, Any]) -> None:
    assert _render(case) == case["html"]


def test_every_rust_backed_class_is_covered() -> None:
    assert {c["cls"] for c in CASES} == set(MODULES)
    assert len(CASES) >= 280


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

    for name in MODULES:
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
