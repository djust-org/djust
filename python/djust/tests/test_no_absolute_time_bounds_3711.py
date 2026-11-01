"""No test may bound a measured duration with an absolute number of seconds (#3711).

``assert time.perf_counter() - start < 1.0`` is a claim about the machine, not the
code: main's CI failed on exactly that (#3710) when a runner stalled for ~100 s inside
an unrelated test (#3715). What such a test is really asking is either "does this scale
linearly" (judge the growth: ``djust.tests._growth.assert_scales_linearly``) or "did this
not block" (assert an ordering: an event set, a frame received).

This is the ratchet that keeps the sweep from regrowing. It finds, by AST, every
``assert`` that compares a DURATION (a difference of two clock readings, or anything
computed from one) with a number that is not itself measured, counts them per test
file, and lets the count only fall. ``BASELINE`` is data: lowering a count is the whole
edit, and the test fails both ways, so a fixed site cannot be quietly re-added later
and a stale entry cannot linger.

What counts as a bound, and what does not:

* ``assert elapsed < 2``, ``assert time.perf_counter() - t0 < BUDGET_S``,
  ``assert 0 < took < 3``, ``assert 2.0 > took``: counted.
* ``assert large < small * 10`` and ``assert big / small < 10``: a ratio of two
  measurements has no unit, so it is not a bound.
* ``assert time.monotonic() < deadline``: a clock reading (a deadline inside a polling
  loop that waits for something to happen) is a wait, which #3709 covers.
* ``assert elapsed > 0.1``: a lower bound cannot be made false by a stall.

Only collected test files (``test_*.py`` / ``*_test.py``) are read: a file pytest never
imports cannot flake a run.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Optional

import pytest

REPO = Path(__file__).resolve().parents[3]
ROOTS = ("python", "tests")
SKIP_PARTS = {"node_modules", "static", ".venv", "target", "__pycache__", "worktrees"}

#: Test files that still carry absolute duration bounds, and how many. A count only
#: ever goes DOWN; an entry that reaches zero is deleted. Keyed by repo-relative path.
BASELINE: Dict[str, int] = {}

CLOCK = "clock"  # an instant: perf_counter(), loop.time(), a deadline
DURATION = "duration"  # a span: a difference of two instants, or derived from one

_CLOCK_FUNCS = {
    "time",
    "perf_counter",
    "monotonic",
    "process_time",
    "thread_time",
    "perf_counter_ns",
    "monotonic_ns",
    "process_time_ns",
    "thread_time_ns",
}
_LOOP_GETTERS = {"get_event_loop", "get_running_loop", "new_event_loop"}
_PRESERVING = {"min", "max", "round", "abs", "float", "int", "sum"}


def _is_clock_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _CLOCK_FUNCS - {"time"}
    if not isinstance(func, ast.Attribute) or func.attr not in _CLOCK_FUNCS:
        return False
    owner = func.value
    if isinstance(owner, ast.Name):
        return owner.id in {"time", "_time", "_t"} or "loop" in owner.id.lower()
    if isinstance(owner, ast.Call):  # asyncio.get_running_loop().time()
        getter = owner.func
        name = getter.attr if isinstance(getter, ast.Attribute) else getattr(getter, "id", "")
        return name in _LOOP_GETTERS
    return False


def _stronger(a: Optional[str], b: Optional[str]) -> Optional[str]:
    if DURATION in (a, b):
        return DURATION
    return a or b


def _is_number(node: ast.AST) -> bool:
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        return _is_number(node.operand)
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


class _Scope:
    """What each name in one function is: a clock reading, a duration, or neither."""

    def __init__(self, returns_duration: set[str]) -> None:
        self.env: Dict[str, str] = {}
        self.returns_duration = returns_duration

    def kind(self, node: ast.AST) -> Optional[str]:
        if _is_clock_call(node):
            return CLOCK
        if isinstance(node, ast.Name):
            return self.env.get(node.id)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in self.returns_duration:
                return DURATION
            if isinstance(func, ast.Name) and func.id in _PRESERVING:
                kinds = [self.kind(a) for a in node.args]
                return DURATION if DURATION in kinds else (CLOCK if CLOCK in kinds else None)
            return None
        if isinstance(node, ast.UnaryOp):
            return self.kind(node.operand)
        if isinstance(node, ast.Await):
            return self.kind(node.value)  # ``await _case()`` of a helper that returns a span
        if isinstance(node, ast.Subscript):
            return self.kind(node.value)
        if isinstance(node, ast.IfExp):
            return _stronger(self.kind(node.body), self.kind(node.orelse))
        if isinstance(node, (ast.Tuple, ast.List)):
            kinds = [self.kind(e) for e in node.elts]
            return DURATION if DURATION in kinds else (CLOCK if CLOCK in kinds else None)
        if isinstance(node, ast.BinOp):
            return self._binop(node)
        return None

    def _binop(self, node: ast.BinOp) -> Optional[str]:
        left, right = self.kind(node.left), self.kind(node.right)
        if isinstance(node.op, ast.Sub):
            if right == CLOCK:
                return DURATION  # an instant minus an instant; or a remaining time
            if left == DURATION:
                return DURATION
            if left == CLOCK:
                # ``clock - 59`` is an instant in the past; ``clock - something_measured``
                # (an attribute, a subscript) is a span.
                return CLOCK if _is_number(node.right) else DURATION
            return None
        if isinstance(node.op, ast.Add):
            if DURATION == left == right:
                return DURATION
            return _stronger(left, right) if CLOCK in (left, right) else None
        if isinstance(node.op, (ast.Div, ast.FloorDiv)) and left and right:
            return None  # a ratio of two measurements has no unit
        if isinstance(node.op, (ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)):
            return _stronger(left, right)
        return None

    def static(self, node: ast.AST) -> bool:
        """A value that is not measured: numbers, names, attributes, arithmetic on them."""
        if self.kind(node) is not None:
            return False
        if isinstance(node, ast.Constant):
            return _is_number(node)
        if isinstance(node, (ast.Name, ast.Attribute)):
            return True
        if isinstance(node, ast.UnaryOp):
            return self.static(node.operand)
        if isinstance(node, ast.BinOp):
            return self.static(node.left) and self.static(node.right)
        return False


def _returns_duration(tree: ast.AST) -> set[str]:
    """Names of functions that return a measured duration (a ``_took(fn)`` helper)."""
    found: set[str] = set()
    for _ in range(3):  # a helper can build on another
        before = len(found)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            scope = _Scope(found)
            _learn(fn, scope)
            for node in ast.walk(fn):
                if isinstance(node, ast.Return) and node.value is not None:
                    if scope.kind(node.value) == DURATION:
                        found.add(fn.name)
        if len(found) == before:
            break
    return found


def _learn(fn: ast.AST, scope: _Scope) -> None:
    """Fill ``scope.env`` from every assignment in ``fn`` until nothing changes."""
    assigns = [n for n in ast.walk(fn) if isinstance(n, (ast.Assign, ast.AugAssign, ast.AnnAssign))]
    changed = True
    while changed:
        changed = False
        for node in assigns:
            if node.value is None:
                continue
            kind = scope.kind(node.value)
            if isinstance(node, ast.AugAssign):
                kind = _stronger(kind, scope.kind(node.target))
            if kind is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in ast.walk(target):
                    if not isinstance(name, ast.Name):
                        continue
                    old = scope.env.get(name.id)
                    new = _stronger(old, kind)
                    if new != old:
                        scope.env[name.id] = new
                        changed = True


def find_bounds(source: str) -> List[int]:
    """Line numbers of every ``assert`` that bounds a duration with an unmeasured number."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    helpers = _returns_duration(tree)
    lines: set[int] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        scope = _Scope(helpers)
        _learn(fn, scope)
        for node in ast.walk(fn):
            if not isinstance(node, ast.Assert):
                continue
            for cmp_ in ast.walk(node.test):
                if not isinstance(cmp_, ast.Compare):
                    continue
                operands = [cmp_.left, *cmp_.comparators]
                for op, lo, hi in zip(cmp_.ops, operands, operands[1:]):
                    if isinstance(op, (ast.Lt, ast.LtE)):
                        measured, bound = lo, hi
                    elif isinstance(op, (ast.Gt, ast.GtE)):
                        measured, bound = hi, lo
                    else:
                        continue
                    if scope.kind(measured) == DURATION and scope.static(bound):
                        lines.add(node.lineno)
    return sorted(lines)


def _collected_test_files() -> List[Path]:
    files = []
    for root in ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if SKIP_PARTS & set(path.relative_to(REPO).parts):
                continue
            if path.name.startswith("test_") or path.name.endswith("_test.py"):
                files.append(path)
    return sorted(files)


def live_counts() -> Dict[str, int]:
    counts = {}
    for path in _collected_test_files():
        found = find_bounds(path.read_text(encoding="utf-8"))
        if found:
            counts[path.relative_to(REPO).as_posix()] = len(found)
    return counts


def regressions(live: Dict[str, int], baseline: Dict[str, int]) -> Dict[str, str]:
    """Files whose count rose, or that are new, and entries that no longer match."""
    problems = {}
    for path, count in live.items():
        allowed = baseline.get(path, 0)
        if count > allowed:
            problems[path] = f"{count} absolute bound(s), baseline allows {allowed}"
    for path, allowed in baseline.items():
        if live.get(path, 0) < allowed:
            problems[path] = (
                f"baseline says {allowed} but {live.get(path, 0)} remain: lower the baseline"
            )
    return problems


# --------------------------------------------------------------------------- #
# The ratchet
# --------------------------------------------------------------------------- #


def test_the_count_of_absolute_duration_bounds_only_falls():
    problems = regressions(live_counts(), BASELINE)
    assert problems == {}, (
        "Do not bound a duration with a number of seconds (#3711). Judge growth with "
        "djust.tests._growth.assert_scales_linearly, or assert an ordering "
        f"(an event set, a frame received). {problems}"
    )


def test_the_walk_reads_the_test_files_it_claims_to():
    paths = {p.relative_to(REPO).as_posix() for p in _collected_test_files()}
    assert "python/djust/tests/test_no_absolute_time_bounds_3711.py" in paths
    assert len(paths) > 500, "the scan found far fewer test files than the suite has"


# --------------------------------------------------------------------------- #
# The matcher: what it must catch (and survive a rename of), and what it must not
# --------------------------------------------------------------------------- #

CAUGHT = {
    "inline difference": """
def test_a():
    start = time.perf_counter()
    work()
    assert time.perf_counter() - start < 2
""",
    "two statements": """
def test_a():
    t0 = time.monotonic()
    work()
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5
""",
    "renamed variables": """
def test_a():
    zz = time.perf_counter()
    work()
    qq = time.perf_counter() - zz
    assert qq < 0.5
""",
    "process time": """
def test_a():
    start = time.process_time()
    work()
    assert time.process_time() - start < 1.0, unit
""",
    "from-import clock": """
def test_a():
    t = perf_counter()
    work()
    assert perf_counter() - t < 1
""",
    "event loop clock": """
async def test_a():
    t0 = asyncio.get_event_loop().time()
    await work()
    elapsed = loop.time() - t0
    assert elapsed < 2.0
""",
    "best of three": """
def test_a():
    best = float("inf")
    for _ in range(3):
        start = time.perf_counter()
        work()
        best = min(best, time.perf_counter() - start)
    assert best < 2
""",
    "named budget": """
def test_a():
    budget_ms = 20 + n * 0.5
    start = time.perf_counter()
    work()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < budget_ms, "slow"
""",
    "helper that returns a duration": """
def _took(fn):
    start = time.perf_counter()
    fn()
    return time.perf_counter() - start

def test_a():
    elapsed = _took(work)
    assert elapsed < 0.5
""",
    "chained comparison": """
def test_a():
    start = time.time()
    work()
    assert 0 <= time.time() - start < 3
""",
    "bound on the left": """
def test_a():
    start = time.time()
    work()
    assert 2.0 > time.time() - start
""",
    "inside a boolean": """
def test_a():
    t0 = time.monotonic()
    work()
    elapsed = time.monotonic() - t0
    assert not released and elapsed < 1.0, "slow"
""",
    "an awaited helper that returns a span among other things": """
async def _case():
    t0 = time.monotonic()
    await work()
    latency = time.monotonic() - t0
    return latency, frame

async def test_a():
    latency, frame = await _case()
    assert latency < 0.6, latency
""",
    "freshness against a stored instant": """
def test_a():
    assert time.time() - backend._heartbeats[key] < 5
""",
    "inside a method of a class": """
class TestA:
    def test_a(self):
        t0 = time.perf_counter()
        work()
        assert time.perf_counter() - t0 <= 0.25
""",
}

NOT_CAUGHT = {
    "growth ratio, multiplied": """
def test_a():
    small = best(1)
    large = best(4)
    assert large < small * 10
""",
    "growth ratio, divided": """
def test_a():
    t0 = time.perf_counter()
    work(1)
    small = time.perf_counter() - t0
    t0 = time.perf_counter()
    work(4)
    big = time.perf_counter() - t0
    ratio = big / small
    assert ratio < 10
""",
    "a deadline inside a polling loop": """
def test_a():
    deadline = time.monotonic() + 10
    while not done():
        assert time.monotonic() < deadline, "never finished"
""",
    "a lower bound": """
def test_a():
    t0 = time.perf_counter()
    time.sleep(0.1)
    assert time.perf_counter() - t0 >= 0.1
""",
    "not a duration at all": """
def test_a():
    assert len(rows) < 5
    assert count <= 10
""",
    "compared with another measurement": """
def test_a():
    a = time.perf_counter() - s0
    b = time.perf_counter() - s1
    assert a < b
""",
    "a timestamp compared with a stored instant": """
def test_a():
    assert backend._next_sweep > time.monotonic()
""",
}


@pytest.mark.parametrize("source", CAUGHT.values(), ids=CAUGHT.keys())
def test_the_matcher_catches(source):
    assert len(find_bounds(source)) == 1


@pytest.mark.parametrize("source", NOT_CAUGHT.values(), ids=NOT_CAUGHT.keys())
def test_the_matcher_leaves_alone(source):
    assert find_bounds(source) == []


def test_a_rename_of_every_identifier_is_still_caught():
    original = CAUGHT["two statements"]
    renamed = (
        original.replace("elapsed", "spent").replace("t0", "begun").replace("test_a", "test_zzz")
    )
    assert renamed != original
    assert find_bounds(original) == find_bounds(renamed) != []


def test_a_new_bound_in_a_clean_file_is_a_regression():
    live = {"python/tests/test_x.py": 1}
    assert "python/tests/test_x.py" in regressions(live, {})


def test_a_new_bound_in_a_file_with_a_baseline_entry_is_a_regression():
    live = {"python/tests/test_x.py": 3}
    assert "python/tests/test_x.py" in regressions(live, {"python/tests/test_x.py": 2})


def test_a_fixed_site_must_lower_the_baseline():
    live = {"python/tests/test_x.py": 1}
    problems = regressions(live, {"python/tests/test_x.py": 2})
    assert "lower the baseline" in problems["python/tests/test_x.py"]


def test_a_renamed_file_fails_both_ways_so_the_baseline_is_moved_with_it():
    baseline = {"python/tests/test_old.py": 2}
    live = {"python/tests/test_new.py": 2}
    assert set(regressions(live, baseline)) == {
        "python/tests/test_old.py",
        "python/tests/test_new.py",
    }


def test_equal_counts_pass():
    assert regressions({"a.py": 2}, {"a.py": 2}) == {}
