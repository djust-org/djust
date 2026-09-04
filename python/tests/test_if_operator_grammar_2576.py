"""`{% if %}` operator grammar — malformed conditions refuse at parse time
like Django's `smartif.IfParser` (#2576).

Django compiles an ``{% if %}`` condition through
``django.template.smartif.IfParser`` (a top-down operator-precedence parser)
at **template-compile time**. Fourteen malformed operator streams raise
``TemplateSyntaxError`` there — an empty condition, a dangling binary
operator (``foo and``), an operator with no left operand (``and``), two
adjacent operands (``abc def``), ``not`` used as an infix operator
(``a not b``), an ``{% else %}`` carrying arguments (``{% else if … %}``),
and a single ``=`` where ``==`` was meant.

djust's Rust template engine (`crates/djust_templates/src/parser.rs`)
previously accepted every one of them and rendered a branch. This module
pins the fix: a faithful port of ``smartif``'s ``nud``/``led``/``lbp``
algorithm (`validate_if_grammar`) runs at the exact point Django's parser
refuses — during ``{% if %}`` / ``{% elif %}`` tag parsing — plus an
``{% else %}``-takes-no-arguments refusal.

The condition grammar still uses djust's ``"Invalid {% if %} condition"``
wording; malformed else clauses use Django's message. The differential below
compares the **presence and type family** of the raised exception. Both are measured
against LIVE, in-process Django on BOTH djust entry points (the plain
``DjustTemplateBackend`` and the ``RustLiveView`` LiveView entry).

The single most likely regression vector is a false positive — a VALID
``{% if %}`` condition that newly refuses. ``TestNoValidConditionRefuses``
sweeps a broad corpus of valid conditions (every operator, chained
comparisons, filters, ``not not``, ``elif``/``else`` chains) and asserts
NONE of them raises on either entry point.

Refs #2576, #2557 (the parse-time scoreboard cells), #2419, #2411, #1646.
"""

from __future__ import annotations

from typing import Callable

import pytest

pytest.importorskip("django")

import django  # noqa: E402
from django.conf import settings  # noqa: E402

if not settings.configured:
    settings.configure(
        DEBUG=True,
        INSTALLED_APPS=["djust"],
        DATABASES={},
        TEMPLATES=[],
    )
    django.setup()

from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.template import TemplateSyntaxError  # noqa: E402

from djust import _rust  # noqa: E402
from djust.template_backend import DjustTemplateBackend  # noqa: E402


# ---------------------------------------------------------------------------
# The three parse paths. Django and both djust entries refuse a malformed
# condition at *compile/parse* time (before any render), so the "parse" here
# is construction of the template object.
# ---------------------------------------------------------------------------
def django_parse(source: str) -> None:
    # Django raises TemplateSyntaxError at Template() construction for a
    # malformed {% if %} condition (smartif runs inside do_if at compile).
    DjangoTemplate(source).render(DjangoContext({}))


def backend_parse(source: str) -> None:
    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    backend.from_string(source)


def liveview_parse(source: str) -> None:
    # The LiveView entry parses lazily: a malformed condition raises at
    # render(), not construction. (It surfaces every Rust parse error as a
    # bare RuntimeError — a pre-existing, tag-agnostic wrapping divergence;
    # e.g. `{% for x %}` behaves identically — so this entry is measured for
    # refuse-or-not, not for the TemplateSyntaxError type. The scoreboard /
    # Django-parity path is the backend, which raises DjustTemplateSyntaxError,
    # a TemplateSyntaxError subclass.)
    view = _rust.RustLiveView(source, [])
    try:
        view.set_raw_py_values({})
    except Exception:  # noqa: BLE001 — irrelevant to a parse-grammar refusal
        pass
    view.render()


def outcome(parse: Callable[[str], None], source: str) -> tuple:
    """``("ok",)`` or ``("raise", is_template_syntax_error)`` — comparable
    across engines WITHOUT depending on the (deliberately different) message.
    """
    try:
        parse(source)
        return ("ok",)
    except TemplateSyntaxError:
        return ("raise", True)
    except Exception:  # noqa: BLE001 — any other type is a mismatch we want to see
        return ("raise", False)


# ---------------------------------------------------------------------------
# The 14 malformed cells, verbatim from Django's own
# tests/template_tests/syntax_tests/test_if.py (`if_tag_error01`..`error12`,
# `else_if_tag_error01`, `if_tag_single_eq`). Each MUST refuse.
# ---------------------------------------------------------------------------
MALFORMED_CASES = [
    pytest.param("{% if %}yes{% endif %}", id="error01-empty"),
    pytest.param("{% if foo and %}yes{% else %}no{% endif %}", id="error02-trailing-and"),
    pytest.param("{% if foo or %}yes{% else %}no{% endif %}", id="error03-trailing-or"),
    pytest.param(
        "{% if not foo and %}yes{% else %}no{% endif %}", id="error04-trailing-and-after-not"
    ),
    pytest.param(
        "{% if not foo or %}yes{% else %}no{% endif %}", id="error05-trailing-or-after-not"
    ),
    pytest.param("{% if abc def %}yes{% endif %}", id="error06-two-operands"),
    pytest.param("{% if not %}yes{% endif %}", id="error07-bare-not"),
    pytest.param("{% if and %}yes{% endif %}", id="error08-bare-and"),
    pytest.param("{% if or %}yes{% endif %}", id="error09-bare-or"),
    pytest.param("{% if == %}yes{% endif %}", id="error10-bare-eq"),
    pytest.param("{% if 1 == %}yes{% endif %}", id="error11-trailing-eq"),
    pytest.param("{% if a not b %}yes{% endif %}", id="error12-not-as-infix"),
    pytest.param(
        "{% if foo is bar %} yes {% else if foo is not bar %} no {% endif %}",
        id="else_if_tag_error01-else-with-args",
    ),
    pytest.param("{% if foo = bar %}yes{% else %}no{% endif %}", id="single_eq"),
]


class TestDjangoParity:
    """Same source → same outcome as Django. The plain backend (the
    scoreboard / Django-parity path) matches Django's exception family
    (TemplateSyntaxError) exactly; the LiveView entry is measured for
    refuse-or-not (see ``liveview_parse``)."""

    @pytest.mark.parametrize("source", MALFORMED_CASES)
    def test_backend_refuses_like_django_same_type(self, source):
        expected = outcome(django_parse, source)
        # Guard: the differential is only meaningful if Django itself refuses.
        assert expected == ("raise", True), (
            f"precondition: Django must refuse {source!r} with TemplateSyntaxError, "
            f"got {expected!r}"
        )
        actual = outcome(backend_parse, source)
        assert actual == expected, f"{source!r}: djust backend {actual!r} != django {expected!r}"

    @pytest.mark.parametrize("source", MALFORMED_CASES)
    def test_liveview_refuses(self, source):
        # Django refuses; the LiveView entry must refuse too (any exception).
        assert outcome(django_parse, source)[0] == "raise"
        actual = outcome(liveview_parse, source)
        assert actual[0] == "raise", (
            f"{source!r}: LiveView entry rendered a branch instead of refusing -> {actual!r}"
        )

    def test_all_fourteen_cells_are_present(self):
        # Pins the count so a future edit can't silently drop a cell.
        assert len(MALFORMED_CASES) == 14


# ---------------------------------------------------------------------------
# Regression guard: the single most likely regression vector is a VALID
# condition that newly refuses. NONE of these may raise on either entry.
# Corpus drawn from Django's own valid `{% if %}` tests + djust usage.
# ---------------------------------------------------------------------------
VALID_CONDITIONS = [
    "{% if foo %}y{% endif %}",
    "{% if not foo %}y{% endif %}",
    "{% if foo and bar %}y{% endif %}",
    "{% if foo or bar %}y{% endif %}",
    "{% if not foo or bar %}y{% endif %}",
    "{% if foo and not bar %}y{% endif %}",
    "{% if not not foo %}y{% endif %}",
    "{% if not not not foo %}y{% endif %}",
    "{% if x in y %}y{% endif %}",
    "{% if x not in y %}y{% endif %}",
    "{% if x is y %}y{% endif %}",
    "{% if x is not y %}y{% endif %}",
    "{% if x == y %}y{% endif %}",
    "{% if x != y %}y{% endif %}",
    "{% if x < y %}y{% endif %}",
    "{% if x <= y %}y{% endif %}",
    "{% if x > y %}y{% endif %}",
    "{% if x >= y %}y{% endif %}",
    "{% if articles|length >= 5 %}y{% endif %}",
    '{% if foo == "bar" %}y{% endif %}',
    "{% if a and b or c %}y{% endif %}",
    "{% if a or b and c %}y{% endif %}",
    "{% if a == b == c %}y{% endif %}",
    "{% if not a and not b %}y{% endif %}",
    "{% if a.b.c %}y{% endif %}",
    "{% if x|default:0 > 5 %}y{% endif %}",
    "{% if foo is True %}y{% else %}n{% endif %}",
    "{% if foo is not None %}y{% endif %}",
    "{% if 1 == 1 %}y{% endif %}",
    "{% if foo %}y{% elif bar %}z{% else %}w{% endif %}",
    "{% if foo %}y{% elif not bar and baz %}z{% endif %}",
    "{% if a in b and c not in d %}y{% endif %}",
    "{% if x %}y{% else %}n{% endif %}",
]


class TestNoValidConditionRefuses:
    """A faithful smartif port must refuse EXACTLY smartif's rejected streams
    and NOTHING valid. A false positive here is the primary regression risk
    of #2576."""

    @pytest.mark.parametrize(
        "parse",
        [
            pytest.param(backend_parse, id="DjustTemplateBackend"),
            pytest.param(liveview_parse, id="RustLiveView"),
        ],
    )
    @pytest.mark.parametrize("source", VALID_CONDITIONS)
    def test_valid_condition_still_parses(self, parse, source):
        # It must NOT refuse on djust (grammar validation is the new code;
        # a false positive raises here)...
        result = outcome(parse, source)
        assert result == ("ok",), f"FALSE POSITIVE: {source!r} newly refuses -> {result!r}"
        # ...and Django agrees it is valid (keeps the corpus honest).
        assert outcome(django_parse, source) == ("ok",), f"corpus error: Django rejects {source!r}"
