"""Explicit short timeouts on Channels communicator waits only ever go down (#3709).

The root ``conftest.py`` gives every communicator wait a 30 s default (#3692), and
``test_ws_communicator_defaults_3464.py`` pins it. An explicit ``timeout=3`` on a
call overrides that default, so the wait is a 3 s wall-clock bound again: a runner
that stalls longer than that (a stop-the-world GC pause was measured at 3.38 s,
#3708; ``tests/timing_report.py`` now says when it happens, #3715) fails a test
whose code is fine. #3692's flake 1 failed on exactly such a call.

A wait for output that WILL arrive takes the default: ``await socket.receive_json_from()``
or ``djust.tests._ws_frames.receive_until`` (a 10 s deadline of its own, not the 30 s default)
for "until this frame". The one legitimate short timeout is a window for ABSENCE ("nothing
arrived within 0.1 s"), which ``receive_nothing`` is for, or a ``receive_*`` on the last line of
a ``pytest.raises`` block.

This is the ratchet. ``BASELINE`` is data: the count may only fall, and the test fails
both ways, so a converted file cannot quietly regrow and a stale entry cannot linger.
``BASELINE_REASONS`` carries the reason each entry is allowed, and a test pins the two to the
same files, so no prose here needs to say how many there are. The set of waits is read off
Channels' communicator classes at run time, not listed (a hand list lost ``connect`` once,
#3692); ``receive_nothing`` is exempt by name.

WHAT IT COVERS (counted per file, against ``BASELINE``). A timeout "reads as short" when it is a
number under ``SHORT_S``: a literal, arithmetic on literals (``1 * 2``), a module-level
constant bound to one (``T = 2``), or the value under ``"timeout"`` in a ``**{...}`` display.
A module constant is NOT read where a function around the call rebinds the name (a parameter, an
assignment, a ``for`` / ``with`` / ``except`` target): that ``T`` is the function's own (#3737).

* ``await <x>.<communicator wait>(timeout=<short>)``, as a keyword or in the timeout's
  positional slot (``await socket.receive_json_from(timeout=3)``, ``await comm.connect(2)``).
  The receiver's name is irrelevant: the METHOD makes it a communicator wait.
* ``await asyncio.wait_for(<communicator wait>, <short>)`` and ``async with
  asyncio.timeout(<short>)`` around one, counted once at the wrapper. ``from asyncio import
  timeout`` / ``wait_for`` (and an ``as`` alias) are the same calls; ``asyncio.shield(<wait>)``,
  ``ensure_future(<wait>)`` / ``create_task(<wait>)`` and a variable assigned from one, in the
  same function, are looked through (#3737).
* ``wait`` is the one communicator method that is also the name of ``asyncio.Event.wait`` and
  ``asyncio.wait``. ``await <x>.wait(timeout=<short>)`` is counted unless ``<x>`` is ``asyncio``
  (``asyncio.wait({task}, timeout=1)`` bounds tasks, not a frame). Wrapped in ``wait_for`` /
  ``timeout``, ``<x>.wait()`` is counted only when the file binds ``<x>`` to a communicator
  (``comm = WebsocketCommunicator(...)``, ``async with ... as comm``, or an annotation naming
  one): a fixture's ``wait()`` is not known to be one and is in ``wait_for_short`` (#3737).
* a helper whose SHORT ``timeout`` default is forwarded to such a wait (``async def
  _receive_until(c, kind, *, timeout=3)``: every caller that says nothing gets 3 s), and
  a call, in the same file, to such a helper that passes a short ``timeout`` as a keyword or
  in the helper's positional slot (``_recv(comm, 2)``). A helper forwards its ``timeout`` when it
  reaches the wait directly OR through a deadline built from it (``deadline = loop.time() +
  timeout`` ... ``receive_json_from(timeout=deadline - loop.time())``): the first version read only
  the bare name and missed ``_frames_until(..., timeout=5)`` and two ``_reply(..., timeout=3.0)``.

A ``try: ... except: pass`` / ``break`` / ``continue`` DRAIN gets no exemption of its own
(#3726): a drain is counted by the shapes above like any other wait. #3721 exempted them as
"absence-shaped", and six were kept on that basis; four of the six read until a ``mount`` /
close / navigation frame that DOES arrive in the positive case and then assert it did, so a
runner that stalled past their 2-3 s window failed 68 tests (a 3.5 s delay on every outgoing
frame reproduces it). Whether a drain is an absence window is a judgement the AST cannot make,
so the genuine ones sit in ``BASELINE`` with their reason beside them. A new one therefore fails
this test until it either waits for the frame that arrives (``receive_until`` /
``receive_settled``), asserts the silence with ``receive_nothing``, or is added to ``BASELINE``
with a reason a reviewer accepts.

WHAT IT READS: every ``test_*.py`` / ``*_test.py`` under ``python/`` and ``tests/``, and every
module in a ``tests`` directory (``djust/tests/_ws_frames.py``, ``tests/conftest.py``): a
helper's short wait reaches every test that calls it (#3709).

WHAT IT DELIBERATELY EXEMPTS (counted only in ``UNCOVERED_BASELINE``):

* ``receive_nothing``;
* the LAST statement of a ``with pytest.raises(...)`` / ``suppress(...)`` body (the line
  that is supposed to time out), whether a direct wait or one wrapped in ``wait_for``.

WHAT IT DOES NOT SEE (totals in ``UNCOVERED_BASELINE``, which also only falls; derive the
live numbers with :func:`uncovered_counts`, the PR description quotes them):

* ``variable_timeouts``: a communicator wait whose ``timeout`` cannot be read as a number
  (``timeout=remaining``, a helper's parameter, a call). Whether it is short depends on a
  caller in another function or file, which this scan does not follow. A helper IMPORTED from
  another module and called with a short literal is not seen either.
* ``wait_for_short``: ``asyncio.wait_for(..., <10)`` and ``asyncio.timeout(<10)`` that do NOT
  wrap a communicator wait (a bound on an event, a queue, a task). Those are not frame waits.
* ``getattr(sock, name)(timeout=1)``, a wait reached through a variable
  (``wait = comm.receive_output; await wait(timeout=1)``), a ``task`` variable assigned in
  another function or reassigned conditionally in a way a plain scan cannot order, and waits on
  objects that are not Channels communicators.
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

#: Communicator methods that, when WRAPPED in ``asyncio.wait_for`` / ``asyncio.timeout``,
#: are more likely something else with the same name (``asyncio.Event.wait``).
WRAPPED_NOT_FRAME_WAITS = {"wait"}

#: A positional index no call reaches (a helper whose ``timeout`` is keyword-only).
_NO_SLOT = 10**6

#: Test files that still carry short communicator timeouts, and how many. A count only
#: ever goes DOWN; an entry that reaches zero is deleted. Keyed by repo-relative path. Each
#: entry has a reason in ``BASELINE_REASONS`` (pinned by a test), and is on purpose.
BASELINE: Dict[str, int] = {
    "python/djust/tests/test_multi_view_render_locks_3252.py": 2,
    "python/djust/tests/test_ws_auth_close_socket.py": 1,
    "python/djust/tests/test_ws_communicator_defaults_3464.py": 1,
}

BASELINE_REASONS: Dict[str, str] = {
    "python/djust/tests/test_multi_view_render_locks_3252.py": (
        "Two strict xfails (BLOCKED_SYNC_THREAD) whose expected outcome IS that the reply does "
        "not arrive: `_reply(..., timeout=3.0)` is the window in which it must not. A stall "
        "can only make that window end with no frame, which is the xfail; it cannot turn the "
        "expected failure into a pass. Lengthening it to PATIENCE would make each take 20 s to "
        "fail. Found when the scan learned to read a deadline derived from `timeout` (#3709)."
    ),
    "python/djust/tests/test_ws_auth_close_socket.py": (
        "An absence window: after the 4403 redirect what arrives is a close, which "
        "receive_json_from rejects, and the assertion is that no event handler ran. A stall "
        "can end the window early and miss a late frame; it cannot fail a correct run."
    ),
    "python/djust/tests/test_ws_communicator_defaults_3464.py": (
        "Pins that an explicit timeout STILL WINS over the 30 s default, so it passes "
        "timeout=0.01 to disconnect (converting it made that test fail, which is how it "
        "was found)."
    ),
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


Consts = Dict[str, float]


def _number(node: ast.AST, consts: Consts) -> Optional[float]:
    """The numeric value of a timeout expression, if it can be read off the source.

    A number, a module-level constant bound to one, ``-x``, and ``+ - * / //`` of those
    (``timeout=1 * 2``, ``timeout=T``). Anything else (a parameter, a call, an attribute)
    is ``None``: whether it is short depends on a caller this scan does not follow.
    """
    if isinstance(node, ast.Constant):
        value = node.value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        return None
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _number(node.operand, consts)
        if inner is None:
            return None
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp):
        left, right = _number(node.left, consts), _number(node.right, consts)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
        except ZeroDivisionError:
            return None
    return None


def _short_number(
    node: ast.AST, consts: Consts, parents: Optional[Dict[ast.AST, ast.AST]] = None
) -> bool:
    if parents is not None:
        consts = _scoped_consts(node, consts, parents)
    value = _number(node, consts)
    return value is not None and value < SHORT_S


def _function_bindings(fn: ast.AST) -> Set[str]:
    """Names a function (or lambda) binds in ITS OWN scope: parameters, assignment, ``for`` /
    ``with`` / ``except`` targets, imports and nested ``def`` / ``class`` names. Not what a
    nested function binds, and not a name declared ``global``."""
    args = fn.args  # type: ignore[attr-defined]
    names = {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
    names.update(a.arg for a in (args.vararg, args.kwarg) if a is not None)
    if isinstance(fn, ast.Lambda):
        return names
    declared_global: Set[str] = set()
    stack: List[ast.AST] = list(fn.body)  # type: ignore[attr-defined]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            continue
        if isinstance(node, ast.Lambda):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.Global):
            declared_global.update(node.names)
        stack.extend(ast.iter_child_nodes(node))
    return names - declared_global


def _scoped_consts(node: ast.AST, consts: Consts, parents: Dict[ast.AST, ast.AST]) -> Consts:
    """``consts`` without the names a function around ``node`` rebinds.

    ``T = 2`` at module level is not the ``T`` of ``def f(socket, T)`` or of a ``T`` assigned
    inside the function (#3737). A default value is evaluated outside the function, so it is
    not inside this scope.
    """
    if not consts or not any(isinstance(n, ast.Name) and n.id in consts for n in ast.walk(node)):
        return consts
    shadowed: Set[str] = set()
    child = node
    while child in parents:
        parent = parents[child]
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and child in parent.body:
            shadowed |= _function_bindings(parent)
        elif isinstance(parent, ast.Lambda) and child is parent.body:
            shadowed |= _function_bindings(parent)
        child = parent
    if not shadowed & consts.keys():
        return consts
    return {k: v for k, v in consts.items() if k not in shadowed}


def _module_constants(tree: ast.AST) -> Consts:
    """``NAME = <number>`` at module level (``T = 2``; later ones may build on earlier)."""
    consts: Consts = {}
    for stmt in getattr(tree, "body", []):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if isinstance(target, ast.Name):
            number = _number(value, consts)
            if number is not None:
                consts[target.id] = number
    return consts


def _in_raises_block(await_node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> bool:
    """Is the wait the LAST statement of a ``with pytest.raises(...)`` / ``suppress(...)`` body?

    That is the line that is supposed to time out; anything before it is a plain wait. A
    ``try`` / ``except: pass`` drain is deliberately NOT an exemption (#3726, see the module
    docstring).

    The walk stops at a function or lambda boundary: a ``def`` nested in a ``with`` body
    is not guarded by it.
    """
    node = await_node
    while node in parents:
        child, node = node, parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return False
        if isinstance(node, (ast.With, ast.AsyncWith)) and child in node.body:
            for item in node.items:
                ctx = item.context_expr
                if isinstance(ctx, ast.Call):
                    func = ctx.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                    if name in ("raises", "suppress") and child is node.body[-1]:
                        return True
    return False


def _timeout_args(call: ast.Call, index: int) -> List[ast.AST]:
    """Every expression that can be the call's timeout.

    The keyword, the positional slot, and the value under ``"timeout"`` in a ``**{...}`` display.
    """
    found = [k.value for k in call.keywords if k.arg == "timeout"]
    if index < len(call.args):
        found.append(call.args[index])
    for keyword in call.keywords:
        if keyword.arg is None and isinstance(keyword.value, ast.Dict):
            for key, value in zip(keyword.value.keys, keyword.value.values):
                if isinstance(key, ast.Constant) and key.value == "timeout":
                    found.append(value)
    return found


def _parse(source: str) -> Optional[Tuple[ast.AST, Dict[ast.AST, ast.AST]]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    return tree, {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _call_name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _names_derived_from_timeout(fn: ast.AST) -> Set[str]:
    """``timeout`` and every local name assigned from an expression that mentions one of them.

    A helper that turns its ``timeout`` into a deadline (``deadline = loop.time() + timeout``,
    then ``receive_json_from(timeout=deadline - loop.time())``) passes the caller's number on
    just as ``timeout=timeout`` does, but never names it at the wait (#3709: the ratchet read
    only the bare name and so missed ``_frames_until(..., timeout=5)``).
    """
    derived = {"timeout"}
    assignments = [
        (target, node.value)
        for node in ast.walk(fn)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and node.value is not None
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Name)
    ]
    changed = True
    while changed:
        changed = False
        for target, value in assignments:
            if target.id not in derived and any(
                isinstance(n, ast.Name) and n.id in derived for n in ast.walk(value)
            ):
                derived.add(target.id)
                changed = True
    return derived


def _forwarding_helpers(tree: ast.AST, waits: Dict[str, int]) -> Dict[str, ast.AST]:
    """``{name: def}`` of helpers that pass their ``timeout`` parameter on to a communicator
    wait (``_receive_until(c, kind, timeout=3)``), directly or through a deadline derived
    from it."""
    found: Dict[str, ast.AST] = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "timeout" not in [a.arg for a in fn.args.args + fn.args.kwonlyargs]:
            continue
        derived = _names_derived_from_timeout(fn)
        awaited = {n.value for n in ast.walk(fn) if isinstance(n, ast.Await)}
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in waits
                # ``wait`` is also ``threading.Condition.wait``; a communicator's is a coroutine.
                and (node.func.attr not in WRAPPED_NOT_FRAME_WAITS or node in awaited)
                and any(
                    isinstance(n, ast.Name) and n.id in derived
                    for v in _timeout_args(node, waits[node.func.attr])
                    for n in ast.walk(v)
                )
            ):
                found[fn.name] = fn
                break
    return found


def _timeout_slot(fn: ast.AST) -> Optional[int]:
    """The positional index of a helper's ``timeout`` parameter as a CALLER counts it
    (``self`` / ``cls`` are not passed), or ``None`` when it is keyword-only."""
    names = [a.arg for a in fn.args.args]  # type: ignore[attr-defined]
    if "timeout" not in names:
        return None
    index = names.index("timeout")
    return index - 1 if names and names[0] in ("self", "cls") else index


def _short_timeout_default(
    fn: ast.AST, consts: Consts, parents: Optional[Dict[ast.AST, ast.AST]] = None
) -> bool:
    args = fn.args  # type: ignore[attr-defined]
    names = [a.arg for a in args.args + args.kwonlyargs]
    defaults = [None] * (len(args.args) - len(args.defaults)) + list(args.defaults)
    defaults += list(args.kw_defaults)
    default = dict(zip(names, defaults)).get("timeout")
    return default is not None and _short_number(default, consts, parents)


def _terminal_name(node: ast.AST) -> str:
    """``comm`` for ``comm`` and ``self.comm`` alike: the last name of a receiver."""
    if isinstance(node, ast.Attribute):
        return node.attr
    return getattr(node, "id", "")


def _is_communicator_annotation(node: Optional[ast.AST]) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.endswith("Communicator")
    return node is not None and _terminal_name(node).endswith("Communicator")


def _communicator_names(tree: ast.AST) -> Set[str]:
    """Names this file binds to a Channels communicator, so ``<name>.wait()`` is known to be
    ``ApplicationCommunicator.wait`` and not ``asyncio.Event.wait`` (#3737).

    ``comm = WebsocketCommunicator(...)``, ``self.comm = ...``, ``async with
    HttpCommunicator(...) as comm``, and a parameter or variable annotated
    ``WebsocketCommunicator``. Only what the file says: a receiver that arrives as a fixture is
    not known, and a ``wait()`` on it stays out of the per-file count (it is in
    ``wait_for_short``).
    """
    found: Set[str] = set()

    def bind(target: ast.AST) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                bind(element)
        else:
            name = _terminal_name(target)
            if name:
                found.add(name)

    def builds_one(value: Optional[ast.AST]) -> bool:
        return isinstance(value, ast.Call) and _call_name(value).endswith("Communicator")

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and builds_one(node.value):
            for target in node.targets:
                bind(target)
        elif isinstance(node, ast.AnnAssign):
            if _is_communicator_annotation(node.annotation) or builds_one(node.value):
                bind(node.target)
        elif isinstance(node, ast.arg):
            if _is_communicator_annotation(node.annotation):
                found.add(node.arg)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None and builds_one(item.context_expr):
                    bind(item.optional_vars)
    return found


def _asyncio_aliases(tree: ast.AST) -> Dict[str, str]:
    """``{local name: asyncio name}`` for ``from asyncio import timeout as limit`` (#3737).

    A bare ``timeout(2)`` or ``wait_for(...)`` is ``asyncio``'s only when the file imported it
    from there: ``timeout`` is also an ordinary variable.
    """
    found: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in ("asyncio", "asyncio.timeouts"):
            for alias in node.names:
                found[alias.asname or alias.name] = alias.name
    return found


def _asyncio_name(call: ast.Call, aliases: Dict[str, str]) -> str:
    """The ``asyncio`` function a call is, by name: ``wait_for`` for ``asyncio.wait_for(...)``
    and for ``bounded(...)`` after ``from asyncio import wait_for as bounded``. An attribute is
    taken by its name; a bare name only through an import."""
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return aliases.get(func.id, func.id if func.id == "wait_for" else "")
    return ""


#: Calls that hand back the SAME awaitable (or a task running it), so a ``wait_for`` around one
#: bounds the wait inside it: ``asyncio.shield(<wait>)``, ``asyncio.ensure_future(<wait>)``,
#: ``asyncio.create_task(<wait>)`` / ``loop.create_task(<wait>)``.
_TRANSPARENT_WRAPPERS = {"shield", "ensure_future", "create_task"}


def _enclosing_scope(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> ast.AST:
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Module)):
            return node
    return node


def _scope_assignments(scope: ast.AST) -> Dict[str, List[ast.AST]]:
    """``{name: [value, ...]}`` for ``name = <value>`` directly in one scope, not in a nested
    function: how ``task = create_task(<wait>)`` is resolved at ``wait_for(task, 2)``."""
    found: Dict[str, List[ast.AST]] = {}
    body = getattr(scope, "body", [])
    stack: List[ast.AST] = list(body) if isinstance(body, list) else [body]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    found.setdefault(target.id, []).append(node.value)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _bounded_calls(
    expr: ast.AST, scope_assignments: Dict[str, List[ast.AST]], depth: int = 0
) -> List[ast.Call]:
    """The calls ``expr`` stands for once ``shield`` / ``ensure_future`` / ``create_task`` and a
    variable assigned from one are looked through (#3737)."""
    if depth > 3:
        return []
    if isinstance(expr, ast.Call):
        if _call_name(expr) in _TRANSPARENT_WRAPPERS and expr.args:
            return _bounded_calls(expr.args[0], scope_assignments, depth + 1)
        return [expr]
    if isinstance(expr, ast.Name):
        found: List[ast.Call] = []
        for value in scope_assignments.get(expr.id, []):
            found.extend(_bounded_calls(value, scope_assignments, depth + 1))
        return found
    return []


def _is_communicator_wait(
    node: ast.AST,
    waits: Dict[str, int],
    helpers: Dict[str, ast.AST],
    communicators: Optional[Set[str]] = None,
) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = _call_name(node)
    if isinstance(node.func, ast.Attribute) and name in waits:
        if name in WRAPPED_NOT_FRAME_WAITS:
            # ``wait`` is Channels' generic "wait for the application" and is also the name of
            # ``asyncio.Event.wait`` and ``asyncio.wait``: it is a frame wait only on a receiver
            # the file binds to a communicator (#3737).
            return _terminal_name(node.func.value) in (communicators or set())
        return True
    return name in helpers


def _wrapped_waits(
    wrapped: ast.AST,
    parents: Dict[ast.AST, ast.AST],
    waits: Dict[str, int],
    helpers: Dict[str, ast.AST],
    communicators: Set[str],
) -> List[ast.Call]:
    """The communicator waits a ``wait_for`` argument stands for."""
    scope = _enclosing_scope(wrapped, parents)
    return [
        call
        for call in _bounded_calls(wrapped, _scope_assignments(scope))
        if _is_communicator_wait(call, waits, helpers, communicators)
    ]


def _is_task_set_wait(call: ast.Call) -> bool:
    """``asyncio.wait({task}, timeout=1)`` bounds a set of tasks: it is not a frame wait, though
    ``wait`` is also the name of ``ApplicationCommunicator.wait`` (#3737)."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr in WRAPPED_NOT_FRAME_WAITS):
        return False
    root: ast.AST = func.value
    while isinstance(root, ast.Attribute):
        root = root.value
    return isinstance(root, ast.Name) and root.id in ("asyncio", "futures", "concurrent")


def _awaits_in(body: List[ast.stmt]) -> List[ast.Await]:
    """Every ``await`` in ``body``, not descending into nested functions."""
    found: List[ast.Await] = []
    stack: List[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Await):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _is_timeout_block(ctx: ast.AST, aliases: Dict[str, str]) -> bool:
    """``asyncio.timeout(...)``, or ``timeout(...)`` / an alias imported from ``asyncio``."""
    if not isinstance(ctx, ast.Call):
        return False
    func = ctx.func
    if isinstance(func, ast.Attribute):
        return func.attr == "timeout"
    return isinstance(func, ast.Name) and aliases.get(func.id) == "timeout"


def _scan(tree: ast.AST, parents: Dict[ast.AST, ast.AST], waits: Dict[str, int]):
    """The sites the per-file ratchet counts: ``(counted, inner)``.

    ``counted`` are AST nodes (one site each, located by ``lineno``); ``inner`` are the
    communicator-wait calls that a counted ``asyncio.wait_for`` / ``asyncio.timeout``
    wrapper already accounts for, so the same wait is not counted twice.
    """
    consts = _module_constants(tree)
    helpers = _forwarding_helpers(tree, waits)
    communicators = _communicator_names(tree)
    aliases = _asyncio_aliases(tree)
    counted: List[ast.AST] = []
    inner: Set[ast.AST] = set()

    for node in ast.walk(tree):
        # ``await asyncio.wait_for(<communicator wait>, <short>)``, through ``shield`` /
        # ``create_task`` / a task variable (#3737)
        if isinstance(node, ast.Call) and node.args and _asyncio_name(node, aliases) == "wait_for":
            bound = node.args[1:2] + [k.value for k in node.keywords if k.arg == "timeout"]
            waited = _wrapped_waits(node.args[0], parents, waits, helpers, communicators)
            if (
                waited
                and any(_short_number(b, consts, parents) for b in bound)
                and not _in_raises_block(node, parents)
            ):
                counted.append(node)
                inner.update(waited)
        # ``async with asyncio.timeout(<short>): ... await <communicator wait> ...``
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                ctx = item.context_expr
                if not (
                    _is_timeout_block(ctx, aliases)
                    and any(_short_number(a, consts, parents) for a in ctx.args)  # type: ignore[attr-defined]
                    and not _in_raises_block(node, parents)
                ):
                    continue
                waited = [
                    a.value
                    for a in _awaits_in(node.body)
                    if _is_communicator_wait(a.value, waits, helpers, communicators)
                ]
                if waited:
                    counted.append(ctx)
                    inner.update(waited)

    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            call = node.value
            if call in inner:
                continue
            if not isinstance(call.func, ast.Attribute) or call.func.attr not in waits:
                continue
            if _is_task_set_wait(call):
                continue
            if _in_raises_block(node, parents):
                continue
            args = _timeout_args(call, waits[call.func.attr])
            if any(_short_number(v, consts, parents) for v in args):
                counted.append(call)
    for fn in helpers.values():
        if _short_timeout_default(fn, consts, parents):
            counted.append(fn)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node not in inner and _call_name(node) in helpers:
            slot = _timeout_slot(helpers[_call_name(node)])
            args = _timeout_args(node, _NO_SLOT if slot is None else slot)
            if any(_short_number(v, consts, parents) for v in args):
                counted.append(node)
    return counted, inner


def find_short_waits(source: str, waits: Optional[Dict[str, int]] = None) -> List[int]:
    """Line numbers of the short communicator timeouts the ratchet counts.

    * an awaited communicator wait whose ``timeout`` READS AS a number under ``SHORT_S``:
      a literal, ``1 * 2``, a module constant, ``**{"timeout": 2}``, as a keyword or in the
      timeout's positional slot, unless it is the last line of a ``pytest.raises`` block;
    * a helper whose short ``timeout`` default is forwarded to a communicator wait (the
      definition), and a call to such a helper in the same file that passes a short
      ``timeout``, as a keyword or in the helper's positional slot;
    * ``asyncio.wait_for(<communicator wait>, <short>)`` and ``async with
      asyncio.timeout(<short>)`` around a communicator wait (counted once, at the wrapper).
    """
    waits = communicator_waits() if waits is None else waits
    parsed = _parse(source)
    if parsed is None:
        return []
    tree, parents = parsed
    counted, _ = _scan(tree, parents, waits)
    return sorted({node.lineno for node in counted})  # type: ignore[attr-defined]


def _count_uncovered(source: str, waits: Dict[str, int]) -> Dict[str, int]:
    """What the ratchet deliberately or unavoidably does not count, in one file."""
    out = {"absence_raises": 0, "variable_timeouts": 0, "wait_for_short": 0}
    parsed = _parse(source)
    if parsed is None:
        return out
    tree, parents = parsed
    consts = _module_constants(tree)
    aliases = _asyncio_aliases(tree)
    counted, inner = _scan(tree, parents, waits)
    accounted = set(counted) | inner
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            call = node.value
            if call in accounted:
                continue
            if (
                isinstance(call.func, ast.Attribute)
                and call.func.attr in waits
                and not _is_task_set_wait(call)
            ):
                args = _timeout_args(call, waits[call.func.attr])
                short = any(_short_number(v, consts, parents) for v in args)
                if _in_raises_block(node, parents) and short:
                    out["absence_raises"] += 1
                elif args and all(
                    _number(v, _scoped_consts(v, consts, parents)) is None for v in args
                ):
                    out["variable_timeouts"] += 1
        if isinstance(node, ast.Call) and node not in accounted:
            name = _asyncio_name(node, aliases)
            if name == "wait_for":
                args = [a for a in node.args[1:2]] + [
                    k.value for k in node.keywords if k.arg == "timeout"
                ]
                if any(_short_number(a, consts, parents) for a in args):
                    out["wait_for_short"] += 1
            elif name == "timeout" and _is_timeout_block(node, aliases):
                if any(_short_number(a, consts, parents) for a in node.args):
                    out["wait_for_short"] += 1
    return out


def _collected_test_files() -> List[Path]:
    """The test files pytest collects, and the helper modules in a ``tests`` directory they call.

    A helper (``djust/tests/_ws_frames.py``, a ``conftest.py``) is where a short wait reaches every
    test that uses it, and it is not named ``test_*``: its two ``receive_output(timeout=3)`` sat
    outside the ratchet while every test file was converted (#3709).
    """
    files = []
    for root in ROOTS:
        for path in (REPO / root).rglob("*.py"):
            parts = path.relative_to(REPO).parts
            if SKIP_PARTS & set(parts):
                continue
            if (
                path.name.startswith("test_")
                or path.name.endswith("_test.py")
                or "tests" in parts[:-1]
            ):
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

    ``absence_raises``: short waits on the last line of a ``pytest.raises`` block (exempt).
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
    "absence_raises": 2,
    "variable_timeouts": 52,
    "wait_for_short": 104,
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


def test_every_baseline_entry_has_a_reason_and_every_reason_an_entry():
    assert set(BASELINE_REASONS) == set(BASELINE)
    assert all(reason.strip() for reason in BASELINE_REASONS.values())


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
    assert "python/djust/tests/_ws_frames.py" in paths, "a helper module is not scanned"
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
    "a helper that forwards its timeout as a deadline, called with a short one (#3709)": """
async def _frames_until(communicator, *types, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        frame = await communicator.receive_json_from(timeout=deadline - loop.time())
        if frame.get("type") in types:
            return frame

async def test_a(socket):
    await _frames_until(socket, "patch", timeout=5)
""",
    "a deadline derived through two assignments, called positionally (#3709)": """
async def _until(communicator, kind, timeout):
    budget = timeout * 2
    deadline = budget + 1
    return await communicator.receive_json_from(timeout=deadline)

async def test_a(socket):
    await _until(socket, "patch", 2)
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
    "a try/except-pass drain (#3726)": """
async def test_a(socket):
    try:
        await socket.receive_json_from(timeout=1)
    except Exception:
        pass
""",
    "a read-until-it-times-out loop whose handler breaks (#3726)": """
async def test_a(socket):
    for _ in range(4):
        try:
            frame = await socket.receive_json_from(timeout=2)
        except Exception:
            break
""",
    "a loop whose handler continues (#3726)": """
async def test_a(socket):
    for _ in range(4):
        try:
            frame = await socket.receive_output(timeout=2)
        except asyncio.TimeoutError:
            continue
""",
    "the drain this ratchet exists for: a loop that waits for a mount that arrives (#3726)": """
async def _ws(communicator):
    outputs = []
    try:
        while True:
            out = await communicator.receive_output(timeout=2)
            outputs.append(out)
            if '"type": "mount"' in (out.get("text") or ""):
                break
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass
    return outputs
""",
    "a helper called with a positional short literal (#3726)": """
async def _recv(communicator, timeout):
    return await communicator.receive_json_from(timeout=timeout)

async def test_a(socket):
    await _recv(socket, 2)
""",
    "a method helper called with a positional short literal (#3726)": """
class T:
    async def _recv(self, communicator, timeout):
        return await communicator.receive_json_from(timeout=timeout)

    async def test_a(self, socket):
        await self._recv(socket, 2)
""",
    "arithmetic on literals (#3726)": """
async def test_a(socket):
    await socket.receive_output(timeout=1 * 2)
""",
    "a module constant (#3726)": """
T = 2

async def test_a(socket):
    await socket.receive_output(timeout=T)
""",
    "a module constant built from another (#3726)": """
BASE = 1
T = BASE + 1

async def test_a(socket):
    await socket.receive_output(timeout=T)
""",
    "a ** display carrying the timeout (#3726)": """
async def test_a(socket):
    await socket.receive_output(**{"timeout": 2})
""",
    "asyncio.wait_for with a positional bound (#3726)": """
async def test_a(socket):
    await asyncio.wait_for(socket.receive_output(), 2)
""",
    "asyncio.wait_for with a keyword bound (#3726)": """
async def test_a(socket):
    await asyncio.wait_for(socket.receive_json_from(), timeout=3)
""",
    "asyncio.wait_for around a wait that is short itself, counted once (#3726)": """
async def test_a(socket):
    await asyncio.wait_for(socket.receive_from(timeout=1), timeout=1)
""",
    "asyncio.wait_for around disconnect (#3726)": """
async def test_a(socket):
    await asyncio.wait_for(socket.disconnect(), timeout=1)
""",
    "asyncio.wait_for around a forwarding helper (#3726)": """
async def _recv(communicator, *, timeout=30):
    return await communicator.receive_json_from(timeout=timeout)

async def test_a(socket):
    await asyncio.wait_for(_recv(socket), 2)
""",
    "an asyncio.timeout block around a wait (#3726)": """
async def test_a(socket):
    async with asyncio.timeout(2):
        await socket.receive_output()
""",
    "the security-import drain shape: wait_for in a try that breaks (#3726)": """
async def test_a(comm):
    for _ in range(3):
        try:
            await asyncio.wait_for(comm.receive_from(), timeout=1)
        except BaseException:
            break
""",
    "just under the line": """
async def test_a(socket):
    await socket.receive_json_from(timeout=9.9)
""",
    # --- the blind spots the PR #3732 re-review found (#3737) ---------------------------
    "wait_for around wait() on a communicator the file constructs (#3737)": """
async def test_a(app):
    comm = WebsocketCommunicator(app, "/ws/")
    await asyncio.wait_for(comm.wait(), 1)
""",
    "wait_for around wait() on an attribute bound to a communicator (#3737)": """
class T:
    async def setup(self, app):
        self.comm = WebsocketCommunicator(app, "/ws/")

    async def test_a(self):
        await asyncio.wait_for(self.comm.wait(), timeout=1)
""",
    "wait_for around wait() on a parameter annotated as a communicator (#3737)": """
async def test_a(comm: WebsocketCommunicator):
    await asyncio.wait_for(comm.wait(), 1)
""",
    "wait_for around wait() on a communicator opened by async with (#3737)": """
async def test_a(app):
    async with HttpCommunicator(app, "GET", "/") as comm:
        await asyncio.wait_for(comm.wait(), 1)
""",
    "an asyncio.timeout block around wait() on a constructed communicator (#3737)": """
async def test_a(app):
    comm = WebsocketCommunicator(app, "/ws/")
    async with asyncio.timeout(2):
        await comm.wait()
""",
    "from asyncio import timeout, then async with timeout(2) (#3737)": """
from asyncio import timeout

async def test_a(socket):
    async with timeout(2):
        await socket.receive_output()
""",
    "from asyncio import timeout as an alias (#3737)": """
from asyncio import timeout as limit

async def test_a(socket):
    async with limit(2):
        await socket.receive_output()
""",
    "from asyncio import wait_for as an alias (#3737)": """
from asyncio import wait_for as bounded

async def test_a(socket):
    await bounded(socket.receive_output(), 2)
""",
    "wait_for around asyncio.shield around a wait (#3737)": """
async def test_a(socket):
    await asyncio.wait_for(asyncio.shield(socket.receive_output()), 2)
""",
    "wait_for around a task variable made from a wait (#3737)": """
async def test_a(socket):
    task = asyncio.ensure_future(socket.receive_output())
    await asyncio.wait_for(task, 2)
""",
    "wait_for around a task variable made by create_task (#3737)": """
async def test_a(socket):
    task = asyncio.create_task(socket.receive_json_from())
    frame = await asyncio.wait_for(task, timeout=3)
""",
    "wait_for around a coroutine variable (#3737)": """
async def test_a(socket):
    pending = socket.receive_output()
    await asyncio.wait_for(pending, 2)
""",
    "wait_for around a shielded task variable (#3737)": """
async def test_a(socket):
    task = asyncio.create_task(socket.receive_output())
    await asyncio.wait_for(asyncio.shield(task), 2)
""",
    "a module constant used where another function shadows it (#3737)": """
T = 2

async def other(socket, T):
    await socket.receive_output(timeout=T)

async def test_a(socket):
    await socket.receive_output(timeout=T)
""",
    "a module constant that a function assigns through a global declaration (#3737)": """
T = 2

async def test_a(socket):
    global T
    T = 2
    await socket.receive_output(timeout=T)
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
    "asyncio.wait_for around an Event, not a frame (#3726)": """
async def test_a(done):
    await asyncio.wait_for(done.wait(), 1)
""",
    "asyncio.wait_for around a queue (#3726)": """
async def test_a(queue):
    await asyncio.wait_for(queue.get(), 1)
""",
    "asyncio.wait_for with a generous bound (#3726)": """
async def test_a(socket):
    await asyncio.wait_for(socket.receive_output(), 30)
""",
    "an asyncio.timeout block around something that is not a frame (#3726)": """
async def test_a(queue):
    async with asyncio.timeout(2):
        await queue.get()
""",
    "a module constant that is generous (#3726)": """
T = 30

async def test_a(socket):
    await socket.receive_output(timeout=T)
""",
    "arithmetic that is generous (#3726)": """
async def test_a(socket):
    await socket.receive_output(timeout=10 * 3)
""",
    "a helper called with a generous positional literal (#3726)": """
async def _recv(communicator, timeout):
    return await communicator.receive_json_from(timeout=timeout)

async def test_a(socket):
    await _recv(socket, 30)
""",
    "a timeout that is a parameter, not readable as a number (#3726)": """
async def test_a(socket, remaining):
    await socket.receive_output(timeout=remaining)
""",
    "wait_for around a wait on the last line of a pytest.raises block (#3726)": """
async def test_a(socket):
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(socket.receive_output(), 1)
""",
    "an asyncio.timeout block as the last line of a pytest.raises block (#3726)": """
async def test_a(socket):
    with pytest.raises(asyncio.TimeoutError):
        async with asyncio.timeout(1):
            await socket.receive_output()
""",
    "a disconnect with only its code": """
async def test_a(socket):
    await socket.disconnect(1000)
""",
    "a helper whose long default is forwarded": """
async def _receive_until(communicator, wanted, *, tries=8, timeout=30):
    return await communicator.receive_json_from(timeout=timeout)
""",
    "a deadline-forwarding helper called with a generous timeout or none (#3709)": """
async def _frames_until(communicator, *types, timeout=15.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    return await communicator.receive_json_from(timeout=deadline - loop.time())

async def test_a(socket):
    await _frames_until(socket, "patch")
    await _frames_until(socket, "patch", timeout=30)
""",
    "a synchronous wait with a derived timeout is not a communicator wait (#3709)": """
class Probe:
    def wait_for(self, predicate, timeout):
        deadline = time.monotonic() + timeout
        left = deadline - time.monotonic()
        self._cond.wait(left)

def test_a(probe):
    probe.wait_for(lambda: True, 2)
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
    # --- the blind spots the PR #3732 re-review found (#3737) ---------------------------
    "asyncio.wait on a set of tasks is not a communicator wait (#3737)": """
async def test_a(task):
    await asyncio.wait({task}, timeout=1)
""",
    "asyncio.wait on a list with a positional bound is not one either (#3737)": """
async def test_a(tasks):
    await asyncio.wait(tasks, timeout=0.5)
""",
    "wait_for around wait() on a receiver nothing says is a communicator (#3737)": """
async def test_a(done):
    await asyncio.wait_for(done.wait(), 1)
""",
    "wait_for around wait() on an asyncio.Event the file constructs (#3737)": """
async def test_a():
    done = asyncio.Event()
    await asyncio.wait_for(done.wait(), 1)
""",
    "wait_for around a generous bound on a constructed communicator (#3737)": """
async def test_a(app):
    comm = WebsocketCommunicator(app, "/ws/")
    await asyncio.wait_for(comm.wait(), 30)
""",
    "from asyncio import timeout around something that is not a frame (#3737)": """
from asyncio import timeout

async def test_a(queue):
    async with timeout(2):
        await queue.get()
""",
    "a bare timeout() that was not imported from asyncio is not asyncio.timeout (#3737)": """
async def test_a(socket):
    async with timeout(2):
        await socket.receive_output()
""",
    "a module constant shadowed by a parameter (#3737)": """
T = 2

async def test_a(socket, T):
    await socket.receive_output(timeout=T)
""",
    "a module constant shadowed by a local assignment (#3737)": """
T = 2

async def test_a(socket):
    T = pick()
    await socket.receive_output(timeout=T)
""",
    "a module constant shadowed by a loop variable (#3737)": """
T = 2

async def test_a(socket, values):
    for T in values:
        await socket.receive_output(timeout=T)
""",
    "a module constant shadowed by an enclosing function's parameter (#3737)": """
T = 2

def make(T):
    async def test_a(socket):
        await socket.receive_output(timeout=T)
    return test_a
""",
    "a module constant shadowed by a keyword-only parameter (#3737)": """
T = 2

async def test_a(socket, *, T):
    await socket.receive_output(timeout=T)
""",
    "wait_for around a shielded wait with a generous bound (#3737)": """
async def test_a(socket):
    await asyncio.wait_for(asyncio.shield(socket.receive_output()), 30)
""",
    "wait_for around a task variable made from something that is not a frame (#3737)": """
async def test_a(queue):
    task = asyncio.create_task(queue.get())
    await asyncio.wait_for(task, 2)
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


# --------------------------------------------------------------------------- #
# A counted wait is counted ONCE, and what is not counted lands in the aggregates
# --------------------------------------------------------------------------- #


def _uncovered(source):
    return _count_uncovered(source, communicator_waits())


@pytest.mark.parametrize("source", CAUGHT.values(), ids=CAUGHT.keys())
def test_a_counted_shape_is_not_also_in_the_aggregates(source):
    """A site is counted per file OR in ``UNCOVERED_BASELINE``, never both.

    (A forwarding helper's own ``receive_json_from(timeout=timeout)`` is a variable timeout by
    design: whether it is short depends on its callers, which are what is counted.)
    """
    expected = {"absence_raises": 0, "variable_timeouts": 0, "wait_for_short": 0}
    if (
        "timeout=timeout" in source
        or "timeout=deadline" in source
        or "async def other(socket, T)" in source
    ):
        # the shadowed ``T`` in ``other`` cannot be read as a number: a variable timeout
        expected["variable_timeouts"] = 1
    assert _uncovered(source) == expected


def test_a_bound_on_something_that_is_not_a_frame_is_an_aggregate_not_a_file_count():
    source = """
async def test_a(done):
    await asyncio.wait_for(done.wait(), 1)
"""
    assert find_short_waits(source) == []
    assert _uncovered(source)["wait_for_short"] == 1


def test_a_timeout_that_cannot_be_read_is_an_aggregate_not_a_file_count():
    source = """
async def test_a(socket, remaining):
    await socket.receive_output(timeout=remaining)
"""
    assert find_short_waits(source) == []
    assert _uncovered(source)["variable_timeouts"] == 1


def test_asyncio_wait_on_tasks_is_in_no_aggregate_not_even_with_a_variable_timeout():
    """``asyncio.wait`` is not a frame wait (#3737): it is not a "variable communicator timeout"
    either, so it must not inflate ``UNCOVERED_BASELINE``."""
    source = """
async def test_a(tasks, remaining):
    await asyncio.wait(tasks, timeout=remaining)
"""
    assert find_short_waits(source) == []
    assert _uncovered(source) == {"absence_raises": 0, "variable_timeouts": 0, "wait_for_short": 0}


def test_a_wait_on_the_last_line_of_a_raises_block_is_the_absence_aggregate():
    source = """
async def test_a(socket):
    with pytest.raises(asyncio.TimeoutError):
        await socket.receive_output(timeout=1)
"""
    assert find_short_waits(source) == []
    assert _uncovered(source)["absence_raises"] == 1
