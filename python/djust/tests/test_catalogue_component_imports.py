"""Every catalogue page must document an import that actually imports.

The USAGE snippet's import line used to be spelled out in
`catalogue_detail.html` as `from djust_components.components.<name> import
<Name>`, and both halves of that were wrong:

* `djust_components` is the **static** namespace (`static/djust_components/`),
  not a Python package — so `from djust_components...` raised
  `ModuleNotFoundError` on every page, for every component;
* the class name is not always the snake→CamelCase of the module name —
  `qr_code` defines `QRCode`, `form_validation` defines two components, and
  `server_event_toast` defines only a mixin.

A copy-paste that fails is worse than no snippet, so the line is now derived
from the module and emitted over the public `djust.components` namespace only
when it exports that exact class. Names shadowed by descriptors use their
defining module. These tests execute every generated line and verify identity.
"""

from djust.theming.gallery.component_registry import (
    _COMPONENT_TO_CATEGORY,
    get_python_component_import,
)
from djust.theming.gallery.catalogue import build_catalogue_detail_context


def _python_components() -> list[str]:
    return [
        name
        for name in sorted(_COMPONENT_TO_CATEGORY)
        if build_catalogue_detail_context(name).get("component_type") == "python"
    ]


def test_there_are_python_components_to_check():
    """Guard: a scan that finds nothing would pass every assertion below."""
    assert len(_python_components()) > 100


def test_every_generated_import_line_executes():
    """The point of the fix. This fails on the old `djust_components.…` line."""
    broken = {}
    for name in _python_components():
        line = build_catalogue_detail_context(name)["import_line"]
        if not line:
            continue  # documented as having no class — asserted separately
        try:
            exec(line, {})
        except Exception as exc:  # noqa: BLE001 — any failure is the finding
            broken[name] = f"{line!r} -> {type(exc).__name__}: {exc}"

    assert not broken, f"catalogue documents unimportable lines: {broken}"


def test_generated_imports_name_the_classes_used_by_the_preview():
    import importlib

    mismatches = []
    for name in _python_components():
        module_path, names = get_python_component_import(name)
        namespace = {}
        exec(build_catalogue_detail_context(name)["import_line"], namespace)
        module = importlib.import_module(module_path)
        for class_name in names:
            if namespace[class_name] is not getattr(module, class_name):
                mismatches.append((name, class_name))
    assert not mismatches, f"examples import different classes from their previews: {mismatches}"


def test_a_module_with_no_component_class_documents_no_import():
    """`server_event_toast` defines only a mixin.

    The old template emitted `import ServerEventToast` for it — a name that
    exists nowhere.
    """
    ctx = build_catalogue_detail_context("server_event_toast")

    assert ctx["component_type"] == "python"
    assert ctx["import_line"] == ""
    assert ctx["class_name"] == ""


def test_class_name_is_read_from_the_module_not_guessed():
    """`qr_code` defines `QRCode`; snake→CamelCase would say `QrCode`."""
    _module, names = get_python_component_import("qr_code")

    assert names == ["QRCode"]


def test_a_module_with_several_components_documents_all_of_them():
    """`form_validation` defines two; documenting one would be arbitrary."""
    _module, names = get_python_component_import("form_validation")

    assert names == ["FieldError", "FormErrors"]

    line = build_catalogue_detail_context("form_validation")["import_line"]
    assert "FieldError" in line and "FormErrors" in line


def test_pages_do_not_name_the_static_namespace_as_a_package():
    """The static namespace must never appear in a Python import line."""
    for name in _python_components():
        line = build_catalogue_detail_context(name)["import_line"]
        assert "djust_components." not in line, f"{name}: {line!r}"


# ---------------------------------------------------------------------------
# Template components — the preview is rendered, not printed
# ---------------------------------------------------------------------------


def _template_component_names() -> list[str]:
    return [
        name
        for name in sorted(_COMPONENT_TO_CATEGORY)
        if build_catalogue_detail_context(name).get("component_type") == "template"
    ]


def test_there_are_template_components_to_check():
    assert len(_template_component_names()) >= 20


def test_every_template_component_gets_a_rendered_preview():
    """The LIVE PREVIEW must show the component, not its invocation.

    The preview was a hand-written chain of `{% if name == "button" %}…{% elif %}`
    covering 11 of the 24 template components. The other 13 fell through to an
    `{% else %}` that printed the call — so a section headed LIVE PREVIEW showed
    `tabs(id=…, active=0)` as text. The previews are now rendered from each
    component's own template with the contract's example kwargs, which needs no
    per-component entry and cannot fall behind the component list.
    """
    unrendered = {}
    for name in _template_component_names():
        ctx = build_catalogue_detail_context(name)
        previews = ctx.get("template_examples_html") or []
        if not previews:
            unrendered[name] = "no previews produced"
            continue
        html = "".join(p["html"] for p in previews)
        if not html.strip():
            unrendered[name] = "preview is empty"
        elif "dj-component-preview-error" in html:
            unrendered[name] = html[:160]

    assert not unrendered, f"template components with no rendered preview: {unrendered}"


def test_the_preview_is_not_the_invocation():
    """`tabs(...)` as text is the specific shape the fallback produced."""
    import re

    for name in _template_component_names():
        ctx = build_catalogue_detail_context(name)
        for preview in ctx.get("template_examples_html") or []:
            assert not re.match(rf"^\s*{re.escape(name)}\(", preview["html"]), (
                f"{name}: the preview is the invocation, not the component"
            )
