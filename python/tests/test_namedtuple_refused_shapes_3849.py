"""A namedtuple the engine cannot rebuild must never fail a render (#3849 review).

PR #3856 made ``serialize_value(for_render=True)`` keep a namedtuple's type, and the engine holds
it as ``Value::NamedTuple`` (name, fields, items). Every time a native tag is called the engine
converts the WHOLE context back to Python, and for a ``Value::NamedTuple`` it rebuilds the class with
``collections.namedtuple(name, fields)``, which REFUSES some shapes a real class can have:

* ``namedtuple("R", ["a", "a", "b"], rename=True)``: its ``_fields`` is ``('a', '_1', 'b')`` and a
  leading underscore is refused (``rename=True`` is a common idiom for raw-SQL rows);
* a duplicate, a Python keyword, a field that is not an identifier, two fields equal only after NFKC
  normalisation (``SyntaxError``, not ``ValueError``);
* a class whose NAME is not an identifier.

The conversion raised ``ValueError`` for ANY native tag in the template, whether the namedtuple was
an operand or merely sat in the context (``{% static %}``, ``{% now %}``, ``{% badge %}``, ``{% lorem %}`` ...). Two
mechanisms now stop it, and each has a test that fails when only that mechanism is removed:

* ``serialize_value`` keeps the type only when ``namedtuple(name, fields)`` accepts the shape
  (pinned by ``TestTheSerializerKeepsOnlyABuildableShape``, which calls it directly: the engine
  fallback would hide its removal on the Django-backend route);
* ``named_tuple_or_tuple`` in ``crates/djust_core/src/lib.rs`` falls back to a plain tuple when the
  class cannot be built (pinned by the ``RustLiveView`` and ``_rust`` cases, which hand the engine
  the value as it is and never pass through ``serialize_value``).

Also here: the rebuild runs the class's own ``__new__`` on already-serialized values, so ANY
exception from it degrades to the plain tuple (``TestARebuildThatRaisesDegrades``).

Engine path: both native entry points (Django-backend engine, ``RustLiveView``) and ``_rust``.

Refs #3849, #3856, #1646, #1468.
"""

# ruff: noqa: F811

from __future__ import annotations

import logging
import re
from collections import namedtuple
from typing import Any

import pytest
from django.utils.translation import gettext_lazy

from djust.components import rust_handlers
from djust.template.serialization import serialize_value

from python.tests.test_typed_tuple_shapes_3849 import (  # noqa: F401
    GATE,
    engines,
    render_backend,
    render_django,
    render_live,
)


def _odd(name: str, fields: Any, items: tuple = (1, 2, 3)) -> Any:
    """An instance of a tuple class whose ``_fields`` and ``__name__`` are exactly as given."""
    cls = type(
        name,
        (tuple,),
        {"_fields": fields, "__slots__": (), "__new__": lambda cls, *a: tuple.__new__(cls, a)},
    )
    return cls(*items[: len(fields)])


Renamed = namedtuple("Renamed", ["a", "a", "b"], rename=True)

#: ``(id, value, buildable)``: every field-name / class-name shape ``namedtuple()`` refuses, plus
#: the shapes it accepts that must KEEP their type.
SHAPES = [
    ("rename_true_duplicate", Renamed(1, 2, 3), False),
    ("duplicate", _odd("D", ("a", "a")), False),
    ("leading_underscore", _odd("U", ("_a", "b")), False),
    ("keyword_field", _odd("K", ("class", "b")), False),
    ("non_identifier_field", _odd("F", ("a b", "c")), False),
    ("non_identifier_class_name", _odd("Bad Name", ("a", "b")), False),
    ("keyword_class_name", _odd("class", ("a", "b")), False),
    ("non_string_field", _odd("N", (1, 2)), False),
    # Equal only after NFKC normalisation: ``namedtuple()`` raises SyntaxError, not ValueError.
    ("nfkc_duplicate", _odd("Q", ("\ufb01", "fi")), False),
    ("ordinary", namedtuple("Ok", "a b")(1, 2), True),
    ("list_fields", _odd("L", ["a", "b"]), True),
]
REFUSED = [(i, v) for i, v, ok in SHAPES if not ok]
ALL_IDS = [i for i, _, _ in SHAPES]
ALL_VALUES = [v for _, v, _ in SHAPES]
REFUSED_IDS = [i for i, _ in REFUSED]
REFUSED_VALUES = [v for _, v in REFUSED]

#: Where the odd value sits: the context holds it, no tag reads it.
PLACEMENTS = {
    "direct": lambda v: {"v": v},
    "in_list": lambda v: {"v": [v]},
    "in_dict": lambda v: {"v": {"k": v}},
    "in_tuple": lambda v: {"v": (v, 1)},
}


@pytest.fixture(scope="module")
def backend():
    from djust.template.backend import DjustTemplateBackend

    rust_handlers.register_with_rust_engine()
    return DjustTemplateBackend(
        {"NAME": "refused-3849", "DIRS": [], "APP_DIRS": True, "OPTIONS": {}}
    )


def _text(out: Any) -> str:
    return re.sub(r"\s+", " ", str(out)).strip()


STATIC = "{% load static %}{% static 'a.css' %}"
UNRELATED = {
    "static": STATIC,
    "now": "{% now 'Y' %}",
    "badge": "{% badge label='x' %}",
    "lorem": "{% lorem 1 w %}",
    "cycle": "{% cycle 'a' 'b' %}",
}


def _live(src: str, ctx: dict[str, Any]) -> str:
    from djust._rust import RustLiveView

    view = RustLiveView(src)
    view.update_state(dict(ctx))
    return _text(view.render())


class TestAnOddNamedtupleInTheContextDoesNotFailAnUnrelatedTag:
    @pytest.mark.parametrize("placement", sorted(PLACEMENTS))
    @pytest.mark.parametrize("src", list(UNRELATED.values()), ids=list(UNRELATED))
    @pytest.mark.parametrize("value", REFUSED_VALUES, ids=REFUSED_IDS)
    def test_the_django_backend(self, backend, value, src, placement):
        ctx = PLACEMENTS[placement](value)
        expected = _text(backend.from_string(src).render({"v": None}))
        assert _text(backend.from_string(src).render(ctx)) == expected

    @pytest.mark.parametrize("placement", sorted(PLACEMENTS))
    @pytest.mark.parametrize("src", list(UNRELATED.values()), ids=list(UNRELATED))
    @pytest.mark.parametrize("value", REFUSED_VALUES, ids=REFUSED_IDS)
    def test_rust_live_view(self, value, src, placement):
        """``update_state`` hands the engine the value as it is: no ``serialize_value`` here, so
        this is the case only the engine's own fallback can answer."""
        ctx = PLACEMENTS[placement](value)
        assert _live(src, ctx) == _live(src, {"v": None})

    @pytest.mark.parametrize("value", REFUSED_VALUES, ids=REFUSED_IDS)
    def test_the_engine_directly(self, value):
        from djust import _rust

        out = _rust.render_template("{% badge label='x' %}{{ v|length }}", {"v": value})
        assert out.endswith(str(len(value)))

    @pytest.mark.parametrize("value", REFUSED_VALUES, ids=REFUSED_IDS)
    def test_the_odd_value_is_still_there_as_its_items(self, backend, value):
        """The fallback loses the type, never the value."""
        out = _text(backend.from_string("{{ v.0 }}-{{ v|length }}").render({"v": value}))
        assert out == f"{value[0]}-{len(value)}"

    @pytest.mark.parametrize("value", ALL_VALUES, ids=ALL_IDS)
    def test_an_odd_namedtuple_as_an_operand_does_not_fail_either(self, backend, value):
        out = backend.from_string("{% approval_gate reject_label=v %}").render({"v": value})
        assert "Approve" in out


class TestTheSerializerKeepsOnlyABuildableShape:
    """``serialize_value`` called directly: the engine's fallback cannot hide its removal."""

    @pytest.mark.parametrize("value", REFUSED_VALUES, ids=REFUSED_IDS)
    def test_a_refused_shape_is_a_plain_tuple(self, value):
        out = serialize_value(value, for_render=True)
        assert type(out) is tuple and out == tuple(value)

    @pytest.mark.parametrize(
        "value", [v for _, v, ok in SHAPES if ok], ids=[i for i, _, ok in SHAPES if ok]
    )
    def test_a_buildable_shape_keeps_its_type(self, value):
        out = serialize_value(value, for_render=True)
        assert type(out) is type(value) and tuple(out) == tuple(value)

    def test_the_answer_is_the_real_constructors(self):
        """Not a copy of its rules: whatever ``namedtuple()`` accepts is kept."""
        for fields in (("a", "b"), ("a", "_b"), ("_", "b"), ("a", "a"), ("def", "b"), ("é", "b")):
            try:
                namedtuple("T", fields)
                accepted = True
            except ValueError:
                accepted = False
            value = _odd("T", fields)
            kept = type(serialize_value(value, for_render=True)) is type(value)
            assert kept is accepted, fields


class _Raises(namedtuple("_Raises", "label n")):
    """A namedtuple whose own ``__new__`` raises ``exc`` when it is given a plain ``str``."""

    exc: type[BaseException] = ValueError

    def __new__(cls, label, n):
        if type(label) is str:
            raise cls.exc("not rebuildable from a serialized value")
        return super().__new__(cls, label, n)


def _raising(exc: type[BaseException]) -> Any:
    cls = type("Raising", (_Raises,), {"exc": exc})
    return cls(gettext_lazy("hello"), 1)


EXCEPTIONS = [ValueError, AssertionError, RuntimeError, KeyError, AttributeError, TypeError]


class TestARebuildThatRaisesDegrades:
    @pytest.mark.parametrize("exc", EXCEPTIONS, ids=[e.__name__ for e in EXCEPTIONS])
    def test_serialize_value_returns_the_plain_tuple(self, exc):
        out = serialize_value(_raising(exc), for_render=True)
        assert type(out) is tuple and out == ("hello", 1)

    @pytest.mark.parametrize("exc", EXCEPTIONS, ids=[e.__name__ for e in EXCEPTIONS])
    def test_a_render_still_prints_the_value(self, backend, exc):
        out = _text(backend.from_string("{{ v }}").render({"v": _raising(exc)}))
        assert "hello" in out

    def test_it_is_logged_at_debug_with_percent_style_arguments(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="djust.template.serialization"):
            serialize_value(_raising(AssertionError), for_render=True)
        records = [r for r in caplog.records if "could not be rebuilt" in r.getMessage()]
        assert records and records[0].levelno == logging.DEBUG
        assert records[0].args, "the log call must use %s-style arguments, not an f-string"
        # The exception text rides ``exc_info`` (only where diagnostics are allowed), never the
        # message: ``log_failure`` (ADR-038) keeps the message value-free.
        assert "not rebuildable" not in records[0].getMessage()
        assert records[0].exc_info and "not rebuildable" in str(records[0].exc_info[1])


#: The one refused shape whose TYPE Django prints and the engine cannot: a real ``rename=True`` namedtuple
#: (``Renamed(a=1, _1=2, b=3)``). The fallback keeps the items and loses the class, by design.
_TYPE_IS_LOST = {"rename_true_duplicate"}


class TestARefusedShapeAgainstDjango3849:
    """The review of #3856: these tests compared a refused shape with a ``None`` baseline (the same
    template without the value), which proves the render does not fail but not what it prints. As an
    OPERAND the value is printed, so Django's own tag is the reference."""

    @pytest.mark.parametrize("route", ["backend", "live"])
    @pytest.mark.parametrize(
        "name,value",
        [(n, v) for n, v in REFUSED if n not in _TYPE_IS_LOST],
        ids=lambda x: x if isinstance(x, str) else None,
    )
    def test_an_operand_prints_as_django_prints_it(self, engines, route, name, value):
        render = {"backend": render_backend, "live": render_live}[route]
        assert render(engines, GATE, {"v": value}) == render_django(engines, GATE, {"v": value})

    def test_a_real_rename_true_namedtuple_reaches_the_tag_as_itself_through_the_backend(
        self, engines
    ):
        """Through ``DjustTemplateBackend`` the tag's own node is handed the operand from the raw
        context (one render path per tag), so it prints as Django does."""
        value = Renamed(1, 2, 3)
        assert "Renamed(a=1, _1=2, b=3)" in render_django(engines, GATE, {"v": value})
        assert render_backend(engines, GATE, {"v": value}) == render_django(
            engines, GATE, {"v": value}
        )

    def test_a_real_rename_true_namedtuple_as_state_loses_its_class_and_keeps_its_items(
        self, engines
    ):
        """Recorded, not fixed: as LiveView state the engine cannot rebuild that class
        (``namedtuple()`` refuses ``_1``), so the plain tuple is printed."""
        value = Renamed(1, 2, 3)
        out = render_live(engines, GATE, {"v": value})
        assert "Renamed" not in out and "(1, 2, 3)" in out, out
