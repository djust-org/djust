"""The typed channel carries every tuple SHAPE, and a large tuple costs counted work (#3849).

#3843 (#3768) put a ``tuple`` back on its way into a native component handler: the engine hands the
handler a JSON list ``[1, 2]``, Django's tag prints ``(1, 2)``, so ``_parse_args`` takes the real
tuple off the typed channel (``TYPED_ARGS``) and returns a ``TupleValue``. Its independent review
found two things it did not do:

1. **A namedtuple operand flattened to a plain tuple.** ``{% approval_gate reject_label=v %}`` over
   ``v = P(1, 2)`` printed ``(1, 2)`` (before #3843 ``[1, 2]``; Django ``P(x=1, y=2)``), and seven
   cells flipped from their default to ``(1, 2)`` (``dashboard_grid`` ``gap`` / ``row_height``,
   ``map_picker`` ``height``, ``resizable_panel`` ``initial_size`` / ``min_size`` / ``max_size``,
   ``sortable_grid`` ``gap``: the CSS-length validator accepts a parenthesised group). Two causes,
   one per layer: ``serialize_value(for_render=True)`` rebuilt EVERY tuple as ``tuple(items)``
   (``python/djust/template/serialization.py``), and the engine's narrow ask
   (``TYPED_ARGS_STANDINS_ONLY``) handed over a ``Value::Tuple`` but not a ``Value::NamedTuple``.
2. **A large tuple cost more than the PR stated.** Not wall-clock (this file never asserts a
   duration): COUNTED work. ``_parse_args`` decoded the operand's JSON text into a list it then
   threw away, and ``_from_typed`` copied a flat tuple one element at a time in Python.

The shapes the channel must handle, each pinned below on every route (the Django-backend template
engine, ``RustLiveView``, and Django's own tag as the reference): a plain tuple, a namedtuple, a
namedtuple of one field and of none, a tuple holding a namedtuple, a namedtuple holding a tuple, a
nested tuple, an empty tuple, a one-element tuple, and a tuple SUBCLASS.

A tuple subclass is recorded, not fixed: the engine converts it to its ``Value::Tuple`` (it keeps
no class), so a subclass that overrides ``__repr__`` prints as the plain tuple. A subclass that
does not override it prints as the tuple under Django too, which is the case pinned as equal.

Engine path (CLAUDE.md "engine-path declaration"): both engines, and both native entry points.

Refs #3849, #3843, #3768, #1646, #1468.
"""

from __future__ import annotations

import re
import sys
from collections import namedtuple
from pathlib import Path
from typing import Any

import pytest
from django.template import Context, Engine
from django.utils.html import escape

from djust.components import rust_handlers

COMPONENTS = "djust.components.templatetags.djust_components"
P = namedtuple("P", "x y")
P1 = namedtuple("P1", "a")
P0 = namedtuple("P0", "")


class Plain(tuple):
    """A tuple subclass that does NOT override ``__repr__``: prints as the tuple everywhere."""

    __slots__ = ()


class Spelled(tuple):
    """A tuple subclass that spells itself: the engine keeps no class, so this one is lost."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "Spelled" + tuple.__repr__(self)


@pytest.fixture(scope="module")
def engines():
    from djust.template.backend import DjustTemplateBackend

    rust_handlers.register_with_rust_engine()
    rust = DjustTemplateBackend(
        {"NAME": "tuple-shapes-3849", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )
    django = Engine(
        dirs=[str(Path(rust_handlers.__file__).resolve().parent / "templates")],
        libraries={"djust_components": COMPONENTS},
    )
    return rust, django


def _text(out: Any) -> str:
    return re.sub(r"\s+", " ", str(out)).strip()


def render_backend(engines, src: str, ctx: dict[str, Any]) -> str:
    return _text(engines[0].from_string(src).render(dict(ctx)))


def render_django(engines, src: str, ctx: dict[str, Any]) -> str:
    return _text(engines[1].from_string("{% load djust_components %}" + src).render(Context(ctx)))


def render_live(engines, src: str, ctx: dict[str, Any]) -> str:
    """``RustLiveView``: the LiveView entry point, which hands the context over as view state."""
    from djust._rust import RustLiveView

    view = RustLiveView(src)
    view.update_state(dict(ctx))
    return _text(view.render())


ROUTES = {"backend": render_backend, "live": render_live}

#: Every shape the channel must carry. ``(id, value)``; Django's print is the expectation.
SHAPES = [
    ("plain", (1, 2)),
    ("namedtuple", P(1, 2)),
    ("namedtuple_one_field", P1(7)),
    ("namedtuple_no_fields", P0()),
    ("tuple_holding_namedtuple", (P(1, 2), 3)),
    ("namedtuple_holding_tuple", P((1, 2), (3,))),
    ("namedtuple_holding_namedtuple", P(P1(1), P1(2))),
    ("nested", ((1, "a"), (2, "b"))),
    ("empty", ()),
    ("one_element", (7,)),
    ("subclass_without_repr", Plain((1, 2))),
]
SHAPE_IDS = [i for i, _ in SHAPES]
SHAPE_VALUES = [v for _, v in SHAPES]

GATE = "{% approval_gate reject_label=v %}"


class TestEveryShapeRendersAsDjangoPrintsIt:
    @pytest.mark.parametrize("route", sorted(ROUTES))
    @pytest.mark.parametrize("value", SHAPE_VALUES, ids=SHAPE_IDS)
    def test_the_native_text_is_the_python_tags(self, engines, route, value):
        expected = render_django(engines, GATE, {"v": value})
        assert escape(repr(value)) in expected, "Django must print the value itself"
        assert ROUTES[route](engines, GATE, {"v": value}) == expected

    def test_the_reported_reproduction(self, engines):
        """The issue's report: the namedtuple printed ``(1, 2)`` on the Django-backend path."""
        out = render_backend(engines, GATE, {"v": P(1, 2)})
        assert "P(x=1, y=2)" in out and "(1, 2) Approve" not in out, out

    def test_a_spelled_subclass_reaches_the_tag_as_itself_through_the_backend(self, engines):
        """Through ``DjustTemplateBackend`` the tag's own node is handed the operand from the raw
        context, so a ``__repr__`` override prints as Django prints it (one render path per tag).
        As LiveView STATE the value is normalised before it reaches the engine, which keeps no
        tuple class (``Value::Tuple``): recorded, not fixed."""
        value = Spelled((1, 2))
        assert "Spelled(1, 2)" in render_django(engines, GATE, {"v": value})
        assert render_backend(engines, GATE, {"v": value}) == render_django(
            engines, GATE, {"v": value}
        )
        out = render_live(engines, GATE, {"v": value})
        assert "Spelled" not in out and "(1, 2)" in out, out


#: The seven cells the review named: a namedtuple in a CSS-length slot used to flip from the
#: default to ``(1, 2)``. Equal to Django's now, whatever Django does with them.
SEVEN_CELLS = [
    ("dashboard_grid gap", "{% dashboard_grid gap=v %}x{% enddashboard_grid %}"),
    ("dashboard_grid row_height", "{% dashboard_grid row_height=v %}x{% enddashboard_grid %}"),
    ("map_picker height", "{% map_picker height=v %}"),
    (
        "resizable_panel initial_size",
        "{% resizable_panel initial_size=v %}x{% endresizable_panel %}",
    ),
    ("resizable_panel min_size", "{% resizable_panel min_size=v %}x{% endresizable_panel %}"),
    ("resizable_panel max_size", "{% resizable_panel max_size=v %}x{% endresizable_panel %}"),
    ("sortable_grid gap", "{% sortable_grid gap=v %}"),
]


class TestTheSevenCellsTheReviewNamed:
    @pytest.mark.parametrize("route", sorted(ROUTES))
    @pytest.mark.parametrize("src", [s for _, s in SEVEN_CELLS], ids=[n for n, _ in SEVEN_CELLS])
    @pytest.mark.parametrize("value", [P(1, 2), (1, 2)], ids=["namedtuple", "tuple"])
    def test_native_equals_django(self, engines, route, src, value):
        assert ROUTES[route](engines, src, {"v": value}) == render_django(
            engines, src, {"v": value}
        )


class TestTheContextKeepsTheNamedtupleForEverythingElse:
    """``serialize_value(for_render=True)`` no longer rebuilds a namedtuple as a plain tuple, and
    that is visible beyond the typed channel: ``{{ v }}`` prints ``P(x=1, y=2)`` as Django does
    (it printed ``(1, 2)``). The other sources below are REGRESSION guards: ``{{ v.x }}`` already
    matched Django on ``main`` (through the raw-Python sidecar), and a loop and the simple filters
    agree. Compared with Django's own template over one source and context."""

    SOURCES = [
        "{{ v }}",
        "{{ v.x }}",
        "{{ v.0 }}",
        "{% for i in v %}[{{ i }}]{% endfor %}",
        "{{ v|length }}",
        "{{ v|first }}",
        "{{ v|join:'-' }}",
        "{% if v %}yes{% else %}no{% endif %}",
    ]

    @pytest.mark.parametrize("src", SOURCES)
    @pytest.mark.parametrize("value", [P(1, 2), (1, 2), Plain((1, 2))], ids=["nt", "tuple", "sub"])
    def test_the_backend_equals_django(self, engines, src, value):
        from django.template import Template

        expected = _text(Template(src).render(Context({"v": value})))
        assert _text(engines[0].from_string(src).render({"v": value})) == expected

    @pytest.mark.parametrize("filter_name", ["dictsort", "dictsortreversed"])
    def test_dictsort_over_namedtuples_is_recorded_not_fixed(self, engines, filter_name):
        """Unchanged from ``main`` and NOT fixed here: the native ``dictsort`` /
        ``dictsortreversed`` read a dict's key, so a list of namedtuples renders EMPTY natively
        where Django sorts it by attribute. Pinned so the gap is not mistaken for parity."""
        src = "{% for r in rows|" + filter_name + ":'x' %}[{{ r.x }}]{% endfor %}"
        rows = [P(2, "b"), P(1, "a")]
        assert render_django(engines, src, {"rows": rows}) != ""
        assert render_backend(engines, src, {"rows": rows}) == ""

    def test_a_namedtuple_whose_new_wants_more_degrades_to_the_plain_tuple(self, engines):
        """Rebuilding positionally can raise ``TypeError``; the value must still render."""

        class Odd(namedtuple("Odd", "a b")):
            def __new__(cls, a, b, extra):
                return super().__new__(cls, a, b)

        value = tuple.__new__(Odd, (1, 2))
        out = engines[0].from_string("{{ v }}").render({"v": value})
        assert _text(out) == "(1, 2)"


# --------------------------------------------------------------------------- #
# _parse_args: the one reader


# --------------------------------------------------------------------------- #
# Cost: counted work, never a duration


def _line_events_in(module_file: str, call) -> int:
    """How many Python source lines of ``module_file`` executed while ``call()`` ran."""
    count = 0

    def local(frame, event, arg):
        nonlocal count
        if event == "line":
            count += 1
        return local

    def tracer(frame, event, arg):
        return local if frame.f_code.co_filename == module_file else None

    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        call()
    finally:
        sys.settrace(previous)
    return count


class TestTheLiveViewStatePathIsRecordedNotChanged:
    """A tuple held as LiveView STATE is a list before it reaches the engine, namedtuple or not.

    ``normalize_django_value`` is the state contract (JSON-shaped state; ``json.dumps`` and Django's
    own ``DjangoJSONEncoder`` have no tuple either, ``test_tuple_roundtrip_2276``), and it is not
    the typed channel this issue is about. Recorded so the two entry points are not confused:
    ``RustLiveView.update_state`` (a context handed over as it is, pinned above) keeps the
    namedtuple, the Python ``LiveView`` state path does not, for any tuple. Decision: not changed.
    """

    @pytest.mark.parametrize("value", [(1, 2), P(1, 2), ()], ids=["tuple", "namedtuple", "empty"])
    def test_state_normalisation_is_a_list(self, value):
        from djust.serialization import normalize_django_value

        assert normalize_django_value(value) == list(value)
        assert type(normalize_django_value(value)) is list
