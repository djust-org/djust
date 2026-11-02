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
``receive_nothing`` is for, or a ``receive_*`` in one of the absence idioms below.

This is the ratchet. ``BASELINE`` is data: the count may only fall, and the test fails
both ways, so a converted file cannot quietly regrow and a stale entry cannot linger.
The set of waits is read off Channels' communicator classes at run time, not listed (a
hand list lost ``connect`` once, #3692); ``receive_nothing`` is exempt by name.

WHAT IT COVERS (counted per file, against ``BASELINE``):

* ``await <x>.<communicator wait>(timeout=<number under SHORT_S>)``, as a keyword or in
  the timeout's positional slot (``await socket.receive_json_from(timeout=3)``,
  ``await comm.connect(2)``). The receiver's name is irrelevant: the METHOD makes it a
  communicator wait.
* a helper whose SHORT ``timeout`` default is forwarded to such a wait (``async def
  _receive_until(c, kind, *, timeout=3)``: every caller that says nothing gets 3 s), and
  a call, in the same file, to such a helper with a short numeric ``timeout``.

WHAT IT DELIBERATELY EXEMPTS (absence idioms, counted only in ``UNCOVERED_BASELINE``):

* ``receive_nothing``;
* the LAST statement of a ``with pytest.raises(...)`` / ``suppress(...)`` body;
* a wait in a ``try`` body whose handlers do nothing but ``pass`` / ``break`` /
  ``continue`` ("read until nothing more arrives"). A handler that re-raises, assigns,
  asserts or returns makes the wait a real one and it is counted. A ``pass`` handler is
  still a judgement the AST cannot make: ``try: receive_json_from(timeout=1) / except
  Exception: pass`` is an absence window when what arrives may be a ``websocket.close``
  (``test_ws_auth_close_socket.py``), and a plain wait when it drains a connect ack that
  always comes. #3721 converted the second kind and kept the first; a new one needs
  the same judgement in review.

WHAT IT DOES NOT SEE (totals in ``UNCOVERED_BASELINE``, which also only falls; derive the
live numbers with :func:`uncovered_counts`, the PR description quotes them):

* ``variable_timeouts``: a communicator wait whose ``timeout`` is a name or expression
  (``timeout=remaining``, ``timeout=deadline``, a helper's parameter). Whether it is
  short depends on a caller in another function or file, which this scan does not follow.
  A helper IMPORTED from another module and called with a short literal is not seen either.
* ``wait_for_short``: ``asyncio.wait_for(..., <10)`` and ``asyncio.timeout(<10)``, which
  are not communicator waits at all (often a bound on a non-frame event).
* ``**{"timeout": 3}``, ``getattr(sock, name)(timeout=1)``, and waits on objects that
  are not Channels communicators.
* ``test_ws_communicator_defaults_3464.py``'s own ``disconnect(timeout=0.01)`` is the one
  deliberate ``BASELINE`` entry.
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


ABSENCE_RAISES = "raises"  # the raising line of a ``with pytest.raises(...)`` / ``suppress``
ABSENCE_DRAIN = "drain"  # a ``try`` whose handlers only ``pass`` / ``break`` / ``continue``


def _absence_kind(await_node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> Optional[str]:
    """Which absence idiom a wait is in, if any.

    * ``raises``: the LAST statement of a ``with pytest.raises(...)`` / ``suppress(...)``
      body (the line that is supposed to time out; anything before it is a plain wait).
    * ``drain``: inside a ``try`` body whose handlers do nothing but ``pass``, ``break``
      or ``continue`` ("read until nothing more arrives", "drain a frame if one was
      sent"). A handler that re-raises, assigns, asserts or returns is a real handler,
      so the wait inside it is NOT an absence window and is counted.

    The walk stops at a function or lambda boundary: a ``def`` nested in a ``try`` body
    is not guarded by it.
    """
    node = await_node
    while node in parents:
        child, node = node, parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return None
        if isinstance(node, (ast.With, ast.AsyncWith)) and child in node.body:
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call):
                    func = ctx.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name in ("raises", "suppress") and child is node.body[-1]:
                        return ABSENCE_RAISES
        if isinstance(node, ast.Try) and child in node.body and node.handlers:
            only_nothing = all(
                all(isinstance(s, (ast.Pass, ast.Break, ast.Continue)) for s in h.body)
                for h in node.handlers
            )
            if only_nothing:
                return ABSENCE_DRAIN
    return None


def _timeout_args(call: ast.Call, index: int) -> List[ast.AST]:
    keyword = next((k.value for k in call.keywords if k.arg == "timeout"), None)
    positional = call.args[index] if index < len(call.args) else None
    return [v for v in (keyword, positional) if v is not None]


def _parse(source: str) -> Optional[Tuple[ast.AST, Dict[ast.AST, ast.AST]]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return tree, {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _forwarding_helpers(tree: ast.AST, waits: Dict[str, int]) -> Dict[str, ast.AST]:
    """``{name: def}`` of helpers that pass their ``timeout`` parameter on to a communicator
    wait (``_receive_until(c, kind, timeout=3)``)."""
    found: Dict[str, ast.AST] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "timeout" not in [a.arg for a in fn.args.args + fn.args.kwonlyargs]:
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in waits
                and any(
                    isinstance(v, ast.Name) and v.id == "timeout"
                    for v in _timeout_args(node, waits[node.func.attr])
                )
            ):
                found[fn.name] = fn
                break
    return found


def _short_timeout_default(fn: ast.AST) -> bool:
    args = fn.args  # type: ignore[attr-defined]
    names = [a.arg for a in args.args + args.kwonlyargs]
    defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
    defaults += list(args.kw_defaults)
    default = dict(zip(names, defaults)).get("timeout")
    return default is not None and _short_number(default)


def find_short_waits(source: str, waits: Optional[Dict[str, int]] = None) -> List[int]:
    """Line numbers of the short communicator timeouts the ratchet counts.

    Three shapes: an awaited communicator wait with a short numeric ``timeout`` outside
    an absence idiom; a helper whose short ``timeout`` default is forwarded to a
    communicator wait (the definition); and a call to such a helper in the same file
    that passes a short numeric ``timeout`` itself.
    """
    waits = communicator_waits() if waits is None else waits
    parsed = _parse(source)
    if parsed is None:
        return []
    tree, parents = parsed
    lines: Set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr not in waits:
            continue
        if _absence_kind(node, parents):
            continue
        if any(_short_number(v) for v in _timeout_args(call, waits[call.func.attr])):
            lines.add(call.lineno)
    helpers = _forwarding_helpers(tree, waits)
    for fn in helpers.values():
        if _short_timeout_default(fn):
            lines.add(fn.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in helpers and any(
                kw.arg == "timeout" and _short_number(kw.value) for kw in node.keywords
            ):
                lines.add(node.lineno)
    return sorted(lines)


def _count_uncovered(source: str, waits: Dict[str, int]) -> Dict[str, int]:
    """What the ratchet deliberately or unavoidably does not count, in one file."""
    out = {"absence_drains": 0, "absence_raises": 0, "variable_timeouts": 0, "wait_for_short": 0}
    parsed = _parse(source)
    if parsed is None:
        return out
    tree, parents = parsed
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr in waits:
                args = _timeout_args(call, waits[call.func.attr])
                kind = _absence_kind(node, parents)
                if kind and any(_short_number(v) for v in args):
                    out["absence_drains" if kind == ABSENCE_DRAIN else "absence_raises"] += 1
                elif args and not any(isinstance(v, ast.Constant) for v in args):
                    out["variable_timeouts"] += 1
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "wait_for":
                args = [a for a in node.args[1:2]] + [
                    k.value for k in node.keywords if k.arg == "timeout"
                ]
                if any(_short_number(a) for a in args):
                    out["wait_for_short"] += 1
            elif name == "timeout" and isinstance(func, ast.Attribute):
                if any(_short_number(a) for a in node.args):
                    out["wait_for_short"] += 1
    return out


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


def uncovered_counts() -> Dict[str, int]:
    """Totals, over the live tree, of the short waits the per-file ratchet does NOT count.

    ``absence_drains`` / ``absence_raises``: short waits in an absence idiom (exempt).
    ``variable_timeouts``: communicator waits whose ``timeout`` is a name or expression
    (a helper's parameter, ``remaining``, ``deadline``): whether it is short depends on
    a caller this scan does not follow. ``wait_for_short``: ``asyncio.wait_for(...,
    <10)`` and ``asyncio.timeout(<10)``, which are not communicator waits at all.
    """
    waits = communicator_waits()
    totals: Dict[str, int] = {}
    for path in _collected_test_files():
        for key, n in _count_uncovered(path.read_text(encoding="utf-8"), waits).items():
            totals[key] = totals.get(key, 0) + n
    return totals


#: What the ratchet does not see, as totals at the time it was written. These only fall
#: too: a class cannot grow quietly just because the per-file count above cannot see it.
UNCOVERED_BASELINE: Dict[str, int] = {
    "absence_drains": 6,
    "absence_raises": 2,
    "variable_timeouts": 57,
    "wait_for_short": 110,
}


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


def test_what_the_ratchet_does_not_see_only_falls_too():
    live = uncovered_counts()
    assert set(live) == set(UNCOVERED_BASELINE), live
    problems = {
        key: f"live {live[key]}, baseline {UNCOVERED_BASELINE[key]}"
        for key in live
        if live[key] != UNCOVERED_BASELINE[key]
    }
    assert problems == {}, (
        "The uncovered classes (see the module docstring) may only fall: a rise is a new "
        "short wait the per-file ratchet cannot count, a fall means lowering the "
        f"baseline. {problems}"
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
    "a helper whose short default is forwarded": """
async def _receive_until(communicator, wanted, *, tries=8, timeout=3):
    for _ in range(tries):
        frame = await communicator.receive_json_from(timeout=timeout)
        if frame.get("type") == wanted:
            return frame
""",
    "a call to a forwarding helper with a short timeout": """
async def _receive_until(communicator, wanted, *, tries=8, timeout=30):
    return await communicator.receive_json_from(timeout=timeout)

async def test_a(socket):
    await _receive_until(socket, "mount", timeout=3)
""",
    "a try whose handler re-raises": """
async def test_a(socket):
    try:
        await socket.receive_json_from(timeout=3)
    except Exception as exc:
        raise AssertionError("no frame") from exc
""",
    "a try whose handler assigns": """
async def test_a(socket):
    try:
        frame = await socket.receive_json_from(timeout=3)
    except Exception:
        frame = None
    assert frame
""",
    "a try inside a nested function": """
async def test_a(socket):
    try:
        async def later():
            await socket.receive_json_from(timeout=3)
    except Exception:
        pass
""",
    "an earlier statement of a raises block": """
async def test_a(socket):
    with pytest.raises(asyncio.TimeoutError):
        await socket.receive_json_from(timeout=3)
        await socket.receive_nothing()
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
    "a read-until-nothing loop (handler breaks)": """
async def test_a(socket):
    for _ in range(4):
        try:
            frame = await socket.receive_json_from(timeout=2)
        except Exception:
            break
""",
    "a helper whose long default is forwarded": """
async def _receive_until(communicator, wanted, *, tries=8, timeout=30):
    return await communicator.receive_json_from(timeout=timeout)
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
