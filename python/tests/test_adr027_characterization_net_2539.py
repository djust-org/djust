"""ADR-027's characterization net (#2539), after the kill-switch was deleted
(ADR-027 Step 5, #2628).

Movement 1 recorded TODAY's bytes per cell, wrong ones included, so the flip
(movement 3) had a per-cell delta to show. Movements 2–3 added the
``template_resolve_lazy`` axis and flipped its default ON; #2621 closed the
last held cells; #2628 deleted the flag and every code arm it selected. What
survives is the former flag-ON behaviour, unconditionally, and this file now
pins ONE contract: **every non-floor row renders Django's bytes on both djust
paths**. The recorded OFF-path columns and the ``*_WRONG_TODAY`` /
``*_WRONG_UNDER_LAZY`` sets went with the flag.

Three columns, three real entries
---------------------------------
* ``django_render`` — Django's own engine, the premise column. It goes red on
  a Django bump, by design.
* ``plain_render`` — ``DjustTemplateBackend.from_string(...).render(...)``,
  the user-facing plain entry. ``TestThePlainEntriesAgree`` pins the two raw
  entries it binds (``_rust.render_template`` / ``render_template_with_dirs``)
  to the same bytes (the #1646 twin check).
* ``liveview_render`` — the REAL LiveView entry: a ``LiveView`` subclass,
  ``LiveViewTestClient.mount()`` + ``.render()``, which runs
  ``view.render(request)`` → ``_sync_state_to_rust`` (the ``_JSON_FRIENDLY``
  filter, ``_protect_sidecar_value``, ``update_state``, ``set_raw_py_values``).
  NOT the ``RustLiveView`` + ``set_raw_py_values(dict(ctx))`` stand-in of
  ``test_sidecar_on_all_render_paths_2501.py::liveview_render`` — the #1650
  reproduction-fidelity lesson ``TestTheHarnessIsTheRealPath`` makes
  executable.

Normalisation, two mechanisms for two reasons
---------------------------------------------
Every column passes through ``ADDR`` (``0x[0-9a-f]+`` → ``0x…``) because a
function/generator repr always carries an address (rows J, J2, V). AND the
fixture classes whose INSTANCE is rendered bare (``Plain``, ``Cls``,
``Outer``, ``Mutating``, ``SafeObj``) define a fixed ``__repr__``, because row
T's ``|length`` counts ``len(str(o))`` and an address-bearing repr makes that
count platform-dependent. With both, every cell is a literal, platform-stable
byte string.

What is pinned, per issue
-------------------------
* #2502 — ``do_not_call_in_templates`` renders the bound method as-is (row A).
* #2504 — a filtered / dict-view ``{% for %}`` operand reaches attributes
  (rows N, N2), and so does a plain object inside a top-level list/tuple on
  the LiveView path (rows N0, N0b).
* #2505 — local bindings never read a shadowed OUTER object (M2, M3, M6).
* #2506 / #2507 — permanent security pins: a lookup exception never fails
  OPEN; ``{{ c.unmount }}`` never runs a mutator.
* E1–E4 — the serialization floor (SECURE_DEFAULTS Pattern 1) differs from
  Django DELIBERATELY and is never moved to Django's bytes.
* The sink ``Context::walk_live`` (``crates/djust_core/src/context.rs``) has
  exactly one caller, ``walk_from_handle``, reached UNCONDITIONALLY from
  ``resolve_without_builtins`` — ``TestTheSinkHasExactlyOneCaller2539``.

Refs #2539, #2535 (ADR-027), #2628, #2621, #2502, #2504, #2505, #2506, #2507,
#2516, #2517, #2624, #1646, #1650, #1468, #1039, #1125, #1104.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable
from unittest import mock

import pytest

pytest.importorskip("django")

from django.contrib.auth.models import User  # noqa: E402
from django.core.exceptions import PermissionDenied  # noqa: E402
from django.template import Context as DjangoContext  # noqa: E402
from django.template import Template as DjangoTemplate  # noqa: E402
from django.utils.safestring import mark_safe  # noqa: E402

from djust import _rust  # noqa: E402
from djust._template_guards import ALTERS_DATA_COMPONENT_METHODS  # noqa: E402
from djust.components.base import Component, LiveComponent  # noqa: E402
from djust.live_view import LiveView  # noqa: E402
from djust.mixins.rust_bridge import RustBridgeMixin  # noqa: E402
from djust.testing import LiveViewTestClient  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON_DIR = ROOT / "python"
CONTEXT_RS = ROOT / "crates" / "djust_core" / "src" / "context.rs"
THIS_FILE = pathlib.Path(__file__).resolve()

ADDR = re.compile(r"0x[0-9a-f]+")
DJ_ROOT = re.compile(r"<div dj-root[^>]*>(.*)</div>", re.S)


# ---------------------------------------------------------------------------
# Fixture classes — fixed `__repr__` on the ones rendered bare (see module doc)
# ---------------------------------------------------------------------------
class Plain:
    def __init__(self) -> None:
        self.inst_attr = "in-dict"

    def __repr__(self) -> str:
        return "<Plain>"


class Cls:
    cls_attr = "class-level"

    def __repr__(self) -> str:
        return "<Cls>"


class Outer:
    cls_attr = "OUTER"

    def __repr__(self) -> str:
        return "<Outer>"


class Mutating:
    def keep(self) -> str:
        return "kept"

    keep.do_not_call_in_templates = True  # type: ignore[attr-defined]

    def __repr__(self) -> str:
        return "<Mutating>"


class SafeObj:
    def __str__(self) -> str:
        return mark_safe("<b>s</b>")

    def __repr__(self) -> str:
        return "<SafeObj>"


class Sub:
    sub = "deep"


class Nested:
    attr = Sub()


class Silent(Exception):
    silent_variable_failure = True


class NotSilent(Exception):
    silent_variable_failure = False


class Raiser:
    @property
    def attr_err(self) -> str:
        raise AttributeError("nope")

    @property
    def key_err(self) -> str:
        raise KeyError("nope")

    @property
    def silent(self) -> str:
        raise Silent("quiet")

    @property
    def loud(self) -> str:
        raise RuntimeError("authz")

    @property
    def loud_false(self) -> str:
        raise NotSilent("explicit-false")

    def silent_method(self) -> str:
        raise Silent("quiet-method")


class Presenter:
    def __init__(self, user: Any) -> None:
        self.user = user

    def get_user(self) -> Any:
        return self.user


class Doodad:
    """Django's `test_callables.Doodad`."""

    def __init__(self, value: int) -> None:
        self.num_calls = 0
        self.value = value

    def __call__(self) -> dict:
        self.num_calls += 1
        return {"the_value": self.value}


class DoodadAlters(Doodad):
    alters_data = True


class MyClass(list):
    """Django's `test_subscriptable_class` shape (row P, one of the #2517 crashes)."""

    class_property = "Example property"
    do_not_call_in_templates = True

    @classmethod
    def class_method(cls) -> str:
        return "Example method"


class GetItemRaiser:
    def __getitem__(self, key: Any) -> Any:
        raise RuntimeError("getitem authz")


class NpLike:
    foo = "attr-foo"

    def __getitem__(self, key: Any) -> Any:
        raise ValueError("bad index")


def gen():
    yield 1
    yield 2


#: Row J/J2's callable: a module-level lambda (so its repr is exactly
#: `<function <lambda> at 0x…>`, the ADR's bytes) that COUNTS its calls, so
#: the Django side is provably non-trivial (`TestTheDjangoSideIsNonTrivial`).
J_CALLS: list[int] = []
_j_lambda = lambda: J_CALLS.append(1) or "foo bar"  # noqa: E731


def make_user() -> User:
    return User(username="alice", password="pbkdf2$hash", is_staff=True)


def cycle() -> dict:
    """Row H: a reference cycle through a plain object's public attribute."""
    d: dict = {"x": 1}

    class C:
        def __init__(self, d: dict) -> None:
            self.d = d

    d["t"] = C(d)
    return d


class ShellCard(Component):
    template = None
    cls_attr = "shell-class-level"

    def _render_custom(self) -> str:
        return "<b>shellcard</b>"


# ---------------------------------------------------------------------------
# The three columns
# ---------------------------------------------------------------------------
def django_render(source: str, context: dict) -> str:
    return DjangoTemplate(source).render(DjangoContext(dict(context)))


def plain_render(source: str, context: dict) -> str:
    """The user-facing plain entry: `DjustTemplateBackend`."""
    from djust.template_backend import DjustTemplateBackend

    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    return backend.from_string(source).render(context=dict(context), request=None)


def render_template(source: str, context: dict) -> str:
    return _rust.render_template(source, dict(context))


def render_template_with_dirs(source: str, context: dict) -> str:
    return _rust.render_template_with_dirs(source, dict(context), [])


def liveview_render(source: str, context: dict) -> str:
    """The REAL LiveView entry (see the module docstring). Needs `django_db`."""

    class _V(LiveView):
        def mount(self, request, **kwargs):
            pass

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(context)
            return ctx

    _V.template = f"<div dj-root>{source}</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    html = client.render()
    match = DJ_ROOT.search(html)
    assert match is not None, html
    return match.group(1)


PATHS = ("plain", "liveview")
RENDER: dict[str, Callable[[str, dict], str]] = {
    "plain": plain_render,
    "liveview": liveview_render,
}
#: Every djust entry a user's template can take — for the pins that must
#: hold on ALL of them (#1104).
ALL_FOUR = [
    pytest.param(render_template, id="render_template"),
    pytest.param(render_template_with_dirs, id="render_template_with_dirs"),
    pytest.param(plain_render, id="DjustTemplateBackend"),
    pytest.param(liveview_render, id="LiveView"),
]


def raised_type(exc_value: BaseException) -> type:
    """The type the PROJECT'S code raised, seen through the backend's
    `Exception(...) from e` wrapper (lifted from the 2501 file)."""
    if type(exc_value) is Exception and exc_value.__cause__ is not None:
        return type(exc_value.__cause__)
    return type(exc_value)


@dataclass(frozen=True)
class Raises:
    """A column whose answer is an exception of this type."""

    exc_type: type


def observe(render: Callable[[str, dict], str], source: str, context: dict) -> Any:
    """Render, normalised — or the `Raises` the render produced."""
    try:
        return ADDR.sub("0x…", render(source, context))
    except Exception as exc:  # noqa: BLE001 — a measurement; the TYPE is what is compared
        return Raises(raised_type(exc))


# ---------------------------------------------------------------------------
# The expectation table — Django's bytes, plus the floor
# ---------------------------------------------------------------------------
#: The process died in cells H-plain / P-plain / P-liveview / P0-* before
#: #2624. They are asserted in a child by `TestTheFormerCrashCells`.


@dataclass(frozen=True)
class Row:
    id: str
    source: str
    make_ctx: Callable[[], dict]
    #: Django's own bytes, measured every run by
    #: `test_django_renders_what_the_table_says`. Every djust cell is held to
    #: this column — except a floor row.
    django: Any
    #: E1–E4: the serialization floor (SECURE_DEFAULTS Pattern 1). Holds the
    #: bytes djust renders INSTEAD of Django's; these cells differ from Django
    #: DELIBERATELY and must never be "moved to Django's bytes".
    floor: str | None = None


CLASS_LEVEL_2 = "class-level,class-level,"
MARKER_DICT = "{&#x27;do_not_call_in_templates&#x27;: True}"
#: Row P0: Django's step 3 is `current[int(bit)]`, which on a `list`-subclass
#: CLASS honours `__class_getitem__` and yields an INT-keyed `types.GenericAlias`.
MYCLASS_ALIAS_0 = f"{MyClass.__module__}.MyClass[0]"


def _r(id_, source, make_ctx, django, *, floor=None) -> Row:
    return Row(id_, source, make_ctx, django, floor)


def _fresh_j() -> dict:
    J_CALLS.clear()
    return {"callable": _j_lambda}


def _fresh_j2() -> dict:
    J_CALLS.clear()
    return {"var": {"callable": _j_lambda}}


ROWS: list[Row] = [
    _r(
        "A",
        "{{ o.keep }}",
        lambda: {"o": Mutating()},
        "&lt;bound method Mutating.keep of &lt;Mutating&gt;&gt;",
    ),
    _r("B", "{{ o.attr.sub }}", lambda: {"o": Nested()}, "deep"),
    _r("C", "{{ d.1 }}", lambda: {"d": {1: "one"}}, "one"),
    _r("D1", "{{ x.0 }}", lambda: {"x": ["zero", "one"]}, "zero"),
    _r("D2", "{{ x.0 }}", lambda: {"x": {"0": "strkey"}}, "strkey"),
    _r("D3", "{{ x.0 }}", lambda: {"x": {0: "intkey"}}, "intkey"),
    _r("E1", "{{ u.password }}", lambda: {"u": make_user()}, "pbkdf2$hash", floor=""),
    _r(
        "E2",
        "{{ p.get_user.password }}",
        lambda: {"p": Presenter(make_user())},
        "pbkdf2$hash",
        floor="",
    ),
    _r(
        "E3",
        "{{ p.user.password }}",
        lambda: {"p": Presenter(make_user())},
        "pbkdf2$hash",
        floor="",
    ),
    _r(
        "E4",
        "{% for u in us %}[{{ u.password }}]{% endfor %}",
        lambda: {"us": [make_user()]},
        "[pbkdf2$hash]",
        floor="[]",
    ),
    _r("F1", "{{ r.attr_err }}", lambda: {"r": Raiser()}, Raises(AttributeError)),
    _r("F2", "{{ r.key_err }}", lambda: {"r": Raiser()}, Raises(KeyError)),
    _r("F3", "{{ r.silent }}", lambda: {"r": Raiser()}, ""),
    _r("F4", "{{ r.loud }}", lambda: {"r": Raiser()}, Raises(RuntimeError)),
    _r("F5", "{{ r.loud_false }}", lambda: {"r": Raiser()}, Raises(NotSilent)),
    _r("F6", "{{ r.silent_method }}", lambda: {"r": Raiser()}, ""),
    _r("G", "{% if x|default_if_none:y %}yes{% else %}no{% endif %}", lambda: {"y": 1}, "yes"),
    # G2 / G3 guard the two halves of the #2539 `ignore_failures` fix, and
    # both are RIGHT today — which is what makes them regression guards rather
    # than progress rows: each goes red if its defect returns.
    #
    # G2 is the STRICT half. Django resolves a `{% with %}` operand with
    # `ignore_failures=False`, so a missing `y` is `string_if_invalid` and
    # `default_if_none` does NOT fire. The first version of movement 2 put the
    # None substitution in the shared pipe branch, which claimed `{% with %}`,
    # `{% include … with %}` and every tag/filter argument along with the five
    # tags that really do ignore failures — and rendered `[D]` here.
    _r("G2", "{% with x=y|default_if_none:'D' %}[{{ x }}]{% endwith %}", lambda: {}, "[]"),
    # G3 is the TYPED half, and the reason row G alone was not enough: row G's
    # `y` is 1, so a fallback that came back as the STRING "1" answered `yes`
    # by luck. `y = 0` is Django's own `test_if_tag_badarg02` value and tells
    # the two apart — `Value::Integer(0)` is falsy, `Value::String("0")` is
    # not. The dispatch table takes a `&str`, so the fallback was stringified
    # until the resolution site started returning the argument's own value.
    _r("G3", "{% if x|default_if_none:y %}yes{% else %}no{% endif %}", lambda: {"y": 0}, "no"),
    # H-plain used to SEGFAULT: the eager `__dict__` walk recursed through the
    # cycle. Fixed by the conversion's depth ceiling (#2624).
    _r("H", "{{ x }}", cycle, "1"),
    _r("I", "{{ o }}", lambda: {"o": Plain()}, "&lt;Plain&gt;"),
    _r("J", "{{ callable }}", _fresh_j, "foo bar"),
    _r("J2", "{{ var.callable }}", _fresh_j2, "foo bar"),
    _r("K", "{{ d.the_value }}", lambda: {"d": Doodad(42)}, "42"),
    _r("K2", "{{ d.the_value }}", lambda: {"d": DoodadAlters(42)}, ""),
    _r("K3", "{{ d.value }}", lambda: {"d": Doodad(42)}, ""),
    _r("K4", "{{ d.value }}", lambda: {"d": DoodadAlters(42)}, ""),
    _r("L", "{{ d.items }}", lambda: {"d": {"items": "the-key"}}, "the-key"),
    _r(
        "M",
        "{% for x in p|slice:':2' %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": Plain()},
        CLASS_LEVEL_2,
    ),
    _r(
        "M2",
        "{% for x in p|slice:':2' %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": Outer()},
        CLASS_LEVEL_2,
    ),
    _r(
        "M3",
        "{% for x in p %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": Outer()},
        CLASS_LEVEL_2,
    ),
    _r(
        "M4",
        "{% for x in p %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": 5},
        CLASS_LEVEL_2,
    ),
    _r(
        "M5",
        "{% for x in p|slice:':2' %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": 5},
        CLASS_LEVEL_2,
    ),
    _r(
        "M6",
        "{% with x=p.0 %}{{ x.cls_attr }}{% endwith %}",
        lambda: {"p": [Cls(), Cls()], "x": Outer()},
        "class-level",
    ),
    _r(
        "N",
        "{% for r in rows|slice:':1' %}{{ r.cls_attr }},{% endfor %}",
        lambda: {"rows": [Cls(), Cls()]},
        "class-level,",
    ),
    _r(
        "N2",
        "{% for r in dd.values %}{{ r.cls_attr }},{% endfor %}",
        lambda: {"dd": {"a": Cls()}},
        "class-level,",
    ),
    _r(
        "N0",
        "{% for r in rows %}{{ r.cls_attr }},{% endfor %}",
        lambda: {"rows": [Cls(), Cls()]},
        CLASS_LEVEL_2,
    ),
    _r("N0b", "{{ rows.0.cls_attr }}", lambda: {"rows": (Cls(), Cls())}, "class-level"),
    _r("O", "{{ s }}", lambda: {"s": SafeObj()}, "<b>s</b>"),
    # P used to SEGFAULT on both paths (#2624).
    _r(
        "P",
        "{{ class_var.class_property }} | {{ class_var.class_method }}",
        lambda: {"class_var": MyClass},
        "Example property | Example method",
    ),
    # P0 is the NUMERIC-index form (#2624) — undeclared until that issue,
    # and a segfault on both paths. Django's step 3 is `current[int(bit)]`,
    # so ITS answer is the int-keyed alias, not the class attribute; the
    # ADR-027 sink matches it.
    _r("P0", "{{ class_var.0 }}", lambda: {"class_var": MyClass}, MYCLASS_ALIAS_0),
    _r("Q", "{{ k }}", lambda: {"k": Cls}, "&lt;Cls&gt;"),
    _r("R", "{{ g.x }}", lambda: {"g": GetItemRaiser()}, Raises(RuntimeError)),
    _r("S", "{% if r.silent %}T{% else %}F{% endif %}", lambda: {"r": Raiser()}, "F"),
    _r("T", "{% if o %}T{% else %}F{% endif %}/{{ o|length }}", lambda: {"o": Plain()}, "T/0"),
    _r("U", "{{ u.username }}", lambda: {"u": make_user()}, "alice"),
    _r("V", "{% for i in g %}{{ i }}{% endfor %}", lambda: {"g": gen()}, "12"),
    _r("W", "{{ np.foo }}", lambda: {"np": NpLike()}, "attr-foo"),
]

ROW_BY_ID: dict[str, Row] = {row.id: row for row in ROWS}

#: The serialization floor — a stated SET (#1125), listed apart because these
#: are the only cells that may differ from Django.
FLOOR_ROWS = frozenset("E1 E2 E3 E4".split())

#: Every (row, path) cell, all run in-process since #2624 retired the crash
#: cells (`TestTheFormerCrashCells` still asserts those in a child).
CELLS = [pytest.param(row, path, id=f"{row.id}-{path}") for row in ROWS for path in PATHS]


def assert_matches_django(row: Row, path: str, actual: Any) -> None:
    """THE contract since #2628: a non-floor cell equals **Django's** column,
    which `test_django_renders_what_the_table_says` measures against the real
    Django engine every run; a floor cell renders its recorded floor bytes
    and never Django's. Module-level so `TestTheTableIsLoadBearing` can call
    the SAME function on a wrong answer."""
    if row.floor is not None:
        assert actual != row.django and actual == row.floor, (
            f"row {row.id} on {path}: the serialization floor moved — {actual!r}. This cell is a "
            f"SECURITY pin (SECURE_DEFAULTS Pattern 1); matching Django here is a leak, never "
            f"progress."
        )
        return
    assert actual == row.django, (
        f"row {row.id} on {path} does not answer Django's bytes: {actual!r} != {row.django!r}"
    )


# ---------------------------------------------------------------------------
# 1. The differential
# ---------------------------------------------------------------------------
@pytest.mark.django_db
class TestTheDifferentialTable:
    @pytest.mark.parametrize("row", ROWS, ids=[r.id for r in ROWS])
    def test_django_renders_what_the_table_says(self, row: Row) -> None:
        """The premise column. Goes red on a Django bump, by design."""
        actual = observe(django_render, row.source, row.make_ctx())
        assert actual == row.django, (
            f"row {row.id}: DJANGO's own answer moved ({actual!r} != {row.django!r}) — a Django "
            f"version change, not a djust change; re-measure the premise column."
        )

    @pytest.mark.parametrize(("row", "path"), CELLS)
    def test_the_djust_column_answers_django(self, row: Row, path: str) -> None:
        """The headline gate. Until #2628 this was the flag-ON claim, made
        under an explicit push and again under the shipped default; the flag
        is gone, so the ambient render is the only state there is."""
        actual = observe(RENDER[path], row.source, row.make_ctx())
        assert_matches_django(row, path, actual)


class TestThePlainEntriesAgree:
    """The two raw entries `DjustTemplateBackend` binds answer the backend's
    bytes on every row — the #1646 twin check."""

    @pytest.mark.parametrize("row", ROWS, ids=[r.id for r in ROWS])
    @pytest.mark.parametrize(
        "entry",
        [
            pytest.param(render_template, id="render_template"),
            pytest.param(render_template_with_dirs, id="render_template_with_dirs"),
        ],
    )
    def test_a_raw_entry_answers_the_backends_bytes(self, row: Row, entry) -> None:
        via_backend = observe(plain_render, row.source, row.make_ctx())
        via_entry = observe(entry, row.source, row.make_ctx())
        assert via_entry == via_backend, f"row {row.id}: {entry.__name__} diverges from the backend"
        assert_matches_django(row, "plain", via_backend)


class TestTheTableIsSelfConsistent:
    def test_every_row_has_a_django_column_and_a_unique_id(self) -> None:
        assert len(ROWS) == 48
        assert len(ROW_BY_ID) == 48
        assert len(CELLS) == 48 * 2
        for row in ROWS:
            assert row.django is not None, f"row {row.id} lacks a Django column"

    def test_the_floor_rows_are_exactly_the_stated_set(self) -> None:
        floor = {r.id for r in ROWS if r.floor is not None}
        assert floor == FLOOR_ROWS
        for rid in floor:
            row = ROW_BY_ID[rid]
            assert row.floor != row.django, rid
            assert "pbkdf2$hash" in row.django and "pbkdf2$hash" not in row.floor, rid


class TestTheTableIsLoadBearing:
    """#1039 / #1468: `assert_matches_django` goes red in every direction."""

    def test_a_non_django_answer_fails_by_name(self) -> None:
        row = ROW_BY_ID["I"]
        assert_matches_django(row, "plain", row.django)  # the genuine answer passes
        with pytest.raises(AssertionError, match="does not answer Django's bytes"):
            assert_matches_django(row, "plain", row.django + "x")

    def test_a_floor_cell_that_leaks_fails_as_a_security_pin(self) -> None:
        row = ROW_BY_ID["E1"]
        assert_matches_django(row, "plain", row.floor)
        with pytest.raises(AssertionError, match="SECURITY pin"):
            assert_matches_django(row, "plain", row.django)
        with pytest.raises(AssertionError, match="SECURITY pin"):
            assert_matches_django(row, "plain", row.floor + "x")


class TestTheDjangoSideIsNonTrivial:
    """Gate-off siblings of the differential (#1468): the Django column is
    produced by a real mechanism, not a coincidence of reprs."""

    def test_the_lambda_is_called_exactly_once_by_django(self) -> None:
        for row_id in ("J", "J2"):
            row = ROW_BY_ID[row_id]
            ctx = row.make_ctx()
            assert J_CALLS == []
            assert django_render(row.source, ctx) == "foo bar"
            assert len(J_CALLS) == 1, row_id

    def test_test_callables_shapes_count_their_calls(self) -> None:
        d = Doodad(42)
        assert django_render(ROW_BY_ID["K3"].source, {"d": d}) == ""
        assert d.num_calls == 1
        d2 = DoodadAlters(42)
        assert django_render(ROW_BY_ID["K4"].source, {"d": d2}) == ""
        assert d2.num_calls == 0

    def test_the_generator_is_exhausted_by_django(self) -> None:
        g = gen()
        assert django_render(ROW_BY_ID["V"].source, {"g": g}) == "12"
        with pytest.raises(StopIteration):
            next(g)

    def test_the_marker_dict_row_is_a_bound_method_in_django(self) -> None:
        assert ROW_BY_ID["A"].django.startswith("&lt;bound method")
        assert django_render("{{ o.keep }}", {"o": Mutating()}).startswith("&lt;bound method")

    def test_django_never_answers_the_outer_object(self) -> None:
        for row_id in ("M2", "M3", "M6"):
            row = ROW_BY_ID[row_id]
            assert "OUTER" not in row.django
            assert "OUTER" not in django_render(row.source, row.make_ctx())

    def test_the_for_rows_are_non_empty_in_django(self) -> None:
        for row_id in ("N", "N2", "N0"):
            row = ROW_BY_ID[row_id]
            assert row.django.strip(",")
            assert django_render(row.source, row.make_ctx()).strip(",")


# ---------------------------------------------------------------------------
# 2. The former crash cells — subprocess per cell, asserting they RENDER
# ---------------------------------------------------------------------------
CHILD = r"""
import json
import re
import sys

import django
from django.conf import settings

settings.configure(
    SECRET_KEY="x",
    DEBUG=False,
    ROOT_URLCONF=__name__,
    ALLOWED_HOSTS=["*"],
    INSTALLED_APPS=[
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "django.contrib.sessions",
        "djust",
    ],
    SESSION_ENGINE="django.contrib.sessions.backends.signed_cookies",
    DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
    TEMPLATES=[
        {
            "BACKEND": "djust.template_backend.DjustTemplateBackend",
            "NAME": "djust",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {},
        }
    ],
)
urlpatterns = []
django.setup()

from djust.live_view import LiveView
from djust.template_backend import DjustTemplateBackend
from djust.testing import LiveViewTestClient


class MyClass(list):
    class_property = "Example property"
    do_not_call_in_templates = True

    @classmethod
    def class_method(cls):
        return "Example method"


class Mutating:
    def keep(self):
        return "kept"

    keep.do_not_call_in_templates = True

    def __repr__(self):
        return "<Mutating>"


def cycle():
    d = {"x": 1}

    class C:
        def __init__(self, d):
            self.d = d

    d["t"] = C(d)
    return d


SHAPES = {
    "P": ("{{ class_var.class_property }} | {{ class_var.class_method }}", lambda: {"class_var": MyClass}),
    "P0": ("{{ class_var.0 }}", lambda: {"class_var": MyClass}),
    "H": ("{{ x }}", cycle),
    "A": ("{{ o.keep }}", lambda: {"o": Mutating()}),
}
key, path = sys.argv[1], sys.argv[2]
src, make_ctx = SHAPES[key]
if path == "plain":
    backend = DjustTemplateBackend(
        params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
    )
    out = backend.from_string(src).render(context=make_ctx(), request=None)
else:
    context = make_ctx()

    class _V(LiveView):
        def mount(self, request, **kwargs):
            pass

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx.update(context)
            return ctx

    _V.template = "<div dj-root>" + src + "</div>"
    client = LiveViewTestClient(_V)
    client.mount()
    html = client.render()
    out = re.search(r"<div dj-root[^>]*>(.*)</div>", html, re.S).group(1)
print("RENDERED " + json.dumps(out))
"""

#: The signals a memory-fault crash arrives as. A FIX is `returncode == 0`
#: with a `RENDERED` line — so any of these keeps the "still crashes today"
#: reading honest across platforms while a fix still flips to a named failure.
CRASH_SIGNALS = {-signal.SIGSEGV, -signal.SIGBUS, -signal.SIGABRT}

#: The cells that USED to kill the process — the three #2516/#2517 crash
#: cells plus the numeric-index variant #2624 found undeclared. Every one
#: renders since the conversion gained a depth ceiling (#2624); each is still
#: run in a CHILD, because "does not segfault" is a claim only
#: a subprocess can make, and a returning crash must flip to a named failure
#: rather than take the suite with it.
FORMER_CRASH_CELLS = [
    ("H", "plain"),
    ("P", "plain"),
    ("P", "liveview"),
    ("P0", "plain"),
    ("P0", "liveview"),
]


def run_child(key: str, path: str) -> subprocess.CompletedProcess:
    """The repo's `python/` goes FIRST on `PYTHONPATH` so a worktree run
    imports the checkout under test, not an installed djust (#2533)."""
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(PYTHON_DIR), env.get("PYTHONPATH", "")) if p
    )
    return subprocess.run(
        [sys.executable, "-c", CHILD, key, path],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=120,
        check=False,
    )


def child_rendered(proc: subprocess.CompletedProcess) -> str:
    for line in proc.stdout.splitlines():
        if line.startswith("RENDERED "):
            return json.loads(line[len("RENDERED ") :])
    raise AssertionError(f"no RENDERED line; rc={proc.returncode}\n{proc.stderr[-2000:]}")


def child_module_normalized(proc: subprocess.CompletedProcess) -> str:
    """The child's bytes with its ``__main__.`` spelled as this module.

    Rows P / P0 render a ``types.GenericAlias`` whose ``repr`` names the
    class's module — ``__main__`` in the ``-c`` child, this test module in
    process. The table records the in-process spelling; the child's is the
    same bytes under a different module name and nothing else."""
    return child_rendered(proc).replace("__main__.", f"{MyClass.__module__}.")


class TestTheFormerCrashCells:
    """Rows H (reference cycle, conversion), P (`list`-subclass class,
    `__class_getitem__` in the walk) and P0 (its numeric-index form, #2624)
    USED to kill the process. Each is asserted in a child so that a returning
    crash flips to a NAMED failure rather than taking the suite with it, and
    the rendered bytes are held to the same contract the in-process cells are:
    Django's column."""

    @pytest.mark.parametrize(
        ("key", "path"), FORMER_CRASH_CELLS, ids=[f"{k}-{p}" for k, p in FORMER_CRASH_CELLS]
    )
    def test_the_cell_renders(self, key: str, path: str) -> None:
        proc = run_child(key, path)
        assert proc.returncode not in CRASH_SIGNALS, (
            f"row {key} on {path} CRASHES again (rc={proc.returncode}) — the #2624 depth "
            f"ceiling or its neighbours regressed; stderr tail={proc.stderr[-500:]!r}"
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        assert_matches_django(ROW_BY_ID[key], path, child_module_normalized(proc))

    @pytest.mark.django_db
    @pytest.mark.parametrize("path", PATHS)
    def test_the_child_renders_a_non_crash_row_like_the_in_process_entry(self, path: str) -> None:
        """The child harness is the same path and not a no-op: row A through
        the child equals row A through the in-process column, and both are
        Django's bytes (not the #2502 marker dict the OFF path rendered)."""
        proc = run_child("A", path)
        assert proc.returncode == 0, proc.stderr[-2000:]
        row = ROW_BY_ID["A"]
        in_process = observe(RENDER[path], row.source, row.make_ctx())
        assert child_rendered(proc) == in_process == row.django
        assert in_process != MARKER_DICT


# ---------------------------------------------------------------------------
# 3. Per-instance characterizations, each with its sibling
# ---------------------------------------------------------------------------
class KeepProbe:
    def keep(self) -> str:
        self.kept_called = True
        return "kept"

    keep.do_not_call_in_templates = True  # type: ignore[attr-defined]

    def plain(self) -> str:
        self.plain_called = True
        return "PLAIN"


@pytest.mark.django_db
class TestDoNotCallRendersTheMarkerDict2502:
    """#2502 on the REAL LiveView entry: the marker dict the OFF path rendered
    for `{{ o.keep }}` is gone with the flag (#2628). The lookup walks the
    live object, so `do_not_call_in_templates` is honoured by the SEGMENT
    WALK — the bound method is rendered as-is, which is Django's answer."""

    def test_2502_is_closed(self) -> None:
        row = ROW_BY_ID["A"]
        django = django_render(row.source, row.make_ctx())
        assert django.startswith("&lt;bound method")
        for path in PATHS:
            actual = observe(RENDER[path], row.source, row.make_ctx())
            assert actual == django, (
                f"#2502 on {path}: djust renders {actual!r}, Django renders {django!r}"
            )
            assert actual != MARKER_DICT

    @pytest.mark.parametrize("render", ALL_FOUR)
    def test_the_guard_is_load_bearing(self, render) -> None:
        """Sibling: `keep` is never called on any path, while an unstamped
        method on the same object IS."""
        stamped = KeepProbe()
        render("{{ o.keep }}", {"o": stamped})
        assert getattr(stamped, "kept_called", False) is False, (
            "do_not_call_in_templates method was CALLED by a lookup"
        )
        unstamped = KeepProbe()
        assert render("{{ o.plain }}", {"o": unstamped}) == "PLAIN"
        assert unstamped.plain_called is True


@pytest.mark.django_db
class TestFilteredAndDictViewOperands2504:
    """#2504 on the real LiveView entry, plus the unfiled real-entry instance
    (N0/N0b): a plain object inside a top-level list/tuple. The by-name
    sidecar could not reach these because a `list` is `_JSON_FRIENDLY` and
    never entered it; the object inside the list now carries its own handle,
    so the loop variable resolves against the live object and the containing
    list stops mattering."""

    @pytest.mark.parametrize("row_id", ["N", "N2", "N0", "N0b"])
    def test_2504_is_closed(self, row_id: str) -> None:
        row = ROW_BY_ID[row_id]
        django = django_render(row.source, row.make_ctx())
        assert django.strip(","), "premise: Django renders it"
        actual = liveview_render(row.source, row.make_ctx())
        assert actual == django, (
            f"#2504 row {row_id}: djust renders {actual!r}, Django renders {django!r}"
        )
        assert actual.strip(","), "the attribute is still unreachable"


@pytest.mark.django_db
class TestShadowingNeverResolvesAgainstTheOuterObject2505:
    """Local bindings cannot resolve attributes on a shadowed outer object.

    The shadowing bug was the by-NAME sidecar answering the OUTER object for
    a loop/`with` variable that reuses its name. The bound value carries its
    own handle, so the name is never looked up in a by-name map and cannot
    collide — the structural cure rather than a shadowing rule."""

    @pytest.mark.parametrize("row_id", ["M2", "M3", "M6"])
    @pytest.mark.parametrize("path", PATHS)
    def test_2505_is_closed(self, row_id: str, path: str) -> None:
        row = ROW_BY_ID[row_id]
        django = django_render(row.source, row.make_ctx())
        assert "OUTER" not in django
        actual = RENDER[path](row.source, row.make_ctx())
        assert actual == django, (
            f"#2505 row {row_id} on {path}: djust renders {actual!r}, Django renders {django!r}"
        )
        assert "OUTER" not in actual, "the outer object still answers"


def render_page_shell(template: str, *, with_serialized_context: bool, card_cls=ShellCard) -> str:
    """The page-shell path: `render_full_template(request, serialized_context=…)`
    (lifted from `test_sidecar_on_all_render_paths_2501.py`). Returns the
    `<nav>` the template was placed in."""
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import RequestFactory

    class _V(LiveView):
        def mount(self, request, **kwargs):
            self.c = card_cls()

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["c"] = self.c
            return ctx

    _V.template = "<div dj-root>inner</div>"
    _V._full_template = f"<html><body><div dj-root>inner</div><nav>{template}</nav></body></html>"

    request = RequestFactory().get("/")
    SessionMiddleware(lambda r: r).process_request(request)
    request.session.save()

    view = _V()
    view.setup(request)
    view.mount(request)
    view._full_template = _V._full_template
    serialized_context = view.get_context_data() if with_serialized_context else None
    html = view.render_full_template(request, serialized_context=serialized_context)
    match = re.search(r"<nav>(.*?)</nav>", html, re.S)
    assert match is not None, html
    return match.group(1)


SHELL_BRANCHES = [
    pytest.param(False, id="else-branch"),
    pytest.param(True, id="sibling"),
]


@pytest.mark.django_db
class TestThePageShellPathHasTheSidecar2589:
    """#2513 pinned the page shell as wiring NO sidecar on either branch (a
    component's dotted spellings rendered empty there). #2589 closed that gap
    — the shell now carries the same `build_render_sidecar` the other entries
    get — so both branches resolve the dotted spellings exactly as the dj-root
    LiveView path does (sibling test below)."""

    @pytest.mark.parametrize("with_serialized_context", SHELL_BRANCHES)
    def test_dotted_spellings_resolve_on_both_branches(self, with_serialized_context: bool) -> None:
        nav = render_page_shell(
            "[{{ c }}][{{ c.render }}][{{ c.render|safe }}][{{ c.cls_attr }}]",
            with_serialized_context=with_serialized_context,
        )
        assert nav == "[<b>shellcard</b>][<b>shellcard</b>][<b>shellcard</b>][shell-class-level]"

    def test_the_dj_root_liveview_path_resolves_the_same_spellings(self) -> None:
        """Sibling: the same component on the dj-root LiveView path DOES
        resolve its dotted spellings (`TestTheLiveViewPathsComponentExclusion`
        in the 2501 file pins `{{ c.render|safe }}`; this is its twin against
        the real entry), so the empty cells above are the shell's missing
        sidecar and not the component."""
        out = liveview_render("[{{ c.render|safe }}][{{ c.cls_attr }}]", {"c": ShellCard()})
        assert out == "[<b>shellcard</b>][shell-class-level]"


@pytest.mark.django_db
class TestTheHarnessIsTheRealPath:
    """The #1650 lesson (module docstring, N0) made executable: the LiveView
    column IS `LiveViewTestClient.render()` → `_sync_state_to_rust`, never a
    `set_raw_py_values` stand-in."""

    def test_the_liveview_column_runs_sync_state_to_rust(self) -> None:
        calls: list[int] = []
        original = RustBridgeMixin._sync_state_to_rust

        def spy(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)

        # The CLAIM is the spy; the render is here only to prove it was a real
        # one (row A, held to Django's bytes like every other cell).
        with mock.patch.object(RustBridgeMixin, "_sync_state_to_rust", spy):
            assert liveview_render("{{ o.keep }}", {"o": Mutating()}) == ROW_BY_ID["A"].django
        assert calls, "the LiveView column did not go through _sync_state_to_rust"

    def test_no_code_in_this_file_calls_the_stand_in(self) -> None:
        """Every CALL node in this module's AST (docstrings and the `CHILD`
        string are data, not calls): none is `.set_raw_py_values(...)`."""
        source = inspect.getsource(liveview_render)
        assert "LiveViewTestClient(" in source
        tree = ast.parse(THIS_FILE.read_text(encoding="utf-8"))
        stand_in_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "set_raw_py_values"
        ]
        assert stand_in_calls == [], (
            "a helper in this file hands the raw context to the sidecar directly — "
            "that is the stand-in, not the real entry (#1650)"
        )


# ---------------------------------------------------------------------------
# 4. Security pins
# ---------------------------------------------------------------------------
class _GuardedDoc:
    title = "Q3 layoffs memo"

    @property
    def is_restricted(self) -> bool:
        raise PermissionDenied("acl backend down")

    def is_restricted_method(self) -> bool:
        raise PermissionDenied("acl backend down")


class _Holder:
    def __init__(self, doc: Any) -> None:
        self.doc = doc


GUARD_SOURCE = "{% if not d.doc.is_restricted %}{{ d.doc.title }}{% else %}(withheld){% endif %}"


@pytest.mark.django_db
class TestALookupExceptionNeverFailsOpen2506:
    """#2506, pinned on every djust entry INCLUDING the real LiveView one:
    an exception from a lookup propagates (or renders per Django's
    `silent_variable_failure` rule) and is never rendered as an authorised
    value. These assert the FIX and must outlive the flip."""

    def test_a_raising_property_propagates_through_the_real_entry(self) -> None:
        with pytest.raises(RuntimeError) as exc:
            liveview_render("{{ r.loud }}", {"r": Raiser()})
        assert "authz" in str(exc.value)

    @pytest.mark.parametrize("render", ALL_FOUR)
    @pytest.mark.parametrize("guard", ["is_restricted", "is_restricted_method"])
    def test_the_gated_content_never_renders(self, render, guard: str) -> None:
        source = GUARD_SOURCE.replace("is_restricted", guard)
        assert django_render(source, {"d": _Holder(_GuardedDoc())}) == "(withheld)"
        with pytest.raises(Exception) as exc:
            render(source, {"d": _Holder(_GuardedDoc())})
        assert raised_type(exc.value) is PermissionDenied
        assert "Q3 layoffs memo" not in str(exc.value)

    @pytest.mark.parametrize("render", ALL_FOUR)
    def test_a_non_raising_gate_renders_both_ways(self, render) -> None:
        """Non-vacuity: an engine that refused the whole template would pass
        every assertion above."""

        class _Open:
            title = "public memo"
            is_restricted = False

        class _Shut(_Open):
            is_restricted = True

        for doc, expected in ((_Open(), "public memo"), (_Shut(), "(withheld)")):
            assert django_render(GUARD_SOURCE, {"d": _Holder(doc)}) == expected
            assert render(GUARD_SOURCE, {"d": _Holder(doc)}) == expected

    @pytest.mark.parametrize("render", ALL_FOUR)
    def test_a_getitem_raising_runtime_error_propagates(self, render) -> None:
        """Row R: an error OUTSIDE Django's step-1 catch set is a real
        `__getitem__` failure on every path."""
        with pytest.raises(Exception) as exc:
            render("{{ g.x }}", {"g": GetItemRaiser()})
        assert raised_type(exc.value) is RuntimeError

    @pytest.mark.parametrize("render", ALL_FOUR)
    def test_silence_is_decided_by_the_attributes_truth(self, render) -> None:
        """F3 / S / F6 render empty (truthy `silent_variable_failure`, also
        when raised INSIDE an auto-called method); F5's explicit `False`
        propagates — on every path, as in Django."""
        assert render("{{ r.silent }}", {"r": Raiser()}) == ""
        assert render("{% if r.silent %}T{% else %}F{% endif %}", {"r": Raiser()}) == "F"
        assert render("{{ r.silent_method }}", {"r": Raiser()}) == ""
        with pytest.raises(Exception) as exc:
            render("{{ r.loud_false }}", {"r": Raiser()})
        assert raised_type(exc.value) is NotSilent

    @pytest.mark.parametrize("render", ALL_FOUR)
    @pytest.mark.parametrize("row_id", ["F1", "F2", "F4", "F5"])
    def test_a_raising_row_never_renders_as_a_value(self, render, row_id: str) -> None:
        row = ROW_BY_ID[row_id]
        outcome = observe(render, row.source, row.make_ctx())
        assert isinstance(outcome, Raises), f"row {row_id} rendered {outcome!r} instead of raising"
        assert outcome == row.django


MUTATORS = sorted(ALTERS_DATA_COMPONENT_METHODS)


def make_recording_card(calls: list) -> type:
    """A `LiveComponent` (the class that carries all five mutators) whose
    overrides RECORD — the 2501 file's `_widget`, on the page-shell path."""

    class _Card(LiveComponent):
        template = "<b>shellcard</b>"

        def get_context_data(self) -> dict:
            return {}

        def mount(self, **kwargs) -> None:
            calls.append("mount")

        def unmount(self) -> None:
            calls.append("unmount")
            super().unmount()

        def update(self, **kwargs):
            calls.append("update")
            return self

        def trigger_update(self) -> None:
            calls.append("trigger_update")

        def clear_context_providers(self) -> None:
            calls.append("clear_context_providers")

    return _Card


@pytest.mark.django_db
class TestMutatorsAreNeverAutoCalled2507:
    """#2507 — `{{ c.unmount }}` never runs a mutator, pinned on the page-shell
    path (both branches) for every guarded name on a subclass that OVERRIDES
    it.

    Since #2589 the shell carries a sidecar and the live handle reaches
    `unmount` on this path, so this is load-bearing (gate-off: drop the
    `__init_subclass__` re-stamp ⇒ red). Its siblings are
    `TestComponentMutatorsAreNeverAutoCalled` (all four columns) and
    `TestMutatorsRefusedOnTheLiveViewPath` in the 2501 file — cited, not
    copied.
    """

    @pytest.mark.parametrize("with_serialized_context", SHELL_BRANCHES)
    @pytest.mark.parametrize("method", MUTATORS)
    def test_an_overridden_mutator_is_not_run_by_the_shell(
        self, method: str, with_serialized_context: bool
    ) -> None:
        calls: list = []
        card_cls = make_recording_card(calls)
        nav = render_page_shell(
            "[{{ c }}][{{ c.%s }}]" % method,
            with_serialized_context=with_serialized_context,
            card_cls=card_cls,
        )
        # Presence AND silence: the card itself renders (so the lookup did
        # reach `c`), and the mutator segment renders nothing.
        assert nav.endswith("][]"), f"{method} rendered something: {nav!r}"
        assert nav != "[][]", "the card itself rendered empty — the shell never resolved `c`"
        # Construction dispatches `mount` through the framework's own
        # lifecycle — a legitimate DIRECT call; nothing else may appear.
        assert [c for c in calls if c != "mount"] == [], f"{method} was CALLED during the render"
        assert calls.count("mount") <= 1

    @pytest.mark.parametrize("method", MUTATORS)
    def test_the_marker_survives_an_override(self, method: str) -> None:
        card_cls = make_recording_card([])
        assert getattr(getattr(card_cls, method), "alters_data", False) is True
        assert getattr(getattr(LiveComponent, method), "alters_data", False) is True

    def test_the_guarded_set_is_the_documented_one(self) -> None:
        assert set(MUTATORS) == {
            "mount",
            "unmount",
            "update",
            "trigger_update",
            "clear_context_providers",
        }
        assert "render" not in ALTERS_DATA_COMPONENT_METHODS
        assert getattr(LiveComponent.render, "alters_data", False) is False


# ---------------------------------------------------------------------------
# 5. The sink has exactly one caller, reached unconditionally
# ---------------------------------------------------------------------------
def _production(source: str) -> str:
    """Rust source with `//` comment lines and any `#[cfg(test)]` module
    removed (the `_production` idiom of `test_encoded_attributes_2481.py`)."""
    head = source.split("#[cfg(test)]", 1)[0]
    return "\n".join(line for line in head.splitlines() if not line.lstrip().startswith("//"))


CALLERS = re.compile(r"(?<!fn )walk_live\(")
#: Every Rust source in the workspace other than the file that defines the
#: sink — a caller added in `djust_templates` or `djust_live` must redden the
#: unrouted pin just as one in `context.rs` does.
OTHER_CRATE_SOURCES = sorted(
    path for path in (ROOT / "crates").glob("*/src/**/*.rs") if path != CONTEXT_RS
)


def _lookup_segment_body(ctx: str) -> str:
    return ctx.split("fn lookup_segment", 1)[1].split("\n}\n", 1)[0]


def _fn_body(source: str, header: str) -> str:
    """The body of a `fn`, from its header to the next same-indent `}`."""
    after = source.split(header, 1)[1]
    return after.split("\n    }\n", 1)[0]


class TestTheSinkHasExactlyOneCaller2539:
    """`Context::walk_live` is defined once, reads no `Encoded` attribute map,
    calls no `lookup_segment`, and has EXACTLY ONE caller —
    `Context::walk_from_handle` — and none anywhere else in the workspace.
    Since #2628 the route to it is UNCONDITIONAL: the `crate::resolve_lazy()`
    gate that used to enclose it was deleted with the flag."""

    def test_the_sink_is_defined_once(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        assert ctx.count("fn walk_live") == 1
        assert "pub enum Walked" in ctx
        assert "Object(pyo3::Bound<'py, pyo3::PyAny>)" in ctx
        assert "Invalid," in ctx

    def test_exactly_one_call_site_and_it_is_walk_from_handle(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        assert len(CALLERS.findall(ctx)) == 1, (
            "the ADR-027 sink must have EXACTLY ONE call site — a second resolver "
            "reaching it is the #1646 shape the whole movement exists to avoid"
        )
        assert "walk_live" in _fn_body(ctx, "fn walk_from_handle"), (
            "the one caller is not `walk_from_handle`"
        )
        # The half of the movement-1 pin that stays TRUE FOREVER: the routing
        # point returns an OWNED `Value`, and `lookup_segment` returns a
        # BORROW into the value stack, so it can never be the call site.
        assert "walk_live" not in _lookup_segment_body(ctx)
        assert len(OTHER_CRATE_SOURCES) > 20, OTHER_CRATE_SOURCES
        for path in OTHER_CRATE_SOURCES:
            # Comment-strip only: `_production` cuts a file at its FIRST
            # `#[cfg(test)]`, which in e.g. `djust_templates/src/lib.rs`
            # hides 350 lines of production code that follow the test module
            # (found by gate-off: a probe caller appended there stayed green).
            source = path.read_text(encoding="utf-8")
            stripped = "\n".join(
                line for line in source.splitlines() if not line.lstrip().startswith("//")
            )
            callers = CALLERS.findall(stripped)
            assert callers == [], (
                f"{path.relative_to(ROOT)} calls walk_live — a SECOND routing point"
            )

    def test_the_route_is_unconditional(self) -> None:
        """The call site in `resolve_without_builtins` is the surviving shape
        of the former flag-ON arm: no `resolve_lazy` gate encloses it (#2628)."""
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        body = _fn_body(ctx, "fn resolve_without_builtins")
        assert "if let Some(answer) = self.walk_from_handle(key)? {" in body, (
            "resolve_without_builtins does not route through walk_from_handle"
        )
        assert "resolve_lazy" not in body, "the deleted ADR-027 gate is back"
        assert "resolve_lazy" not in ctx, "the deleted ADR-027 flag is read somewhere in context.rs"

    def test_it_keeps_the_existing_reader_pins(self) -> None:
        """`TestTheSinkHasExactlyTheReadersItClaims` (#2481) counts ONE
        `.attrs.get(` and ONE `lookup_segment(` caller in the whole file —
        the helper adds neither."""
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        assert "pub fn to_hashmap" in ctx
        body = ctx.split("fn walk_live", 1)[1].split("\n    pub fn to_hashmap", 1)[0]
        assert "walk_one_segment" in body, "the slice no longer spans the helper"
        assert ".attrs.get(" not in body
        assert "lookup_segment(" not in body

    def test_the_pin_goes_red_in_every_direction(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        # A SECOND caller.
        two = ctx + "\nlet _ = self.walk_live(py, obj, &parts, key);\n"
        assert two != ctx, "the ADD mutation did not apply"
        assert len(CALLERS.findall(two)) == 2
        # ZERO callers.
        none = ctx.replace("self.walk_live(", "self.walk_dead(", 1)
        assert none != ctx, "the REMOVE mutation did not apply"
        assert CALLERS.findall(none) == []
        # A caller OUTSIDE `walk_from_handle` — the count still reads 1, so
        # only the containment half catches this one.
        moved = ctx.replace(
            "self.walk_live(py, handle.bind(py).clone(), rest, key)",
            "self.walk_dead(py, handle.bind(py).clone(), rest, key)",
            1,
        ).replace(
            "fn walk_one_segment",
            'fn elsewhere(&self) { let _ = self.walk_live(py, o, &[], ""); }\n    fn walk_one_segment',
            1,
        )
        assert moved != ctx, "the MOVE mutation did not apply"
        assert len(CALLERS.findall(moved)) == 1, "the move must keep the COUNT at one"
        assert "walk_live" not in _fn_body(moved, "fn walk_from_handle")
        # The route GATED again.
        gated = ctx.replace(
            "if let Some(answer) = self.walk_from_handle(key)? {",
            "if crate::resolve_lazy() { if let Some(answer) = self.walk_from_handle(key)? {",
            1,
        )
        assert gated != ctx, "the GATE mutation did not apply"
        assert "resolve_lazy" in _fn_body(gated, "fn resolve_without_builtins")


class TestTheHandleNeverReachesTheWire2539:
    """The `Encoded` handle is TRANSIENT: it is not serialized, not compared
    and not handed back to Python. Each of those is a `Value`-level contract
    with a whole class of consequence behind it, so each is pinned at the
    source rather than only exercised."""

    CORE_RS = ROOT / "crates" / "djust_core" / "src" / "lib.rs"

    def _core(self) -> str:
        return _production(self.CORE_RS.read_text(encoding="utf-8"))

    def test_the_serializer_does_not_write_it(self) -> None:
        """P4. The `ENCODED_TAG` payload stays ELEVEN slots — which is what
        makes "no wire pin moves" literally true, and why
        `test_encoded_wire_positions_2471_2472.rs`'s assertions are unedited."""
        core = self._core()
        arm = core.split("Value::Encoded(e) if !serializer.is_human_readable()", 1)[1]
        arm = arm.split("Value::", 1)[0]
        assert "live" not in arm, "the serializer writes the transient handle"
        assert "e.eq_class" in arm, "the slice no longer spans the payload"

    def test_partial_eq_does_not_compare_it(self) -> None:
        """P5. A round trip drops the handle, so comparing it would make every
        restored value unequal to the one it was written from."""
        body = self._core().split("impl PartialEq for Encoded", 1)[1].split("\n}", 1)[0]
        assert "self.eq_class == other.eq_class" in body, "the slice moved"
        assert "live" not in body

    def test_neither_into_py_object_arm_hands_it_back(self) -> None:
        """P6 / #2509: a handle can never reach a custom-tag handler's
        `py_context`, because both `IntoPyObject` impls map an `Encoded` to
        its DISPLAY string and nothing else."""
        core = self._core()
        owned = core.split("impl<'py> IntoPyObject<'py> for Value", 1)[1].split("\nimpl", 1)[0]
        arm = owned.split("Value::Encoded(", 1)[1].split("Value::", 1)[0]
        assert "display" in arm, "the Encoded arm stopped handing back the display string"
        assert "live" not in arm, "a handle arm appeared in IntoPyObject for Value"
        # The `&Value` impl has NO arm of its own — it clones and delegates,
        # which is the stronger guarantee: there is one place to add a handle
        # arm, not two. Pinned so a future hand-rolled copy has to face this.
        borrowed = core.split("impl<'py> IntoPyObject<'py> for &Value", 1)[1].split("\n}\n", 1)[0]
        assert "Value::Encoded" not in borrowed, (
            "the &Value impl grew its own match — it must keep delegating to the owned one"
        )
        assert "self.clone().into_pyobject(py)" in borrowed

    def test_opaque_and_temporal_handles_have_exactly_four_producers(self) -> None:
        core = self._core()
        body = core.split("pub fn opaque_value", 1)[1].split("\n/// ", 1)[0]
        assert "resolve_lazy" not in body, "the deleted ADR-027 gate is back (#2628)"
        producer = "Some(std::sync::Arc::new(ob.clone().unbind()))"
        # FOUR producers since #2628: `opaque_value` (unconditional since
        # #2628), `handle_only_encoded` (the terminal arm for an object one of
        # `opaque_value`'s probes refused — #2628, replacing the deleted
        # `__dict__` dump), `django_json_encoded` (isinstance-checked against
        # the four temporal types) and `slim_timedelta_encoded` (#2770,
        # EXACT-type-checked against `timedelta` — the slim builder for the
        # nested `utcoffset` / `dst` results).
        assert core.count(producer) == 4
        assert producer in body
        handle_only = core.split("fn handle_only_encoded", 1)[1].split("\n}\n", 1)[0]
        assert producer in handle_only
        temporal = core.split("pub fn django_json_encoded", 1)[1].split("\n}\n", 1)[0]
        assert producer in temporal
        slim = core.split("pub fn slim_timedelta_encoded", 1)[1].split("\n}\n", 1)[0]
        assert producer in slim
        assert "if !ob.get_type().is(timedelta_cls) {" in slim, (
            "the slim producer must attach a handle only to an exact `timedelta`"
        )
        crossing = core.split("pub fn temporal_object", 1)[1].split("impl<'py> IntoPyObject", 1)[0]
        assert "is_instance(&module.getattr(kind)?)" in crossing

    def test_the_teardown_clears_every_nested_handle(self) -> None:
        """P7. `RustLiveView.state` is the one place a `Value` outlives a
        render, so it gets an explicit clear; the containers a handle can ride
        in are all covered."""
        live_rs = _production(
            (ROOT / "crates" / "djust_live" / "src" / "lib.rs").read_text(encoding="utf-8")
        )
        assert "fn clear_live_handles(&mut self)" in live_rs
        walker = live_rs.split("fn clear_live_handles_in", 1)[1].split("\n}\n", 1)[0]
        assert "encoded.live = None" in walker
        for container in ("Value::List(items)", "Value::Object(map)", "Value::DictView"):
            assert container in walker, f"{container} is not walked — a handle can hide there"

    def test_the_clear_has_a_real_caller_at_the_teardown(self) -> None:
        """A CALLER pin, not a definition one. The first version of this class
        asserted only that the method's text existed — which is decorative: a
        clear nothing calls protects nothing, and the suite would have stayed
        green if routing never reached it (#1859)."""
        consumer = (PYTHON_DIR / "djust" / "websocket.py").read_text(encoding="utf-8")
        assert consumer.count("_clear_live_handles(") == 2, (
            "expected exactly one definition and one call site of the teardown helper"
        )
        # And the call is at the disconnect teardown, immediately before the
        # view reference it drains is dropped.
        assert "_clear_live_handles(self.view_instance)\n        self.view_instance = None" in (
            consumer
        ), "the clear is not wired at the disconnect teardown"


class TestTheStrictHelpersAreTheSinksOwn2539:
    """P8 / §3.3: `walk_one_segment` uses Django's exact step-3 tuple; the
    loose helper — which carries an extra `AttributeError` for the PRE-ADR
    walk's benefit — is named only by that walk."""

    def test_the_sink_names_the_strict_helper(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        segment = _fn_body(ctx, "fn walk_one_segment")
        assert "is_django_index_lookup_error_strict(" in segment
        assert "is_django_index_lookup_error(py" not in segment
        loose = _fn_body(ctx, "fn resolve_without_builtins")
        assert "is_django_index_lookup_error(py" in loose

    def test_the_strict_set_is_djangos_exact_tuple(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        body = ctx.split("fn is_django_index_lookup_error_strict", 1)[1].split("\n}", 1)[0]
        for exc in ("PyIndexError", "PyValueError", "PyKeyError", "PyTypeError"):
            assert exc in body, exc
        assert "PyAttributeError" not in body, (
            "AttributeError is not in Django's step-3 tuple (base.py:909-918)"
        )

    def test_the_underscore_refusal_is_the_first_statement(self) -> None:
        """§3.2. Defence in depth, so its position is the whole of it: a guard
        after step 1 would already have called `__getitem__`."""
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        segment = _fn_body(ctx, "fn walk_one_segment")
        assert "part.starts_with('_')" in segment
        assert segment.index("starts_with('_')") < segment.index("get_item"), (
            "the leading-underscore refusal must precede any item access"
        )


class TestTheRenderEnvReachesEveryRenderEntry2539:
    """P2: the ONE function every render path calls pushes BOTH ambient
    settings (the ADR-027 flag was the third until #2628 deleted it), and the
    four Python render entries call it."""

    def test_apply_render_env_pushes_both(self) -> None:
        from djust import render_env

        body = inspect.getsource(render_env.apply_render_env)
        for applier in ("apply_active_timezone", "apply_number_format"):
            assert f"{applier}()" in body, f"{applier} is not pushed by apply_render_env"
        assert "resolve_lazy" not in body, "the deleted ADR-027 push is back (#2628)"
        assert not hasattr(render_env, "apply_resolve_lazy")

    @pytest.mark.parametrize(
        "module_path",
        [
            "djust/mixins/rust_bridge.py",
            "djust/simple_live_view.py",
            "djust/template/rendering.py",
            "djust/components/base.py",
        ],
    )
    def test_every_python_render_entry_calls_it(self, module_path: str) -> None:
        source = (PYTHON_DIR / module_path).read_text(encoding="utf-8")
        assert "apply_render_env" in source, (
            f"{module_path} is a render entry that never acquires the ambient settings — "
            f"the timezone (#2209) and the number format (#2221) both default to whatever "
            f"the thread last rendered with"
        )

    def test_the_flag_is_gone_from_the_package(self) -> None:
        """#2628 deleted the kill-switch. No production module reads the key,
        `config.py` has no default for it, and the Rust module exports neither
        the setter nor the getter."""
        from djust.config import LiveViewConfig

        assert "template_resolve_lazy" not in LiveViewConfig._defaults
        assert not hasattr(_rust, "set_resolve_lazy")
        assert not hasattr(_rust, "resolve_lazy_enabled")
        offenders = []
        for path in sorted((PYTHON_DIR / "djust").rglob("*.py")):
            if "tests" in path.relative_to(PYTHON_DIR).parts:
                continue  # the package sweep is `test_adr027_step5_deletion_2628.py`'s
            text = path.read_text(encoding="utf-8", errors="replace")
            if "template_resolve_lazy" in text:
                offenders.append(str(path.relative_to(PYTHON_DIR)))
        assert offenders == [], f"these still name the deleted ADR-027 flag: {offenders}"


# ---------------------------------------------------------------------------
# The two "stays"/fixes of ADR-027 Step 5 (#2628) that had no pytest pin —
# the #2775 review's 🟡 (a) and 🟡 (b). Each is a parity cell against Django
# on every djust entry, and each has a named gate-off that turns it red.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestTheAliasFallbackStays2628:
    """The by-name sidecar's alias fallback (`Context::resolve_alias` in
    `resolve_without_builtins`) is a Django-parity mechanism for a value that
    carries NO live handle — a model crosses as a floored dict (ADR-027
    (b)(1)) — and ADR-027 Step 5 kept it for that reason. Until this pin the
    only thing that went red without it was an incidental benchmark cell.

    Gate-off (verified): `match self.resolve_alias(key)` →
    `match None::<String>` in `crates/djust_core/src/context.rs` turns the
    unfiltered row red on every entry (`'' != '1'`).
    """

    @staticmethod
    def _alice() -> User:
        u = User.objects.create_user("alice", password="pw-secret")
        u.groups.create(name="g1")
        return u

    @pytest.mark.parametrize("render", ALL_FOUR)
    def test_a_model_rebound_by_with_reaches_its_reverse_relation(self, render) -> None:
        source = "{% with q=user %}{{ q.groups.count }}{% endwith %}"
        u = self._alice()
        assert django_render(source, {"user": u}) == "1", "premise: Django renders it"
        assert render(source, {"user": u}) == "1"

    @pytest.mark.parametrize("render", ALL_FOUR)
    def test_the_filtered_rebinding_is_a_recorded_gap_not_a_regression(self, render) -> None:
        """A FILTERED operand registers no alias (`context.rs`, #2504 — the
        guards are `Context::is_safe`'s XSS boundary), and a model has no
        handle to fall back on, so this cell renders `''` where Django renders
        `1`. Pre-existing on every entry; recorded so the row above cannot be
        "fixed" by widening the alias guards without this going green — and
        so a future change that closes it is noticed."""
        source = "{% with q=user|default:user %}{{ q.groups.count }}{% endwith %}"
        u = self._alice()
        assert django_render(source, {"user": u}) == "1"
        assert render(source, {"user": u}) == ""


class TestStringIfInvalidReachesTheLiveWalk2628:
    """`walk_live`'s `CallOutcome::Empty` arm (an args-required method, an
    `alters_data` refusal) substitutes the ENGINE's `string_if_invalid` and
    keeps walking — Django's `_resolve_lookup`. Until #2628 it substituted a
    literal `""`; nothing noticed because an attribute-bearing object took the
    by-name sidecar walk, which answered `Missing` and let the renderer
    substitute the option. Routing such objects through the handle turned
    Django's `basic-syntax20` from OK to `''` — caught by the scoreboard
    ratchet, not the unit suite. This is the unit-suite pin.

    Gate-off (verified): `Context::string_if_invalid_object` body →
    `PyString::new(py, "")` turns both rows red (`'' != 'INVALID'`).
    """

    class SomeClass:
        def method2(self, o):  # args-required: Django's Empty
            return o

    @staticmethod
    def _engines(marker: str):
        from django.template.backends.django import DjangoTemplates

        from djust.template_backend import DjustTemplateBackend

        params = {
            "NAME": "sii",
            "DIRS": [],
            "APP_DIRS": False,
            "OPTIONS": {"string_if_invalid": marker},
        }
        return (
            DjangoTemplates({**params, "NAME": "dj-sii"}),
            DjustTemplateBackend({**params, "NAME": "du-sii"}),
        )

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("{{ var.method2 }}", id="basic-syntax20"),
            pytest.param("{{ var.method2.lower }}", id="keeps-walking-past-the-substitution"),
        ],
    )
    def test_an_args_required_method_renders_the_engines_string_if_invalid(self, source) -> None:
        django_engine, djust_engine = self._engines("INVALID")
        ctx = {"var": self.SomeClass()}
        expected = str(django_engine.from_string(source).render(dict(ctx)))
        # `{{ var.method2 }}` is `INVALID`; `{{ var.method2.lower }}` is
        # `invalid` — Django substitutes the option and KEEPS WALKING.
        assert expected.lower() == "invalid", "premise: Django substitutes the option here"
        assert str(djust_engine.from_string(source).render(dict(ctx))) == expected
