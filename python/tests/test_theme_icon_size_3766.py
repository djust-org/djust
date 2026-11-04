"""``theme_icon(size=...)`` no longer writes its argument into the markup (#3766).

``theme_icon`` built each icon with an f-string that interpolated ``size`` and returned
``mark_safe(icons.get(name, ""))``: ``size='1" onload="alert(1)'`` rendered
``<svg width="1" onload="alert(1)" ...>``, the banned ``mark_safe(f'...')`` with an
interpolated value (CLAUDE.md, Security Rules 1). It is now validated (a number or a CSS
length; anything else renders the default size and logs a warning, never raising) and the
markup is built with ``format_html``.

What each class proves:

* ``TestHostileSize`` drives ``{% theme_icon %}`` through BOTH engines (Django and the
  djust backend) and reads the FINAL attributes of the printed ``<svg>`` with
  ``html.parser``, not the sanitiser's own verdict (#1825). ``theme_icon`` has no Rust tag
  handler of its own (``rust_handlers._THEME_TAG_NAMES`` lists the ``theme_tags`` tags
  only); under the djust backend it is the same Python function, reached through the
  library, so one function is what both engines run.
* ``TestBenignSizeIsUnchanged`` pins the output for the sizes that worked before against a
  golden written from the pre-fix markup, byte for byte.
* ``TestNoInterpolatedMarkSafeInTheming`` is derived from the source (an AST walk of every
  file under ``djust/theming``): no ``mark_safe`` call takes an interpolated string. Its
  detector is checked against a synthetic bug trigger of each shape, so it cannot go green
  by matching nothing.

Refs #3766, #3749, #1825, #1468.
"""

from __future__ import annotations

import ast
import logging
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.template import engines
from django.utils.functional import lazy

pytestmark = pytest.mark.theming

ROOT = Path(__file__).resolve().parents[2]
THEMING = ROOT / "python/djust/theming"

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
# The repo rule, derived from the source


def _interpolates(node: ast.AST) -> bool:
    """Whether an expression builds a string out of other values (f-string, ``%``, ``.format``, ``+``)."""
    for child in ast.walk(node):
        if isinstance(child, ast.JoinedStr) and any(
            isinstance(v, ast.FormattedValue) for v in child.values
        ):
            return True
        if isinstance(child, ast.BinOp) and isinstance(child.op, (ast.Mod, ast.Add)):
            # ``"a" + "b"`` and ``"%d" % 3`` are constant folds; anything else is data.
            if not (isinstance(child.left, ast.Constant) and isinstance(child.right, ast.Constant)):
                return True
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Attribute)
            and child.func.attr == "format"
        ):
            return True
    return False


def _mark_safe_interpolations(source: str) -> list[int]:
    lines = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "mark_safe")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "mark_safe")
            )
            and node.args
            and _interpolates(node.args[0])
        ):
            lines.append(node.lineno)
    return lines


class TestNoInterpolatedMarkSafeInTheming:
    def test_the_detector_sees_each_shape(self):
        """A synthetic bug trigger per shape: a detector that matches nothing proves nothing."""
        trigger = {
            "f-string": 'mark_safe(f"<svg width={size}>")',
            "format": 'mark_safe("<svg width={}>".format(size))',
            "percent": 'mark_safe("<svg width=%s>" % size)',
            "concat": 'mark_safe("<svg width=" + size + ">")',
            "module-attr": 'safestring.mark_safe(f"<i>{x}</i>")',
        }
        for label, code in trigger.items():
            assert _mark_safe_interpolations(code), label
        clean = {
            "constant": 'mark_safe("<br>")',
            "adjacent-constants": 'mark_safe("<a>" "<b>")',
            "constant-concat": 'mark_safe("<a>" + "<b>")',
            "f-string-without-values": 'mark_safe(f"<br>")',
            "format_html": 'format_html("<i>{}</i>", x)',
            "name": "mark_safe(rendered)",
        }
        for label, code in clean.items():
            assert not _mark_safe_interpolations(code), label

    def test_the_theming_package_has_no_interpolated_mark_safe(self):
        files = sorted(THEMING.rglob("*.py"))
        assert files, "found no djust_theming sources to scan"
        found = {}
        for path in files:
            lines = _mark_safe_interpolations(path.read_text())
            if lines:
                found[str(path.relative_to(ROOT))] = lines
        assert found == {}, (
            "mark_safe() of an interpolated string (use format_html / escape, "
            f"CLAUDE.md Security Rules 1): {found}"
        )

    def test_theme_icon_builds_no_string_out_of_size(self):
        """The old shape hid the f-string in a dict that ``mark_safe`` later read."""
        path = THEMING / "templatetags/theme_components.py"
        tree = ast.parse(path.read_text())
        fn = next(
            n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "theme_icon"
        )
        assert not _interpolates(fn), "theme_icon interpolates a value into a string"
        assert not any(isinstance(n, ast.JoinedStr) for n in ast.walk(fn))
