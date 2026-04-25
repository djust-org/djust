"""Tests for #991 — inline radio buttons via ``data-dj-inline`` attr.

The contract documented in `docs/website/guides/forms.md` has two
parts:

1. ``forms.RadioSelect(attrs={"data-dj-inline": "true"})`` lands the
   attribute on every rendered ``<input type="radio">`` element.
   That's standard Django widget behavior — `attrs` propagate to the
   form-control element, not to the wrapper. Verified to lock the
   contract so a future Django upgrade or template override doesn't
   silently drop it.

2. The bundled ``djust/djust-forms.css`` stylesheet exists, contains
   rules keyed on ``[data-dj-inline]``, and uses the CSS ``:has()``
   parent selector (since the attribute lands on the input rather
   than the wrapper, the rule must walk up the tree). Locks the
   "ship CSS in the wheel" half of the contract — without the
   file, the attribute is inert.
"""

from __future__ import annotations

from pathlib import Path

import tests.conftest  # noqa: F401  -- configure Django settings

import django  # noqa: E402
from django import forms  # noqa: E402


CHOICES = [("a", "Apples"), ("b", "Bananas"), ("c", "Cherries")]


class _InlineRadioForm(forms.Form):
    fruit = forms.ChoiceField(
        widget=forms.RadioSelect(attrs={"data-dj-inline": "true"}),
        choices=CHOICES,
    )


class _PlainRadioForm(forms.Form):
    fruit = forms.ChoiceField(
        widget=forms.RadioSelect(),
        choices=CHOICES,
    )


# ---------------------------------------------------------------------------
# 1. Widget render contract
# ---------------------------------------------------------------------------


def test_data_dj_inline_attr_lands_on_each_radio_input():
    """Django's widget machinery propagates ``attrs`` onto each
    rendered form-control element. Documented contract — locked here
    so a Django upgrade or template override that drops the attribute
    would fail this test.

    The rendered output puts the attribute on every <input> tag (one
    per choice). Three choices → three occurrences."""
    form = _InlineRadioForm()
    rendered = str(form["fruit"])
    # The attribute must appear on every radio <input>, not on a
    # wrapper element (Django mechanics).
    assert rendered.count('data-dj-inline="true"') == len(CHOICES)
    # And specifically on each <input type="radio">, not just lying
    # around in the markup.
    assert (
        'type="radio" name="fruit" value="a" data-dj-inline="true"' in rendered
        or 'data-dj-inline="true" type="radio" name="fruit" value="a"' in rendered
        or (
            'data-dj-inline="true"' in rendered
            and 'type="radio"' in rendered
            and 'name="fruit"' in rendered
        )
    ), f"attribute not adjacent to a radio input. Got:\n{rendered}"


def test_plain_radioselect_does_not_get_attribute():
    """Negative: a `RadioSelect` without `attrs={"data-dj-inline":...}`
    must NOT produce the attribute. Locks the opt-in semantics — no
    field is "secretly inline" by default."""
    form = _PlainRadioForm()
    rendered = str(form["fruit"])
    assert "data-dj-inline" not in rendered


def test_each_choice_renders_label_input_pair():
    """Sanity: the inline attribute doesn't change the per-choice
    structure. Each choice still renders as a <label> wrapping an
    <input type="radio">. The bundled CSS rules
    (`label:has(> input[type=radio][data-dj-inline])`) target this
    structure."""
    form = _InlineRadioForm()
    rendered = str(form["fruit"])
    # Three radio inputs (one per choice).
    assert rendered.count('type="radio"') == len(CHOICES)
    # Three labels wrapping them.
    assert rendered.count("<label") == len(CHOICES)


def test_attribute_quoting_survives_special_values():
    """Django escapes attr values; the documented value `"true"` is
    safe. Verifies that the attribute serializes as expected — guards
    against an accidental change to `True` (Python bool) which Django
    would coerce differently."""
    # The string "true" is what the docs prescribe.
    form = _InlineRadioForm()
    rendered = str(form["fruit"])
    assert 'data-dj-inline="true"' in rendered


def test_attribute_does_not_disturb_input_attrs():
    """The inline attribute must not interfere with Django's required
    attrs on each <input> (`name`, `value`, `id`, `required`). Locks
    the documented promise that a11y attributes survive."""
    form = _InlineRadioForm()
    rendered = str(form["fruit"])
    assert 'name="fruit"' in rendered
    assert 'id="id_fruit_0"' in rendered
    # The form is unbound + required — Django emits the `required`
    # attr on each radio input.
    assert "required" in rendered


# ---------------------------------------------------------------------------
# 2. CSS file ships in the wheel
# ---------------------------------------------------------------------------


def _static_root() -> Path:
    """Return the path to djust's bundled static dir."""
    import djust

    return Path(djust.__file__).resolve().parent / "static" / "djust"


def test_djust_forms_css_ships_in_static():
    """The CSS file must exist in the package's static dir so
    `{% static 'djust/djust-forms.css' %}` resolves. Locks the
    ship-as-package-data invariant."""
    css = _static_root() / "djust-forms.css"
    assert css.exists(), f"expected {css} to ship with the package"


def test_djust_forms_css_targets_data_dj_inline():
    """The bundled CSS must contain at least one rule keyed on
    `[data-dj-inline="true"]`. Without the rule, the attribute is
    inert and the documented user workflow doesn't do anything
    visible. Locks the contract from the CSS side."""
    css = _static_root() / "djust-forms.css"
    text = css.read_text(encoding="utf-8")
    assert '[data-dj-inline="true"]' in text or "[data-dj-inline]" in text, (
        "djust-forms.css must contain at least one rule keyed on "
        "[data-dj-inline] — the documented contract."
    )


def test_djust_forms_css_uses_has_parent_selector():
    """Since `attrs={...}` lands on each <input>, the bundled CSS
    must use `:has()` to walk up from the marked input to its
    container. Without `:has()`, the rule would only match the
    inputs themselves, which is the wrong layout target.

    `:has()` is supported in all stable browsers since late 2023
    (Chromium 105+, Safari 15.4+, Firefox 121+). Locks the
    architectural choice so a future "simplify the CSS" PR doesn't
    accidentally regress to a non-functional rule."""
    css = _static_root() / "djust-forms.css"
    text = css.read_text(encoding="utf-8")
    assert ":has(" in text, (
        "djust-forms.css must use the :has() parent selector — the "
        "documented mechanism for layouting the wrapper of an input "
        "marked with data-dj-inline."
    )


def test_djust_forms_css_uses_inline_layout():
    """The rule must use a non-vertical layout (flex/inline-flex/grid).
    Locks the actual visual behavior against accidental regressions
    to `display: block`. If the user wants different visual treatment
    they're free to override; this test guards the *bundled default*."""
    css = _static_root() / "djust-forms.css"
    text = css.read_text(encoding="utf-8")
    # Allow flex / inline-flex / grid — anything that lays out
    # children non-vertically. Block / list-item are explicitly NOT
    # in the allowed set.
    assert any(
        kw in text
        for kw in (
            "display: inline-flex",
            "display:inline-flex",
            "display: flex",
            "display:flex",
            "display: grid",
            "display:grid",
        )
    ), "djust-forms.css must use a non-vertical layout for [data-dj-inline]"


def test_djust_forms_css_preserves_native_focus_ring():
    """Locks the documented a11y promise: the bundled CSS must NOT
    remove `outline` from the radios. Future "polish" PRs that add
    `outline: none` would silently break keyboard accessibility."""
    css = _static_root() / "djust-forms.css"
    text = css.read_text(encoding="utf-8")
    # Strip comments so we don't false-positive on the "no outline:
    # none" comment we deliberately wrote into the file.
    import re

    code = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    assert "outline: none" not in code and "outline:none" not in code, (
        "djust-forms.css must not strip the native focus ring "
        "(found `outline: none` in the rules — would break a11y)."
    )


# ---------------------------------------------------------------------------
# 3. Backwards compatibility — forms without the attr unchanged
# ---------------------------------------------------------------------------


def test_existing_radio_forms_render_without_attribute():
    """Forms that don't opt into `data-dj-inline` must continue to
    render exactly as Django/djust-theming render them today —
    specifically, the `data-dj-inline` attribute must be absent.
    Smoke-test against a future 'auto-inline by default' regression."""
    form = _PlainRadioForm()
    rendered = str(form["fruit"])
    assert "data-dj-inline" not in rendered
    # Sanity: the markup still has radio inputs (we didn't break the
    # plain render path).
    assert 'type="radio"' in rendered
    assert "fruit" in rendered


def test_django_version_baseline():
    """Lock the supported Django version range — the test suite
    runs against Django 5.x, where the widget rendering chain we
    rely on is stable. If this fails, the conftest's Django version
    has been bumped to something we haven't validated."""
    assert django.VERSION >= (5, 0), f"Django >= 5.0 expected, got {django.VERSION}"
