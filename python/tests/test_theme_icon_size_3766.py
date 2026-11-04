"""``theme_icon(size=...)`` no longer writes its argument into the markup (#3766).

``theme_icon`` built each icon with an f-string that interpolated ``size`` and returned
``mark_safe(icons.get(name, ""))``: ``size='1" onload="alert(1)'`` rendered
``<svg width="1" onload="alert(1)" ...>``, the banned ``mark_safe(f'...')`` with an
interpolated value (CLAUDE.md, Security Rules 1). It is now validated (a number or a CSS
length; anything else renders the default size and logs a warning) and the markup is built with
``format_html``. A value with no text at all (a ``__str__`` that raises, an int over the 4300-digit
limit) renders the default too, where it used to propagate.

What each class proves:

* ``TestHostileSize`` drives ``{% theme_icon %}`` through BOTH engines (Django and the
  djust backend) and reads the FINAL attributes of the printed ``<svg>`` with
  ``html.parser``, not the sanitiser's own verdict (#1825). ``theme_icon`` has no Rust tag
  handler of its own (``rust_handlers._THEME_TAG_NAMES`` lists the ``theme_tags`` tags
  only); under the djust backend it is the same Python function, reached through the
  library, so one function is what both engines run.
* ``TestBenignSizeIsUnchanged`` pins the output for the sizes that worked before against a
  golden written from the pre-fix markup, byte for byte.
* The repo-wide rule ("no ``mark_safe`` of a string built from a raw value") is
  ``test_safe_markup_guard_3766.py``.

Refs #3766, #3775, #3749, #1825, #1468.
"""

from __future__ import annotations

import ast
import logging
from decimal import Decimal
from fractions import Fraction
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import engines
from django.utils.functional import lazy

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[2]

ICONS = ("check", "x", "alert", "info")
DEFAULT = "20"

#: The pre-fix markup, with ``{size}`` where the old f-string interpolated it. A golden, on
#: purpose: it is what a page that worked before must still print, byte for byte.
GOLDEN = {
    "check": '<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"></polyline></svg>',
    "x": '<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>',
    "alert": '<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>',
    "info": '<svg width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="16" x2="12" y2="12"></line><line x1="12" y1="8" x2="12.01" y2="8"></line></svg>',
}


class _RaisingStr:
    """A value whose ``__str__`` raises (hostile or just broken)."""

    def __str__(self) -> str:
        raise RuntimeError("no text for you")


class _ChangingStr:
    """A value whose ``__str__`` answers differently each time (a TOCTOU between check and print)."""

    def __init__(self) -> None:
        self.calls = 0

    def __str__(self) -> str:
        self.calls += 1
        return "16" if self.calls == 1 else '16" onload="alert(1)'


#: What a caller can hand ``size``. Each must render the DEFAULT size and nothing else.
HOSTILE = {
    "attribute-injection": '1" onload="alert(1)',
    "single-quote": "1' onload='alert(1)",
    "tag-close": '16"><script>alert(1)</script>',
    "gt": "16>",
    "lt": "<16",
    "entity": "&quot; onload=&quot;alert(1)",
    "entity-digits": "&#49;&#54;",
    "trailing-space": "16 ",
    "leading-space": " 16",
    "inner-space": "1 6",
    "newline": "16\nonload=x",
    "tab": "16\t",
    "non-numeric": "abc",
    "empty": "",
    "unit-only": "px",
    "bad-unit": "16furlongs",
    "css-expression": "16px;x",
    "css-function": "calc(1px+1px)",
    "url": "url(javascript:alert(1))",
    "signed-plus": "+16",
    "signed-minus": "-16",
    "exponent": "1e3",
    "hex": "0x10",
    "underscore": "1_6",
    "fullwidth-digits": "１６",
    "arabic-digits": "١٦",
    "huge": "99999999",
    "huge-float": "1000.5",
    "overprecise": "1.2345",
    "dot-only": ".",
    "trailing-dot": "16.",
    "leading-dot": ".5",
    "very-long": "1" * 5000,
    "zero": "0",
    "zero-float": "0.0",
    "negative-int": -16,
    "negative-float": -1.5,
    "huge-int": 10**9,
    "bool-true": True,
    "bool-false": False,
    "float-inf": float("inf"),
    "float-nan": float("nan"),
    "float-exponent": 1e-05,
    "float-huge": 1e30,
    "bytes": b"16",
    "list": ["16"],
    "dict": {"a": 1},
    "object": object(),
    "decimal-nan": Decimal("NaN"),
    # Deliberate refusals (the number is outside what an icon can be, or is not a plain number):
    "tiny-float": 0.0001,
    "above-range-float": 1234.5,
    "above-range-int": 1001,
    "fraction": Fraction(3, 2),
    "decimal-huge": Decimal("1E+30"),
    "decimal-snan": Decimal("sNaN"),
    # ``str()`` itself fails: an int over the 4300-digit limit, and a raising ``__str__``.
    "int-over-str-limit": 10**5000,
    "raising-str": _RaisingStr(),
}

#: Values that worked before and must keep working: (value, what is printed).
BENIGN = {
    "int": (16, "16"),
    "default-int": (20, "20"),
    "digit-string": ("16", "16"),
    "small": (1, "1"),
    "big-allowed": (1000, "1000"),
    "float": (1.5, "1.5"),
    "float-whole": (16.0, "16"),
    # CSS units are case-insensitive, and a computed float carries binary noise.
    "upper-px": ("16PX", "16px"),
    "upper-rem": ("1.5REM", "1.5rem"),
    "mixed-case-unit": ("2Em", "2em"),
    "upper-percent": ("50%", "50%"),
    "computed-float": (14 * 1.1, "15.4"),
    "one-third": (1 / 3, "0.333"),
    "float-sum": (0.1 + 0.2, "0.3"),
    "decimal-exponent": (Decimal("1E+2"), "100"),
    "decimal-noise": (Decimal("15.40000000001"), "15.4"),
    "float-three-decimals": (0.125, "0.125"),
    "string-extra-zero": ("1.50", "1.5"),
    "decimal": (Decimal("16"), "16"),
    "decimal-frac": (Decimal("1.25"), "1.25"),
    "leading-zero": ("016", "16"),
    "frac-string": ("1.5", "1.5"),
    "px": ("24px", "24px"),
    "rem": ("1.5rem", "1.5rem"),
    "em": ("2em", "2em"),
    "ex": ("3ex", "3ex"),
    "pt": ("12pt", "12pt"),
    "percent": ("100%", "100%"),
    "lazy-string": (lazy(lambda: "18", str)(), "18"),
}


@pytest.fixture(scope="module", params=["django", "djust"])
def engine(request):
    if request.param == "django":
        return engines["django"]
    from djust.template import DjustTemplateBackend

    return DjustTemplateBackend({"NAME": "djust", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}})


def _render(engine, source: str, context: dict | None = None) -> str:
    return engine.from_string("{% load theme_components %}" + source).render(context or {})


def _icon(engine, size, name: str = "check") -> str:
    return _render(engine, "{% theme_icon name size=u %}", {"name": name, "u": size})


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    handle_startendtag = handle_starttag


def _tags(html: str) -> list[tuple[str, dict[str, str | None]]]:
    parser = _Tags()
    parser.feed(html)
    parser.close()
    return parser.tags


class TestHostileSize:
    @pytest.mark.parametrize("label", sorted(HOSTILE))
    @pytest.mark.parametrize("icon", ICONS)
    def test_renders_the_default_size_and_nothing_else(self, engine, label, icon):
        html = _icon(engine, HOSTILE[label], icon)
        assert html == GOLDEN[icon].format(size=DEFAULT), html

    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_the_printed_svg_carries_exactly_its_own_attributes(self, engine, label):
        """Read the final attributes (not the string): no event handler, nothing extra."""
        tags = _tags(_icon(engine, HOSTILE[label]))
        svg = [attrs for tag, attrs in tags if tag == "svg"]
        assert len(svg) == 1, tags
        assert set(svg[0]) == {"width", "height", "viewbox", "fill", "stroke", "stroke-width"}, svg
        assert svg[0]["width"] == svg[0]["height"] == DEFAULT
        assert [tag for tag, _ in tags if tag not in ("svg", "polyline")] == [], tags

    def test_the_reported_payload(self, engine):
        """The reproduction in the issue, verbatim."""
        html = _icon(engine, '1" onload="alert(1)')
        assert "onload" not in html
        assert "alert" not in html

    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_unknown_icon_name_prints_nothing_for_any_size(self, engine, label):
        assert _icon(engine, HOSTILE[label], "no-such-icon") == ""

    @pytest.mark.parametrize("label", sorted(HOSTILE))
    def test_a_hostile_size_warns_and_never_raises(self, label, caplog):
        from djust.theming.templatetags import theme_components

        with caplog.at_level(logging.WARNING, logger=theme_components.logger.name):
            theme_components.theme_icon("check", HOSTILE[label])
        warned = [r for r in caplog.records if r.name == theme_components.logger.name]
        assert len(warned) == 1, warned
        # The value is user data: its type is logged, never its content.
        assert warned[0].getMessage() == (
            f"theming: theme_icon size= (a {type(HOSTILE[label]).__name__}) is not a number "
            "or CSS length; rendering size 20"
        )

    def test_the_text_of_the_value_is_read_once(self):
        """What is judged is what is printed: a ``__str__`` that changes cannot pass then print."""
        from djust.theming.templatetags.theme_components import theme_icon

        value = _ChangingStr()
        html = theme_icon("check", value)
        assert value.calls == 1
        assert html == GOLDEN["check"].format(size="16")
        assert "onload" not in html

    def test_none_means_not_given(self, engine, caplog):
        from djust.theming.templatetags import theme_components

        with caplog.at_level(logging.DEBUG, logger=theme_components.logger.name):
            html = theme_components.theme_icon("check", None)
        assert html == GOLDEN["check"].format(size=DEFAULT)
        assert [r for r in caplog.records if r.name == theme_components.logger.name] == []

    def test_the_bad_size_is_not_a_template_error_under_either_engine(self, engine):
        # A literal in the template, not just a context value.
        html = _render(engine, "{% theme_icon 'check' size='abc' %}")
        assert html == GOLDEN["check"].format(size=DEFAULT)


class TestBenignSizeIsUnchanged:
    @pytest.mark.parametrize("label", sorted(BENIGN))
    @pytest.mark.parametrize("icon", ICONS)
    def test_prints_the_size_it_always_printed(self, engine, label, icon):
        value, shown = BENIGN[label]
        assert _icon(engine, value, icon) == GOLDEN[icon].format(size=shown)

    @pytest.mark.parametrize("icon", ICONS)
    def test_no_size_and_the_documented_literals(self, engine, icon):
        assert _render(engine, "{% theme_icon '" + icon + "' %}") == GOLDEN[icon].format(size=20)
        assert _render(engine, "{% theme_icon '" + icon + "' size=16 %}") == GOLDEN[icon].format(
            size=16
        )
        assert _render(engine, "{% theme_icon '" + icon + "' size='16' %}") == GOLDEN[icon].format(
            size=16
        )

    @pytest.mark.parametrize("label", sorted(BENIGN))
    def test_a_good_size_logs_nothing(self, label, caplog):
        from djust.theming.templatetags import theme_components

        with caplog.at_level(logging.DEBUG, logger=theme_components.logger.name):
            theme_components.theme_icon("check", BENIGN[label][0])
        assert [r for r in caplog.records if r.name == theme_components.logger.name] == []

    def test_the_result_is_marked_safe_text(self):
        from django.utils.safestring import SafeData

        from djust.theming.templatetags.theme_components import theme_icon

        assert isinstance(theme_icon("check", 16), SafeData)
        assert theme_icon("nope", 16) == ""


# --------------------------------------------------------------------------- #
# The repo rule. The first version of this file carried a detector that matched
# ``mark_safe(<f-string>)`` literally; it found nothing in the pre-fix ``theme_icon`` (the f-strings
# sat in a dict) and is replaced by ``scripts/lib/safe_markup_taint.py``, tested in
# ``test_safe_markup_guard_3766.py``.


class TestThemeIconBuildsNoStringOutOfSize:
    def test_the_function_holds_no_f_string_and_no_mark_safe_of_a_built_string(self):
        path = ROOT / "python/djust/theming/templatetags/theme_components.py"
        tree = ast.parse(path.read_text())
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "theme_icon"
        )
        assert not any(isinstance(n, ast.JoinedStr) for n in ast.walk(fn))
