"""The safe-markup guard learns flow, narrowing, the project's escapers and the wrapper sinks (#3820).

``scripts/lib/safe_markup_taint.py`` (#3766) was name-based and single-function: it treated every
binding of a name as reaching every use (so ``width = int(width)`` left ``f"{width}"`` flagged), knew
no escaper but Django's, and did not see ``_safe(...)`` / ``safe_html(...)``, the two wrappers around
``mark_safe`` that carry about 190 call sites. #3774 audited what it flagged and kept 93 functions
that were the guard's own limits. This teaches it, and each rule is held to the same standard: a
dirty shape must be flagged and its clean twin must not be, so a rule that is removed, or one that
matches everything, fails by name.

What each class proves:

* ``TestFlowSensitiveBindings``: the cases where a coercion dominates a use clear it, and every case
  where it does not (a branch, a loop back edge, a handler, a re-taint, ``+=``, a closure, a
  ``global``, a ``:=``, a ``finally``, an ``import`` or ``match`` capture, ``locals()``) keeps it flagged.
* ``TestNarrowing``: ``x in CONSTANT``, ``x == "a"`` and ``isinstance(x, int)`` clear ``x`` on the branch
  where they hold, from either edge; a non-constant collection, a mutated one, the wrong edge and
  ``assert`` (``python -O`` removes it) clear nothing.
* ``TestTheDifferentialFuzz``: the flow analysis against REAL EXECUTION on random programs, and the
  same fuzz run on deliberately broken analyses, every one of which must be caught.
* ``TestProjectEscapersAreTrustedByOrigin`` / ``TestTheTrustedHelpersKeepTheirContract``: a helper is
  trusted by where it is DEFINED (a re-export, a local twin and a same-named import from elsewhere
  are not), and each one's output is parsed under a hostile payload.
* ``TestWrapperSinks``: ``_safe`` and ``safe_html`` are sinks, a new wrapper around ``mark_safe`` has to
  be named, and the canary from the issue is flagged.
* ``TestEngineRenderedContent``: the block-handler ``content`` rule is limited to the module and
  signature, and the engine really does pass a SafeString.
* ``TestClassAttributesAndLocalHelpers``: a class table, ``d.get(k, DEFAULT)`` and a call to a helper this
  file defines are judged by what they hold or return.
* ``TestNoEscaperIsImportedThroughAReExport``: the lint the issue asks for.

Refs #3820, #3774, #3766, #3775, #1859, #1468.
"""

from __future__ import annotations

import ast
import functools
import importlib
import random
import re
import textwrap
from pathlib import Path

import flow_fuzz_3820 as fuzz
import payload_sweep_3774 as sweep
import pytest
import safe_markup_corpus_3848 as corpus3848
import safe_markup_rows_3848 as rows3848
from django.utils.html import conditional_escape
from django.utils.safestring import SafeData, mark_safe

from scripts.lib import safe_markup_audit as audit
from scripts.lib import safe_markup_taint as taint
from scripts.lib.safe_markup_taint import find_unsafe_markup
from tests import sweep_sample

pytestmark = pytest.mark.components

ROOT = Path(__file__).resolve().parents[2]

PRELUDE = (
    "from django.utils.html import conditional_escape, escape, format_html\n"
    "from django.utils.safestring import mark_safe\n"
)


def flagged(source: str, module: str | None = None, is_package: bool = False) -> list[str]:
    return [
        f.function
        for f in find_unsafe_markup(PRELUDE + textwrap.dedent(source), module, is_package)
    ]


# ---------------------------------------------------------------------------
# Flow-sensitive bindings
# ---------------------------------------------------------------------------

#: label -> (source, flagged?). A False row is a coercion / validation the guard now proves; a True
#: row is a case that LOOKS like one and must stay flagged.
FLOW = {
    "a coercion that dominates the use clears the name": (
        """
        def f(kw):
            w = kw.get("w", 400)
            w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "a coercion in only one branch does not": (
        """
        def f(kw, c):
            w = kw.get("w", 400)
            if c:
                w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a coercion in both branches does": (
        """
        def f(kw, c):
            w = kw.get("w", 400)
            if c:
                w = int(w)
            else:
                w = 5
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "an elif chain without an else leaves the raw value": (
        """
        def f(v, a, b):
            w = v
            if a:
                w = int(w)
            elif b:
                w = 3
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "an elif chain with an else does not": (
        """
        def f(v, a, b):
            w = v
            if a:
                w = int(w)
            elif b:
                w = 3
            else:
                w = 4
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "a raw binding after the coercion re-taints": (
        """
        def f(kw):
            w = int(kw.get("w", 400))
            w = kw.get("x")
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a use BEFORE the coercion is still raw": (
        """
        def f(kw):
            w = kw.get("w", 400)
            s = mark_safe(f"<i w={w}>")
            w = int(w)
            return s
        """,
        True,
    ),
    "a loop back edge carries the raw value to the top of the body": (
        """
        def f(kw, xs):
            w = 1
            for x in xs:
                out = mark_safe(f"<i w={w}>")
                w = x
            return out
        """,
        True,
    ),
    "a coercion inside the loop clears the use after it": (
        """
        def f(kw, xs):
            w = 1
            for x in xs:
                w = int(x)
                out = mark_safe(f"<i w={w}>")
            return out
        """,
        False,
    ),
    "a continue carries the raw value out of the loop": (
        """
        def f(xs):
            w = ""
            for x in xs:
                w = x
                if x:
                    continue
                w = int(x)
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a break carries the raw value out of the loop": (
        """
        def f(xs):
            w = ""
            for x in xs:
                w = x
                if x:
                    break
                w = int(x)
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a for-else that always runs coerces": (
        """
        def f(xs):
            w = ""
            for x in xs:
                w = x
            else:
                w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "a for-else skipped by a break does not": (
        """
        def f(xs):
            w = ""
            for x in xs:
                w = x
                if x:
                    break
            else:
                w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "+= is not a binding and taints the name at every use": (
        """
        def f(kw):
            w = int(kw.get("w"))
            w += kw.get("s")
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "append is not a binding and taints the list at every use": (
        """
        def f(v):
            items = []
            items.append(v)
            items = []
            return mark_safe("".join(items))
        """,
        True,
    ),
    "a list literal that is replaced is replaced": (
        """
        def f(v):
            items = [v]
            items = []
            return mark_safe("".join(items))
        """,
        False,
    ),
    "try/except that coerces in the body and defaults in the handler": (
        """
        def f(kw):
            w = kw.get("w")
            try:
                w = int(w)
            except ValueError:
                w = 400
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "a handler that does nothing keeps the raw value": (
        """
        def f(kw):
            w = kw.get("w")
            try:
                w = int(w)
            except ValueError:
                pass
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "an exception can leave the body between two statements": (
        """
        def f(kw):
            w = 1
            try:
                if kw:
                    w = kw.get("z")
                    g()
                    w = int(w)
            except ValueError:
                return mark_safe(f"<i w={w}>")
            return ""
        """,
        True,
    ),
    "a with can swallow an exception, so its body may not have run": (
        """
        def f(kw):
            w = kw.get("w")
            with ctx():
                w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a with can swallow an exception raised between two statements of its body": (
        """
        def f(kw):
            w = 1
            with ctx():
                w = kw.get("z")
                g()
                w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a closure reads every binding of the name": (
        """
        def f(kw):
            w = kw.get("w")
            w = int(w)
            def g():
                return mark_safe(f"<i w={w}>")
            return g
        """,
        True,
    ),
    "a global name is never cleared": (
        """
        def f(kw):
            global w
            w = kw.get("w")
            w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a walrus binding disables the name": (
        """
        def f(kw):
            w = kw.get("w")
            w = int(w)
            if (w := kw.get("z")):
                pass
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a name bound in a finally block is never cleared": (
        """
        def f(kw):
            w = kw.get("w")
            w = int(w)
            try:
                pass
            finally:
                w = kw.get("q")
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a match capture rebinds the name": (
        """
        def f(v):
            w = int(v)
            match v:
                case str(w):
                    pass
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "an import rebinds the name": (
        """
        def f(v):
            w = int(v)
            import os as w
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "an async with target rebinds the name": (
        """
        async def f(v):
            w = int(v)
            async with ctx() as w:
                pass
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a later with item reads what an earlier one bound": (
        """
        def f(v):
            w = int(v)
            with open(v) as w, mark_safe(f"<i w={w}>") as out:
                pass
        """,
        True,
    ),
    "locals() makes every name unknowable": (
        """
        def f(v):
            w = v
            w = int(w)
            d = locals()
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a comprehension variable of the same name is not the function's": (
        """
        def f(v, xs):
            w = int(v)
            ys = [f"<i>{w}</i>" for w in xs]
            return mark_safe("".join(ys))
        """,
        True,
    ),
    "a generator expression runs when it is CONSUMED, after the name is rebound": (
        """
        def f(v):
            w = int(v)
            gen = (f"<i>{w}</i>" for _ in range(2))
            w = v
            return mark_safe("".join(gen))
        """,
        True,
    ),
    "a list comprehension runs now": (
        """
        def f(v):
            w = int(v)
            items = [f"<i>{w}</i>" for _ in range(2)]
            w = v
            return mark_safe("".join(items))
        """,
        False,
    ),
    "unreachable code after a return is conservative": (
        """
        def f(w):
            w = int(w)
            return mark_safe(f"<i w={w}>")
            w = 5
        """,
        True,
    ),
    "a parameter default is not a coercion": (
        """
        def f(w=400):
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "an early return guard then a coercion": (
        """
        def f(w):
            if not w:
                return ""
            w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "a tuple unpack of constants kills the raw binding": (
        """
        def f(v):
            w = v
            w, z = 1, 2
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "a tuple unpack of an unknown value binds raw": (
        """
        def f(v):
            w = 1
            w, z = v
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "a string rebuilt from a clean value is clean": (
        """
        def f(v):
            s = f"<i>{v}</i>"
            s = escape(s)
            return mark_safe(s)
        """,
        False,
    ),
    "an alias taken before the reassignment keeps the old binding": (
        """
        def f(v):
            s = f"<i>{v}</i>"
            t = s
            s = "x"
            return mark_safe(t)
        """,
        True,
    ),
    "a nested def's own coercion does not clear the outer name": (
        """
        def f(v):
            w = v
            def g(x):
                w = int(x)
                return w
            return mark_safe(f"<i w={w}>")
        """,
        True,
    ),
    "async def is analysed like def": (
        """
        async def f(w):
            w = int(w)
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
    "while True with a break": (
        """
        def f(w):
            while True:
                w = int(w)
                break
            return mark_safe(f"<i w={w}>")
        """,
        False,
    ),
}


class TestFlowSensitiveBindings:
    @pytest.mark.parametrize("label", sorted(FLOW))
    def test_the_verdict(self, label):
        source, expect_flagged = FLOW[label]
        assert bool(flagged(source)) is expect_flagged, label

    def test_the_motivating_shape_from_the_issue(self):
        """`width = kw.get("width", 400)` then `width = int(width)`: flagged by #3766, clear now."""
        assert flagged(FLOW["a coercion that dominates the use clears the name"][0]) == []

    def test_the_old_guard_flagged_it(self):
        """Shown, not asserted: with the flow analysis switched off the same code is flagged."""
        source = FLOW["a coercion that dominates the use clears the name"][0]
        original = taint._Flow.run
        try:
            taint._Flow.run = lambda self: None
            assert flagged(source) == ["f"]
        finally:
            taint._Flow.run = original


# ---------------------------------------------------------------------------
# Narrowing
# ---------------------------------------------------------------------------

NARROWING = {
    "not in a constant tuple, then a default": (
        """
        MODES = ("a", "b")
        def f(kw):
            m = kw.get("m")
            if m not in MODES:
                m = "a"
            return mark_safe(f"<i class={m}>")
        """,
        False,
    ),
    "not in with the default forgotten": (
        """
        MODES = ("a", "b")
        def f(kw):
            m = kw.get("m")
            if m not in MODES:
                pass
            return mark_safe(f"<i class={m}>")
        """,
        True,
    ),
    "not in, then raise": (
        """
        MODES = ("a", "b")
        def f(kw):
            m = kw.get("m")
            if m not in MODES:
                raise ValueError
            return mark_safe(f"<i class={m}>")
        """,
        False,
    ),
    "inside the branch where it holds": (
        """
        MODES = ("a", "b")
        def f(kw):
            m = kw.get("m")
            if m in MODES:
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        False,
    ),
    "inside the WRONG branch": (
        """
        MODES = ("a", "b")
        def f(kw):
            m = kw.get("m")
            if m not in MODES:
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        True,
    ),
    "a collection that something appends to": (
        """
        MODES = ["a", "b"]
        def g(x):
            MODES.append(x)
        def f(kw):
            m = kw.get("m")
            if m in MODES:
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        True,
    ),
    "a collection that is a parameter": (
        """
        def f(kw, allowed):
            m = kw.get("m")
            if m in allowed:
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        True,
    ),
    "a collection with a non-literal member": (
        """
        def f(kw, other):
            m = kw.get("m")
            if m in ("a", other):
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        True,
    ),
    "a class attribute the class writes out": (
        """
        class V:
            MODES = ("a", "b")
            def f(self, m):
                if m not in self.MODES:
                    m = "a"
                return mark_safe(f"<i class={m}>")
        """,
        False,
    ),
    "a class attribute an instance assignment shadows": (
        """
        class V:
            MODES = ("a", "b")
            def __init__(self, x):
                self.MODES = x
            def f(self, m):
                if m not in self.MODES:
                    m = "a"
                return mark_safe(f"<i class={m}>")
        """,
        True,
    ),
    "a dict's keys are a constant set": (
        """
        SIZES = {"sm": "1", "lg": "2"}
        def f(s):
            if s not in SIZES:
                s = "sm"
            return mark_safe(f"<i class={s}>")
        """,
        False,
    ),
    "frozenset of literals": (
        """
        OK = frozenset({"a", "b"})
        def f(s):
            if s in OK:
                return mark_safe(f"<i class={s}>")
            return ""
        """,
        False,
    ),
    "the false edge of an or": (
        """
        MODES = ("a", "b")
        def f(m):
            if m not in MODES or m == "z":
                return ""
            return mark_safe(f"<i class={m}>")
        """,
        False,
    ),
    "the true edge of an or says nothing": (
        """
        MODES = ("a", "b")
        def f(m, c):
            if m in MODES or c:
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        True,
    ),
    "the false edge of an and says nothing": (
        """
        MODES = ("a", "b")
        def f(m, c):
            if m in MODES and c:
                pass
            return mark_safe(f"<i class={m}>")
        """,
        True,
    ),
    "equality with a literal": (
        """
        def f(m):
            if m == "a":
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        False,
    ),
    "assert narrows nothing (python -O removes it)": (
        """
        MODES = ("a",)
        def f(m):
            assert m in MODES
            return mark_safe(f"<i class={m}>")
        """,
        True,
    ),
    "isinstance int, then a default": (
        """
        def f(n):
            if not isinstance(n, int):
                n = 0
            return mark_safe(f"<i w={n}>")
        """,
        False,
    ),
    "isinstance (int, float) on the true branch": (
        """
        def f(n):
            if isinstance(n, (int, float)):
                return mark_safe(f"<i w={n}>")
            return ""
        """,
        False,
    ),
    "isinstance str says nothing": (
        """
        def f(n):
            if isinstance(n, str):
                return mark_safe(f"<i w={n}>")
            return ""
        """,
        True,
    ),
    "a conditional expression": (
        """
        MODES = ("a", "b")
        def f(m):
            return mark_safe(f'<i class={m if m in MODES else "a"}>')
        """,
        False,
    ),
    "a conditional expression on the wrong arm": (
        """
        MODES = ("a", "b")
        def f(m):
            return mark_safe(f'<i class={m if m not in MODES else "a"}>')
        """,
        True,
    ),
    "and as a guard inside one expression": (
        """
        MODES = ("a", "b")
        def f(m):
            return mark_safe(f"<i class={m in MODES and m}>")
        """,
        False,
    ),
    "and as a guard with the wrong test": (
        """
        MODES = ("a", "b")
        def f(m):
            return mark_safe(f"<i class={m not in MODES and m}>")
        """,
        True,
    ),
    "a narrowed name that is then reassigned from raw data": (
        """
        MODES = ("a", "b")
        def f(m, x):
            if m in MODES:
                m = x
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        True,
    ),
    "a narrowed name that is then appended to": (
        """
        MODES = ("a", "b")
        def f(m, x):
            if m in MODES:
                m += x
                return mark_safe(f"<i class={m}>")
            return ""
        """,
        True,
    ),
    # #3846: ``str(x) in TABLE`` narrows the EXPRESSION ``str(x)``, never ``x`` itself
    "str(x) in TABLE narrows str(x)": (
        """
        TABLE = {"a": "1"}
        def f(status):
            status = str(status) if str(status) in TABLE else "a"
            return mark_safe(f"<i class={status}>")
        """,
        False,
    ),
    "str(x) in TABLE, then an early return": (
        """
        TABLE = {"a": "1"}
        def f(status):
            if str(status) not in TABLE:
                return ""
            return mark_safe(f"<i class={str(status)}>")
        """,
        False,
    ),
    "str(x) in TABLE does not narrow x": (
        """
        TABLE = {"a": "1"}
        def f(status):
            if str(status) in TABLE:
                return mark_safe(f"<i class={status}>")
            return ""
        """,
        True,
    ),
    "str(x) in TABLE, then x rebound": (
        """
        TABLE = {"a": "1"}
        def f(status, other):
            if str(status) in TABLE:
                status = other
                return mark_safe(f"<i class={str(status)}>")
            return ""
        """,
        True,
    ),
    "str(x) in TABLE on one path only": (
        """
        TABLE = {"a": "1"}
        def f(status, c):
            if c:
                if str(status) not in TABLE:
                    return ""
            return mark_safe(f"<i class={str(status)}>")
        """,
        True,
    ),
    "str(x) in a parameter": (
        """
        def f(status, table):
            if str(status) in table:
                return mark_safe(f"<i class={str(status)}>")
            return ""
        """,
        True,
    ),
    "str(x) in TABLE, then x changed in place (review of #4068)": (
        """
        T = {"[]": 1}
        def f(items, raw):
            if str(items) in T:
                items.append(raw)
                return mark_safe(f"<b>{str(items)}</b>")
            return ""
        """,
        True,
    ),
    "str(x) in TABLE, then an attribute of x set (review of #4068)": (
        """
        T = {"ok": 1}
        class Obj:
            name = "ok"
            def __str__(self):
                return self.name
        def f(obj, raw):
            if str(obj) in T:
                obj.name = raw
                return mark_safe(f"<b>{str(obj)}</b>")
            return ""
        """,
        True,
    ),
    "str(x) in TABLE, then x handed to a call": (
        """
        T = {"ok": 1}
        def f(obj):
            if str(obj) in T:
                mutate(obj)
                return mark_safe(f"<b>{str(obj)}</b>")
            return ""
        """,
        True,
    ),
    "str(x) in TABLE, x read by a closure": (
        """
        T = {"ok": 1}
        def f(obj, raw):
            def g():
                obj.name = raw
            if str(obj) in T:
                g()
                return mark_safe(f"<b>{str(obj)}</b>")
            return ""
        """,
        True,
    ),
    "str(x) in TABLE says nothing about str(y)": (
        """
        TABLE = {"a": "1"}
        def f(status, other):
            if str(status) in TABLE:
                return mark_safe(f"<i class={str(other)}>")
            return ""
        """,
        True,
    ),
}


class TestNarrowing:
    @pytest.mark.parametrize("label", sorted(NARROWING))
    def test_the_verdict(self, label):
        source, expect_flagged = NARROWING[label]
        assert bool(flagged(source)) is expect_flagged, label


# ---------------------------------------------------------------------------
# The differential fuzz: the analysis against real execution
# ---------------------------------------------------------------------------


def _directed_false_negatives() -> list[str]:
    """Hand-written FLOW / NARROWING / STRUCTURE rows that must be flagged and are not."""
    missed = [
        label
        for table in (FLOW, NARROWING, STRUCTURE, rows3848.ROWS)
        for label, (source, expect) in table.items()
        if expect and not flagged(source)
    ]
    # the unsafe functions of the review of #3848, each of which RUNS and emits the payload raw
    missed += [case for case, (source, _) in corpus3848.CASES.items() if not flagged(source)]
    return missed


def _one_pass_loop(self, stmt, pre, is_for):
    """A broken ``_Flow.loop``: the body runs once, no back edge."""
    frame = {"break": [], "continue": []}
    self.loops.append(frame)
    body_in = self.bind(stmt, pre) if is_for else pre
    end = self.block(stmt.body, body_in)
    self.loops.pop()
    return taint._merge(pre, end, *frame["break"], *frame["continue"])


def _loop_without(drop: str):
    """A broken ``_Flow.loop`` that forgets the ``break`` (or ``continue``) states."""

    def loop(self, stmt, pre, is_for):
        head = pre
        for _ in range(self.MAX_PASSES):
            frame = {"break": [], "continue": []}
            self.loops.append(frame)
            if is_for:
                body_in, done = self.bind(stmt, head), head
            else:
                self.loads(stmt.test, head)
                body_in, done = self.split(stmt.test, head)
            end = self.block(stmt.body, body_in)
            self.loops.pop()
            states = [pre, end] + ([] if drop == "continue" else frame["continue"])
            new_head = taint._merge(*states)
            if new_head == head:
                break
            head = new_head
        after = self.block(stmt.orelse, done)
        return taint._merge(after, *([] if drop == "break" else frame["break"]))

    return loop


def _wrap_stmt(monkeypatch, kind, replacement):
    original = taint._Flow.stmt

    def stmt(self, s, st):
        return replacement(self, s, st) if type(s) is kind else original(self, s, st)

    monkeypatch.setattr(taint._Flow, "stmt", stmt)


def _break_if_follows_only_true(monkeypatch):
    def only_true(self, s, st):
        self.loads(s.test, st)
        true, false = self.split(s.test, st)
        self.block(s.orelse, false)
        return self.block(s.body, true)

    _wrap_stmt(monkeypatch, ast.If, only_true)


def _break_assert_narrows(monkeypatch):
    def narrows(self, s, st):
        self.loads(s.test, st)
        true, _ = self.split(s.test, st)
        return true

    _wrap_stmt(monkeypatch, ast.Assert, narrows)


def _break_split_swapped(monkeypatch):
    original = taint._Flow.split
    monkeypatch.setattr(
        taint._Flow, "split", lambda self, test, state: original(self, test, state)[::-1]
    )


def _break_any_collection_narrows(monkeypatch):
    monkeypatch.setattr(taint._Module, "constant_set", lambda self, e, sc, depth=0: True)


def _break_merge_keeps_first(monkeypatch):
    def first(*states):
        live = [s for s in states if s is not None]
        return live[0] if live else None

    monkeypatch.setattr(taint, "_merge", first)


def _break_exceptions_see_only_the_start(monkeypatch):
    monkeypatch.setattr(taint._Flow, "_anywhere", staticmethod(lambda state, coll: state))


def _break_a_helper_is_always_clean(monkeypatch):
    monkeypatch.setattr(taint._Module, "_returns_clean", lambda self, local, d, seen: True)


def _break_mutation_is_ignored(monkeypatch):
    monkeypatch.setattr(taint._Module, "_mutated_or_rebound", lambda self, name: False)
    monkeypatch.setattr(taint._Module, "_stable", lambda self, key, values, owner=None: True)


def _break_hazards_are_ignored(monkeypatch):
    """The scan that finds ``global`` rebinding, ``setattr`` / ``__dict__`` / ``globals()`` and every
    in-place change finds nothing."""
    original = taint._Module._scan_hazards

    def blind(self):
        original(self)
        self.rebound_names.clear()
        self.assigned_attrs.clear()
        self.dynamic_attrs = self.dynamic_globals = False
        for found in (self.mutated, self.escaped, self.escaped_elements):
            found.clear()
        for found in (self.mutated_in, self.escaped_in, self.escaped_elements_in):
            found.clear()

    monkeypatch.setattr(taint._Module, "_scan_hazards", blind)


def _break_a_foreign_read_is_vouched(monkeypatch):
    monkeypatch.setattr(taint._Module, "_unvouched", lambda self, name, values, owner, sc: values)


def _break_class_bodies_are_visible_to_methods(monkeypatch):
    def find(self, name):
        scope = self
        while scope is not None:
            if name in scope.assigns or name in scope.contents or name in scope.pieces:
                return scope
            scope = scope.parent
        return None

    monkeypatch.setattr(taint._Scope, "find", find)


def _break_equality_narrows_against_anything(monkeypatch):
    original = taint._Flow.split

    def split(self, test, state):
        if (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.left, ast.Name)
        ):
            return self.narrow(state, test.left, test), state
        return original(self, test, state)

    monkeypatch.setattr(taint._Flow, "split", split)


def _break_depth_fails_open(monkeypatch):
    original = taint._Module.tainted

    def tainted(self, e, sc, depth, seen):
        if depth > taint._DEPTH:
            return False
        return original(self, e, sc, depth, seen)

    monkeypatch.setattr(taint._Module, "tainted", tainted)


def _break_wrappers_are_invisible(monkeypatch):
    monkeypatch.setattr(taint._Module, "_collect_wrappers", lambda self: None)


def _break_position_is_ignored(monkeypatch):
    monkeypatch.setattr(taint._Module, "_position_ok", lambda self, kind, prefix: True)


def _break_any_json_escape_is_clean(monkeypatch):
    monkeypatch.setattr(taint._Module, "_json_dumped", lambda self, e, sc, d, seen: True)
    original = taint._Module._position_ok
    monkeypatch.setattr(
        taint._Module,
        "_position_ok",
        lambda self, kind, prefix: (
            True if kind.startswith("json") else original(self, kind, prefix)
        ),
    )


def _break_star_args_are_ignored(monkeypatch):
    monkeypatch.setattr(taint._Module, "_has_star", staticmethod(lambda call: False))


def _break_a_trusted_origin_may_be_rebound(monkeypatch):
    original = taint._Module.origin

    def origin(self, e, sc, depth=0):
        saved, self.rebound_names = self.rebound_names, set()
        saved_g, self.dynamic_globals = self.dynamic_globals, False
        try:
            return original(self, e, sc, depth)
        finally:
            self.rebound_names, self.dynamic_globals = saved, saved_g

    monkeypatch.setattr(taint._Module, "origin", origin)


def _break_str_facts_survive_a_merge(monkeypatch):
    """A ``str(x) in T`` fact kept at a join where one path does not have it (#3846)."""
    original = taint._merge

    def merge(*states):
        live = [st for st in states if st is not None]
        out = original(*states)
        if out is None:
            return out
        out = dict(out)
        for st in live:
            for key, value in st.items():
                if isinstance(key, tuple):
                    out.setdefault(key, value)
        return out

    monkeypatch.setattr(taint, "_merge", merge)


def _break_nested_binding_reads_one_call(monkeypatch):
    """A nested function's parameters bound from its FIRST call site only (#3845)."""
    original = taint._Module._only_called

    def first_only(self, name, holder):
        calls = original(self, name, holder)
        return calls[:1] if calls else calls

    monkeypatch.setattr(taint._Module, "_only_called", first_only)


def _break_anything_is_a_date(monkeypatch):
    """Any receiver of ``isoformat()`` / ``weekday()`` is a date (#3847)."""
    monkeypatch.setattr(
        taint._Module, "_is_date", lambda self, e, sc, depth=0, seen=frozenset(): True
    )


def _break_closure_writes_stay_in_the_closure(monkeypatch):
    """A closure's ``parts.append(x)`` does not reach the enclosing ``parts`` (#3850)."""
    monkeypatch.setattr(taint._Module, "_closure_writes", lambda self: None)


def _break_get_default_is_ignored(monkeypatch):
    monkeypatch.setattr(taint._Module, "_defaults", staticmethod(lambda call: []))


#: name -> a function that breaks the analysis one way. Each one MUST be caught.
MUTANTS = {
    "a loop runs its body once": lambda mp: mp.setattr(taint._Flow, "loop", _one_pass_loop),
    "a loop forgets its break states": lambda mp: mp.setattr(
        taint._Flow, "loop", _loop_without("break")
    ),
    "a loop forgets its continue states": lambda mp: mp.setattr(
        taint._Flow, "loop", _loop_without("continue")
    ),
    "an if follows only its true branch": _break_if_follows_only_true,
    "an assert narrows": _break_assert_narrows,
    "narrowing on the wrong edge": _break_split_swapped,
    "membership in any collection narrows": _break_any_collection_narrows,
    "a merge keeps the first path": _break_merge_keeps_first,
    "an exception sees only the state before the try or with": _break_exceptions_see_only_the_start,
    "a call to a local helper is always clean": _break_a_helper_is_always_clean,
    "a collection mutated elsewhere is still constant": _break_mutation_is_ignored,
    "d.get(k, default) ignores the default": _break_get_default_is_ignored,
    # the review of #3848
    "no hazard is found (global, setattr, __dict__, globals(), in-place change)": _break_hazards_are_ignored,
    "a read from another scope is always vouched for": _break_a_foreign_read_is_vouched,
    "a class body is visible to its methods": _break_class_bodies_are_visible_to_methods,
    "equality narrows against any expression": _break_equality_narrows_against_anything,
    "the depth bound fails open": _break_depth_fails_open,
    "a wrapper is not a sink unless named": _break_wrappers_are_invisible,
    "a value-escaper is clean in any position": _break_position_is_ignored,
    "escape_json_for_script is clean for anything": _break_any_json_escape_is_clean,
    "star arguments are not examined": _break_star_args_are_ignored,
    "a trusted import may be rebound": _break_a_trusted_origin_may_be_rebound,
    # #3846
    "a str(x) fact survives a join it does not hold on": _break_str_facts_survive_a_merge,
    # #3845, #3847, #3850
    "a nested function is bound from its first call only": _break_nested_binding_reads_one_call,
    "anything with isoformat() is a date": _break_anything_is_a_date,
    "a closure's writes stay in the closure": _break_closure_writes_stay_in_the_closure,
}


@functools.lru_cache(maxsize=None)
def _baseline_survey(programs: int, seed: int) -> dict:
    return fuzz.survey(programs, seed)


@pytest.fixture(scope="module")
def tree_scan():
    """The guard over the real tree, once (about 6 s)."""
    return audit.scan()


#: Tiers (#4074). The fixed-seed survey and the directed rows run in full on every pull request;
#: the other seeds and the mutants are ``@pytest.mark.sweep``: before merge a seeded sample, every
#: one nightly, and every one whenever the analysis or the fuzzer changes (``SHARED_PATHS``), which
#: is when a mutant can newly survive.
SWEEP_POPULATIONS = {
    "guard-fuzz/seeds": lambda: ["1", "2", "3"],
    "guard-fuzz/mutants": lambda: sorted(MUTANTS),
}
SHARED_PATHS = (
    "scripts/lib/safe_markup_taint.py",
    "python/tests/flow_fuzz_3820.py",
    "python/tests/test_safe_markup_guard_3820.py",
    "tests/sweep_sample.py",
)
#: Units a sampled run draws beyond the ones a change forces (about a minute together).
SEED_BUDGET = 1
MUTANT_BUDGET = 6


class TestTheDifferentialFuzz:
    PROGRAMS = 700
    MUTANT_PROGRAMS = 350
    SEED = 20260811

    @pytest.mark.pr_tier
    def test_no_sink_that_held_a_raw_value_at_runtime_was_cleared(self):
        result = _baseline_survey(self.PROGRAMS, self.SEED)
        assert result["false_negatives"] == [], result["false_negatives"][:1]

    @pytest.mark.pr_tier
    def test_the_fuzz_is_not_vacuous(self):
        """A guard that flagged everything would pass the check above; these counts say it did not."""
        result = _baseline_survey(self.PROGRAMS, self.SEED)
        assert result["programs"] >= 0.95 * self.PROGRAMS
        assert result["tainted"] >= 300, result  # sinks that really received a raw value
        assert result["cleared_and_executed"] >= 500, result  # sinks the guard cleared that ran

    @pytest.mark.sweep("guard-fuzz/seeds")
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_other_seeds_agree(self, seed):
        sweep_sample.skip_unless_sampled(
            "guard-fuzz/seeds", str(seed), ["1", "2", "3"], budget=SEED_BUDGET, shared=SHARED_PATHS
        )
        assert fuzz.survey(250, seed)["false_negatives"] == []

    @pytest.mark.sweep("guard-fuzz/mutants")
    @pytest.mark.parametrize("mutant", sorted(MUTANTS))
    def test_a_broken_analysis_is_caught(self, mutant, monkeypatch):
        """The oracle can fail (#1468): break the analysis one way and either the fuzz or a directed
        row above must notice. A mutant nothing notices would mean its rule is decorative."""
        sweep_sample.skip_unless_sampled(
            "guard-fuzz/mutants", mutant, sorted(MUTANTS), budget=MUTANT_BUDGET, shared=SHARED_PATHS
        )
        MUTANTS[mutant](monkeypatch)
        result = fuzz.survey(self.MUTANT_PROGRAMS, self.SEED)
        caught = result["false_negatives"] or _directed_false_negatives()
        assert caught, f"nothing noticed: {mutant}"

    def test_the_unbroken_analysis_has_no_directed_misses(self):
        assert _directed_false_negatives() == []


# ---------------------------------------------------------------------------
# Project escapers, by origin
# ---------------------------------------------------------------------------

UTILS = "djust.components.utils"
ESCAPERS = {
    "safe_url": "safe_url(x)",
    "url_attr": "url_attr(x)",
    "srcset_attr": "srcset_attr(x)",
}


def _use(helper_call: str, imports: str) -> str:
    return f"{imports}\ndef f(x):\n    return mark_safe(f'<a href=\"{{{helper_call}}}\">')\n"


class TestProjectEscapersAreTrustedByOrigin:
    @pytest.mark.parametrize("name", sorted(ESCAPERS))
    def test_imported_from_the_module_that_defines_it(self, name):
        source = _use(ESCAPERS[name], f"from {UTILS} import {name}")
        assert flagged(source) == []

    @pytest.mark.parametrize("name", sorted(ESCAPERS))
    def test_the_same_call_through_a_reexport_is_flagged(self, name):
        source = _use(ESCAPERS[name], f"from djust.components.templatetags._registry import {name}")
        assert flagged(source) == ["f"]

    @pytest.mark.parametrize("name", sorted(ESCAPERS))
    def test_a_same_named_function_from_elsewhere_is_flagged(self, name):
        assert flagged(_use(ESCAPERS[name], f"from somewhere.other import {name}")) == ["f"]

    @pytest.mark.parametrize("name", sorted(ESCAPERS))
    def test_a_local_twin_is_flagged(self, name):
        source = f"def {name}(v):\n    return v\n" + _use(ESCAPERS[name], "")
        assert flagged(source) == ["f"]

    def test_a_relative_import_resolves_when_the_module_is_known(self):
        source = _use("safe_url(x)", "from ..utils import safe_url")
        assert flagged(source, module="djust.components.templatetags._forms") == []
        assert flagged(source, module="djust.components.ui.card") == []  # also one level up
        assert flagged(source, module="djust.other.mod") == ["f"]  # that is djust.utils
        assert flagged(source) == ["f"]  # no module name: unresolved, never trusted

    def test_a_package_init_resolves_relative_to_itself(self):
        source = _use("safe_url(x)", "from .utils import safe_url")
        assert flagged(source, module="djust.components", is_package=True) == []
        assert flagged(source, module="djust.components") == ["f"]  # a plain module: one level up

    def test_an_escaper_imported_two_ways_in_one_file_is_trusted_nowhere(self):
        """``imports`` is one map per file; the second import used to overwrite the first."""
        source = (
            "def f(x):\n    from django.utils.html import escape\n"
            "    return mark_safe(f'<b>{escape(x)}</b>')\n"
            "def g(x):\n    from re import escape\n"
            "    return mark_safe(f'<b>{escape(x)}</b>')\n"
        )
        assert flagged(source) == ["f", "g"]

    def test_a_def_in_the_defining_module_has_that_modules_origin(self):
        source = (
            "def safe_url(v):\n    return v\n"
            "def f(x):\n    return mark_safe(f'<a href=\"{safe_url(x)}\">')\n"
        )
        assert flagged(source) == ["f"]  # no module: a local def is not the project's
        assert flagged(source, module=UTILS) == []  # in utils.py itself, it IS

    def test_attr_name_and_escape_json_for_script(self):
        """Neither is cleared as a bare call (#3848): ``attr_name`` returns a LEGAL name and ``onmouseover``
        is legal; ``escape_json_for_script`` leaves ``"`` alone. The second is clean for ``json.dumps``
        output in script TEXT only (rows in ``safe_markup_rows_3848``)."""
        a = "from djust._html import attr_name\ndef f(k):\n    return mark_safe(f'<i {attr_name(k)}=1>')\n"
        b = (
            "import json\n"
            "from djust.security.json_script import escape_json_for_script as j\n"
            "def f(x):\n    return mark_safe(f'<script>{j(json.dumps(x))}</script>')\n"
        )
        assert flagged(a) == ["f"] and flagged(b) == []
        assert flagged(b.replace("djust.security.json_script", "djust.security")) == [
            "f"
        ]  # a re-export

    def test_the_css_validators_return_their_default_as_given(self):
        clean = "from djust.components.utils import safe_css_color\n"

        def case(call: str, params: str = "v") -> str:
            return (
                clean
                + f"def f({params}):\n    return mark_safe(f'<i style=\"color:{{{call}}}\">')\n"
            )

        assert flagged(case('safe_css_color(v, "red")')) == []
        assert flagged(case("safe_css_color(v)")) == []
        assert flagged(case("safe_css_color(v, d)", "v, d")) == ["f"]
        assert flagged(case("safe_css_color(v, default=d)", "v, d")) == ["f"]
        assert flagged(case('safe_css_color(v, default="red")')) == []

    def test_a_record_builder_and_its_fields(self):
        head = "from djust.components.components.image_upload_preview import upload_attrs\n"
        unpack = head + (
            "def f(a):\n    root, zone, field = upload_attrs(a, 0, True)\n"
            "    return mark_safe(f'<div{root}{zone}{field}>')\n"
        )
        assert flagged(unpack) == []
        assert flagged(unpack.replace(head, "from elsewhere import upload_attrs\n")) == ["f"]
        attrs = (
            "from djust.components.components.signature_pad import pad_extras\n"
            "def f(a):\n    extras = pad_extras(a)\n    return mark_safe(f'<div{extras.root}>')\n"
        )
        assert flagged(attrs) == []
        assert flagged(
            attrs.replace("signature_pad import pad_extras", "elsewhere import pad_extras")
        ) == ["f"]
        # a name that holds a record on one path and something raw on another is raw
        mixed = attrs.replace(
            "    extras = pad_extras(a)\n",
            "    extras = pad_extras(a)\n    if a:\n        extras = a\n",
        )
        assert flagged(mixed) == ["f"]

    def test_an_imported_constant_is_trusted_by_origin(self):
        ok = (
            "from djust.components.components.image_upload_preview import ITEMS_HTML\n"
            "def f(x):\n    return mark_safe(f'<div>{ITEMS_HTML}{x}</div>')\n"
        )
        assert flagged(ok) == ["f"]  # x is still raw
        only = ok.replace("{x}", "")
        assert flagged(only) == []
        assert flagged(
            only.replace("djust.components.components.image_upload_preview", "elsewhere")
        ) == ["f"]

    def test_every_trusted_origin_names_something_that_exists(self):
        """A rename must not leave a dead entry that trusts nothing, or a name that now means more."""
        origins = {
            *taint.CLEAN_ORIGINS,
            *taint.CLEAN_IF_DEFAULT_CLEAN,
            *taint.CLEAN_RECORD_ORIGINS,
            *taint.CLEAN_NAME_ORIGINS,
            *taint.JSON_ESCAPERS,
            *taint.JSON_PRODUCERS,
            *taint.CONTEXT_SENSITIVE_ORIGINS,
            *taint.CLEAN_PROJECT_ORIGINS,
            *taint.DELTA_ORIGINS,
            *taint.CALENDAR_CLASSES,
        }
        missing = []
        for origin in sorted(origins):
            module, _, name = origin.rpartition(".")
            if not module:
                continue  # a builtin
            try:
                found = hasattr(importlib.import_module(module), name)
            except ImportError:
                found = False
            if not found:
                missing.append(origin)
        assert missing == [], missing

    def test_an_imported_constant_is_still_a_string_literal_in_its_module(self):
        for origin in taint.CLEAN_NAME_ORIGINS:
            module, _, name = origin.rpartition(".")
            tree = ast.parse((ROOT / "python" / (module.replace(".", "/") + ".py")).read_text())
            values = [
                n.value
                for n in tree.body
                if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == name for t in n.targets)
            ]
            assert len(values) == 1 and isinstance(
                values[0], (ast.Constant, ast.JoinedStr, ast.BinOp)
            )
            # a literal, or literals joined by implicit concatenation: no name, no call
            assert not any(isinstance(n, (ast.Name, ast.Call)) for n in ast.walk(values[0])), origin


# ---------------------------------------------------------------------------
# The helpers keep the contract the guard trusts them for
# ---------------------------------------------------------------------------

PAYLOAD = sweep.PAYLOAD
URL = sweep.URL_PAYLOAD
HOSTILE_URLS = [
    URL,
    "javascript:alert(1)",
    " JaVa\tScRiPt:alert(1)",
    "vbscript:x",
    "data:text/html,<script>alert(1)</script>",
    PAYLOAD,
    '/ok" onmouseover="alert(1)',
]
HTML_SPECIAL = set("<>\"'")  # `&` is checked apart: escaped text contains it, as an entity
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|#x27|#39);")


class TestTheTrustedHelpersKeepTheirContract:
    """The guard treats these calls as clean whatever their argument. Prove it, by parsing."""

    @pytest.mark.parametrize("value", HOSTILE_URLS)
    @pytest.mark.parametrize("helper", ["safe_url", "url_attr"])
    def test_a_url_is_escaped_for_text_and_attribute_and_a_script_scheme_is_neutralised(
        self, helper, value
    ):
        from djust.components import utils

        out = getattr(utils, helper)(value)
        assert not (HTML_SPECIAL & set(out)), out
        assert "&" not in _ENTITY.sub("", out), out  # an ampersand only as an entity
        assert sweep.injections(f'<a href="{out}" title="{out}">{out}</a>') == []
        assert not out.lower().lstrip().startswith(("javascript:", "vbscript:", "data:"))

    @pytest.mark.parametrize("value", HOSTILE_URLS)
    def test_a_srcset_is_escaped_candidate_by_candidate(self, value):
        from djust.components.utils import srcset_attr

        out = srcset_attr(f"{value} 2x, /b.png 1x")
        assert not (HTML_SPECIAL & set(out)), out
        assert "&" not in _ENTITY.sub("", out), out
        assert sweep.injections(f'<img srcset="{out}">') == []

    def test_url_attr_passes_a_value_the_developer_marked_safe_like_conditional_escape(self):
        """The same contract as ``conditional_escape``, which the guard trusts already."""
        from djust.components.utils import url_attr

        marked = mark_safe("/a?b=1&amp;c=2")
        assert url_attr(marked) == str(marked) == str(conditional_escape(marked))
        assert url_attr(marked, honor_safe=False) != str(marked)  # opt out: it is escaped

    @pytest.mark.parametrize("name", ["x onfocus=alert(1) y", 'a"b', "a<b", "a b", "", None, "a/b"])
    def test_attr_name_refuses_what_cannot_be_a_name(self, name):
        from djust._html import attr_name

        with pytest.raises(ValueError):
            attr_name(name)

    @pytest.mark.parametrize(
        "name", ["type", "dj-input", "aria-label", "@click", ":class", "x-on:click.prevent"]
    )
    def test_attr_name_returns_a_legal_name_unchanged(self, name):
        from djust._html import attr_name

        assert attr_name(name) == name

    def test_escape_json_for_script_leaves_nothing_that_can_close_the_element(self):
        import json

        from djust.security.json_script import escape_json_for_script

        out = escape_json_for_script(json.dumps({"a": PAYLOAD, "b": "</script><!--"}))
        assert not ({"<", ">", "&"} & set(out))
        assert json.loads(out)["b"] == "</script><!--"

    @pytest.mark.parametrize(
        "helper", ["safe_css_color", "safe_css_length", "safe_css_ratio", "safe_css_number"]
    )
    def test_a_css_validator_returns_a_value_free_of_markup_and_css_breakouts(self, helper):
        from djust.components import utils

        func = getattr(utils, helper)
        rng = random.Random(3820)
        alphabet = list("abcxyz0123456789#%.-+*/(), _") + list("<>\"'&;:{}\\`\n\r\t!@=[]|")
        candidates = [
            PAYLOAD,
            "red;position:fixed",
            "url(javascript:alert(1))",
            "expression(alert(1))",
            'red"onmouseover=',
            "10px}</style><b>",
            "calc(1px + url(x))",
            "10px /* x */",
        ] + [
            "".join(rng.choice(alphabet) for _ in range(rng.randrange(1, 14))) for _ in range(3000)
        ]
        banned = set("<>\"'&;:{}\\`")
        for value in candidates:
            out = func(value, "DEFAULT")
            if out == "DEFAULT":
                continue
            assert not (banned & set(out)), (helper, value, out)
            assert "\n" not in out.strip() and "\r" not in out.strip(), (helper, value, out)

    @pytest.mark.parametrize(
        "helper", ["safe_css_color", "safe_css_length", "safe_css_ratio", "safe_css_number"]
    )
    def test_a_css_validator_returns_its_default_AS_GIVEN(self, helper):
        """Why a raw ``default`` makes the result raw: the guard checks that argument."""
        from djust.components import utils

        assert getattr(utils, helper)(PAYLOAD, PAYLOAD) == PAYLOAD

    def test_rich_html_is_a_sanitiser(self):
        from djust.components.utils import rich_html

        out = rich_html(f"<b>{PAYLOAD}</b><script>alert(1)</script><a href='javascript:x'>y</a>")
        assert sweep.injections(f"<div>{out}</div>") == []
        assert "<script" not in out.lower()
        marked = mark_safe("<i>kept</i>")
        assert rich_html(marked) == "<i>kept</i>"

    def test_rich_html_is_markup_and_not_attribute_safe(self):
        """The limit the guard cannot see: ``format_html`` output has it too. Written down so the
        trust in ``rich_html`` is not read as 'safe anywhere'."""
        from djust.components.utils import rich_html

        assert "<strong>" in rich_html("<strong>x</strong>")

    def test_the_extras_builders_escape_everything_they_were_given(self):
        from djust.components.components.image_cropper import cropper_extras
        from djust.components.components.image_upload_preview import upload_attrs
        from djust.components.components.map_picker import map_picker_attrs
        from djust.components.components.signature_pad import pad_extras
        from djust.components.components.voice_input import voice_extras

        root, zone, field = upload_attrs(PAYLOAD, PAYLOAD, True)
        for frag in (root, zone, field):
            assert sweep.injections(f"<div{frag}></div>") == [], frag
        voice = voice_extras(PAYLOAD, PAYLOAD)
        assert (
            sweep.injections(
                f"<div{voice.button_attrs}>{voice.wrap_open}{voice.disclosure}{voice.status}"
                f"{voice.interim}{voice.wrap_close}</div>"
            )
            == []
        )
        assert (
            sweep.injections(f"<div{map_picker_attrs(PAYLOAD, PAYLOAD, URL, PAYLOAD)}></div>") == []
        )
        pad = pad_extras(PAYLOAD, PAYLOAD, PAYLOAD)
        assert (
            sweep.injections(
                f"<div{pad.root}><canvas{pad.canvas}></canvas>{pad.typed}{pad.undo}{pad.toggle}"
                f"{pad.status}</div>"
            )
            == []
        )
        cropper = cropper_extras()
        assert all(isinstance(part, str) for part in cropper)

    def test_clamp_columns_and_cells_return_ints(self):
        from djust.components.components.dashboard_grid import clamp_cells, clamp_columns

        for hostile in (PAYLOAD, None, "9" * 40, -5, 1e99, "3"):
            try:
                cols = clamp_columns(hostile)
            except (ValueError, TypeError, OverflowError):
                continue  # refusing is fine: nothing is returned to print
            assert isinstance(cols, int)
            cells = clamp_cells(cols, 1, 1, 1, 1)
            assert all(isinstance(c, int) for c in cells)

    def test_menu_attrs_prints_two_fixed_words(self):
        from djust.components.components.markdown_editor import menu_attrs

        for value in (PAYLOAD, None, True, False, 0, "yes"):
            out = menu_attrs(value, value)
            assert out in {
                f' data-bubble-menu="{a}" data-floating-menu="{b}"'
                for a in ("true", "false")
                for b in ("true", "false")
            }

    def test_render_markdown_neutralises_raw_markup_and_script_urls(self):
        pytest.importorskip("djust._rust")
        from djust.markdown import render_markdown

        out = str(
            render_markdown(f"{PAYLOAD}\n\n[x](javascript:alert(1))\n\n<script>alert(1)</script>")
        )
        assert sweep.injections(f"<div>{out}</div>") == []
        assert "<script" not in out.lower() and 'href="javascript' not in out.lower()


# ---------------------------------------------------------------------------
# The wrapper sinks
# ---------------------------------------------------------------------------


class TestWrapperSinks:
    @pytest.mark.parametrize("wrapper", sorted(taint.WRAPPER_SINKS))
    def test_an_interpolated_string_is_flagged_and_its_clean_twin_is_not(self, wrapper):
        dirty = f"def f(v):\n    return {wrapper}(f'<div>{{v}}</div>')\n"
        clean = f"def f(v):\n    return {wrapper}(f'<div>{{escape(v)}}</div>')\n"
        assert flagged(dirty) == ["f"]
        assert flagged(clean) == []

    def test_the_canary_from_the_issue(self):
        """`def _zz(v): return safe_html(f"<div>{v}</div>")` was flagged by nothing."""
        assert flagged('def _zz(v):\n    return safe_html(f"<div>{v}</div>")\n') == ["_zz"]

    def test_a_bare_parameter_is_still_how_trusted_markup_is_passed_in(self):
        assert flagged("def f(markup):\n    return _safe(markup)\n") == []

    @pytest.mark.parametrize(
        "imports,call",
        [
            ("from djust._html import safe_html as sh", "sh"),
            ("from djust import _html", "_html.safe_html"),
            ("import djust._html as h", "h.safe_html"),
        ],
    )
    def test_an_alias_is_followed(self, imports, call):
        assert flagged(f"{imports}\ndef f(v):\n    return {call}(f'<b>{{v}}</b>')\n") == ["f"]

    def test_a_join_a_list_and_a_name_reach_the_wrapper_too(self):
        assert flagged("def f(xs):\n    return _safe(''.join(f'<li>{x}</li>' for x in xs))\n") == [
            "f"
        ]
        assert flagged("def f(x):\n    s = '<i>' + x\n    return safe_html(s)\n") == ["f"]

    @pytest.mark.pr_tier
    def test_the_wrappers_are_sinks_of_the_guard_and_the_scan_finds_them_in_the_tree(
        self, tree_scan
    ):
        assert taint.WRAPPER_SINKS <= taint.SINKS
        sites = {fn for (path, fn) in tree_scan if path.endswith("rust_handlers.py")}
        assert any(name.endswith("Handler.render") for name in sites)

    @pytest.mark.pr_tier
    def test_a_new_wrapper_around_mark_safe_has_to_be_named(self):
        """Every function in the package whose body is one call of a sink on its own parameter must be a
        sink by name, or the next one is invisible exactly as `_safe` and `safe_html` were."""
        unnamed = []
        for path in audit.package_files():
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                params = {
                    a.arg for a in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                }
                body = [
                    s
                    for s in node.body
                    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                ]
                if len(body) > 2:
                    continue
                for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
                    callee = getattr(call.func, "id", getattr(call.func, "attr", None))
                    if (
                        callee in {"mark_safe", "SafeString", "SafeText"}
                        and call.args
                        and isinstance(call.args[0], ast.Name)
                        and call.args[0].id in params
                        and node.name not in taint.SINKS
                    ):
                        unnamed.append(f"{path.relative_to(ROOT)}:{node.name}")
        assert unnamed == [], (
            "a function that marks its argument safe is not a sink of the guard; add its name to "
            f"SINKS / WRAPPER_SINKS in scripts/lib/safe_markup_taint.py: {unnamed}"
        )

    def test_the_wrappers_still_do_what_the_guard_assumes(self):
        """``_safe`` / ``safe_html`` mark their argument safe unchanged: that is why they are sinks."""
        from djust._html import safe_html
        from djust.components import rust_handlers
        from djust.components.templatetags import _dev_tools

        for wrapper in (safe_html, rust_handlers._safe, _dev_tools._safe):
            out = wrapper("<b>x</b>")
            assert isinstance(out, SafeData) and str(out) == "<b>x</b>"


# ---------------------------------------------------------------------------
# The block body
# ---------------------------------------------------------------------------

HANDLER = "djust.components.rust_handlers"
BLOCK = textwrap.dedent(
    """
    class H:
        def render(self, args, content, context):
            return _safe(f"<div>{content}</div>")
    """
)


class TestEngineRenderedContent:
    def test_content_of_a_block_handler_render_is_clean_in_the_handlers_module(self):
        assert flagged(BLOCK, module=HANDLER) == []

    def test_but_not_anywhere_else_and_not_without_a_module_name(self):
        assert flagged(BLOCK, module="djust.components.other") == ["H.render"]
        assert flagged(BLOCK) == ["H.render"]

    @pytest.mark.parametrize(
        "signature",
        [
            "self, args, body, context",
            "self, args, context",
            "self, content, args, context",
            "self, args, content, context, extra",
        ],
    )
    def test_only_the_block_handler_signature(self, signature):
        source = BLOCK.replace("self, args, content, context", signature).replace(
            "{content}", "{args}"
        )
        assert flagged(source, module=HANDLER) == ["H.render"]  # `args` is raw whatever the shape

    def test_only_a_method_named_render(self):
        assert flagged(BLOCK.replace("def render", "def build"), module=HANDLER) == ["H.build"]

    def test_a_reassignment_of_content_re_taints_it(self):
        source = BLOCK.replace(
            "        return", "        content = context.get('x')\n        return"
        )
        assert flagged(source, module=HANDLER) == ["H.render"]

    def test_the_engine_really_hands_a_block_handler_a_safe_string(self):
        """The contract the rule rests on (registry.rs, #2379), run against the compiled engine."""
        rust = pytest.importorskip("djust._rust")
        seen: list = []

        class Capture:
            def render(self, args, content, context):
                seen.append(content)
                return mark_safe(f"<div>{content}</div>")

        rust.register_block_tag_handler("g3820_cap", "endg3820_cap", Capture())
        try:
            out = rust.render_template(
                "{% g3820_cap %}<p>{{ p }}</p>{% endg3820_cap %}",
                {"p": "<script>alert(1)</script>"},
            )
        finally:
            rust.unregister_block_tag_handler("g3820_cap")
        assert len(seen) == 1 and isinstance(seen[0], SafeData), type(seen[0])
        assert "&lt;script&gt;" in str(seen[0]) and "<script>" not in str(seen[0])
        assert "<script>" not in out

    def test_every_block_handler_in_the_module_has_the_signature_the_rule_names(self):
        tree = ast.parse((ROOT / "python/djust/components/rust_handlers.py").read_text())
        shapes = {}
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for fn in (
                n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "render"
            ):
                args = [a.arg for a in fn.args.args]
                if "content" in args:
                    shapes[cls.name] = args
        assert shapes and all(
            a == ["self", "args", "content", "context"] for a in shapes.values()
        ), shapes


# ---------------------------------------------------------------------------
# Class attributes, container defaults and local helpers
# ---------------------------------------------------------------------------

STRUCTURE = {
    "a class table looked up by a raw key": (
        """
        class V:
            ICONS = {"a": "<i>", "b": "<b>"}
            def f(self, k):
                return mark_safe(f"<p>{self.ICONS.get(k, '')}</p>")
        """,
        False,
    ),
    "a class table with a raw default": (
        """
        class V:
            ICONS = {"a": "<i>", "b": "<b>"}
            def f(self, k, d):
                return mark_safe(f"<p>{self.ICONS.get(k, d)}</p>")
        """,
        True,
    ),
    "a module table with a raw default": (
        """
        ICONS = {"a": "<i>"}
        def f(k, d):
            return mark_safe(f"<p>{ICONS.get(k, d)}</p>")
        """,
        True,
    ),
    "a module table with a raw default keyword": (
        """
        ICONS = {"a": "<i>"}
        def f(k, d):
            return mark_safe(f"<p>{ICONS.get(k, default=d)}</p>")
        """,
        True,
    ),
    "a module table with a literal default": (
        """
        ICONS = {"a": "<i>"}
        def f(k):
            return mark_safe(f"<p>{ICONS.get(k, '')}</p>")
        """,
        False,
    ),
    "iterating a table with a raw default": (
        """
        ICONS = {"a": ["<i>"]}
        def f(k, d):
            return mark_safe("".join(ICONS.get(k, d)))
        """,
        True,
    ),
    "a class attribute an instance assignment shadows, read as a value": (
        """
        class V:
            LABEL = "x"
            def __init__(self, v):
                self.LABEL = v
            def f(self):
                return mark_safe(f"<p>{self.LABEL}</p>")
        """,
        True,
    ),
    "a class attribute read as a value": (
        """
        class V:
            LABEL = "x"
            def f(self):
                return mark_safe(f"<p>{self.LABEL}</p>")
        """,
        False,
    ),
    "a dataclass field is a default the constructor replaces": (
        """
        @dataclass
        class V:
            label: str = "x"
            def f(self):
                return mark_safe(f"<p>{self.label}</p>")
        """,
        True,
    ),
    "an annotated class attribute is not a constant either": (
        """
        class V(NamedTuple):
            label: str = "x"
            def f(self):
                return mark_safe(f"<p>{self.label}</p>")
        """,
        True,
    ),
    "a decorated class is not trusted for its helpers": (
        """
        @some_decorator
        class V:
            def item(self, x):
                return f"<i>{escape(x)}</i>"
            def f(self, v):
                return mark_safe(f"<ul>{self.item(v)}</ul>")
        """,
        True,
    ),
    "a method is not a constant": (
        """
        class V:
            def label(self):
                return "x"
            def f(self):
                return mark_safe(f"<p>{self.label}</p>")
        """,
        True,
    ),
    "a helper that escapes inside": (
        """
        def item(x):
            return f"<i>{escape(x)}</i>"
        def f(v):
            return mark_safe(f"<ul>{item(v)}</ul>")
        """,
        False,
    ),
    "a helper that interpolates its parameter": (
        """
        def item(x):
            return f"<i>{x}</i>"
        def f(v):
            return mark_safe(f"<ul>{item(v)}</ul>")
        """,
        True,
    ),
    "a helper that returns its parameter": (
        """
        def ident(x):
            return x
        def f(v):
            return mark_safe(f"<ul>{ident(v)}</ul>")
        """,
        True,
    ),
    "a helper with one raw return among clean ones": (
        """
        def item(x, p):
            if p:
                return "<i>k</i>"
            return x
        def f(v, p):
            return mark_safe(f"<ul>{item(v, p)}</ul>")
        """,
        True,
    ),
    "a recursive helper that escapes at the bottom": (
        """
        def deep(x, n):
            if n:
                return "<u>" + deep(x, n - 1)
            return escape(x)
        def f(v):
            return mark_safe(f"<ul>{deep(v, 2)}</ul>")
        """,
        False,
    ),
    "a recursive helper that returns the raw value at the bottom": (
        """
        def deep(x, n):
            if n:
                return "<u>" + deep(x, n - 1)
            return x
        def f(v):
            return mark_safe(f"<ul>{deep(v, 2)}</ul>")
        """,
        True,
    ),
    "a method of the same class": (
        """
        class V:
            def item(self, x):
                return f"<i>{escape(x)}</i>"
            def f(self, v):
                return mark_safe(f"<ul>{self.item(v)}</ul>")
        """,
        False,
    ),
    "a method an instance assignment replaces": (
        """
        class V:
            def item(self, x):
                return f"<i>{escape(x)}</i>"
            def __init__(self, g):
                self.item = g
            def f(self, v):
                return mark_safe(f"<ul>{self.item(v)}</ul>")
        """,
        True,
    ),
    "a subclass in the same file overrides the class table": (
        """
        class Base:
            MODES = ("a", "b")
            def f(self, m):
                if m not in self.MODES:
                    m = "a"
                return mark_safe(f"<i class={m}>")
        class Sub(Base):
            MODES = tuple(request_values())
        """,
        True,
    ),
    "a subclass further down the same file overrides the class table": (
        """
        class Base:
            MODES = ("a", "b")
            def f(self, m):
                if m not in self.MODES:
                    m = "a"
                return mark_safe(f"<i class={m}>")
        class Mid(Base):
            pass
        class Leaf(Mid):
            MODES = tuple(request_values())
        """,
        True,
    ),
    "an unrelated class of the same file has the same table name": (
        """
        class Base:
            MODES = ("a", "b")
            def f(self, m):
                if m not in self.MODES:
                    m = "a"
                return mark_safe(f"<i class={m}>")
        class Other:
            MODES = tuple(request_values())
        """,
        False,
    ),
    "a subclass in the same file overrides the helper method": (
        """
        class Base:
            def item(self, x):
                return f"<i>{escape(x)}</i>"
            def f(self, v):
                return mark_safe(f"<ul>{self.item(v)}</ul>")
        class Sub(Base):
            def item(self, x):
                return f"<i>{x}</i>"
        """,
        True,
    ),
    "a helper defined twice": (
        """
        def item(x):
            return "k"
        def item(x):
            return x
        def f(v):
            return mark_safe(f"<ul>{item(v)}</ul>")
        """,
        True,
    ),
    "a decorated helper": (
        """
        def item(x):
            return "k"
        item = wrap(item)
        def f(v):
            return mark_safe(f"<ul>{item(v)}</ul>")
        """,
        True,
    ),
    "a helper with an unknown decorator": (
        """
        @deco
        def item(x):
            return "k"
        def f(v):
            return mark_safe(f"<ul>{item(v)}</ul>")
        """,
        True,
    ),
    "a staticmethod helper": (
        """
        class V:
            @staticmethod
            def item(x):
                return f"<i>{escape(x)}</i>"
            def f(self, v):
                return mark_safe(f"<ul>{self.item(v)}</ul>")
        """,
        False,
    ),
    "a generator is not its yields": (
        """
        def items(x):
            yield "k"
        def f(v):
            return mark_safe(f"<ul>{items(v)}</ul>")
        """,
        True,
    ),
    "a nested helper": (
        """
        def f(v):
            def item(x):
                return f"<i>{escape(x)}</i>"
            return mark_safe(f"<ul>{item(v)}</ul>")
        """,
        False,
    ),
    "a helper that returns nothing": (
        """
        def item(x):
            return
        def f(v):
            return mark_safe(f"<ul>{item(v)}</ul>")
        """,
        False,
    ),
    "a comprehension of constants is clean as a value": (
        """
        def f(rows):
            n = max(len(r) for r in rows)
            return mark_safe(f"<td colspan={n}>")
        """,
        False,
    ),
    "a comprehension of raw values is not": (
        """
        def f(rows):
            n = max(r for r in rows)
            return mark_safe(f"<td colspan={n}>")
        """,
        True,
    ),
    "sum of clean numbers": (
        """
        def f(rows):
            vals = []
            for r in rows:
                vals.append(float(r))
            return mark_safe(f"<b>{sum(vals)}</b>")
        """,
        False,
    ),
    "sum of raw values": (
        """
        def f(rows):
            return mark_safe(f"<b>{sum(rows)}</b>")
        """,
        True,
    ),
    # #3845: a nested function whose every call site is in view holds what those calls pass
    "a nested function called with literal arguments": (
        """
        def f(density):
            def btn(val, label):
                return f'<button data-d="{val}">{label}</button>'
            return mark_safe(f"<p>{btn('compact', 'Compact')}</p>")
        """,
        False,
    ),
    "a nested function called once with raw data": (
        """
        def f(density, raw):
            def btn(val, label):
                return f'<button data-d="{val}">{label}</button>'
            return mark_safe(f"<p>{btn('compact', 'C')}{btn(raw, 'R')}</p>")
        """,
        True,
    ),
    "a nested function also handed to map": (
        """
        def f(xs):
            def btn(val):
                return f'<b>{val}</b>'
            extra = list(map(btn, xs))
            return mark_safe(f"<p>{btn('a')}</p>")
        """,
        True,
    ),
    "a nested function that is returned": (
        """
        def f(xs):
            def btn(val):
                return f'<b>{val}</b>'
            out = mark_safe(f"<p>{btn('a')}</p>")
            return btn
        """,
        True,
    ),
    "a nested function with a raw default": (
        """
        def f(raw):
            def btn(val=raw):
                return f'<b>{val}</b>'
            return mark_safe(f"<p>{btn()}</p>")
        """,
        True,
    ),
    "a nested function called from another nested function with raw data": (
        """
        def f(raw):
            def btn(val):
                return f'<b>{val}</b>'
            def g(y):
                return btn(y)
            return mark_safe(f"<p>{btn('a')}{g(raw)}</p>")
        """,
        True,
    ),
    "a sink inside a nested function called with literals": (
        """
        def f(raw):
            def btn(val):
                return mark_safe(f'<b>{val}</b>')
            return btn('a')
        """,
        False,
    ),
    "a sink inside a nested function called with raw data": (
        """
        def f(raw):
            def btn(val):
                return mark_safe(f'<b>{val}</b>')
            return btn(raw)
        """,
        True,
    ),
    # #3847: type facts the standard library and the project guarantee
    "calendar days are ints": (
        """
        import calendar
        def f(year, month):
            weeks = calendar.Calendar().monthdayscalendar(int(year), int(month))
            return mark_safe("".join(f"<td>{d}</td>" for w in weeks for d in w))
        """,
        False,
    ),
    "a method of that name on something that is not a calendar": (
        """
        def f(year, month, cal):
            weeks = cal.monthdayscalendar(year, month)
            return mark_safe("".join(f"<td>{d}</td>" for w in weeks for d in w))
        """,
        True,
    ),
    "calendar dates are not in the digit table": (
        """
        import calendar
        def f(year, month):
            weeks = calendar.Calendar().monthdatescalendar(int(year), int(month))
            return mark_safe("".join(f"<td>{d}</td>" for w in weeks for d in w))
        """,
        True,
    ),
    "a date's isoformat and weekday": (
        """
        from datetime import date, timedelta
        def f(year):
            current = date(int(year), 1, 1)
            current += timedelta(days=1)
            end = date(int(year), 12, 31)
            return mark_safe(
                f"<i data-d={current.isoformat()} w={current.weekday()} n={(end - current).days}>"
            )
        """,
        False,
    ),
    "isoformat with a separator from the caller": (
        """
        from datetime import datetime
        def f(sep):
            return mark_safe(f"<i title={datetime(2020, 1, 1).isoformat(sep=sep)}>")
        """,
        True,
    ),
    "isoformat with a positional separator from the caller": (
        """
        from datetime import datetime
        def f(sep):
            return mark_safe(f"<i title={datetime(2020, 1, 1).isoformat(sep)}>")
        """,
        True,
    ),
    "strftime of a date takes its format from the caller": (
        """
        from datetime import date
        def f(year, fmt):
            return mark_safe(f"<i>{date(int(year), 1, 1).strftime(fmt)}</i>")
        """,
        True,
    ),
    "isoformat of something that is not a date": (
        """
        def f(when):
            return mark_safe(f"<i>{when.isoformat()}</i>")
        """,
        True,
    ),
    "a date name that is also bound to raw data": (
        """
        from datetime import date
        def f(year, other, c):
            current = date(int(year), 1, 1)
            if c:
                current = other
            return mark_safe(f"<i>{current.isoformat()}</i>")
        """,
        True,
    ),
    "staticmethod of an imported digit helper": (
        """
        from djust.components.utils import interpolate_color
        class H:
            _ic = staticmethod(interpolate_color)
            def r(self, a, b, t):
                return mark_safe(f'<i style="fill:{self._ic(a, b, t)}">')
        """,
        False,
    ),
    "staticmethod of a helper from elsewhere": (
        """
        from somewhere.other import interpolate_color
        class H:
            _ic = staticmethod(interpolate_color)
            def r(self, a, b, t):
                return mark_safe(f'<i style="fill:{self._ic(a, b, t)}">')
        """,
        True,
    ),
    "staticmethod alias the file also rebinds": (
        """
        from djust.components.utils import interpolate_color
        class H:
            _ic = staticmethod(interpolate_color)
            def r(self, a, b, t):
                return mark_safe(f'<i style="fill:{self._ic(a, b, t)}">')
        def patch(h, g):
            h._ic = g
        """,
        True,
    ),
    "a module-level function is not bound from its call sites": (
        """
        def btn(val):
            return mark_safe(f'<b>{val}</b>')
        def f(raw):
            return btn('a')
        """,
        True,
    ),
}


class TestClassAttributesAndLocalHelpers:
    @pytest.mark.parametrize("label", sorted(STRUCTURE))
    def test_the_verdict(self, label):
        source, expect_flagged = STRUCTURE[label]
        assert bool(flagged(source)) is expect_flagged, label

    def test_a_dict_get_default_was_a_hole_in_the_first_guard(self):
        """Found while teaching the guard class tables: ``TABLE.get(k, default)`` was judged by the
        TABLE only, so a raw ``default`` came out clean. Shown with the rule switched off."""
        source, _ = STRUCTURE["a module table with a raw default"]
        original = taint._Module.__dict__["_defaults"]  # the staticmethod object itself
        try:
            taint._Module._defaults = staticmethod(lambda call: [])
            assert flagged(source) == []  # the old behaviour: not flagged
        finally:
            taint._Module._defaults = original
        assert flagged(source) == ["f"]

    def test_the_helper_rule_does_not_change_what_an_unknown_call_marks(self):
        """``mark_safe(helper(x))`` where helper builds a raw string stays an UNFLAGGED known miss
        (test_safe_markup_guard_3766.KNOWN_MISSES): judging a call's result inside a larger string
        is new; flagging the call itself would be a policy change, not this issue."""
        source = (
            "def helper(x):\n    return f'<a {x}>'\ndef f(x):\n    return mark_safe(helper(x))\n"
        )
        assert flagged(source) == []


# ---------------------------------------------------------------------------
# The lint the issue asks for
# ---------------------------------------------------------------------------


class TestNoEscaperIsImportedThroughAReExport:
    """#3774 found three tag modules importing ``conditional_escape`` through ``._registry``; the guard
    rightly did not trust the re-export, and importing from ``django.utils.html`` removed 29 sinks.
    A lint finds the whole class without running the guard."""

    DEFINING = {
        "conditional_escape": {"django.utils.html"},
        "safe_url": {UTILS},
        "url_attr": {UTILS},
        "srcset_attr": {UTILS},
        "safe_css_color": {UTILS},
        "safe_css_length": {UTILS},
        "safe_css_ratio": {UTILS},
        "safe_css_number": {UTILS},
        "rich_html": {UTILS},
        "attr_name": {"djust._html"},
        "escape_json_for_script": {"djust.security.json_script"},
        "format_html": {"django.utils.html"},
    }
    #: ``_registry`` is the re-export itself (it imports from the defining module); the package
    #: ``__init__`` of ``djust.security`` publishes ``escape_json_for_script`` for outside users.
    EXEMPT = {
        "python/djust/components/templatetags/_registry.py",
        "python/djust/security/__init__.py",
    }

    @pytest.mark.pr_tier
    def test_every_module_imports_an_escaper_from_the_module_that_defines_it(self):
        offenders = []
        for path in audit.package_files():
            rel = str(path.relative_to(ROOT))
            if rel in self.EXEMPT:
                continue
            module, is_package = audit.module_name(path)
            resolver = taint._Module("", module, is_package)
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.ImportFrom):
                    continue
                source = resolver._absolute(node.level, node.module or "")
                for alias in node.names:
                    wanted = self.DEFINING.get(alias.name)
                    if wanted and source not in wanted:
                        offenders.append(f"{rel}:{node.lineno} from {source} import {alias.name}")
        assert offenders == [], (
            "the guard trusts an escaper by the module that DEFINES it; import it from there: "
            f"{offenders}"
        )

    def test_the_lint_can_fail(self, tmp_path):
        resolver = taint._Module("", "djust.components.templatetags._x", False)
        tree = ast.parse(
            "from ._registry import conditional_escape\nfrom django.utils.html import escape\n"
        )
        sources = [
            resolver._absolute(n.level, n.module or "")
            for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom)
        ]
        assert sources == ["djust.components.templatetags._registry", "django.utils.html"]
        assert sources[0] not in self.DEFINING["conditional_escape"]
        assert sources[1] in {"django.utils.html"}


# ---------------------------------------------------------------------------
# The limits, pinned (so that when the guard learns one, the entry moves)
# ---------------------------------------------------------------------------

#: Code that is SAFE and still flagged: the reason the 13 handlers in the audited baseline are there.
STILL_FLAGGED_BUT_SAFE = {
    "a parameter the caller escaped": """
        def item(e_event):
            return f'<i dj-click="{e_event}">'
        def f(event):
            return mark_safe(f"<p>{item(conditional_escape(event))}</p>")
        """,
}


class TestTheLimitsArePinned:
    @pytest.mark.parametrize("label", sorted(STILL_FLAGGED_BUT_SAFE))
    def test_safe_code_the_guard_still_cannot_prove(self, label):
        """When one of these stops being flagged the guard learned it: move the row, and delete the
        audit entries it clears."""
        assert flagged(STILL_FLAGGED_BUT_SAFE[label]) != [], label

    def test_the_docstring_names_the_limits_this_adds(self):
        doc = taint.__doc__ or ""
        for needle in ("WHERE a clean fragment is placed", "ANOTHER file", "NAME-based"):
            assert needle in doc, needle


# ---------------------------------------------------------------------------
# The real tree, before and after
# ---------------------------------------------------------------------------


@pytest.mark.pr_tier
class TestTheRealTree:
    def test_the_baseline_shrank_and_every_entry_is_still_a_site(self, tree_scan):
        found = tree_scan
        problems = audit.reconcile(found, audit.AUDITED)
        assert not any(problems.values()), problems
        sinks = sum(len(v) for v in found.values())
        # Over the same tree, with `_safe` / `safe_html` as sinks, the first guard flagged 199 sinks.
        assert sinks == sum(e.sites for e in audit.AUDITED.values())
        assert sinks <= 46, sinks  # 57 before #3845 / #3846 / #3847 proved ten functions

    def test_the_wrappers_are_scanned_in_every_file_that_calls_them(self, tree_scan):
        files = {path for (path, _fn) in tree_scan}
        assert any(f.endswith("rust_handlers.py") for f in files)
        assert any(f.endswith("_dev_tools.py") for f in files)


class TestCleanProjectOrigins:
    """#3847: an origin trusted for ANY argument is pinned by what it does with a hostile one."""

    def test_interpolate_color_prints_a_hex_colour_or_raises(self):
        import re

        from djust.components.utils import interpolate_color

        # ``#rrggbb``; a factor outside 0..1 can push a channel past ff or below 0 (more hex digits,
        # a ``-``), never a character that means anything in markup
        hex_colour = re.compile(r"#[0-9a-f-]+")
        for c1, c2, t in (("#000000", "#ffffff", 0.5), ("#abc", "#def", 2.0), ("#fff", "#000", -9)):
            assert hex_colour.fullmatch(interpolate_color(c1, c2, t))
        for hostile in ('"><script>', "#zz0000", "", "#12"):
            with pytest.raises((ValueError, IndexError)):
                interpolate_color(hostile, "#000000", 0.5)

    def test_the_date_constructors_exist(self):
        import datetime

        for origin in taint.DATE_ORIGINS:
            parts = origin.split(".")
            assert parts[0] == "datetime"
            obj = datetime
            for part in parts[1:]:
                obj = getattr(obj, part)
            assert callable(obj), origin
