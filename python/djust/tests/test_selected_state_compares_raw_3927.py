"""An option is drawn as selected by comparing RAW text; escaping is for the output only (#3927).

``combobox`` compared the HTML-escaped option value with the raw selected value, so a value that
escapes to something different (``&``, ``<``, ``>``, ``"``, ``'``) was never drawn as selected,
in single or in multi mode. The same shape was in the native ``dj_select`` and ``dj_radio``
handlers and in the class-based ``DependentSelect``.

Every cell renders through BOTH engines (the Django tag, and the Rust engine, where ``combobox``
runs on the ``{% load %}`` bridge so its output is the Python tag's). The cells:

* a value with each character that escapes, and an already-escaped-looking value, which must stay
  DISTINCT from the plain one (``a&amp;b`` is not ``a&b``);
* ordinary values unchanged, an empty value, ``None``, integers;
* marked-safe and lazy values (a marker means "no escaping needed in a text slot"; it is not part
  of the text that is compared).
"""

from __future__ import annotations

import html
import re

import pytest
from django.utils.functional import lazy
from django.utils.safestring import mark_safe

from djust.components.components.dependent_select import DependentSelect
from djust.tests.test_components_csp_aria import _django, _rust

ENGINES = [("django", _django), ("rust", _rust)]

#: Each character that HTML escapes, an already-escaped-looking value, an ordinary one.
TRICKY = ["a&b", "a<b", "a>b", 'say "hi"', "it's", "<b>&\"'</b>", "a&amp;b", "ab"]

_lazy_str = lazy(lambda text: text, str)


def _combobox_options(out: str) -> list[tuple[str, bool]]:
    """``[(raw data-value, drawn selected)]`` per option, and the class and aria agree."""
    found = []
    for cls, aria, value in re.findall(
        r'<div class="combobox-option([^"]*)" id="[^"]*" role="option" '
        r'aria-selected="(true|false)" dj-click="[^"]*" data-value="([^"]*)"',
        out,
    ):
        selected = "combobox-option-selected" in cls
        assert selected == (aria == "true"), f"class and aria-selected disagree in: {out}"
        found.append((html.unescape(value), selected))
    return found


def _single(render, options, value):
    return _combobox_options(
        render(
            '{% combobox name="c" options=o value=v %}',
            {"o": [{"value": o, "label": f"L{i}"} for i, o in enumerate(options)], "v": value},
        )
    )


def _multi(render, options, selected):
    return _combobox_options(
        render(
            '{% combobox name="c" options=o multiple=True selected=s %}',
            {"o": [{"value": o, "label": f"L{i}"} for i, o in enumerate(options)], "s": selected},
        )
    )


@pytest.mark.parametrize("engine,render", ENGINES)
@pytest.mark.parametrize("value", TRICKY)
class TestComboboxSelectedIsDrawn:
    def test_single_mode(self, engine, render, value):
        assert _single(render, ["x", value], value) == [("x", False), (value, True)]

    def test_multi_mode(self, engine, render, value):
        assert _multi(render, ["x", value], [value]) == [("x", False), (value, True)]

    def test_multi_mode_with_a_string_option(self, engine, render, value):
        """An option that is a bare string (not a dict) compares the same way."""
        out = render(
            '{% combobox name="c" options=o multiple=True selected=s %}',
            {"o": ["x", value], "s": [value]},
        )
        assert _combobox_options(out) == [("x", False), (value, True)]

    def test_single_mode_with_a_string_option(self, engine, render, value):
        out = render('{% combobox name="c" options=o value=v %}', {"o": ["x", value], "v": value})
        assert _combobox_options(out) == [("x", False), (value, True)]


@pytest.mark.parametrize("engine,render", ENGINES)
class TestEscapedLookingValueStaysDistinct:
    """``a&amp;b`` as a RAW value is a different value from ``a&b``: only the equal one is selected."""

    @pytest.mark.parametrize(
        "selected,expected",
        [
            ("a&b", [("a&b", True), ("a&amp;b", False)]),
            ("a&amp;b", [("a&b", False), ("a&amp;b", True)]),
        ],
    )
    def test_single_mode(self, engine, render, selected, expected):
        assert _single(render, ["a&b", "a&amp;b"], selected) == expected

    @pytest.mark.parametrize(
        "selected,expected",
        [
            (["a&b"], [("a&b", True), ("a&amp;b", False)]),
            (["a&amp;b"], [("a&b", False), ("a&amp;b", True)]),
            (["a&b", "a&amp;b"], [("a&b", True), ("a&amp;b", True)]),
        ],
    )
    def test_multi_mode(self, engine, render, selected, expected):
        assert _multi(render, ["a&b", "a&amp;b"], selected) == expected


@pytest.mark.parametrize("engine,render", ENGINES)
class TestOrdinaryAndEdgeValuesAreUnchanged:
    def test_ordinary_values(self, engine, render):
        assert _single(render, ["ab", "cd"], "cd") == [("ab", False), ("cd", True)]
        assert _multi(render, ["ab", "cd", "ef"], ["ab", "ef"]) == [
            ("ab", True),
            ("cd", False),
            ("ef", True),
        ]

    def test_nothing_selected(self, engine, render):
        assert _single(render, ["a&b"], "") == [("a&b", False)]
        assert _multi(render, ["a&b"], []) == [("a&b", False)]

    def test_an_empty_option_value_is_selected_by_an_empty_value(self, engine, render):
        assert _single(render, ["", "a&b"], "") == [("", True), ("a&b", False)]

    def test_integer_values_match_by_their_text(self, engine, render):
        assert _single(render, [1, 2], 2) == [("1", False), ("2", True)]
        assert _single(render, [1, 2], "2") == [("1", False), ("2", True)]
        assert _multi(render, [1, 2, 3], [1, 3]) == [("1", True), ("2", False), ("3", True)]
        assert _multi(render, [1, 2], ["2"]) == [("1", False), ("2", True)]

    def test_none_is_compared_by_its_text_like_any_other_value(self, engine, render):
        """``None`` stringifies on both sides, as it did: an option ``None`` matches a value ``None``."""
        assert _single(render, [None, "a&b"], None) == [("None", True), ("a&b", False)]
        assert _single(render, ["a&b"], None) == [("a&b", False)]

    def test_the_label_is_still_escaped_as_text(self, engine, render):
        out = render(
            '{% combobox name="c" options=o value=v %}',
            {"o": [{"value": "a&b", "label": "<i>&</i>"}], "v": "a&b"},
        )
        assert ">&lt;i&gt;&amp;&lt;/i&gt;</div>" in out and "<i>" not in out
        assert 'data-value="a&amp;b"' in out


@pytest.mark.parametrize("engine,render", ENGINES)
class TestMarkedSafeAndLazyValuesCompareByTheirText:
    """The marker is not part of the text compared (#3898): ``mark_safe("a&b")`` is the text ``a&b``."""

    def test_marked_safe_option_value_against_a_plain_selection(self, engine, render):
        assert _single(render, [mark_safe("a&b")], "a&b") == [("a&b", True)]
        assert _multi(render, [mark_safe("a&b")], ["a&b"]) == [("a&b", True)]

    def test_plain_option_value_against_a_marked_safe_selection(self, engine, render):
        assert _single(render, ["a&b"], mark_safe("a&b")) == [("a&b", True)]
        assert _multi(render, ["a&b"], [mark_safe("a&b")]) == [("a&b", True)]

    def test_marked_safe_escaped_looking_text_is_not_the_plain_text(self, engine, render):
        assert _single(render, [mark_safe("a&amp;b")], "a&b") == [("a&amp;b", False)]
        assert _multi(render, [mark_safe("a&amp;b")], ["a&b"]) == [("a&amp;b", False)]
        assert _single(render, ["a&b"], mark_safe("a&amp;b")) == [("a&b", False)]

    def test_lazy_option_value(self, engine, render):
        assert _single(render, [_lazy_str("a&b")], "a&b") == [("a&b", True)]
        assert _multi(render, [_lazy_str("a&b")], ["a&b"]) == [("a&b", True)]

    def test_a_marked_safe_value_is_still_escaped_in_the_attribute(self, engine, render):
        out = render(
            '{% combobox name="c" options=o value=v %}',
            {"o": [{"value": mark_safe('"><b>'), "label": "L"}], "v": '"><b>'},
        )
        assert 'data-value="&quot;&gt;&lt;b&gt;"' in out and '"><b>' not in out


class _Liar(str):
    """A ``str`` whose ``str()`` is itself and whose equality says yes to anything with its hash."""

    def __str__(self) -> str:
        return self

    def __eq__(self, other: object) -> bool:
        return True

    def __hash__(self) -> int:
        return hash("y")


def test_a_str_subclass_cannot_decide_the_comparison_by_its_own_equality():
    """The comparison reads the EXACT ``str`` of each side (``_exact_str``), so a value type that
    answers ``==`` / ``hash`` for itself does not choose the selected option. (Django engine only:
    the Rust engine turns the context value into a plain ``str`` before the tag sees it.)"""
    liar = _Liar("zzz")
    assert _single(_django, [liar], "y") == [("zzz", False)]
    assert _single(_django, ["y"], liar) == [("y", False)]
    assert _multi(_django, [liar], ["y"]) == [("zzz", False)]
    assert _multi(_django, ["y"], [liar]) == [("y", False)]


# --- The same shape in the sinks the audit found (#1646) ---------------------------------------


def _selects(out: str) -> list[tuple[str, bool]]:
    return [
        (html.unescape(v), bool(sel))
        for v, sel in re.findall(r'<option value="([^"]*)"( selected)?>', out)
    ]


@pytest.mark.parametrize("engine,render", ENGINES)
@pytest.mark.parametrize("value", TRICKY)
def test_dj_select_marks_the_selected_option_on_both_engines(engine, render, value):
    out = render(
        '{% dj_select name="c" options=o value=v %}',
        {"o": [{"value": "x", "label": "X"}, {"value": value, "label": "Y"}], "v": value},
    )
    assert _selects(out) == [("x", False), (value, True)]


@pytest.mark.parametrize("engine,render", ENGINES)
def test_dj_select_keeps_an_escaped_looking_value_distinct(engine, render):
    out = render(
        '{% dj_select name="c" options=o value=v %}',
        {"o": [{"value": "a&b", "label": "A"}, {"value": "a&amp;b", "label": "B"}], "v": "a&amp;b"},
    )
    assert _selects(out) == [("a&b", False), ("a&amp;b", True)]


@pytest.mark.parametrize("engine,render", ENGINES)
@pytest.mark.parametrize("value", TRICKY)
def test_dj_radio_is_checked_when_it_equals_the_current_value(engine, render, value):
    src = '{% dj_radio name="c" value=v current_value=cur %}'
    assert " checked" in render(src, {"v": value, "cur": value})
    assert " checked" not in render(src, {"v": value, "cur": "other"})


@pytest.mark.parametrize("engine,render", ENGINES)
def test_dj_radio_keeps_an_escaped_looking_value_distinct(engine, render):
    src = '{% dj_radio name="c" value=v current_value=cur %}'
    assert " checked" not in render(src, {"v": "a&amp;b", "cur": "a&b"})
    assert " checked" not in render(src, {"v": "a&b", "cur": "a&amp;b"})


@pytest.mark.parametrize("value", TRICKY)
def test_dependent_select_class_marks_the_selected_option(value):
    out = DependentSelect(
        name="c", parent="p", options=[{"value": "x", "label": "X"}, {"value": value}], value=value
    ).render()
    assert _selects(out) == [("", False), ("x", False), (value, True)]


def test_dependent_select_class_keeps_an_escaped_looking_value_distinct():
    options = [{"value": "a&b"}, {"value": "a&amp;b"}]
    out = DependentSelect(name="c", parent="p", options=options, value="a&amp;b").render()
    assert _selects(out) == [("", False), ("a&b", False), ("a&amp;b", True)]
