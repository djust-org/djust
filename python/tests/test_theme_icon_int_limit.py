"""``theme_icon(size=...)`` never raises for a long digit string, on ANY interpreter (follow-up to #3766).

``tests/test_theme_icon_size_3766.py::TestHostileSize`` failed on the Python 3.15 CI cell (and
passed on 3.12) for its ``very-long`` row, a 5000-digit string:

    ValueError: Exceeds the limit (4300 digits) for integer string conversion

Why it differs by interpreter: the size text matches ``_ICON_SIZE_RE`` (it is all digits), and
``_icon_size`` then built ``Decimal(<the digits>)``. CPython's C ``_decimal`` parses digits itself
and never calls ``int()``, so a 5000-digit string is fine there (and the number is then refused
for being over 1000). The pure-Python ``_pydecimal`` (what the 3.15.1 runner's ``decimal`` was
running; the traceback is ``_pydecimal.py:507 ... int(intpart+fracpart)``) builds the value with
``int(intpart + fracpart)``, which is subject to ``sys.get_int_max_str_digits()`` (default 4300).
The ``Decimal(...)`` call sat outside the ``try`` that guards the text, so the ``ValueError``
reached the template. ``theme_icon`` documents that it WARNS and NEVER RAISES.

These tests make that interpreter difference an explicit input rather than a property of the
machine they run on:

* ``TestUnderThePurePythonDecimal`` swaps the module's ``Decimal`` for ``_pydecimal``'s and
  forces the digit limit, so they fail on 3.12 too without the fix.
* ``TestNoLongDigitStringReachesDecimal`` records the length of every string handed to
  ``Decimal`` and asserts it stays tiny, which holds (and fails without the fix) whichever
  implementation is installed.

Refs #3766, #3775, #1468.
"""

from __future__ import annotations

import _pydecimal
import logging
import sys

import pytest

pytestmark = pytest.mark.theming

DEFAULT = "20"

#: Digit strings an attacker (or a bad data column) can hand ``size``: (input, what is printed).
#: ``None`` means "refused: the default size and one warning".
LONG = {
    "ones": ("1" * 5000, None),
    "nines": ("9" * 5000, None),
    "just-over-limit": ("1" * 4301, None),
    "min-settable-limit": ("1" * 700, None),
    "huge": ("1" * 200_000, None),
    "long-fraction-part": ("1" * 5000 + ".5", None),
    "with-unit": ("1" * 5000 + "px", None),
    "all-zeros": ("0" * 5000, None),
    "zeros-then-dot": ("0" * 5000 + ".0", None),
    # Leading zeros are not magnitude: these worked before ("016" prints 16) and still do.
    "leading-zeros": ("0" * 5000 + "16", "16"),
    "leading-zeros-unit": ("0" * 5000 + "1.5rem", "1.5rem"),
    "leading-zeros-at-the-max": ("0" * 5000 + "1000", "1000"),
    "leading-zeros-over-the-max": ("0" * 5000 + "1001", None),
}


@pytest.fixture(params=[4300, 640], ids=["limit-4300-the-default", "limit-640-the-minimum"])
def pure_python_decimal(monkeypatch, request):
    """Run ``theme_components`` on ``_pydecimal`` with a digit limit in force (4300, or 640).

    The module's constants are ``Decimal`` objects of the C implementation; a C and a pure-Python
    ``Decimal`` do not compare, so they are rebuilt from the same values.
    """
    from djust.theming.templatetags import theme_components as tc

    monkeypatch.setattr(tc, "Decimal", _pydecimal.Decimal)
    monkeypatch.setattr(tc, "_ICON_SIZE_STEP", _pydecimal.Decimal("0.001"))
    monkeypatch.setattr(tc, "_ICON_SIZE_MAX", _pydecimal.Decimal(1000))
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(request.param)
    try:
        yield tc
    finally:
        sys.set_int_max_str_digits(previous)


def test_the_premise_the_pure_python_decimal_really_raises_here(pure_python_decimal):
    """Without this the tests below prove nothing: the digit limit must bite ``_pydecimal``."""
    with pytest.raises(ValueError, match="Exceeds the limit"):
        _pydecimal.Decimal("1" * (sys.get_int_max_str_digits() + 1))


class TestUnderThePurePythonDecimal:
    @pytest.mark.parametrize("label", sorted(LONG))
    def test_a_long_digit_string_never_raises_and_prints_what_it_means(
        self, pure_python_decimal, label, caplog
    ):
        text, shown = LONG[label]
        with caplog.at_level(logging.WARNING, logger=pure_python_decimal.logger.name):
            html = pure_python_decimal.theme_icon("check", text)
        warned = [r for r in caplog.records if r.name == pure_python_decimal.logger.name]
        if shown is None:
            assert f'width="{DEFAULT}" height="{DEFAULT}"' in html, html
            assert len(warned) == 1, warned
        else:
            assert f'width="{shown}" height="{shown}"' in html, html
            assert warned == []

    @pytest.mark.parametrize("label", sorted(LONG))
    def test_the_size_helper_is_total(self, pure_python_decimal, label):
        text, shown = LONG[label]
        assert pure_python_decimal._icon_size(text) == (shown or DEFAULT)

    def test_a_huge_decimal_value_is_refused_too(self, pure_python_decimal):
        # The float / Decimal branch rounds before it matches; a 5000-digit Decimal cannot be
        # rounded to three decimals (precision), and must also be the default, not an error.
        # (built from a digit tuple: a 5000-digit STRING cannot be parsed under the limit)
        huge = _pydecimal.Decimal((0, (1,) * 5000, 0))
        assert pure_python_decimal._icon_size(huge) == DEFAULT

    def test_a_template_with_a_long_size_renders_under_both_engines(self, pure_python_decimal):
        from django.template import engines

        html = (
            engines["django"]
            .from_string("{% load theme_components %}{% theme_icon 'check' size=u %}")
            .render({"u": "1" * 5000})
        )
        assert 'width="20"' in html


class TestNoLongDigitStringReachesDecimal:
    """Whichever ``Decimal`` is installed, the digits are bounded BEFORE it is called."""

    @pytest.fixture
    def seen(self, monkeypatch):
        from djust.theming.templatetags import theme_components as tc

        lengths: list[int] = []
        real = tc.Decimal

        class Spy(real):  # type: ignore[valid-type, misc]
            def __new__(cls, value="0", *args, **kwargs):
                lengths.append(len(str(value)))
                return super().__new__(cls, value, *args, **kwargs)

        monkeypatch.setattr(tc, "Decimal", Spy)
        return lengths

    @pytest.mark.parametrize("label", sorted(LONG))
    def test_decimal_is_only_ever_given_a_short_string(self, seen, label):
        from djust.theming.templatetags import theme_components as tc

        text, shown = LONG[label]
        assert tc._icon_size(text) == (shown or DEFAULT)
        # Even the longest valid size ("1000.000") is 8 characters.
        assert max(seen, default=0) <= 16, seen


class TestADecimalThatRaisesIsStillTheDefault:
    """The defensive catch: whatever ``Decimal`` raises, ``theme_icon`` does not."""

    @pytest.mark.parametrize(
        "exc", [ValueError("x"), ArithmeticError("x"), _pydecimal.InvalidOperation()]
    )
    def test_decimal_failing_on_a_matching_string_is_the_default(self, monkeypatch, exc, caplog):
        from djust.theming.templatetags import theme_components as tc

        def boom(*args, **kwargs):
            raise exc

        monkeypatch.setattr(tc, "Decimal", boom)
        with caplog.at_level(logging.WARNING, logger=tc.logger.name):
            assert tc._icon_size("16") == DEFAULT
        assert len([r for r in caplog.records if r.name == tc.logger.name]) == 1


class TestTheDecimalStateTagSibling:
    """``decode_state_roundtrip`` must not crash a reconnect on a colliding dict (same class).

    ``{"__djust_decimal__": "<5000 digits>"}`` is a user dict that collides with the state tag.
    Its docstring promises such a dict is left alone, but only ``InvalidOperation`` was caught,
    and the pure-Python ``Decimal`` raises ``ValueError`` at the digit limit.
    """

    def test_a_long_payload_is_left_alone_not_raised(self, monkeypatch):
        from djust import serialization

        monkeypatch.setattr(serialization, "Decimal", _pydecimal.Decimal)
        monkeypatch.setattr(serialization, "InvalidOperation", _pydecimal.InvalidOperation)
        previous = sys.get_int_max_str_digits()
        sys.set_int_max_str_digits(4300)
        try:
            colliding = {serialization.STATE_DECIMAL_TAG: "1" * 5000}
            assert serialization.decode_state_roundtrip(colliding) == colliding
        finally:
            sys.set_int_max_str_digits(previous)
