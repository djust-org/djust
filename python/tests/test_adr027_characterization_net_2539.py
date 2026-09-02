"""ADR-027 movement 1 (#2539): the characterization net, against the CURRENT
sidecar. No routing change — every djust cell below is TODAY's bytes, the
wrong ones included, so that the flip (movement 3) has an explicit, per-cell
delta to show rather than a silent pass.

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
  ``test_sidecar_on_all_render_paths_2501.py::liveview_render``: that stand-in
  hands the raw context to the sidecar directly, which the real sequence never
  does for a ``list`` (it is ``_JSON_FRIENDLY`` and never enters the sidecar),
  so it passes ``for-class-attribute`` where the real entry renders empty —
  rows N0/N0b here, the #1650 reproduction-fidelity gap the 2501 file's
  "RustLiveView" column carries (recorded, not fixed: #1079).

Normalisation, two mechanisms for two reasons
---------------------------------------------
Every column passes through ``ADDR`` (``0x[0-9a-f]+`` → ``0x…``) because a
function/generator repr always carries an address (rows J, J2, V). AND the
fixture classes whose INSTANCE is rendered bare (``Plain``, ``Cls``,
``Outer``, ``Mutating``, ``SafeObj``) define a fixed ``__repr__``, because row
T's LiveView ``|length`` counts ``len(str(o))`` and an address-bearing repr
makes that count platform-dependent (11-char macOS vs 14-char Linux
addresses). With both, every cell is a literal, platform-stable byte string.

What is pinned, per issue
-------------------------
* #2502 — ``do_not_call_in_templates`` renders the marker dict (row A; the
  three plain paths are the strict xfail at
  ``test_object_attribute_resolution_2501.py::test_do_not_call_in_templates_is_used_as_is``).
* #2504 — a filtered / dict-view ``{% for %}`` operand cannot reach attributes
  (rows N, N2), plus the unfiled real-entry instance: a plain object inside a
  TOP-LEVEL list/tuple reaches no attribute on the LiveView path, filter or no
  filter (rows N0, N0b).
* #2505 — a loop / ``{% with %}`` variable shadowing a top-level name resolves
  against the OUTER object (rows M2, M3, M6 render ``OUTER``). Premise
  correction to the issue: the hazard is NOT confined to the alias-less
  shapes — when the shadowed name is itself a sidecar entry,
  ``raw.contains_key(head)`` wins before the alias is consulted (M3, M6).
* #2513 — the page-shell path wires no sidecar on either branch.
* #2506 / #2507 — permanent security pins: a lookup exception never fails
  OPEN; ``{{ c.unmount }}`` never runs a mutator.
* The dormant sink ``Context::walk_live`` (``crates/djust_core/src/context.rs``)
  is defined, unit-tested (``crates/djust_core/tests/test_django_lookup_sink_2539.rs``)
  and routed NOWHERE — ``TestTheSinkIsDefinedButUnrouted2539``.

Refs #2539, #2535 (ADR-027), #2502, #2504, #2505, #2506, #2507, #2513, #2516,
#2517, #2528, #1646, #1650, #1468, #1039, #1125, #1104, #1079.
"""

from __future__ import annotations

import ast
import dataclasses
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
# The expectation table — TODAY's bytes, per path, the wrong ones included
# ---------------------------------------------------------------------------
class _Sentinel:
    def __init__(self, name: str) -> None:
        self.name = name

    def __repr__(self) -> str:
        return self.name


#: The process dies in this cell today (rows H, P). Asserted in a child
#: process by `TestTheTwoCrashes`; excluded from the in-process cells.
SEGFAULT = _Sentinel("SEGFAULT")


@dataclass(frozen=True)
class Row:
    id: str
    source: str
    make_ctx: Callable[[], dict]
    django: Any
    plain: Any
    liveview: Any
    #: E1–E4: the serialization floor (SECURE_DEFAULTS Pattern 1). These
    #: cells differ from Django DELIBERATELY and must never be "moved to
    #: Django's bytes" — the wrong-row check treats them as a security pin.
    floor: bool = False


CLASS_LEVEL_2 = "class-level,class-level,"
OUTER_2 = "OUTER,OUTER,"
MARKER_DICT = "{&#x27;do_not_call_in_templates&#x27;: True}"
LAMBDA_REPR = "&lt;function &lt;lambda&gt; at 0x…&gt;"
GEN_REPR = "&lt;generator object gen at 0x…&gt;"
CLS_CLASS_REPR = f"&lt;class &#x27;{Cls.__module__}.Cls&#x27;&gt;"


def _r(id_, source, make_ctx, django, plain, liveview, *, floor=False) -> Row:
    return Row(id_, source, make_ctx, django, plain, liveview, floor)


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
        MARKER_DICT,
        MARKER_DICT,
    ),
    _r("B", "{{ o.attr.sub }}", lambda: {"o": Nested()}, "deep", "deep", "deep"),
    _r("C", "{{ d.1 }}", lambda: {"d": {1: "one"}}, "one", "one", "one"),
    _r("D1", "{{ x.0 }}", lambda: {"x": ["zero", "one"]}, "zero", "zero", "zero"),
    _r("D2", "{{ x.0 }}", lambda: {"x": {"0": "strkey"}}, "strkey", "strkey", "strkey"),
    _r("D3", "{{ x.0 }}", lambda: {"x": {0: "intkey"}}, "intkey", "intkey", "intkey"),
    _r("E1", "{{ u.password }}", lambda: {"u": make_user()}, "pbkdf2$hash", "", "", floor=True),
    _r(
        "E2",
        "{{ p.get_user.password }}",
        lambda: {"p": Presenter(make_user())},
        "pbkdf2$hash",
        "",
        "",
        floor=True,
    ),
    _r(
        "E3",
        "{{ p.user.password }}",
        lambda: {"p": Presenter(make_user())},
        "pbkdf2$hash",
        "",
        "",
        floor=True,
    ),
    _r(
        "E4",
        "{% for u in us %}[{{ u.password }}]{% endfor %}",
        lambda: {"us": [make_user()]},
        "[pbkdf2$hash]",
        "[]",
        "[]",
        floor=True,
    ),
    _r(
        "F1",
        "{{ r.attr_err }}",
        lambda: {"r": Raiser()},
        Raises(AttributeError),
        Raises(AttributeError),
        Raises(AttributeError),
    ),
    _r(
        "F2",
        "{{ r.key_err }}",
        lambda: {"r": Raiser()},
        Raises(KeyError),
        Raises(KeyError),
        Raises(KeyError),
    ),
    _r("F3", "{{ r.silent }}", lambda: {"r": Raiser()}, "", "", ""),
    _r(
        "F4",
        "{{ r.loud }}",
        lambda: {"r": Raiser()},
        Raises(RuntimeError),
        Raises(RuntimeError),
        Raises(RuntimeError),
    ),
    _r(
        "F5",
        "{{ r.loud_false }}",
        lambda: {"r": Raiser()},
        Raises(NotSilent),
        Raises(NotSilent),
        Raises(NotSilent),
    ),
    _r("F6", "{{ r.silent_method }}", lambda: {"r": Raiser()}, "", "", ""),
    _r(
        "G",
        "{% if x|default_if_none:y %}yes{% else %}no{% endif %}",
        lambda: {"y": 1},
        "yes",
        "no",
        "no",
    ),
    _r("H", "{{ x }}", cycle, "1", SEGFAULT, "1"),
    _r(
        "I",
        "{{ o }}",
        lambda: {"o": Plain()},
        "&lt;Plain&gt;",
        "{&#x27;inst_attr&#x27;: &#x27;in-dict&#x27;}",
        "&lt;Plain&gt;",
    ),
    _r("J", "{{ callable }}", _fresh_j, "foo bar", LAMBDA_REPR, "None"),
    _r("J2", "{{ var.callable }}", _fresh_j2, "foo bar", LAMBDA_REPR, "None"),
    _r("K", "{{ d.the_value }}", lambda: {"d": Doodad(42)}, "42", "42", "42"),
    _r("K2", "{{ d.the_value }}", lambda: {"d": DoodadAlters(42)}, "", "", ""),
    _r("K3", "{{ d.value }}", lambda: {"d": Doodad(42)}, "", "42", ""),
    _r("K4", "{{ d.value }}", lambda: {"d": DoodadAlters(42)}, "", "42", ""),
    _r("L", "{{ d.items }}", lambda: {"d": {"items": "the-key"}}, "the-key", "the-key", "the-key"),
    _r(
        "M",
        "{% for x in p|slice:':2' %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": Plain()},
        CLASS_LEVEL_2,
        ",,",
        ",,",
    ),
    _r(
        "M2",
        "{% for x in p|slice:':2' %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": Outer()},
        CLASS_LEVEL_2,
        OUTER_2,
        OUTER_2,
    ),
    _r(
        "M3",
        "{% for x in p %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": Outer()},
        CLASS_LEVEL_2,
        OUTER_2,
        OUTER_2,
    ),
    _r(
        "M4",
        "{% for x in p %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": 5},
        CLASS_LEVEL_2,
        CLASS_LEVEL_2,
        ",,",
    ),
    _r(
        "M5",
        "{% for x in p|slice:':2' %}{{ x.cls_attr }},{% endfor %}",
        lambda: {"p": [Cls(), Cls()], "x": 5},
        CLASS_LEVEL_2,
        ",,",
        ",,",
    ),
    _r(
        "M6",
        "{% with x=p.0 %}{{ x.cls_attr }}{% endwith %}",
        lambda: {"p": [Cls(), Cls()], "x": Outer()},
        "class-level",
        "OUTER",
        "OUTER",
    ),
    _r(
        "N",
        "{% for r in rows|slice:':1' %}{{ r.cls_attr }},{% endfor %}",
        lambda: {"rows": [Cls(), Cls()]},
        "class-level,",
        ",",
        ",",
    ),
    _r(
        "N2",
        "{% for r in dd.values %}{{ r.cls_attr }},{% endfor %}",
        lambda: {"dd": {"a": Cls()}},
        "class-level,",
        ",",
        ",",
    ),
    _r(
        "N0",
        "{% for r in rows %}{{ r.cls_attr }},{% endfor %}",
        lambda: {"rows": [Cls(), Cls()]},
        CLASS_LEVEL_2,
        CLASS_LEVEL_2,
        ",,",
    ),
    _r(
        "N0b",
        "{{ rows.0.cls_attr }}",
        lambda: {"rows": (Cls(), Cls())},
        "class-level",
        "class-level",
        "",
    ),
    _r(
        "O",
        "{{ s }}",
        lambda: {"s": SafeObj()},
        "<b>s</b>",
        "&lt;b&gt;s&lt;/b&gt;",
        "&lt;b&gt;s&lt;/b&gt;",
    ),
    _r(
        "P",
        "{{ class_var.class_property }} | {{ class_var.class_method }}",
        lambda: {"class_var": MyClass},
        "Example property | Example method",
        SEGFAULT,
        SEGFAULT,
    ),
    _r("Q", "{{ k }}", lambda: {"k": Cls}, "&lt;Cls&gt;", CLS_CLASS_REPR, "None"),
    _r(
        "R",
        "{{ g.x }}",
        lambda: {"g": GetItemRaiser()},
        Raises(RuntimeError),
        Raises(RuntimeError),
        Raises(RuntimeError),
    ),
    _r("S", "{% if r.silent %}T{% else %}F{% endif %}", lambda: {"r": Raiser()}, "F", "F", "F"),
    _r(
        "T",
        "{% if o %}T{% else %}F{% endif %}/{{ o|length }}",
        lambda: {"o": Plain()},
        "T/0",
        "T/1",
        "T/7",
    ),
    _r("U", "{{ u.username }}", lambda: {"u": make_user()}, "alice", "alice", "alice"),
    _r("V", "{% for i in g %}{{ i }}{% endfor %}", lambda: {"g": gen()}, "12", GEN_REPR, GEN_REPR),
    _r("W", "{{ np.foo }}", lambda: {"np": NpLike()}, "attr-foo", "attr-foo", "attr-foo"),
]

ROW_BY_ID: dict[str, Row] = {row.id: row for row in ROWS}

#: Rows whose djust cell is NOT Django's answer today, per path — a stated
#: SET, not a floor (#1125): an unrelated PR that fixes or breaks a cell must
#: edit the table AND this set. The floor rows (E1–E4) are listed apart.
PLAIN_WRONG_TODAY = frozenset("A G H I J J2 K3 K4 M M2 M3 M5 M6 N N2 O P Q T V".split())
LIVEVIEW_WRONG_TODAY = frozenset("A G J J2 M M2 M3 M4 M5 M6 N N2 N0 N0b O P Q T V".split())
FLOOR_ROWS = frozenset("E1 E2 E3 E4".split())


def recorded(row: Row, path: str) -> Any:
    return getattr(row, path)


#: (row, path) cells that run IN-PROCESS: everything but the three crash cells.
CELLS = [
    pytest.param(row, path, id=f"{row.id}-{path}")
    for row in ROWS
    for path in PATHS
    if recorded(row, path) is not SEGFAULT
]
CRASH_CELLS = [(row.id, path) for row in ROWS for path in PATHS if recorded(row, path) is SEGFAULT]


# The two assertion functions the table is read through. Module-level so that
# `TestTheTableIsLoadBearing` can call the SAME functions on a mutated row.
def assert_column_is_todays_bytes(row: Row, path: str, actual: Any) -> None:
    expected = recorded(row, path)
    assert expected is not SEGFAULT, (
        f"row {row.id} on {path} is a crash cell; not an in-process cell"
    )
    assert actual == expected, (
        f"row {row.id} on {path}: today's bytes moved.\n  recorded: {expected!r}\n  actual:   {actual!r}"
        f"\n  Django:   {row.django!r}\nIf this is ADR-027 landing, move the row to Django's bytes "
        f"and update PLAIN_WRONG_TODAY / LIVEVIEW_WRONG_TODAY."
    )


def assert_wrong_rows_are_wrong_and_right_rows_are_right(row: Row, path: str, actual: Any) -> None:
    """Derived from the table, not stored: a cell recorded EQUAL to Django
    must still equal Django; a cell recorded DIFFERENT must still differ AND
    still be today's bytes. A fixed shape therefore fails loudly by name."""
    expected = recorded(row, path)
    if expected == row.django:
        assert actual == row.django, (
            f"row {row.id} on {path} REGRESSED away from Django's bytes: "
            f"{actual!r} != {row.django!r}"
        )
        return
    if row.floor:
        assert actual != row.django and actual == expected, (
            f"row {row.id} on {path}: the serialization floor moved — {actual!r}. This cell is a "
            f"SECURITY pin (SECURE_DEFAULTS Pattern 1) and is never moved to Django's bytes."
        )
        return
    assert actual != row.django, (
        f"row {row.id} on {path} now matches Django ({actual!r}) — ADR-027 landed here; "
        f"move the row to Django's bytes and drop {row.id!r} from the *_WRONG_TODAY set."
    )
    assert actual == expected, (
        f"row {row.id} on {path} is wrong in a NEW way: {actual!r} (recorded {expected!r}, "
        f"Django {row.django!r})"
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
    def test_the_djust_column_is_todays_bytes(self, row: Row, path: str) -> None:
        actual = observe(RENDER[path], row.source, row.make_ctx())
        assert_column_is_todays_bytes(row, path, actual)

    @pytest.mark.parametrize(("row", "path"), CELLS)
    def test_wrong_rows_are_wrong_and_right_rows_are_right(self, row: Row, path: str) -> None:
        actual = observe(RENDER[path], row.source, row.make_ctx())
        assert_wrong_rows_are_wrong_and_right_rows_are_right(row, path, actual)


class TestThePlainEntriesAgree:
    """The two raw entries `DjustTemplateBackend` binds answer the backend's
    bytes on every non-crash row — the #1646 twin check. Their crash rows
    (H, P) share the backend's conversion and are asserted by
    `TestTheTwoCrashes` through the backend only."""

    NON_CRASH_PLAIN = [pytest.param(row, id=row.id) for row in ROWS if row.plain is not SEGFAULT]

    @pytest.mark.parametrize("row", NON_CRASH_PLAIN)
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
        assert via_backend == row.plain, f"row {row.id}: the backend column itself moved"


class TestTheTableIsSelfConsistent:
    def test_every_row_has_three_columns_and_a_unique_id(self) -> None:
        assert len(ROWS) == 45
        assert len(ROW_BY_ID) == 45
        for row in ROWS:
            for column in ("django", "plain", "liveview"):
                assert recorded(row, column) is not None, f"row {row.id} lacks {column}"
            assert row.django is not SEGFAULT, f"row {row.id}: Django never segfaults"

    def test_the_wrong_sets_are_exactly_the_stated_sets(self) -> None:
        plain_wrong = {r.id for r in ROWS if r.plain != r.django and not r.floor}
        liveview_wrong = {r.id for r in ROWS if r.liveview != r.django and not r.floor}
        assert plain_wrong == PLAIN_WRONG_TODAY, (
            f"plain: +{plain_wrong - PLAIN_WRONG_TODAY} -{PLAIN_WRONG_TODAY - plain_wrong}"
        )
        assert liveview_wrong == LIVEVIEW_WRONG_TODAY, (
            f"liveview: +{liveview_wrong - LIVEVIEW_WRONG_TODAY} "
            f"-{LIVEVIEW_WRONG_TODAY - liveview_wrong}"
        )
        floor = {r.id for r in ROWS if r.floor}
        assert floor == FLOOR_ROWS
        for rid in floor:
            row = ROW_BY_ID[rid]
            assert row.plain != row.django and row.liveview != row.django, rid

    def test_the_crash_cells_are_exactly_three(self) -> None:
        assert sorted(CRASH_CELLS) == [("H", "plain"), ("P", "liveview"), ("P", "plain")]
        assert len(CELLS) == 45 * 2 - 3


class TestTheTableIsLoadBearing:
    """#1039 / #1468: the table's assertion functions go red in both
    directions. Each mutation asserts it APPLIED before its result is read
    (the v1.1.0-13 gate-off rule)."""

    def test_a_perturbed_recorded_cell_reddens_the_bytes_check(self) -> None:
        row = ROW_BY_ID["B"]
        assert_column_is_todays_bytes(row, "plain", "deep")  # the genuine bytes pass
        mutated = dataclasses.replace(row, plain=row.plain + "x")
        assert mutated != row, "the mutation did not apply"
        with pytest.raises(AssertionError, match="today's bytes moved"):
            assert_column_is_todays_bytes(mutated, "plain", "deep")

    def test_a_wrong_row_moved_to_djangos_bytes_reddens_the_wrong_check(self) -> None:
        """An unrelated PR that 'tidies' a wrong cell into Django's bytes while
        the engine still renders the old ones is caught by the wrong-check."""
        row = ROW_BY_ID["I"]
        todays_bytes = row.plain
        assert_wrong_rows_are_wrong_and_right_rows_are_right(row, "plain", todays_bytes)
        mutated = dataclasses.replace(row, plain=row.django)
        assert mutated != row, "the mutation did not apply"
        with pytest.raises(AssertionError, match="REGRESSED"):
            assert_wrong_rows_are_wrong_and_right_rows_are_right(mutated, "plain", todays_bytes)

    def test_a_fixed_shape_fails_by_name(self) -> None:
        """The flip's delta: the engine starts answering Django's bytes on a
        row recorded wrong. The check must fail and name ADR-027."""
        row = ROW_BY_ID["I"]
        with pytest.raises(AssertionError, match="ADR-027 landed here"):
            assert_wrong_rows_are_wrong_and_right_rows_are_right(row, "plain", row.django)

    def test_a_floor_cell_that_leaks_fails_as_a_security_pin(self) -> None:
        row = ROW_BY_ID["E1"]
        assert_wrong_rows_are_wrong_and_right_rows_are_right(row, "plain", "")
        with pytest.raises(AssertionError, match="SECURITY pin"):
            assert_wrong_rows_are_wrong_and_right_rows_are_right(row, "plain", row.django)


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
# 2. The two crashes — subprocess per cell, asserting the crash TODAY
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


class TestTheTwoCrashes:
    """Rows H (reference cycle, conversion) and P (`list`-subclass class,
    `__class_getitem__` in the walk) kill the process TODAY. Each cell is
    asserted in a child so the suite survives it, and so that a fix flips to
    a NAMED failure ("no longer crashes — move the row to Django's bytes")
    rather than to a silent pass."""

    @pytest.mark.parametrize(("key", "path"), CRASH_CELLS, ids=[f"{k}-{p}" for k, p in CRASH_CELLS])
    def test_the_cell_still_crashes_today(self, key: str, path: str) -> None:
        proc = run_child(key, path)
        assert proc.returncode in CRASH_SIGNALS, (
            f"row {key} on {path} no longer crashes (rc={proc.returncode}, "
            f"stdout tail={proc.stdout[-300:]!r}, stderr tail={proc.stderr[-300:]!r}) — the #2516/#2517 "
            f"crash is fixed; move the row to Django's bytes "
            f"({ROW_BY_ID[key].django!r}) and update the *_WRONG_TODAY sets."
        )

    @pytest.mark.django_db
    @pytest.mark.parametrize("path", PATHS)
    def test_the_child_renders_a_non_crash_row_like_the_in_process_entry(self, path: str) -> None:
        """The child harness is the same path and not a no-op: row A through
        the child equals row A through the in-process column."""
        proc = run_child("A", path)
        assert proc.returncode == 0, proc.stderr[-2000:]
        row = ROW_BY_ID["A"]
        assert child_rendered(proc) == recorded(row, path) == MARKER_DICT
        assert observe(RENDER[path], row.source, row.make_ctx()) == MARKER_DICT


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
    """#2502 on the REAL LiveView entry. The plain paths are the strict xfail
    at `test_object_attribute_resolution_2501.py::test_do_not_call_in_templates_is_used_as_is`,
    which the flip deletes."""

    def test_the_real_entry_renders_the_marker_dict(self) -> None:
        row = ROW_BY_ID["A"]
        assert liveview_render(row.source, row.make_ctx()) == MARKER_DICT
        assert django_render(row.source, row.make_ctx()).startswith("&lt;bound method")

    @pytest.mark.parametrize("render", ALL_FOUR)
    def test_the_guard_is_load_bearing(self, render) -> None:
        """Sibling: the marker dict is the CONVERSION mangling a kept bound
        method, not the guard failing — `keep` is never called on any path,
        while an unstamped method on the same object IS."""
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
    (N0/N0b): a plain object inside a top-level list/tuple reaches NO
    attribute on the LiveView path, filter or no filter — a `list` is
    `_JSON_FRIENDLY` in `_sync_state_to_rust` and never enters the sidecar."""

    @pytest.mark.parametrize("row_id", ["N", "N2", "N0", "N0b"])
    def test_the_real_entry_reaches_no_attribute(self, row_id: str) -> None:
        row = ROW_BY_ID[row_id]
        assert liveview_render(row.source, row.make_ctx()) == row.liveview
        assert row.liveview.strip(",") == ""
        assert django_render(row.source, row.make_ctx()).strip(","), "premise: Django renders it"

    def test_the_alias_mechanism_is_the_discriminating_one_on_the_plain_path(self) -> None:
        """Sibling: the UNFILTERED operand on the plain path resolves (an alias
        is registered), the filtered one does not — so the empty cells above
        are the alias guard refusing, not the walk failing."""
        unfiltered, filtered = ROW_BY_ID["N0"], ROW_BY_ID["N"]
        assert plain_render(unfiltered.source, unfiltered.make_ctx()) == CLASS_LEVEL_2
        assert plain_render(filtered.source, filtered.make_ctx()) == ","


@pytest.mark.django_db
class TestShadowingResolvesAgainstTheOuterObject2505:
    """#2505: the wrong bytes are the OUTER object's, not empty — on both djust
    paths, for the filtered loop (M2), the unfiltered loop (M3; the premise
    correction) and `{% with %}` (M6). Django never answers `OUTER`."""

    @pytest.mark.parametrize("row_id", ["M2", "M3", "M6"])
    @pytest.mark.parametrize("path", PATHS)
    def test_the_outer_object_answers(self, row_id: str, path: str) -> None:
        row = ROW_BY_ID[row_id]
        actual = RENDER[path](row.source, row.make_ctx())
        assert "OUTER" in actual and actual == recorded(row, path)
        assert "OUTER" not in django_render(row.source, row.make_ctx())

    def test_the_controls_pin_the_head_short_circuit(self) -> None:
        """M4/M5 with a SCALAR outer `x` (no sidecar entry for the head): the
        plain path is CORRECT unfiltered — the alias resolves — and empty
        filtered. This is the cell a fix to the `raw.contains_key(head)`
        short-circuit that broke the alias path would redden."""
        m4, m5 = ROW_BY_ID["M4"], ROW_BY_ID["M5"]
        assert plain_render(m4.source, m4.make_ctx()) == CLASS_LEVEL_2
        assert plain_render(m5.source, m5.make_ctx()) == ",,"
        assert liveview_render(m4.source, m4.make_ctx()) == ",,"
        assert liveview_render(m5.source, m5.make_ctx()) == ",,"


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
class TestThePageShellPathHasNoSidecar2513:
    """#2513: the page shell wires no sidecar on EITHER branch, so a component's
    dotted spellings render empty there — while `{{ c }}` renders the
    component in the SAME render (non-vacuity)."""

    @pytest.mark.parametrize("with_serialized_context", SHELL_BRANCHES)
    def test_dotted_spellings_are_empty_while_the_bare_one_renders(
        self, with_serialized_context: bool
    ) -> None:
        nav = render_page_shell(
            "[{{ c }}][{{ c.render }}][{{ c.render|safe }}][{{ c.cls_attr }}]",
            with_serialized_context=with_serialized_context,
        )
        assert nav == "[<b>shellcard</b>][][][]"

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

        with mock.patch.object(RustBridgeMixin, "_sync_state_to_rust", spy):
            assert liveview_render("{{ o.keep }}", {"o": Mutating()}) == MARKER_DICT
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

    Honest note: today this passes for the trivial reason — the shell wires
    no sidecar (#2513), so no lookup ever reaches `unmount` there. It becomes
    load-bearing at movement 3, when a handle first reaches `unmount` on this
    path, and the flip PR must gate it off (drop the `__init_subclass__`
    re-stamp ⇒ red). The load-bearing pins TODAY are
    `TestComponentMutatorsAreNeverAutoCalled` (all four columns, gate-off
    25/26 reddened) and `TestMutatorsRefusedOnTheLiveViewPath` in the 2501
    file — cited, not copied.
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
# 5. The dormant sink is defined and NOT routed
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


class TestTheSinkIsDefinedButUnrouted2539:
    """`Context::walk_live` and `Walked` exist in `context.rs`; nothing calls
    the helper; `lookup_segment` in particular does not. Movement 2 rewrites
    this pin into "exactly one caller"."""

    def test_the_sink_is_defined_once(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        assert ctx.count("fn walk_live") == 1
        assert "pub enum Walked" in ctx
        assert "Object(pyo3::Bound<'py, pyo3::PyAny>)" in ctx
        assert "Invalid," in ctx

    def test_nothing_calls_it(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        assert CALLERS.findall(ctx) == [], "walk_live has a caller — movement 2 landed early?"
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
                f"{path.relative_to(ROOT)} calls walk_live — movement 2 landed early?"
            )

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

    def test_the_pin_goes_red_in_both_directions(self) -> None:
        ctx = _production(CONTEXT_RS.read_text(encoding="utf-8"))
        with_caller = ctx + "\nlet _ = self.walk_live(py, obj, &parts, key);\n"
        assert with_caller != ctx, "the ADD mutation did not apply"
        assert len(CALLERS.findall(with_caller)) == 1
        without_fn = ctx.replace("fn walk_live", "fn walk_dead", 1)
        assert without_fn != ctx, "the REMOVE mutation did not apply"
        assert without_fn.count("fn walk_live") == 0
        body = _lookup_segment_body(ctx)
        routed_body = body.replace(
            "if let Value::Encoded(encoded) = current {",
            "if let Value::Encoded(encoded) = current { let _ = walk_live;",
            1,
        )
        assert routed_body != body, "the ROUTE mutation did not apply"
        routed = ctx.replace(body, routed_body, 1)
        assert routed != ctx
        assert "walk_live" in _lookup_segment_body(routed)
