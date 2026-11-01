"""Explicit short timeouts on Channels communicator waits only ever go down (#3709).

The root ``conftest.py`` gives every communicator wait a 30 s default (#3692), and
``test_ws_communicator_defaults_3464.py`` pins it. An explicit ``timeout=3`` on a
call overrides that default, so the wait is a 3 s wall-clock bound again: a runner
that stalls longer than that (a stop-the-world GC pause was measured at 3.38 s,
#3708; ``tests/timing_report.py`` now says when it happens, #3715) fails a test
whose code is fine. #3692's flake 1 failed on exactly such a call.

A wait for output that WILL arrive takes the default: ``await socket.receive_json_from()``
or ``djust.tests._ws_frames.receive_until`` for "until this frame". The one legitimate
short timeout is a window for ABSENCE ("nothing arrived within 0.1 s"), which
``receive_nothing`` is for, or a ``receive_*`` inside ``pytest.raises`` / ``try``.

This is the ratchet. It finds, by AST, every ``await <x>.<communicator wait>(...)``
with a numeric ``timeout`` under :data:`SHORT_S` seconds, as a keyword or in the
timeout's positional slot, and counts them per test file. ``BASELINE`` is data: the
count may only fall, and the test fails both ways, so a converted file cannot quietly
regrow and a stale entry cannot linger. The set of waits is read off Channels'
communicator classes at run time, not listed (a hand list lost ``connect`` once,
#3692); ``receive_nothing`` is exempt by name.

What counts, and what does not:

* ``await socket.receive_json_from(timeout=3)`` and ``await comm.connect(2)``: counted.
  The receiver's name is irrelevant (``socket``, ``comm``, ``ws`` ...): the METHOD is
  what makes it a communicator wait.
* ``await socket.receive_nothing(timeout=0.1)``: an absence window, exempt.
* A wait inside ``with pytest.raises(...)`` / ``suppress(...)`` or a ``try`` that has
  handlers (``try: await socket.receive_json_from(timeout=1) except Exception: pass``):
  the idiom for "drain a frame if one was sent" and "nothing arrived within N s". The
  short timeout is the point; the 30 s default would only make the test wait 30 s for
  a frame that was never going to come. A short wait hidden in a ``try`` to dodge this
  pin is a review matter, not something an AST can tell from the real idiom.
* ``await socket.receive_json_from(timeout=30)`` or any value from :data:`SHORT_S` up:
  not short.
* ``release.wait(timeout=2)`` (a ``threading.Event``, not awaited): not a communicator.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pytest
from channels.testing import HttpCommunicator, WebsocketCommunicator

REPO = Path(__file__).resolve().parents[3]
ROOTS = ("python", "tests")
SKIP_PARTS = {"node_modules", "static", ".venv", "target", "__pycache__", "worktrees"}

#: A numeric timeout under this many seconds is "short". The 30 s default is the
#: point; 10 is where a wait stops being plausibly a stall-sized window.
SHORT_S = 10

#: The one communicator wait that is a window for absence.
ABSENCE_WINDOWS = {"receive_nothing"}

#: Test files that still carry explicit short communicator timeouts, and how many.
#: A count only ever goes DOWN; an entry that reaches zero is deleted. Keyed by
#: repo-relative path.
#:
#: The one entry is on purpose: ``test_ws_communicator_defaults_3464.py`` pins that an
#: explicit timeout STILL WINS over the 30 s default, so it passes ``timeout=0.01`` to
#: ``disconnect`` (converting it made that test fail, which is how it was found).
BASELINE: Dict[str, int] = {
    "python/djust/tests/test_ws_communicator_defaults_3464.py": 1,
}


def communicator_waits() -> Dict[str, int]:
    """``{method name: positional index of its timeout}`` for every communicator wait.

    Derived from the Channels classes, like the 30 s default's own pin: a method a
    Channels release adds is covered the day it appears.
    """
    found: Dict[str, int] = {}
    for communicator in (WebsocketCommunicator, HttpCommunicator):
        for cls in communicator.__mro__:
            for name, func in vars(cls).items():
                if name.startswith("_") or name in ABSENCE_WINDOWS:
                    continue
                if not inspect.iscoroutinefunction(func):
                    continue
                params = [p for p in inspect.signature(func).parameters if p != "self"]
                if "timeout" not in params:
                    continue
                found[name] = params.index("timeout")
    return found


def _short_number(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value < SHORT_S
    )


def _is_absence_window(await_node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> bool:
    """A wait inside ``pytest.raises`` / ``suppress`` or a ``try`` with handlers.

    That is the idiom for "nothing arrives within N seconds" (or "drain a frame if one
    was sent"): the short timeout IS the assertion, and a 30 s default would only make
    the test wait 30 s for the frame that was never going to come.
    """
    node = await_node
    while node in parents:
        child, node = node, parents[node]
        if isinstance(node, (ast.With, ast.AsyncWith)) and child in node.body:
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call):
                    func = ctx.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name in ("raises", "suppress"):
                        return True
        if isinstance(node, ast.Try) and child in node.body and node.handlers:
            return True
    return False


def find_short_waits(source: str, waits: Optional[Dict[str, int]] = None) -> List[int]:
    """Line numbers of every awaited communicator wait with a short explicit timeout."""
    waits = communicator_waits() if waits is None else waits
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    lines: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr not in waits:
            continue
        if _is_absence_window(node, parents):
            continue
        index = waits[call.func.attr]
        keyword = next((k.value for k in call.keywords if k.arg == "timeout"), None)
        positional = call.args[index] if index < len(call.args) else None
        if any(v is not None and _short_number(v) for v in (keyword, positional)):
            lines.add(call.lineno)
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
    waits = communicator_waits()
    counts = {}
    for path in _collected_test_files():
        found = find_short_waits(path.read_text(encoding="utf-8"), waits)
        if found:
            counts[path.relative_to(REPO).as_posix()] = len(found)
    return counts


def regressions(live: Dict[str, int], baseline: Dict[str, int]) -> Dict[str, str]:
    """Files whose count rose, or that are new, and entries that no longer match."""
    problems = {}
    for path, count in live.items():
        allowed = baseline.get(path, 0)
        if count > allowed:
            problems[path] = f"{count} short communicator timeout(s), baseline allows {allowed}"
    for path, allowed in baseline.items():
        if live.get(path, 0) < allowed:
            problems[path] = (
                f"baseline says {allowed} but {live.get(path, 0)} remain: lower the baseline"
            )
    return problems


# --------------------------------------------------------------------------- #
# The ratchet
# --------------------------------------------------------------------------- #


def test_the_count_of_short_communicator_timeouts_only_falls():
    problems = regressions(live_counts(), BASELINE)
    assert problems == {}, (
        "An explicit short timeout on a communicator wait overrides the 30 s default "
        "(#3709). Wait with no timeout (the default), or djust.tests._ws_frames."
        "receive_until / receive_settled; keep a short timeout only as a window for "
        f"absence (receive_nothing). {problems}"
    )


def test_the_baseline_is_the_data_it_claims_to_be():
    assert all(isinstance(n, int) and n > 0 for n in BASELINE.values())
    assert all(path.startswith(("python/", "tests/")) for path in BASELINE)


def test_the_waits_are_derived_from_channels_and_exclude_the_absence_window():
    waits = communicator_waits()
    assert {"connect", "disconnect", "receive_output", "receive_from", "receive_json_from"} <= set(
        waits
    )
    assert "receive_nothing" not in waits
    assert waits["receive_json_from"] == 0 and waits["disconnect"] == 1


def test_the_walk_reads_the_test_files_it_claims_to():
    paths = {p.relative_to(REPO).as_posix() for p in _collected_test_files()}
    assert "python/djust/tests/test_ws_communicator_defaults_3464.py" in paths
    assert len(paths) > 500, "the scan found far fewer test files than the suite has"


# --------------------------------------------------------------------------- #
# The matcher: what it must catch (and survive a rename of), and what it must not
# --------------------------------------------------------------------------- #

CAUGHT = {
    "keyword timeout": """
async def test_a(socket):
    await socket.receive_json_from(timeout=3)
""",
    "renamed receiver": """
async def test_a(zz):
    await zz.receive_json_from(timeout=3)
""",
    "attribute receiver": """
async def test_a(self):
    frame = await self.comm.sock.receive_output(timeout=2)
""",
    "float timeout": """
async def test_a(socket):
    await socket.receive_from(timeout=0.5)
""",
    "positional timeout": """
async def test_a(socket):
    await socket.receive_output(2)
""",
    "connect": """
async def test_a(socket):
    connected, _ = await socket.connect(timeout=2)
""",
    "disconnect, positional slot": """
async def test_a(socket):
    await socket.disconnect(1000, 3)
""",
    "inside an expression": """
async def test_a(socket):
    assert (await socket.receive_json_from(timeout=3))["type"] == "pong"
""",
    "just under the line": """
async def test_a(socket):
    await socket.receive_json_from(timeout=9.9)
""",
}

NOT_CAUGHT = {
    "default timeout": """
async def test_a(socket):
    await socket.receive_json_from()
""",
    "the absence window": """
async def test_a(socket):
    assert await socket.receive_nothing(timeout=0.1)
""",
    "a generous timeout": """
async def test_a(socket):
    await socket.receive_json_from(timeout=30)
""",
    "exactly the line": """
async def test_a(socket):
    await socket.receive_json_from(timeout=10)
""",
    "a threading event, not awaited": """
def test_a(release):
    release.wait(timeout=2)
""",
    "a named constant": """
async def test_a(socket):
    await socket.receive_json_from(timeout=WAIT_S)
""",
    "an unrelated awaited call with a timeout": """
async def test_a(socket):
    await asyncio.wait_for(socket.receive_json_from(), timeout=3)
""",
    "a disconnect with only its code": """
async def test_a(socket):
    await socket.disconnect(1000)
""",
    "an absence window under try/except": """
async def test_a(socket):
    try:
        await socket.receive_json_from(timeout=1)
    except Exception:
        pass
""",
    "an absence window under pytest.raises": """
async def test_a(socket):
    with pytest.raises(asyncio.TimeoutError):
        await socket.receive_output(timeout=0.5)
""",
    "an absence window under suppress": """
async def test_a(socket):
    with contextlib.suppress(asyncio.TimeoutError):
        await socket.receive_from(timeout=0.5)
""",
}

#: The same calls, but the guard is somewhere else: still caught.
STILL_CAUGHT = {
    "after the try block": """
async def test_a(socket):
    try:
        await socket.send_json_to({})
    except Exception:
        pass
    await socket.receive_json_from(timeout=3)
""",
    "in the except handler": """
async def test_a(socket):
    try:
        await socket.send_json_to({})
    except Exception:
        await socket.receive_json_from(timeout=3)
""",
    "in a try with only a finally": """
async def test_a(socket):
    try:
        await socket.receive_json_from(timeout=3)
    finally:
        await socket.disconnect()
""",
}


@pytest.mark.parametrize("source", CAUGHT.values(), ids=CAUGHT.keys())
def test_the_matcher_catches(source):
    assert len(find_short_waits(source)) == 1


@pytest.mark.parametrize("source", NOT_CAUGHT.values(), ids=NOT_CAUGHT.keys())
def test_the_matcher_leaves_alone(source):
    assert find_short_waits(source) == []


@pytest.mark.parametrize("source", STILL_CAUGHT.values(), ids=STILL_CAUGHT.keys())
def test_the_absence_exemption_is_only_for_the_guarded_call(source):
    assert len(find_short_waits(source)) == 1


def test_a_rename_of_the_receiver_and_the_test_is_still_caught():
    original = CAUGHT["keyword timeout"]
    renamed = original.replace("socket", "wire").replace("test_a", "test_zzz")
    assert renamed != original
    assert find_short_waits(original) == find_short_waits(renamed) != []


def test_a_new_short_timeout_in_a_clean_file_is_a_regression():
    assert "python/tests/test_x.py" in regressions({"python/tests/test_x.py": 1}, {})


def test_a_new_short_timeout_in_a_file_with_a_baseline_entry_is_a_regression():
    live, baseline = {"python/tests/test_x.py": 3}, {"python/tests/test_x.py": 2}
    assert "python/tests/test_x.py" in regressions(live, baseline)


def test_a_converted_file_must_lower_the_baseline():
    problems = regressions({"python/tests/test_x.py": 1}, {"python/tests/test_x.py": 2})
    assert "lower the baseline" in problems["python/tests/test_x.py"]


def test_a_renamed_file_fails_both_ways_so_the_baseline_moves_with_it():
    problems = regressions({"python/tests/test_new.py": 2}, {"python/tests/test_old.py": 2})
    assert set(problems) == {"python/tests/test_old.py", "python/tests/test_new.py"}


def test_equal_counts_pass():
    assert regressions({"a.py": 2}, {"a.py": 2}) == {}


def test_the_positions_are_a_pair_of_ints():
    waits = communicator_waits()
    assert all(isinstance(i, int) and i >= 0 for i in waits.values())
    pair: Tuple[str, int] = next(iter(waits.items()))
    assert isinstance(pair[0], str)
