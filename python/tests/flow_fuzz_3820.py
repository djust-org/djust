"""A differential fuzz of the safe-markup guard's flow analysis against real execution (#3820).

``scripts/lib/safe_markup_taint.py`` decides, per use of a name, which bindings can reach it. Hand
written cases can only show what their author thought of, so this generates small random functions
(assignments, coercions, ``if`` / ``for`` / ``while`` / ``try`` / ``with``, ``break`` / ``continue`` /
``return``, membership narrowing, local helpers), RUNS each one with a recognisable marker string as
the raw value, and checks the one direction that matters:

    a ``mark_safe`` call that actually received the marker was flagged by the guard.

A static "clean" for a sink that at runtime held the marker is a false negative: the analysis
cleared a name that a real execution still found raw. The other direction (flagged, never raw) is
the guard erring on the side of caution and is not checked.

The generator is deterministic for a ``seed``. ``test_safe_markup_guard_3820.py`` runs it on the real
analysis (no false negatives) and on deliberately broken ones (every mutant must be caught), so a
fuzz that cannot fail does not pass for evidence.
"""

from __future__ import annotations

import random
import sys

from scripts.lib.safe_markup_taint import find_unsafe_markup

#: Escapes to ``&lt;RAW&gt;``, so its presence in a sink argument means a RAW value got there.
MARK = "<RAW>"
NAMES = ["a", "b", "c"]
HEADER = (
    "from contextlib import nullcontext, suppress\n"
    "from django.utils.html import conditional_escape, escape, format_html\n"
    "from django.utils.safestring import mark_safe\n"
    "MODES = ('k', 'm')\n"
    "def h_clean(x):\n    return f'<b>{escape(x)}</b>'\n"
    "def h_raw(x):\n    return f'<b>{x}</b>'\n"
    "def h_mixed(x, p):\n    y = x\n    if p:\n        y = escape(x)\n    return f'<b>{y}</b>'\n"
    "def h_branches(x, p):\n    if p:\n        return '<i>k</i>'\n    return x\n"
    "def h_rec(x, n=2):\n    if n:\n        return h_rec(x, n - 1)\n    return escape(x)\n"
    "def h_rec_raw(x, n=2):\n    if n:\n        return '<u>' + h_rec_raw(x, n - 1)\n    return x\n"
    "def h_int(x):\n    x = int(x)\n    return f'<s>{x}</s>'\n"
    "def h_none(x):\n    if x:\n        return\n    return 'z'\n"
)


class Raw(str):
    """A str the program treats as raw; ``int()`` / ``float()`` of it succeed, like a numeric string."""

    def __int__(self) -> int:
        return 7

    def __float__(self) -> float:
        return 7.0


class Generator:
    def __init__(self, rng: random.Random) -> None:
        self.r = rng
        self.lines: list[str] = []
        self.indent = 1
        self.in_loop = 0
        self.counter = 0

    def emit(self, text: str) -> None:
        self.lines.append("    " * self.indent + text)

    def name(self) -> str:
        return self.r.choice([*NAMES, "v"])

    def expr(self) -> str:
        n = self.name()
        return [
            lambda: "v",
            lambda: n,
            lambda: f"int({n})",
            lambda: f"float({n})",
            lambda: f'f"<{{{n}}}>"',
            lambda: f"escape({n})",
            lambda: f"conditional_escape({n})",
            lambda: f"{n}.strip()",
            lambda: f'{n} + "s"',
            lambda: '"5"',
            lambda: f"str({n})",
            lambda: f'({n} if p else "5")',
            lambda: f'format_html("<i>{{}}</i>", {n})',
            lambda: f"len({n})",
            lambda: f"h_clean({n})",
            lambda: f"h_raw({n})",
            lambda: f"h_mixed({n}, p)",
            lambda: f"h_branches({n}, q)",
            lambda: f"h_rec({n})",
            lambda: f"h_rec_raw({n})",
            lambda: f"h_int({n})",
        ][self.r.randrange(21)]()

    def cond(self) -> str:
        n = self.name()
        return self.r.choice(
            [
                "p",
                "q",
                "not p",
                "p and q",
                "p or q",
                f"{n} in MODES",
                f"{n} not in MODES",
                f'{n} == "5"',
                f"isinstance({n}, int)",
                f"not isinstance({n}, (int, float))",
                f"{n} in MODES and p",
                f"{n} not in MODES or q",
            ]
        )

    def sink(self, name: str | None = None) -> None:
        n = name or self.name()
        form = self.r.choice(
            [
                f'f"<i>{{{n}}}</i>"',
                f'"<i>" + {n}',
                f'"<i>%s</i>" % ({n},)',
                f'"<i>{{}}</i>".format({n})',
            ]
        )
        self.emit(f"mark_safe({form})")

    def raise_if_q(self) -> None:
        self.indent += 1
        self.emit("if q:")
        self.indent += 1
        self.emit("raise ValueError")
        self.indent -= 2

    def stmt(self, depth: int) -> None:
        r = self.r
        k = r.randrange(24)
        if k >= 20:
            k = 14 if self.in_loop else 19
        if k == 19 and r.random() < 0.7:
            n = r.choice(NAMES)
            self.emit(f"{n} = {r.choice(['int', 'float', 'len'])}({n})")
            return
        if depth <= 0 and k >= 6:
            k = r.randrange(6)
        if k < 5:
            self.emit(f"{r.choice(NAMES)} = {self.expr()}")
        elif k == 5:
            self.sink()
        elif k < 8:
            self.emit(f"if {self.cond()}:")
            self.block(depth - 1)
            if r.random() < 0.6:
                self.emit("else:")
                self.block(depth - 1)
        elif k == 8:
            self.emit(f"if {self.cond()}:")
            self.block(depth - 1)
            self.emit(f"elif {self.cond()}:")
            self.block(depth - 1)
        elif k == 9:
            self.emit("for _i in range(2):")
            self.in_loop += 1
            self.block(depth - 1)
            self.in_loop -= 1
            if r.random() < 0.3:
                self.emit("else:")
                self.block(depth - 1)
        elif k == 10:
            self.counter += 1
            w = f"_w{self.counter}"
            self.emit(f"{w} = 0")
            self.emit(f"while {w} < 2:")
            self.indent += 1
            self.emit(f"{w} = {w} + 1")
            self.indent -= 1
            self.in_loop += 1
            self.block(depth - 1)
            self.in_loop -= 1
        elif k in (11, 12):
            self.emit("try:")
            self.raise_if_q()
            self.block(depth - 1, prefix=False)
            self.emit("except ValueError:")
            self.block(depth - 1)
            if r.random() < 0.3:
                self.emit("else:")
                self.block(depth - 1)
            if r.random() < 0.3:
                self.emit("finally:")
                self.block(depth - 1)
        elif k == 13:
            self.emit(r.choice(["with nullcontext():", "with suppress(ValueError):"]))
            self.raise_if_q()
            self.block(depth - 1, prefix=False)
        elif k == 14 and self.in_loop:
            self.emit(f"if {self.cond()}:")
            self.indent += 1
            self.emit(r.choice(["break", "continue"]))
            self.indent -= 1
        elif k == 15:
            self.emit(f"if {self.cond()}:")
            self.indent += 1
            self.emit("return None")
            self.indent -= 1
        elif k == 16:
            self.emit(f"{r.choice(NAMES)}, {r.choice(NAMES)} = {self.expr()}, {self.expr()}")
        elif k == 17:
            self.emit(f"{r.choice(NAMES)} += {self.expr()}")
        elif k == 18:
            self.emit(
                f"{r.choice(NAMES)} = [{self.expr()}]; {r.choice(NAMES)} = {r.choice(NAMES)}[0]"
            )
        else:
            self.emit(f"{r.choice(NAMES)} = {self.expr()}")

    def block(self, depth: int, prefix: bool = True) -> None:
        self.indent += 1
        for _ in range(self.r.randrange(1, 4)):
            self.stmt(depth)
        self.indent -= 1

    def program(self) -> str:
        self.lines = ["def f(v, p, q):", "    a = v", '    b = "5"', "    c = 3"]
        for _ in range(self.r.randrange(2, 7)):
            self.stmt(3)
        for n in NAMES:
            self.emit(f'mark_safe(f"<i>{{{n}}}</i>")')
        return HEADER + "\n".join(self.lines) + "\n"


def run(source: str) -> dict[int, bool]:
    """``{sink line: it received the marker}`` over every combination of the two flags."""
    hits: dict[int, bool] = {}
    namespace: dict = {}

    def recorder(arg):
        line = sys._getframe(1).f_lineno
        hits[line] = hits.get(line, False) or MARK in str(arg)
        return arg

    exec(compile(source, "<fuzz>", "exec"), namespace)  # noqa: S102 - generated, trusted
    namespace["mark_safe"] = recorder
    for p in (True, False):
        for q in (True, False):
            try:
                namespace["f"](Raw(MARK), p, q)
            except Exception:  # noqa: BLE001 - a generated program may raise; sinks before it count
                pass
    return hits


def survey(programs: int, seed: int) -> dict:
    """Generate ``programs`` functions, run them, compare with the guard.

    ``false_negatives`` lists ``(program number, source)`` for every sink that held the marker at
    runtime and was not flagged. ``cleared_and_executed`` counts sinks the guard cleared that did run:
    a fuzz where the guard flags everything would pass vacuously, so the test asserts it is large.
    """
    rng = random.Random(seed)
    out = {
        "programs": 0,
        "sinks": 0,
        "tainted": 0,
        "cleared_and_executed": 0,
        "false_negatives": [],
    }
    for number in range(programs):
        source = Generator(rng).program()
        try:
            compile(source, "<fuzz>", "exec")
        except SyntaxError:  # pragma: no cover - the generator emits valid Python
            continue
        flagged = {f.lineno for f in find_unsafe_markup(source)}
        hits = run(source)
        out["programs"] += 1
        sinks = [
            i + 1
            for i, line in enumerate(source.split("\n"))
            if "mark_safe(" in line and "import" not in line
        ]
        for line in sinks:
            out["sinks"] += 1
            if line not in flagged and line in hits:
                out["cleared_and_executed"] += 1
            if hits.get(line):
                out["tainted"] += 1
                if line not in flagged:
                    out["false_negatives"].append((number, source))
    return out
