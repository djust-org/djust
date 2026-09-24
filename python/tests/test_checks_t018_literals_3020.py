"""T018 must not report literals or filter names in ``{% if %}`` (#3020).

djustlive got 92 T018 notices on upgrading to 1.2.0, nearly all for quoted
string literals in comparisons (``{% elif app.status == 'deploying' %}``
reported ``'deploying'`` as an undefined variable) and one for a filter name
(``|length``). The ``if`` / ``elif`` / ``while`` scan now blanks string and
numeric literals and filter names before looking for identifiers; a variable
filter ARGUMENT (``|default:fallback``) is still a reference and still checked.
"""

import gc
import re

import pytest

from djust.management.commands.djust_typecheck import _extract_referenced_names


def _names(src):
    return [name for name, _line in _extract_referenced_names(src)]


class TestIfExpressionScan:
    @pytest.mark.parametrize(
        "src,expected",
        [
            ("{% elif app.status == 'deploying' %}", ["app"]),
            ('{% if app.status == "running" %}', ["app"]),
            ("{% if x == 'a b' or y != 'it\\'s' %}", ["x", "y"]),
            ("{% if items|length > 0 %}", ["items"]),
            ("{% if items|length>0 %}", ["items"]),
            ('{% if a|date:"Y" == "2020" %}', ["a"]),
            ("{% if x|yesno:'on,off' %}", ["x"]),
            ("{% if n > 1.5e3 and m < -2 %}", ["n", "m"]),
            ("{% if item.0 == 'a' %}", ["item"]),
            ("{% while count < 10 %}", ["count"]),
            # A variable filter argument is a reference: Django resolves it.
            ("{% if v|default:fallback == 'z' %}", ["v", "fallback"]),
            # A literal containing an operator or a dotted path is one token.
            ("{% if s == 'a.b and c' %}", ["s"]),
        ],
    )
    def test_only_variables_are_reported(self, src, expected):
        assert _names(src) == expected

    def test_line_numbers_are_unchanged(self):
        src = "<p>\n{% if a == 'x' %}\n{% elif b == 'y' %}{% endif %}"
        assert _extract_referenced_names(src) == [("a", 2), ("b", 3)]


def _liveview_available():
    try:
        from djust.live_view import LiveView  # noqa: F401

        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _liveview_available(), reason="Rust extension not available")
def test_t018_fires_only_on_the_undefined_name_3020():
    """The djustlive shape end to end: literals, a filter name and a filter
    argument around one genuinely undefined name."""
    from djust.checks.templates import check_undefined_template_vars
    from djust.live_view import LiveView

    class T018Literals3020View(LiveView):
        template = (
            "<div dj-root>"
            "{% if app.status == 'running' %}r"
            '{% elif app.status == "deploying" %}d'
            "{% elif app.builds|length > 3 %}b"
            "{% elif app.status == missing_status %}m"
            "{% endif %}"
            "</div>"
        )

        def mount(self, request, **kwargs):
            self.app = {"status": "running", "builds": []}

    try:
        errors = check_undefined_template_vars(None)
        names = set()
        for e in errors:
            if e.id == "djust.T018" and "T018Literals3020View" in e.msg:
                m = re.search(r"undefined variable '([^']+)'", e.msg)
                if m:
                    names.add(m.group(1))
        assert names == {"missing_status"}
    finally:
        del T018Literals3020View
        gc.collect()
