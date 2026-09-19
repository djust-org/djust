"""Every component in the gallery must actually render something.

The gallery's failure mode was silence. `render_python_component_example`
returned `""` on *any* problem — a missing class, a bad kwarg, a raising
`render()` — and logged at DEBUG. A component that could not render therefore
looked exactly like a component that legitimately renders nothing: an empty
preview, no message, no console error, no failing test.

Three components were in that state, each for a different reason:

  * ``markdown`` — the example passed ``content=``, but ``Markdown.__init__``
    takes ``text=``; ``**kwargs`` swallowed the mismatch and ``self.text``
    stayed ``""``, which ``_render_custom`` treats as "nothing to render".
  * ``icon`` — the example passed ``size=24`` (pixels) to a component whose
    ``size`` is a *name* (``xs/sm/md/lg``), so ``html.escape`` raised
    ``'int' object has no attribute 'replace'``.
  * ``qr_code`` — the class is ``QRCode``; the lookup guessed ``QrCode`` from
    the module name and missed.

Two of those are bad example data and one is a wrong lookup, so the fixes are
in the data and in `_load_component_class`. The durable half is here: a sweep
that renders every component and fails if any produces nothing, so the next one
cannot ship silently.
"""

import pytest

from djust.theming.gallery.component_registry import (
    _COMPONENT_TO_CATEGORY,
    PYTHON_COMPONENT_EXAMPLES,
    _load_component_class,
    get_python_component_import,
    render_python_component_example,
)

#: Marks a render that failed. `render_python_component_example` returns one of
#: these instead of "" so a failure is visible on the page — asserting on it is
#: how this sweep distinguishes "failed" from "renders nothing on purpose".
_ERROR_MARKER = "dj-component-preview-error"


def _component_names() -> list[str]:
    return [n for n in sorted(_COMPONENT_TO_CATEGORY) if n in PYTHON_COMPONENT_EXAMPLES]


def test_the_sweep_has_components_to_check():
    """Guard: an empty population would make every assertion below vacuous."""
    assert len(_component_names()) >= 40


def test_every_component_renders_non_empty_markup():
    """The regression. This is what was silently failing for three of them."""
    blank, failed = [], {}

    for name in _component_names():
        outputs = [
            render_python_component_example(name, kwargs)
            for kwargs in PYTHON_COMPONENT_EXAMPLES[name]
        ]
        if any(_ERROR_MARKER in out for out in outputs):
            failed[name] = next(out for out in outputs if _ERROR_MARKER in out)[:200]
        elif all(not out.strip() for out in outputs):
            blank.append(name)

    assert not failed, f"components failed to render: {failed}"
    assert not blank, f"components rendered nothing: {blank}"


def test_the_sweep_would_notice_a_failure():
    """Gate-off for the sweep itself.

    A sweep that cannot fail is decoration. Hand it a component that does not
    exist and assert the failure is both detected and *visible* — the second
    half is what the old "" return got wrong.
    """
    out = render_python_component_example("definitely_not_a_component", {})

    assert out, "a failed render must return something, not an empty string"
    assert _ERROR_MARKER in out


def test_component_classes_resolve_by_reading_the_module():
    """`qr_code` defines `QRCode`; guessing from the module name says `QrCode`."""
    cls, class_name = _load_component_class("qr_code")

    assert cls is not None
    assert class_name == "QRCode"
    _module, names = get_python_component_import("qr_code")
    assert class_name in names


@pytest.mark.parametrize("name", ["icon", "markdown", "qr_code"])
def test_the_three_that_were_blank_still_render(name):
    """Named explicitly so a regression says which component, not just "one"."""
    outputs = [
        render_python_component_example(name, kwargs) for kwargs in PYTHON_COMPONENT_EXAMPLES[name]
    ]

    assert outputs, f"{name} has no examples"
    assert all(out.strip() for out in outputs), f"{name} rendered nothing: {outputs!r}"
    assert not any(_ERROR_MARKER in out for out in outputs), outputs
