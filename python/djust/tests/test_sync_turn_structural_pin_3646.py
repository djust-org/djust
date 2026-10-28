"""#3646: the structural pin for ``sync_turn_wrapper`` (moved out of the #3637 test and rebuilt).

What it pins
------------
1. **Every thread hand-off is accounted for.** A callable handed to a thread
   (``sync_to_async``, ``database_sync_to_async``, ``SyncToAsync``,
   ``asyncio.to_thread``, ``anyio.to_thread.run_sync``, ``loop.run_in_executor``,
   ``executor.submit``, ``threading.Thread`` / ``Timer``, a bare ``@sync_to_async``
   decorator, a ``Thread`` subclass), under any import alias, is either

   * STEPPED: ``sync_step(...)`` / ``guarded_step(...)`` with a declared step;
   * a LEAF: a framework function verified to enter its own step; or
   * on ``ALLOWED``, keyed by ``(file, enclosing function, target text)`` with a
     kind and a reason. The key is that exact; a bare name (``run``, ``call``,
     ``render``) matches nothing, so a raw ``component.render`` or a nested helper
     named ``run`` in another function is reported.

2. **ALLOWED entries are verified, not asserted.** A ``plumbing`` entry may not reach
   (by an attribute or string reference, following exact ``Name`` calls and stopping
   at stepped leaves) any application hook the ``HOOKS`` table says is stepped or a
   scope input, unless the entry declares it.

3. **Every documented override point has a recorded disposition**, derived from ALL
   classes in the package, not only ``LiveView`` / ``LiveComponent``. A disposition is
   ``stepped:<step>``, ``inside:<steps>@<file>::<function>`` or
   ``unwrapped:<category>:<reason>``, and each form is checked against the source.

What it cannot see: a hand-off built dynamically (``getattr(asgiref.sync, name)``,
``importlib``), and a hook reached through a call the exact resolver cannot follow
(an attribute call to a method defined in several classes). The behavioural tests in
``test_sync_turn_wrapper_more_hops_3637.py`` and ``test_sync_turn_lifecycle_gaps_3646.py``
cover the paths the structure cannot prove.
"""

from __future__ import annotations

import ast
import collections
import pathlib
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

import pytest

from djust.sync_turn import SYNC_STEPS

PACKAGE = pathlib.Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = _FUNCTIONS + (ast.ClassDef,)

#: Callables that hand their first argument to a worker thread. Also valid as a decorator.
WRAPPERS = {"sync_to_async", "database_sync_to_async", "SyncToAsync"}
#: Every other API that runs a callable on another thread.
OTHER_THREADERS = {"to_thread", "run_in_executor", "submit", "Thread", "Timer", "start_new_thread"}
#: Helpers that return a stepped callable (the second argument is the step).
STEPPERS = {"sync_step", "guarded_step"}
#: Local helpers around ``sync_step`` with a fixed step: name -> (file, step).
HELPER_STEPPERS = {"_lifecycle_step": ("runtime.py", "lifecycle")}
#: Helpers that RUN a function inside a step (they do not return a callable).
RUNNERS = {"run_sync_step", "run_guarded_step", "run_teardown_step"}
#: Helpers whose step is implied: name -> step.
IMPLIED_STEP = {"call_teardown_hook": "lifecycle", "_lifecycle_step": "lifecycle"}
#: The hook-calling helpers of ``_view_hooks``: the step comes from the hook's name.
HOOK_CALLERS = {"call_hook", "call_hook_sync"}
#: ``call_async_result`` / ``call_tick`` take the view and call one fixed hook.
FIXED_HOOK_CALLERS = {
    "call_async_result": ("handle_async_result", "async"),
    "call_tick": ("handle_tick", "tick"),
}

_API = WRAPPERS | OTHER_THREADERS | STEPPERS


@dataclass(frozen=True)
class Handoff:
    file: str
    line: int
    enclosing: str  # dotted names of the enclosing classes / functions, or "<module>"
    api: str  # canonical API name
    text: str  # the handed-over callable, as source
    kind: str  # "call" | "decorator" | "subclass"

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.file, self.enclosing, self.text)

    def describe(self) -> str:
        return "%s:%d %s(%s) in %s" % (self.file, self.line, self.api, self.text, self.enclosing)


def source_files() -> Iterable[Tuple[str, ast.AST]]:
    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE).as_posix()
        if relative.startswith("tests/") or "/tests/" in relative:
            continue
        yield relative, ast.parse(path.read_text())


_SOURCES: Optional[Dict[str, ast.AST]] = None


def sources() -> Dict[str, ast.AST]:
    global _SOURCES
    if _SOURCES is None:
        _SOURCES = dict(source_files())
    return _SOURCES


def _parents(tree: ast.AST) -> Dict[ast.AST, ast.AST]:
    parents: Dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _bare(expr: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    """The canonical identifier an expression names: ``a.b.sync_to_async`` and an alias
    of it both give ``sync_to_async``."""
    if isinstance(expr, ast.Name):
        return aliases.get(expr.id, expr.id)
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


def import_aliases(tree: ast.AST) -> Dict[str, str]:
    """local name -> canonical name: ``from m import x as y`` and a plain re-binding
    (``s = sync_to_async``, repeated to follow a chain)."""
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for item in node.names:
                if item.asname:
                    aliases[item.asname] = item.name
    for _ in range(3):
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                name = _bare(node.value, aliases)
                if name in _API | set(RUNNERS) | set(HELPER_STEPPERS):
                    aliases[node.targets[0].id] = name
    return aliases


def _enclosing(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> str:
    names: List[str] = []
    current = parents.get(node)
    while current is not None:
        if isinstance(current, _SCOPES):
            names.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(names)) or "<module>"


def _call_target(call: ast.Call, api: str) -> Optional[ast.AST]:
    keyword = {k.arg: k.value for k in call.keywords if k.arg}
    args = call.args
    if api in WRAPPERS or api in ("to_thread", "submit", "start_new_thread", "run_sync"):
        return args[0] if args else keyword.get("func")
    if api == "run_in_executor":
        return args[1] if len(args) > 1 else None
    if api == "Thread":
        target = keyword.get("target") or (args[1] if len(args) > 1 else None)
        # ``Thread(target=copy_context().run, args=(body,))`` runs ``body``.
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "run"
            and isinstance(keyword.get("args"), ast.Tuple)
            and keyword["args"].elts
        ):
            return keyword["args"].elts[0]
        return target
    if api == "Timer":
        return args[1] if len(args) > 1 else keyword.get("function")
    return None


def _threader(call: ast.Call, aliases: Dict[str, str]) -> Optional[str]:
    """The canonical thread-handing API a call is, else None."""
    name = _bare(call.func, aliases)
    if name in WRAPPERS or name in ("to_thread", "run_in_executor", "start_new_thread"):
        return name
    if name == "submit" and isinstance(call.func, ast.Attribute):
        return "submit"
    if name == "Thread" or name == "Timer":
        return name
    if name == "run_sync":  # anyio.to_thread.run_sync / ``from anyio.to_thread import run_sync``
        if isinstance(call.func, ast.Attribute):
            return "run_sync" if "to_thread" in ast.unparse(call.func.value) else None
        return "run_sync"
    return None


def iter_handoffs(
    tree: ast.AST, relative: str
) -> List[Tuple[Handoff, Optional[ast.AST], Optional[ast.AST]]]:
    """Every way ``tree`` hands a callable to a thread: ``(handoff, target expression,
    enclosing function node)``. The target is None for a decorator or a subclass."""
    aliases = import_aliases(tree)
    parents = _parents(tree)
    out: List[Tuple[Handoff, Optional[ast.AST], Optional[ast.AST]]] = []

    def enclosing_function(node: ast.AST) -> Optional[ast.AST]:
        current = parents.get(node)
        while current is not None and not isinstance(current, _FUNCTIONS):
            current = parents.get(current)
        return current

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            api = _threader(node, aliases)
            if api is None:
                continue
            target = _call_target(node, api)
            text = ast.unparse(target) if target is not None else "<none>"
            handoff = Handoff(relative, node.lineno, _enclosing(node, parents), api, text, "call")
            out.append((handoff, target, enclosing_function(node)))
        elif isinstance(node, _FUNCTIONS):
            for decorator in node.decorator_list:
                expr = decorator.func if isinstance(decorator, ast.Call) else decorator
                api = _bare(expr, aliases)
                if api in WRAPPERS:
                    handoff = Handoff(
                        relative,
                        node.lineno,
                        _enclosing(node, parents),
                        api or "",
                        "@" + node.name,
                        "decorator",
                    )
                    out.append((handoff, None, None))
        elif isinstance(node, ast.ClassDef) and any(
            _bare(base, aliases) == "Thread" for base in node.bases
        ):
            handoff = Handoff(
                relative,
                node.lineno,
                _enclosing(node, parents),
                "Thread",
                "class " + node.name,
                "subclass",
            )
            out.append((handoff, None, None))
    return out


def _names_in(expr: Optional[ast.AST]) -> Set[str]:
    """Every identifier and short string an expression mentions."""
    found: Set[str] = set()
    if expr is None:
        return found
    for node in ast.walk(expr):
        if isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif (
            isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 60
        ):
            found.add(node.value)
    return found


# ---------------------------------------------------------------------------
# Stepped?
# ---------------------------------------------------------------------------


def _stepper_problem(target: ast.AST, aliases: Dict[str, str], relative: str) -> Optional[str]:
    """None when ``target`` is a correctly stepped callable, else why it is not."""
    if not isinstance(target, ast.Call):
        return "not-a-stepper"
    name = _bare(target.func, aliases)
    if name in HELPER_STEPPERS:
        return None if HELPER_STEPPERS[name][0] == relative else "helper-in-another-file"
    if name not in STEPPERS:
        return "not-a-stepper"
    step = target.args[1] if len(target.args) > 1 else None
    if isinstance(step, ast.Constant) and step.value in SYNC_STEPS:
        return None
    if isinstance(step, ast.Call) and _bare(step.func, aliases) == "_hook_step":
        return None
    return "bad-step"


def _bound_to_step(name: str, function: Optional[ast.AST], aliases: Dict[str, str]) -> bool:
    """``name = sync_step(...)`` / ``name = <leaf>(...)`` in the enclosing function."""
    if function is None:
        return False
    leaf_names = {qualname.split(".")[-1] for _file, qualname in LEAVES}
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Call)
        ):
            callee = _bare(node.value.func, aliases)
            if callee in leaf_names:
                return True
            if _stepper_problem(node.value, aliases, "") in (None, "helper-in-another-file"):
                return True
    return False


def unaccounted(tree: ast.AST, relative: str) -> List[str]:
    """Hand-offs in ``tree`` that are not stepped, bound to a step, or on ALLOWED."""
    aliases = import_aliases(tree)
    problems = []
    for handoff, target, function in iter_handoffs(tree, relative):
        if handoff.kind == "call" and target is not None:
            problem = _stepper_problem(target, aliases, relative)
            if problem is None:
                continue
            if problem == "bad-step":
                problems.append(handoff.describe() + " [step is not a declared SYNC_STEPS name]")
                continue
            if isinstance(target, ast.Name) and _bound_to_step(target.id, function, aliases):
                continue
        if handoff.key not in ALLOWED:
            problems.append(handoff.describe())
    return problems


# ---------------------------------------------------------------------------
# Leaves: framework functions that enter their own step
# ---------------------------------------------------------------------------

#: ``(file, qualname) -> (helper, step)``. The function's own body must call ``helper``
#: (``step`` is the literal it must pass, or None when the helper is itself a leaf that
#: enters the step). Verified by ``test_every_leaf_enters_its_step_inside_its_own_body``.
LEAVES: Dict[Tuple[str, str], Tuple[str, Optional[str]]] = {
    # authorization: one guarded ``auth`` step each, so every caller is covered
    ("auth/core.py", "check_object_permission"): ("run_guarded_step", "auth"),
    ("auth/core.py", "check_view_auth"): ("run_guarded_step", "auth"),
    ("auth/core.py", "check_event_auth"): ("run_guarded_step", "auth"),
    ("auth/core.py", "check_handler_permission"): ("run_guarded_step", "auth"),
    ("auth/core.py", "enforce_object_permission"): ("check_object_permission", None),
    ("auth/core.py", "check_view_auth_lightweight"): ("check_view_auth", None),
    ("auth/core.py", "run_pre_mount_auth"): ("check_view_auth", None),
    # lifecycle
    ("hooks.py", "run_on_mount_hooks"): ("run_guarded_step", "lifecycle"),
    ("_child_lifecycle.py", "run_view_disconnected"): ("run_sync_step", "lifecycle"),
    ("_child_lifecycle.py", "begin_live_connection"): ("sync_step", "lifecycle"),
    ("live_view.py", "LiveView._restore_private_state"): ("run_sync_step", "lifecycle"),
    ("security/state_snapshot.py", "legacy_snapshot_fields"): ("run_sync_step", "lifecycle"),
    # teardown hooks (#3646): every site that calls one goes through call_teardown_hook
    ("_child_lifecycle.py", "call_teardown_hook"): ("run_teardown_step", "lifecycle"),
    ("_child_lifecycle.py", "dispose_child_subtree"): ("call_teardown_hook", None),
    ("_child_lifecycle.py", "discard_sticky_child"): ("call_teardown_hook", None),
    ("_child_lifecycle.py", "release_root_view"): ("call_teardown_hook", None),
    ("mixins/sticky.py", "StickyChildRegistry._unregister_child"): ("call_teardown_hook", None),
    # the hook-calling helpers (their step comes from the hook's name, or is fixed)
    ("_view_hooks.py", "call_hook"): ("sync_step", None),
    ("_view_hooks.py", "call_hook_sync"): ("run_sync_step", None),
    ("_view_hooks.py", "call_async_result"): ("sync_step", "async"),
    ("_view_hooks.py", "call_tick"): ("sync_step", "tick"),
    # hook step
    ("_child_lifecycle.py", "untrack_view_presence"): ("run_sync_step", "hook"),
    ("push.py", "_presence_probe"): ("run_sync_step", "hook"),
    # time travel
    ("time_travel.py", "_stepped_handler"): ("sync_step", "event"),
    ("time_travel.py", "replay_event"): ("_stepped_handler", None),
}

_LEAF_TAILS = {qualname.split(".")[-1] for _file, qualname in LEAVES}
_STEP_HELPERS = STEPPERS | RUNNERS | set(IMPLIED_STEP)


# ---------------------------------------------------------------------------
# ALLOWED: the hand-offs that are neither stepped nor bound to a step
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """Why one hand-off is sound.

    ``leaf``: the target is a ``LEAVES`` function. ``plumbing``: framework code whose
    reachable application hooks are checked against ``HOOKS``; ``reaches`` declares the
    ones it may reach, each with the reason. ``outside``: a thread of a subsystem that
    is not a view's sync turn. ``entry``: the HTTP request entry, which has its own
    scope and enters the steps inside it.
    """

    kind: str
    reason: str
    reaches: Tuple[Tuple[str, str], ...] = ()


def leaf() -> Entry:
    return Entry("leaf", "a stepped leaf (LEAVES)")


def plumbing(reason: str, *reaches: Tuple[str, str]) -> Entry:
    return Entry("plumbing", reason, tuple(reaches))


def outside(reason: str) -> Entry:
    return Entry("outside", reason)


def entry(reason: str) -> Entry:
    return Entry("entry", reason)


def external(reason: str) -> Entry:
    return Entry("external", reason)


_RENDER = (
    "render plumbing (template build, Rust state sync, HTML post-processing, diff): "
    "get_context_data inside it is its own render step"
)
_SESSION = "session, auth-state or storage I/O of the framework; calls no view hook"
_STATE = "framework-owned bookkeeping (component ids, dirty baseline, temporary assigns)"
_RESTORE = (
    "assigns saved state with setattr / safe_setattr: an application property setter "
    "would run here without the scope (guide: Not wrapped); no hook is called by name"
)
_SCOPE_INPUT = (
    "resolves what the scope depends on (the tenant, the caller), so it cannot run inside it"
)
_TEMPLATE_NAME = (
    "get_template() (TemplateMixin) returns the template source and is read inside "
    "this render hop; documented as not wrapped (guide)"
)
_TENANT = ("resolve_tenant", "the tenant is the input of the scope")
_GET_TEMPLATE = ("get_template", _TEMPLATE_NAME)

_AUTH_OF_CHILD = (
    "authorizes a child's turn: calls only the stepped auth leaves, after resolving the tenant"
)
_HTTP_BODY = (
    "the sync HTTP handler body of a turn (get/post): its hooks enter their own steps "
    "inside it (mixins/request.py run_sync_step / call_hook_sync)"
)
_HTTP_ENTRY = (
    "the HTTP request entry (View.dispatch / get): runs under the request's own scope; "
    "the steps are entered inside it by mixins/request.py"
)
_HTTP_BACKGROUND = (
    "the HTTP fallback's background run: its work goes through run_async_callback / "
    "call_async_result, which enter the async step (pinned by the #3625 tests)"
)
_CLOCK = (
    "a RoomClock tick: the djust.clocks subsystem, with its own tenant and connection "
    "scope; not a view hook and not part of a view's sync turn"
)
_LANE = (
    "a per-view lane serializes whole frames; the frame handler it runs enters its own "
    "steps (event/mount/...) as it does when run inline"
)
_ERROR_PAGE = "builds the protected 500 response for an HTTP entry; calls no view hook"
_SAVE = (
    "the state save: serializes and writes the session snapshot; calls no view hook "
    "(a view's private-state getters were captured by the stepped event hop)"
)
_ABANDON = (
    "closes a stream that never started: shutdown() releases the view through "
    "release_root_view, whose teardown hooks enter a lifecycle step off the loop "
    "(test_an_sse_response_close_runs_the_teardown_hooks_inside_a_lifecycle_step)"
)
_PRESERVE = (
    "re-checks each sticky child through the auth leaves and discards a refused one "
    "through discard_sticky_child (a leaf, so its teardown hooks are stepped here)"
)
_PUSH_TURN = (
    "the one-hop server-push turn: the handler runs through run_sync_step with the "
    "hook's step (#3646) and the render through render_background_sync"
)


_DJANGO_TEMPLATE = (
    "Django's own template loader / Template.render on the stream fragment's context; the "
    "context came from get_context_data, entered as a render step on the line above"
)
_RUST_TEMPLATE = "the Rust view's template swap (a PyO3 call), not Python application code"


def refused_async() -> Entry:
    return Entry(
        "refused-async",
        "an async def hook is refused by sync_to_async with the same TypeError with or "
        "without a wrapper (#3625); the call is guarded by is_async_hook just above",
    )


ALLOWED: Dict[Tuple[str, str, str], Entry] = {
    ("_background_render.py", "render_background", "render_background_sync"): plumbing(_RENDER),
    ("_child_async.py", "_execute_body", "_authorize"): plumbing(_AUTH_OF_CHILD, _TENANT),
    ("_child_async.py", "_execute_body.complete", "_authorize"): plumbing(_AUTH_OF_CHILD, _TENANT),
    ("_child_async.py", "_execute_body.complete", "render_embedded_child_html"): plumbing(
        _RENDER, _GET_TEMPLATE
    ),
    (
        "_child_async.py",
        "dispatch_legacy_child_work.run_body.complete",
        "render_embedded_child_html",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    ("_child_lifecycle.py", "fire_view_disconnected", "run_view_disconnected"): leaf(),
    ("_child_lifecycle.py", "arelease_root_view", "_clear_live_handles"): plumbing(
        "drops the Rust live handles (framework state); calls no view hook"
    ),
    ("_child_state_index.py", "asave_indexed_child", "_child_update"): plumbing(_STATE),
    ("_exposure_auth.py", "aauthorize_event", "_event_request"): plumbing(_SESSION, _TENANT),
    ("_exposure_auth.py", "aauthorize_event", "authorize_event"): plumbing(_SESSION, _TENANT),
    ("_exposure_child_persistence.py", "asave_child_states", "_capture_batch"): plumbing(_STATE),
    ("_exposure_sessions.py", "asave_server_state", "server_state_adapter"): plumbing(_RENDER),
    ("_exposure_sessions.py", "asave_server_state", "adapter.contract.project_view"): plumbing(
        _RENDER
    ),
    ("_session_merge.py", "asave_merged", "save_merged"): plumbing(_SESSION),
    ("_turn_ports.py", "run_http_turn.run_body", "call_body"): entry(_HTTP_BODY),
    ("_view_hooks.py", "call_async_result", "handler"): refused_async(),
    ("_view_hooks.py", "call_tick", "view.handle_tick"): refused_async(),
    ("admin_ext/progress.py", "admin_action_with_progress.decorator.wrapper", "_run"): outside(
        "a Django admin action run by admin_action_with_progress in its own thread: not a LiveView turn, no view hook"
    ),
    ("auth/core.py", "arun_pre_mount_auth", "_tenant"): plumbing(_SCOPE_INPUT),
    ("auth/core.py", "acheck_view_auth", "check_view_auth"): leaf(),
    ("auth/core.py", "arun_pre_mount_auth", "run_pre_mount_auth"): leaf(),
    ("auth/core.py", "acheck_view_auth_lightweight", "check_view_auth_lightweight"): leaf(),
    ("clocks.py", "RoomClock._call", "self.name"): outside(_CLOCK),
    ("clocks.py", "_Pool._dispatch", "call"): outside(_CLOCK),
    (
        "components/_interactive.py",
        "DropdownMenu.observe_toggle",
        "get_backend()._claim_observation",
    ): plumbing(_SESSION),
    ("deploy_cli.py", "_login_browser", "http_server.handle_request"): outside(
        "the CLI's one-shot local callback server for browser login"
    ),
    ("dev_server.py", "DjustFileChangeHandler.schedule_reload", "self._debounced_reload"): outside(
        "the dev server's file-watch debounce thread"
    ),
    ("live_view.py", "_protect_http_entry.protected_async", "protected_server_error"): plumbing(
        _ERROR_PAGE
    ),
    ("live_view.py", "LiveView.as_view.view", "self.dispatch"): entry(_HTTP_ENTRY),
    ("mixins/request.py", "_start_background_run", "body"): entry(_HTTP_BACKGROUND),
    ("mixins/request.py", "RequestMixin._aget_turn_body", "self.get"): entry(_HTTP_ENTRY),
    ("mixins/sticky.py", "save_sticky_child_state", "child._get_private_state"): plumbing(_STATE),
    ("mixins/sticky.py", "save_sticky_child_state", "save_session.pop"): plumbing(_SESSION),
    ("mixins/sticky.py", "write_sticky_index_and_prune", "save_session.pop"): plumbing(_SESSION),
    ("multiloop.py", "_Runner.__init__", "self._run"): outside(
        "the multi-loop runner's own event-loop thread"
    ),
    ("push.py", "_sync_presence_scope_group", "_presence_probe"): leaf(),
    ("runtime.py", "_spawn_save", "detached(run)"): plumbing(_SAVE),
    ("runtime.py", "_SaveExecutor.submit", "_run_with_connection_hygiene"): outside(
        "the request-scoped save executor's submit(); the callable it runs is _spawn_save's run (plumbing, above)"
    ),
    ("runtime.py", "_spawn_save", "run"): plumbing(_SAVE),
    ("runtime.py", "WSConsumerTransport.explicit_event_request", "fresh_socket_request"): plumbing(
        _SESSION
    ),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "run_on_mount_hooks"): leaf(),
    ("runtime.py", "ViewRuntime._dispatch_component_event", "view.render_with_diff"): plumbing(
        _RENDER, _GET_TEMPLATE
    ),
    ("runtime.py", "ViewRuntime._build_request", "self._attach_socket_tenant"): plumbing(
        _SCOPE_INPUT
    ),
    ("runtime.py", "ViewRuntime._legacy_event_snapshot", "legacy_snapshot_fields"): leaf(),
    ("runtime.py", "ViewRuntime._render_async_result", "view.render_with_diff"): plumbing(
        _RENDER, _GET_TEMPLATE
    ),
    ("runtime.py", "WSConsumerTransport.on_mount_failed", "untrack_view_presence"): leaf(),
    ("runtime.py", "SSESessionTransport.on_mount_failed", "untrack_view_presence"): leaf(),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "establish_mount_session"): plumbing(
        _SESSION
    ),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "request_binding"): plumbing(_SESSION),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._snapshot_user_private_attrs",
    ): plumbing(_STATE),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._capture_dirty_baseline",
    ): plumbing(_STATE),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "enforce_object_permission"): leaf(),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "navigation_title"): plumbing(
        _RENDER, _GET_TEMPLATE
    ),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "page_shell"): plumbing(
        _RENDER, _GET_TEMPLATE
    ),
    (
        "runtime.py",
        "ViewRuntime._dispatch_sticky_child_event",
        "render_embedded_child_html",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    (
        "runtime.py",
        "ViewRuntime._dispatch_url_change_inner",
        "self.view_instance.render_with_diff",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    ("runtime.py", "ViewRuntime.authorize_explicit_turn", "enforce_object_permission"): leaf(),
    ("runtime.py", "ViewRuntime.authorize_explicit_turn", "_authorize"): plumbing(
        _AUTH_OF_CHILD, _TENANT
    ),
    ("runtime.py", "ViewRuntime._flush_pending_layout", "render_pending_layout"): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime._render_async_result", "view._sync_state_to_rust"): plumbing(
        _RENDER, _GET_TEMPLATE
    ),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._initialize_temporary_assigns",
    ): plumbing(_STATE),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._assign_component_ids",
    ): plumbing(_STATE),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "render_view_with_diff"): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "mount_frame_metadata"): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime._dispatch_sticky_child_event", "resolve_explicit_child"): plumbing(
        _STATE
    ),
    ("runtime.py", "ViewRuntime._dispatch_sticky_child_event", "enforce_object_permission"): leaf(),
    ("runtime.py", "ViewRuntime._dispatch_url_change_inner", "enforce_object_permission"): leaf(),
    (
        "runtime.py",
        "ViewRuntime._dispatch_url_change_inner",
        "self.view_instance._sync_state_to_rust",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    ("runtime.py", "ViewRuntime._check_auth", "run_pre_mount_auth"): leaf(),
    ("runtime.py", "ViewRuntime._explicit_event_snapshot", "snapshot_codec"): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime._render_and_send", "render_view_with_diff"): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "track_session"): plumbing(_SESSION),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._restore_private_state",
    ): leaf(),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._restore_upload_configs",
    ): plumbing(_STATE),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._restore_listen_channels",
    ): plumbing(_STATE),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "load_server_state"): plumbing(_SESSION),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._initialize_rust_view",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._sync_state_to_rust",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "_reconstruct_explicit_page_shell",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._strip_comments_and_whitespace",
    ): plumbing(_RENDER),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._extract_liveview_content",
    ): plumbing(_RENDER),
    (
        "runtime.py",
        "ViewRuntime._dispatch_url_change_inner",
        "self.view_instance._strip_comments_and_whitespace",
    ): plumbing(_RENDER),
    (
        "runtime.py",
        "ViewRuntime._dispatch_url_change_inner",
        "self.view_instance._extract_liveview_content",
    ): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime._explicit_event_snapshot", "request_binding"): plumbing(_SESSION),
    ("runtime.py", "ViewRuntime._explicit_event_snapshot", "codec.capture"): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime.authorize_explicit_turn", "fresh_socket_request"): plumbing(
        _SESSION
    ),
    (
        "runtime.py",
        "ViewRuntime._dispatch_mount_body",
        "view_instance._restore_component_state",
    ): plumbing(_RESTORE),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "snapshot_codec"): plumbing(_RENDER),
    ("runtime.py", "ViewRuntime._dispatch_mount_body", "codec.capture"): plumbing(_RENDER),
    ("security/csrf.py", "abind_csrf_cookie", "bind_csrf_cookie"): plumbing(_SESSION),
    ("sse.py", "_StreamGuard.deadline_expired", "self.abandon"): plumbing(_ABANDON),
    ("sse.py", "SSESession._replace_view", "page_request"): plumbing(_SESSION),
    ("sse.py", "SSESession._run_disconnected", "run_view_disconnected"): leaf(),
    ("sse.py", "SSESession._untrack_presence", "untrack_view_presence"): leaf(),
    ("sse.py", "DjustSSEStreamView.get", "_request_user_pk"): plumbing(_SESSION),
    ("sse.py", "SSESession._mount_slot", "page_request"): plumbing(_SESSION),
    ("sse.py", "SSESession._untrack_replaced_presence", "untrack_view_presence"): leaf(),
    ("sse.py", "DjustSSEStreamView.get", "page_request"): plumbing(_SESSION),
    ("sse.py", "DjustSSEEventView.post", "_request_owns_session"): plumbing(_SESSION),
    ("sse.py", "DjustSSEMessageView.post", "_request_owns_session"): plumbing(_SESSION),
    ("sse.py", "SSESession._release_slot", "release_root_view"): leaf(),
    ("sse.py", "DjustSSEStreamView.get", "django_session.save"): plumbing(_SESSION),
    ("sse.py", "DjustSSEMessageView.post", "protected_server_error"): plumbing(_ERROR_PAGE),
    ("sse.py", "SSESession._replace_view", "PresenceManager.per_connection"): plumbing(_SESSION),
    ("sse.py", "DjustSSEStreamView.get", "protected_server_error"): plumbing(_ERROR_PAGE),
    ("sse.py", "SSESession._replace_view", "release_root_view"): leaf(),
    ("streaming.py", "StreamingMixin.push_state", "render"): plumbing(_RENDER),
    ("streaming.py", "StreamingMixin.push_state", "self._strip_comments_and_whitespace"): plumbing(
        _RENDER
    ),
    ("streaming.py", "StreamingMixin.push_state", "self._extract_liveview_content"): plumbing(
        _RENDER
    ),
    ("streaming.py", "StreamingMixin._render_stream_fragment", "render_to_string"): external(
        _DJANGO_TEMPLATE
    ),
    ("streaming.py", "StreamingMixin._render_stream_fragment", "tmpl.render"): external(
        _DJANGO_TEMPLATE
    ),
    ("templatetags/live_tags.py", "live_render._lazy_thunk", "_render_eager"): plumbing(
        _RENDER, _GET_TEMPLATE
    ),
    ("testing.py", "_call_hook", "context.run"): plumbing(_RENDER),
    ("updates.py", "check_in_background", "run"): plumbing(_RENDER),
    ("websocket.py", "LiveViewConsumer._queue_on_lane", "consumer._run_lane_item"): outside(_LANE),
    ("websocket.py", "LiveViewConsumer.handle_unmount", "self._unmount_slot"): outside(_LANE),
    ("websocket.py", "LiveViewConsumer._handle_upload_frame", "owner._run_lane_item"): outside(
        _LANE
    ),
    (
        "websocket.py",
        "LiveViewConsumer._rederive_live_redirect_user",
        "establish_mount_session",
    ): plumbing(_SESSION),
    (
        "websocket.py",
        "LiveViewConsumer._handle_request_html_locked",
        "self.view_instance._strip_comments_and_whitespace",
    ): plumbing(_RENDER),
    (
        "websocket.py",
        "LiveViewConsumer._handle_request_html_locked",
        "self.view_instance._extract_liveview_content",
    ): plumbing(_RENDER),
    ("websocket.py", "LiveViewConsumer._flush_pending_layout", "render_pending_layout"): plumbing(
        _RENDER
    ),
    (
        "websocket.py",
        "LiveViewConsumer._handle_time_travel_jump_locked",
        "restore_snapshot",
    ): plumbing(_RESTORE),
    (
        "websocket.py",
        "LiveViewConsumer._handle_time_travel_component_jump_locked",
        "restore_component_snapshot",
    ): plumbing(_RESTORE),
    ("websocket.py", "LiveViewConsumer._handle_forward_replay_locked", "replay_event"): leaf(),
    (
        "websocket.py",
        "LiveViewConsumer.handle_bug_capture_share",
        "self.view_instance.render_with_diff",
    ): plumbing(_RENDER, _GET_TEMPLATE),
    ("websocket.py", "LiveViewConsumer.handle_bug_capture_share", "encode_view_state"): plumbing(
        _RENDER
    ),
    (
        "websocket.py",
        "LiveViewConsumer._dispatch_single_event_body",
        "_sync_context_and_render",
    ): plumbing(_RENDER),
    (
        "websocket.py",
        "LiveViewConsumer._dispatch_single_event_body",
        "_sync_strip_and_extract",
    ): plumbing(_RENDER),
    ("websocket.py", "LiveViewConsumer._hotreload_view", "_render_hot"): plumbing(_RENDER),
    ("websocket.py", "LiveViewConsumer._untrack_presence_of", "untrack_view_presence"): leaf(),
    (
        "websocket.py",
        "LiveViewConsumer._release_consumer_view",
        "PresenceManager.per_connection",
    ): plumbing(_SESSION),
    ("websocket.py", "LiveViewConsumer._handle_request_html_locked", "_sync_and_render"): plumbing(
        _RENDER
    ),
    ("websocket.py", "LiveViewConsumer._tick_locked", "tick_turn"): plumbing(
        "the offloaded tick: handle_tick runs through run_sync_step(..., 'tick', ...) in this function's own body"
    ),
    ("websocket.py", "LiveViewConsumer._handle_upload_resume", "mgr.resume_entry"): plumbing(
        _SESSION
    ),
    (
        "websocket.py",
        "LiveViewConsumer._hotreload_view",
        "self.view_instance._rust_view.update_template",
    ): external(_RUST_TEMPLATE),
    (
        "websocket.py",
        "LiveViewConsumer.handle_live_redirect_mount",
        "old_view._preserve_sticky_children",
    ): plumbing(_PRESERVE),
    ("websocket.py", "LiveViewConsumer._handle_request_html_locked", "contract_snapshot"): plumbing(
        _RENDER
    ),
    ("websocket.py", "LiveViewConsumer._run_server_push_turn_offloaded", "turn"): plumbing(
        _PUSH_TURN
    ),
    ("websocket_utils.py", "_validate_event_security", "check_handler_permission"): leaf(),
    ("websocket_utils.py", "_post_only_rules_deny", "check_event_auth"): leaf(),
    ("websocket_utils.py", "_validate_event_security", "check_object_permission"): leaf(),
    ("worker_pool.py", "_SlotExecutor.submit", "_run_checked"): outside(
        "the worker pool's own executor machinery"
    ),
    ("worker_pool.py", "_LanePool.__init__", "self._work"): outside(
        "the worker pool's own thread loop"
    ),
}


# ---------------------------------------------------------------------------
# HOOKS: every documented override point, with a recorded and CHECKED disposition
# ---------------------------------------------------------------------------
#
#   stepped:<step>                   a framework function names the hook and enters <step>
#                                    (or it is an @event_handler: every handler runs in
#                                    the event step of websocket_utils._call_handler)
#   inside:<steps>@<file>::<func>    the hook runs inside the named caller, which runs in
#                                    one of <steps> (the caller is checked to exist and to
#                                    name the hook; that it runs in the step is the
#                                    behavioural tests' job)
#   unwrapped:<category>:<reason>    async-def     every definition is `async def`: awaited on
#                                                  the loop, never wrapped (checked)
#                                    not-threaded  no thread hand-off in the package names
#                                                  it, so it runs in its caller's thread and
#                                                  step (checked)
#                                    scope-input   it produces what the scope depends on, so
#                                                  it cannot run inside it (an ALLOWED entry
#                                                  must declare reaching it)
#
# Keys are ``Class.method``; ``(app).name`` is a hook the framework defines no default for.

HOOK_CATEGORIES = {"async-def", "not-threaded", "scope-input"}

HOOKS: Dict[str, str] = {
    # ---- authorization (leaves: one guarded auth step per check) ----
    "LiveView.get_object": "stepped:auth",
    "LiveView.has_object_permission": "stepped:auth",
    "(app).check_permissions": "stepped:auth",
    # ---- lifecycle ----
    "(app).connected": "stepped:lifecycle",
    "(app).disconnected": "stepped:lifecycle",
    "LiveView._restore_snapshot": "stepped:lifecycle",
    "LiveView._should_restore_snapshot": "stepped:lifecycle",
    "LiveView._capture_snapshot_state": "stepped:lifecycle",
    "StickyChildRegistry._on_sticky_unmount": "stepped:lifecycle",
    "(app)._cleanup_on_unregister": "stepped:lifecycle",
    "UploadMixin._cleanup_uploads": "stepped:lifecycle",
    # ---- the turn's own hooks ----
    "ComponentMixin.mount": "stepped:mount",
    "SimpleLiveView.mount": "stepped:mount",
    "NavigationMixin.handle_params": "stepped:handle_params",
    "LiveView.handle_tick": "stepped:tick",
    "LiveView.handle_restore": "stepped:hook",
    "NotificationMixin.handle_info": "stepped:hook",
    "ContextMixin.get_context_data": "stepped:render",
    "DraftModeMixin.get_context_data": "stepped:render",
    "Component.render": "stepped:render",
    "TemplateMixin.get_template": "stepped:render",
    # ---- cursor / presence ----
    "LiveCursorMixin.handle_cursor_move": "stepped:hook",
    "PresenceMixin.update_presence_heartbeat": "stepped:hook",
    "PresenceMixin.get_presence_key": "stepped:hook",
    "TenantMixin.get_presence_key": "stepped:hook",
    "PresenceMixin._restore_presence": "stepped:hook",
    "PresenceMixin.get_presence_user_id": "inside:mount|hook@presence.py::PresenceMixin.track_presence",
    "PresenceMixin.handle_presence_join": "inside:mount|hook@presence.py::PresenceMixin.track_presence",
    "PresenceMixin.handle_presence_leave": "inside:hook|event@presence.py::PresenceMixin.untrack_presence",
    "PresenceMixin._on_presence_change": "inside:hook@presence.py::PresenceMixin._broadcast_presence_change",
    # ---- tenancy ----
    "TenantMixin.resolve_tenant": (
        "unwrapped:scope-input:the tenant is the input of the scope, resolved before it can open"
    ),
    "TenantMixin.get_state_key_prefix": (
        "unwrapped:not-threaded:a pure key prefix read by the state-key helpers in the thread "
        "that saves or restores state; it opens no scope of its own"
    ),
    # ---- components ----
    "LiveComponent.mount": "inside:mount|event|render@components/base.py::LiveComponent.__init__",
    "LiveComponent.update": "inside:event@mixins/components.py::ComponentMixin.update_component",
    "LiveComponent._handle_event": (
        "inside:event@components/base.py::LiveComponent._make_event_handler.handler"
    ),
    "LiveComponent.unmount": (
        "unwrapped:not-threaded:only the base implementation is called, as LiveComponent.unmount(member), "
        "by the interactive-component collections; an override runs where the app calls it"
    ),
    "ComponentMixin.handle_component_event": (
        "inside:event@mixins/components.py::ComponentMixin._register_component.component_callback"
    ),
    "Component.get_context_data": "inside:render@components/base.py::Component.render",
    "Component._render_custom": "inside:render@components/base.py::Component.render",
    "ContextMixin.get_render_only_context": (
        "inside:render@mixins/context.py::ContextMixin.get_context_data"
    ),
    "JITMixin.serialize_for_template": (
        "unwrapped:not-threaded:an app helper for get_context_data; the framework never calls it, "
        "so it runs where the app calls it (inside the render step in the documented use)"
    ),
    "StickyChildRegistry._on_sticky_update": (
        "inside:render@templatetags/live_tags.py::_apply_sticky_input_changes"
    ),
    "StickyChildRegistry._get_all_child_views": (
        "unwrapped:not-threaded:a registry lookup, not an application hook "
        "(its docstring mentions overriding __init__)"
    ),
    # ---- forms ----
    "FormMixin.get_form_class": "inside:mount|event|render@forms.py::FormMixin._init_form_state",
    "FormMixin.get_form_kwargs": "inside:mount|event|render@forms.py::FormMixin.get_form",
    "FormMixin._create_form": "inside:mount|event@forms.py::FormMixin.mount",
    "FormMixin._ensure_form_state": "inside:event@forms.py::FormMixin.submit_form",
    "FormMixin.as_live": "inside:render@templatetags/live_tags.py::live_form",
    "FormMixin.as_live_field": "inside:render@templatetags/live_tags.py::live_field",
    "AdminFormMixin._create_form": "inside:mount|event@admin_ext/forms.py::AdminFormMixin.mount",
    "AdminFormMixin.get_field_value": "inside:render@templatetags/live_tags.py::field_value",
    "AdminTailwindAdapter.render_field": "inside:render@forms.py::FormMixin.as_live_field",
    "BaseAdapter._merge_widget_attrs": "inside:render@frameworks.py::BaseAdapter._render_input",
    "DraftModeMixin.get_draft_key": "inside:event|render@drafts.py::DraftModeMixin.get_context_data",
    "WizardMixin.on_wizard_complete": "inside:event@wizard.py::WizardMixin.submit_wizard",
    "ReactMixin.get_react_components": "inside:render@react.py::ReactMixin.render_react_component",
    # ---- the data table ----
    "DataTableMixin.handle_cell_edit": (
        "inside:event@components/mixins/data_table.py::DataTableMixin.on_table_cell_edit"
    ),
    "DataTableMixin.on_table_row_save": "stepped:event",
    "DataTableMixin.handle_row_save": (
        "inside:event@components/mixins/data_table.py::DataTableMixin.on_table_row_save"
    ),
    "DataTableMixin.handle_bulk_action": (
        "inside:event@components/mixins/data_table.py::DataTableMixin.on_table_bulk_action"
    ),
    "DataTableMixin.handle_export": (
        "inside:event@components/mixins/data_table.py::DataTableMixin.on_table_export"
    ),
    "DataTableMixin.handle_row_drag": (
        "inside:event@components/mixins/data_table.py::DataTableMixin.on_table_row_drag"
    ),
    "DataTableMixin.handle_copy": (
        "inside:event@components/mixins/data_table.py::DataTableMixin.on_table_copy"
    ),
    "DataTableMixin.handle_import": (
        "inside:event@components/mixins/data_table.py::DataTableMixin._confirm_import"
    ),
    "DataTableMixin.refresh_table_server": (
        "inside:event@components/mixins/data_table.py::DataTableMixin.refresh_table"
    ),
    # ---- uploads: called inline by the UploadManager from the upload frame handler ----
    "UploadWriter.open": (
        "unwrapped:not-threaded:called inline by UploadManager from the upload frame handler on "
        "the event loop, like an async hook; never handed to a thread"
    ),
    "UploadWriter.write_chunk": (
        "unwrapped:not-threaded:called inline by UploadManager from the upload frame handler on "
        "the event loop, like an async hook; never handed to a thread"
    ),
    "UploadWriter.abort": (
        "unwrapped:not-threaded:called inline by UploadManager from the upload frame handler on "
        "the event loop, like an async hook; never handed to a thread"
    ),
    "BufferedUploadWriter.on_part": (
        "unwrapped:not-threaded:called by BufferedUploadWriter.write_chunk/close, inline in the "
        "upload frame handler on the event loop"
    ),
    "BufferedUploadWriter.on_complete": (
        "unwrapped:not-threaded:called by BufferedUploadWriter.close, inline in the upload "
        "frame handler on the event loop"
    ),
    # ---- HTTP phase and URLconf time ----
    "LiveView.as_view": (
        "unwrapped:not-threaded:builds the view callable at URLconf time; never a hop"
    ),
    "RequestMixin.get_http_status": (
        "unwrapped:not-threaded:read by the GET turn body after the render, in the request thread"
    ),
    # ---- not application hooks: framework SPI, tooling, other subsystems ----
    "Transport.next_mount_version": (
        "unwrapped:not-threaded:transport adapter SPI of the runtime, not an application hook"
    ),
    "WSConsumerTransport.on_view_mounted": "unwrapped:async-def:transport SPI, awaited on the loop",
    "WSConsumerTransport.recheck_event_auth": (
        "unwrapped:async-def:transport SPI, awaited on the loop"
    ),
    "ViewRuntime._instantiate_view": (
        "unwrapped:not-threaded:runtime SPI that builds the view instance, not an application hook"
    ),
    "LiveViewConsumer._next_version": (
        "unwrapped:not-threaded:consumer internal (wire version counter), not an application hook"
    ),
    "LiveViewConsumer._live_redirect_url_view": (
        "unwrapped:not-threaded:consumer internal (resolves the redirect target), not an application hook"
    ),
    "LiveViewConsumer._handle_forward_replay_locked": (
        "unwrapped:async-def:consumer internal, awaited on the loop"
    ),
    "DjangoJSONEncoder._serialize_model_safely": (
        "unwrapped:not-threaded:serialization helper, not an application hook"
    ),
    "StateBackend.get_memory_stats": "unwrapped:not-threaded:read by the stats CLI only",
    "LiveViewConfig._rebuild_from_settings": (
        "unwrapped:not-threaded:settings (re)load, not part of a turn"
    ),
    "ChunkEmitter.__init__": "unwrapped:not-threaded:a constructor (its docstring mentions overriding)",
    "_TenantQuerySetManager.unscoped": (
        "unwrapped:not-threaded:a manager method the application calls itself"
    ),
    "LiveViewTestClient._authorization_denial": (
        "unwrapped:not-threaded:test client internal, not part of a production turn"
    ),
    "LiveViewTestClient.trigger_info": (
        "unwrapped:not-threaded:test client entry (it runs handle_info through call_hook_sync, "
        "which enters the hook step)"
    ),
    "_T012Scan.parent": "unwrapped:not-threaded:system-check AST helper, run by manage.py check",
    "AdminWidget.get_context": "inside:render@admin_ext/plugins.py::AdminWidget.render",
    "AdminPlugin.get_pages": (
        "unwrapped:not-threaded:admin site wiring at URLconf time, never a hop"
    ),
    "AdminPlugin.get_widgets": (
        "unwrapped:not-threaded:read by the admin index's get_context_data (inside its render step)"
    ),
    "AdminPlugin.get_nav_items": (
        "unwrapped:not-threaded:admin site wiring while building the navigation, never a hop"
    ),
    "AdminPlugin.ready": (
        "unwrapped:not-threaded:called once when a plugin registers, never a hop"
    ),
    "ThemeCSSGenerator.__init__": (
        "unwrapped:not-threaded:a constructor (CSS generator; its docstring mentions overriding)"
    ),
    "ThemeCSSGenerator._generate_dark_mode": (
        "unwrapped:not-threaded:CSS generation, run by the theme tags and the management command"
    ),
    "ThemeCSSGenerator._generate_system_preference": (
        "unwrapped:not-threaded:CSS generation, run by the theme tags and the management command"
    ),
    "DesignSystemCSSGenerator._generate_dark_mode_colors": (
        "unwrapped:not-threaded:CSS generation, run by the theme tags and the management command"
    ),
    "ThemePackCSSGenerator._generate_framework_css": (
        "unwrapped:not-threaded:CSS generation, run by the theme tags"
    ),
    "ThemeManager.set_pack": "unwrapped:not-threaded:theme API called by the application directly",
    "Command.handle_check_compat": "unwrapped:not-threaded:a management command, run from the shell",
    "ConflictResolver.resolve_conflict": (
        "unwrapped:not-threaded:PWA offline-sync conflict policy, run by SyncManager (its own subsystem)"
    ),
    "SyncManager._sync_create_batch": (
        "unwrapped:not-threaded:PWA offline-sync batch, run by SyncManager (its own subsystem)"
    ),
    "SyncManager._sync_update_batch": (
        "unwrapped:not-threaded:PWA offline-sync batch, run by SyncManager (its own subsystem)"
    ),
    "SyncManager._sync_delete_batch": (
        "unwrapped:not-threaded:PWA offline-sync batch, run by SyncManager (its own subsystem)"
    ),
    "SyncManager._fetch_server_data": (
        "unwrapped:not-threaded:PWA offline-sync fetch, run by SyncManager (its own subsystem)"
    ),
}


# ---------------------------------------------------------------------------
# Checking HOOKS
# ---------------------------------------------------------------------------


def _method_of(key: str) -> str:
    return key.rsplit(".", 1)[-1]


def derived_override_points(trees: Optional[Dict[str, ast.AST]] = None) -> Set[str]:
    """``Class.method`` for every method, in any class of the package, whose docstring
    documents an override."""
    found: Set[str] = set()
    for _relative, tree in (trees or sources()).items():
        for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
            for member in cls.body:
                if isinstance(member, _FUNCTIONS) and re.search(
                    r"overrid", ast.get_docstring(member) or "", re.I
                ):
                    found.add("%s.%s" % (cls.name, member.name))
    return found


def _qualname(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> str:
    chain = [node.name]  # type: ignore[attr-defined]
    current = parents.get(node)
    while current is not None:
        if isinstance(current, _SCOPES):
            chain.append(current.name)
        current = parents.get(current)
    return ".".join(reversed(chain))


_INDEX: Optional[Dict[str, Dict[str, ast.AST]]] = None


def function_index() -> Dict[str, Dict[str, ast.AST]]:
    """file -> qualname -> function node."""
    global _INDEX
    if _INDEX is None:
        _INDEX = {}
        for relative, tree in sources().items():
            parents = _parents(tree)
            _INDEX[relative] = {
                _qualname(node, parents): node
                for node in ast.walk(tree)
                if isinstance(node, _FUNCTIONS)
            }
    return _INDEX


def _find_function(tree: ast.AST, qualname: str) -> Optional[ast.AST]:
    parents = _parents(tree)
    for node in ast.walk(tree):
        if isinstance(node, _FUNCTIONS) and _qualname(node, parents) == qualname:
            return node
    return None


def _hook_steps() -> Dict[str, str]:
    from djust._view_hooks import _HOOK_STEPS

    return dict(_HOOK_STEPS)


_WITNESSES: Optional[Set[Tuple[str, str]]] = None
_TARGET_NAMES: Optional[Set[str]] = None
_CLASS_METHODS: Optional[Dict[str, List[Tuple[str, ast.AST]]]] = None


def stepped_witnesses() -> Set[Tuple[str, str]]:
    global _WITNESSES
    if _WITNESSES is None:
        _WITNESSES = _compute_witnesses()
    return _WITNESSES


def _bound_names(function: Optional[ast.AST], name: str) -> Set[str]:
    """Names mentioned by what ``name`` is assigned in ``function``
    (``handler = getattr(view, "handle_info", None)``)."""
    found: Set[str] = set()
    if function is None:
        return found
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                found |= _names_in(node.value)
    return found


def _compute_witnesses() -> Set[Tuple[str, str]]:
    """``(hook name, step)`` pairs that framework code provably enters.

    * a stepper call (``sync_step`` / ``guarded_step`` / ``run_*_step``) with a
      declared step, for every name its callable argument mentions (resolving a
      plain function name to its definition in the same file, one level);
    * a ``call_hook`` / ``call_hook_sync`` call, for the names in its arguments, with
      the step ``_view_hooks`` derives from the name; ``call_async_result`` and
      ``call_tick`` for their fixed hook;
    * inside a ``LEAVES`` function, every hook it names, with the step it enters.
    """
    out: Set[Tuple[str, str]] = set()
    hook_steps = _hook_steps()
    for relative, tree in sources().items():
        aliases = import_aliases(tree)
        parents = _parents(tree)
        by_name: Dict[str, List[ast.AST]] = collections.defaultdict(list)
        for node in ast.walk(tree):
            if isinstance(node, _FUNCTIONS):
                by_name[node.name].append(node)

        def enclosing_function(node: ast.AST) -> Optional[ast.AST]:
            current = parents.get(node)
            while current is not None and not isinstance(current, _FUNCTIONS):
                current = parents.get(current)
            return current

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _bare(node.func, aliases)
            if name in STEPPERS | RUNNERS | set(HELPER_STEPPERS):
                step = None
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and arg.value in SYNC_STEPS:
                        step = arg.value
                step = step or IMPLIED_STEP.get(name or "")
                if step is None:
                    continue
                fn_args = node.args[2:] if name not in HELPER_STEPPERS else node.args[1:]
                for arg in fn_args[:1]:
                    names = _names_in(arg)
                    if isinstance(arg, ast.Name):
                        for definition in by_name.get(arg.id, []):
                            names |= _names_in(definition)
                        names |= _bound_names(enclosing_function(node), arg.id)
                    out |= {(n, step) for n in names}
            elif name in HOOK_CALLERS:
                for arg in node.args[:1]:
                    names = _names_in(arg)
                    if isinstance(arg, ast.Name):
                        names |= _bound_names(enclosing_function(node), arg.id)
                    out |= {(n, hook_steps.get(n, "hook")) for n in names}
            elif name in FIXED_HOOK_CALLERS:
                hook, step = FIXED_HOOK_CALLERS[name]
                out.add((hook, step))
    for (relative, qualname), (helper, step) in LEAVES.items():
        function = function_index().get(relative, {}).get(qualname)
        if function is None:
            continue
        if step is None:
            # a leaf that calls another leaf enters whatever that one enters
            continue
        out |= {(n, step) for n in _names_in(function)}
    # A function that calls ``call_teardown_hook`` enters ``lifecycle`` for the hooks it names
    # (it looks them up by name, so the callable argument alone would not show them).
    for relative, functions in function_index().items():
        for function in functions.values():
            if any(
                isinstance(n, ast.Call) and _bare(n.func, {}) in IMPLIED_STEP
                for n in ast.walk(function)
            ):
                out |= {(n, "lifecycle") for n in _names_in(function)}
    return out


def _handler_step_exists() -> bool:
    """``websocket_utils._call_handler`` enters the ``event`` step for every handler."""
    function = function_index().get("websocket_utils.py", {}).get("_call_handler")
    if function is None:
        return False
    for node in ast.walk(function):
        if isinstance(node, ast.Call) and _bare(node.func, {}) in STEPPERS:
            if len(node.args) > 1 and getattr(node.args[1], "value", None) == "event":
                return True
    return False


def _is_event_handler(key: str) -> bool:
    for _relative, member in hook_definitions(key):
        for decorator in member.decorator_list:  # type: ignore[attr-defined]
            expr = decorator.func if isinstance(decorator, ast.Call) else decorator
            if _bare(expr, {}) == "event_handler":
                return True
    return False


def hook_definitions(key: str) -> List[Tuple[str, ast.AST]]:
    global _CLASS_METHODS
    if _CLASS_METHODS is None:
        _CLASS_METHODS = collections.defaultdict(list)
        for relative, tree in sources().items():
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                for member in cls.body:
                    if isinstance(member, _FUNCTIONS):
                        _CLASS_METHODS["%s.%s" % (cls.name, member.name)].append((relative, member))
    return list(_CLASS_METHODS.get(key, []))


def handoff_target_names() -> Set[str]:
    """Every identifier or short string mentioned by the callable of any thread hand-off,
    stepped or not."""
    global _TARGET_NAMES
    if _TARGET_NAMES is None:
        _TARGET_NAMES = set()
        for relative, tree in sources().items():
            for _handoff, target, _function in iter_handoffs(tree, relative):
                _TARGET_NAMES |= _names_in(target)
    return _TARGET_NAMES


def check_disposition(key: str, disposition: str) -> Optional[str]:
    """None when the recorded disposition holds in the source, else why not."""
    method = _method_of(key)
    kind, _, rest = disposition.partition(":")
    app_hook = key.startswith("(app).")
    if not app_hook and not hook_definitions(key):
        return "%s is not defined in any class of the package" % key
    if kind == "stepped":
        if rest not in SYNC_STEPS:
            return "%r is not a declared step" % rest
        if (method, rest) in stepped_witnesses():
            return None
        if rest == "event" and _is_event_handler(key) and _handler_step_exists():
            return None
        return "no framework function names %r and enters the %r step" % (method, rest)
    if kind == "inside":
        steps_text, _, where = rest.partition("@")
        steps_ = steps_text.split("|")
        if not steps_ or any(step not in SYNC_STEPS for step in steps_):
            return "%r names a step that is not declared" % steps_text
        relative, _, qualname = where.partition("::")
        function = function_index().get(relative, {}).get(qualname)
        if function is None:
            return "%s::%s does not exist" % (relative, qualname)
        if method not in _names_in(function):
            return "%s::%s does not name %s" % (relative, qualname, method)
        return None
    if kind == "unwrapped":
        category, _, reason = rest.partition(":")
        if category not in HOOK_CATEGORIES:
            return "%r is not a category (%s)" % (category, sorted(HOOK_CATEGORIES))
        if len(reason.strip()) < 20:
            return "the reason is too short to be a reason"
        if category == "async-def":
            definitions = hook_definitions(key)
            if not definitions or not all(
                isinstance(node, ast.AsyncFunctionDef) for _file, node in definitions
            ):
                return "not every definition of %s is `async def`" % key
        elif category == "not-threaded":
            if method in handoff_target_names():
                return "a thread hand-off names %r, so it is threaded" % method
        elif category == "scope-input":
            declared = {hook for entry_ in ALLOWED.values() for hook, _why in entry_.reaches}
            if method not in declared:
                return "no ALLOWED entry declares reaching %s" % method
        return None
    return "unknown disposition %r" % disposition


# ---------------------------------------------------------------------------
# Reachability: what an unstepped hand-off can reach
# ---------------------------------------------------------------------------


def _reach_hooks() -> Set[str]:
    """Hook names a plumbing hop must not reach unless it declares them: those a hop
    steps elsewhere, and the scope inputs."""
    return {
        _method_of(key)
        for key, disposition in HOOKS.items()
        if disposition.startswith("stepped:") or disposition.startswith("unwrapped:scope-input")
    } - REACH_IGNORE


#: Hook names too generic to find by name: ``Template.render`` / ``Component.render`` /
#: ``dict.update``. The ``component.render`` hand-off is pinned by its exact text and the
#: scoped-render behavioural test instead.
REACH_IGNORE = {"render", "update"}


_IMPORTS: Optional[Dict[str, Dict[str, str]]] = None
_NAMES: Optional[Dict[str, List[Tuple[str, str, ast.AST]]]] = None
_ALIASES: Dict[str, Dict[str, str]] = {}


_FOREIGN: Dict[str, Set[str]] = {}


def _imports_by_file() -> Dict[str, Dict[str, str]]:
    """file -> local name -> original name, for ``from x import y [as z]``. Names imported
    from outside the package (django, asgiref, ...) are recorded in ``_FOREIGN``."""
    global _IMPORTS
    if _IMPORTS is None:
        _IMPORTS = {}
        for relative, tree in sources().items():
            local: Dict[str, str] = {}
            foreign: Set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    inside = node.level > 0 or (node.module or "").split(".")[0] == "djust"
                    for item in node.names:
                        local[item.asname or item.name] = item.name
                        if not inside:
                            foreign.add(item.asname or item.name)
            _IMPORTS[relative] = local
            _FOREIGN[relative] = foreign
    return _IMPORTS


def _definitions_named(name: str) -> List[Tuple[str, str, ast.AST]]:
    global _NAMES
    if _NAMES is None:
        _NAMES = collections.defaultdict(list)
        for relative, functions in function_index().items():
            for qualname, node in functions.items():
                _NAMES[node.name].append((relative, qualname, node))  # type: ignore[attr-defined]
    return list(_NAMES.get(name, []))


def aliases_of(relative: str) -> Dict[str, str]:
    if relative not in _ALIASES:
        _ALIASES[relative] = import_aliases(sources()[relative])
    return _ALIASES[relative]


def resolve_target(handoff_key: Tuple[str, str, str]) -> List[Tuple[str, str, ast.AST]]:
    """The definitions a hand-off's callable names: nested in the enclosing function,
    then anywhere in the file, then (an imported name or a method) elsewhere. A name
    with more than six candidates is not resolved."""
    relative, enclosing, text = handoff_key
    inner = re.sub(r"^detached\((.*)\)$", r"\1", text)
    tail = re.split(r"[.()]", inner.rstrip("()"))[-1] or ""
    if not tail:
        return []
    candidates = _definitions_named(tail)
    nested = [c for c in candidates if c[0] == relative and c[1].startswith(enclosing + ".")]
    if nested:
        return nested
    same_file = [c for c in candidates if c[0] == relative]
    if same_file:
        return same_file
    imports = _imports_by_file().get(relative, {})
    if tail in _FOREIGN.get(relative, set()):
        return []  # a callable from outside the package (django, asgiref, ...)
    imported = imports.get(tail)
    if imported and imported != tail:
        candidates = _definitions_named(imported)
    return candidates if 0 < len(candidates) <= 6 else []


def _reached(
    relative: str, node: ast.AST, hooks: Set[str], seen: Set[int], depth: int = 0
) -> Set[str]:
    """Hooks named by ``node`` outside any step, following exact ``Name`` calls and
    stopping at stepped leaves."""
    if id(node) in seen or depth > 4:
        return set()
    seen.add(id(node))
    aliases = aliases_of(relative)
    skip: Set[int] = set()
    for call in ast.walk(node):
        if isinstance(call, ast.Call):
            name = _bare(call.func, aliases)
            if name == "hasattr":  # an existence check is not a call
                skip.update(id(sub) for sub in ast.walk(call))
            elif (
                name in _STEP_HELPERS
                or name in HOOK_CALLERS
                or name in FIXED_HOOK_CALLERS
                or name in HELPER_STEPPERS
            ):
                arguments = call.args if name in HOOK_CALLERS else call.args[1:]
                for argument in arguments:
                    skip.update(id(sub) for sub in ast.walk(argument))
                    # ``hook = getattr(view, "mount", None)`` ... ``step(view, "mount", hook)``:
                    # what the stepped variable was bound to is stepped with it.
                    if isinstance(argument, ast.Name):
                        for assign in ast.walk(node):
                            if isinstance(assign, ast.Assign) and any(
                                isinstance(t, ast.Name) and t.id == argument.id
                                for t in assign.targets
                            ):
                                skip.update(id(sub) for sub in ast.walk(assign.value))
    found: Set[str] = set()
    follow: List[str] = []
    for sub in ast.walk(node):
        if id(sub) in skip:
            continue
        if isinstance(sub, ast.Attribute) and sub.attr in hooks:
            found.add(sub.attr)
        elif isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in hooks:
            found.add(sub.value)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            follow.append(aliases.get(sub.func.id, sub.func.id))
    imports = _imports_by_file().get(relative, {})
    for name in follow:
        if name in _FOREIGN.get(relative, set()):
            continue  # imported from outside the package: not framework code to follow
        name = imports.get(name, name)
        if name in _LEAF_TAILS or name in _STEP_HELPERS:
            continue
        definitions = [d for d in _definitions_named(name) if d[0] == relative] or [
            d for d in _definitions_named(name)
        ]
        if len(definitions) > 2:
            continue
        for other_relative, _qualname, other in definitions:
            found |= _reached(other_relative, other, hooks, seen, depth + 1)
    return found


def reached_hooks(handoff_key: Tuple[str, str, str]) -> Optional[Set[str]]:
    """Hooks an unstepped hand-off reaches, or None when its callable cannot be resolved."""
    definitions = resolve_target(handoff_key)
    if not definitions:
        return None
    hooks = _reach_hooks()
    reached: Set[str] = set()
    for relative, _qualname, node in definitions:
        reached |= _reached(relative, node, hooks, set())
    return reached


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_every_callable_handed_to_a_thread_is_stepped_bound_to_a_step_or_allowed():
    problems = []
    for relative, tree in sources().items():
        problems.extend(unaccounted(tree, relative))
    assert not problems, (
        "a callable is handed to a thread with no sync-turn step: route it through "
        "sync_step / guarded_step, or add it to ALLOWED (file, enclosing function, "
        "target text) with a kind and a reason:\n" + "\n".join(problems)
    )


def test_allowed_has_no_stale_entries():
    used = set()
    for relative, tree in sources().items():
        for handoff, _target, _function in iter_handoffs(tree, relative):
            used.add(handoff.key)
    stale = sorted(set(ALLOWED) - used)
    assert not stale, "ALLOWED entries no hand-off matches any more: %s" % stale


def test_every_allowed_entry_holds():
    problems = []
    declared_somewhere: Set[str] = set()
    known_hooks = {_method_of(k) for k in HOOKS}
    for key, entry_ in sorted(ALLOWED.items()):
        relative, enclosing, text = key
        label = "%s :: %s :: %s" % key
        if not entry_.reason.strip():
            problems.append(label + ": no reason")
        if entry_.kind == "leaf":
            tail = re.split(r"[.()]", text.rstrip("()"))[-1]
            if tail not in _LEAF_TAILS:
                problems.append("%s: marked leaf but %r is not a LEAVES function" % (label, tail))
        elif entry_.kind == "refused-async":
            function = function_index().get(relative, {}).get(enclosing)
            guarded = function is not None and any(
                isinstance(n, ast.Call)
                and _bare(n.func, {}) == "is_async_hook"
                and n.args
                and ast.unparse(n.args[0]) == text
                for n in ast.walk(function)
            )
            if not guarded:
                problems.append("%s: no is_async_hook(%s) guard in %s" % (label, text, enclosing))
        elif entry_.kind == "plumbing":
            reached = reached_hooks(key)
            declared = {hook for hook, _why in entry_.reaches}
            declared_somewhere |= declared
            if reached is None:
                problems.append("%s: plumbing whose callable cannot be resolved" % label)
                continue
            undeclared = sorted(reached - declared)
            if undeclared:
                problems.append(
                    "%s: reaches %s outside any step; step it or declare it" % (label, undeclared)
                )
            stale = sorted(declared - reached)
            if stale:
                problems.append(
                    "%s: declares reaching %s, which it no longer does" % (label, stale)
                )
            for hook, why in entry_.reaches:
                if hook not in known_hooks:
                    problems.append("%s: declares %r, which is not a recorded hook" % (label, hook))
                if len(why.strip()) < 10:
                    problems.append("%s: no reason for reaching %s" % (label, hook))
        elif entry_.kind not in ("outside", "entry", "external"):
            problems.append("%s: unknown kind %r" % (label, entry_.kind))
    assert not problems, "\n".join(problems)


def test_every_documented_override_point_has_a_disposition():
    derived = derived_override_points()
    missing = sorted(derived - set(HOOKS))
    assert not missing, (
        "documented override points (derived from every class in the package) with no "
        "entry in HOOKS; enter a step or say why not:\n" + "\n".join(missing)
    )


def test_every_hook_disposition_holds():
    problems = []
    for key, disposition in sorted(HOOKS.items()):
        problem = check_disposition(key, disposition)
        if problem:
            problems.append("%s -> %s: %s" % (key, disposition[:60], problem))
    assert not problems, "\n".join(problems)


def test_app_hooks_are_still_referenced():
    names = set()
    for _relative, tree in sources().items():
        names |= _names_in(tree)
    stale = [k for k in HOOKS if k.startswith("(app).") and _method_of(k) not in names]
    assert not stale, stale


def test_a_scope_input_hook_is_declared_by_some_entry():
    for key, disposition in HOOKS.items():
        if disposition.startswith("unwrapped:scope-input"):
            assert check_disposition(key, disposition) is None, key


# ---- the leaves --------------------------------------------------------------------


@pytest.mark.parametrize("leaf_key", sorted(LEAVES), ids=lambda k: "%s::%s" % k)
def test_every_leaf_enters_its_step_inside_its_own_body(leaf_key):
    relative, qualname = leaf_key
    helper, step = LEAVES[leaf_key]
    function = function_index().get(relative, {}).get(qualname)
    assert function is not None, "%s no longer defines %s" % leaf_key
    calls = [n for n in _own_calls(function) if _bare(n.func, {}) == helper]
    assert calls, "%s::%s must call %s itself" % (relative, qualname, helper)
    if step is not None and helper not in IMPLIED_STEP:
        assert any(
            any(getattr(arg, "value", None) == step for arg in call.args) for call in calls
        ), "%s::%s must call %s(..., %r, ...)" % (relative, qualname, helper, step)


def _own_calls(function: ast.AST):
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, ast.Call):
            yield node
        if not isinstance(node, _FUNCTIONS + (ast.Lambda,)):
            pending.extend(ast.iter_child_nodes(node))


def test_a_helper_stepper_wraps_the_step_it_claims():
    for name, (relative, step) in HELPER_STEPPERS.items():
        function = function_index()[relative][name]
        assert any(
            _bare(n.func, {}) == "sync_step"
            and len(n.args) > 1
            and getattr(n.args[1], "value", None) == step
            for n in ast.walk(function)
            if isinstance(n, ast.Call)
        ), name


# ---- sinks: the application calls whose enclosing function is pinned ---------------

_AUTH_SINKS = {"get_object", "has_object_permission", "check_permissions"}
EXPECTED_AUTH_SINKS: Dict[Tuple[str, str, str], Optional[str]] = {
    ("auth/core.py", "_resolve_object_permission", "get_object"): "check_object_permission",
    ("auth/core.py", "_resolve_object_permission", "has_object_permission"): (
        "check_object_permission"
    ),
    ("auth/core.py", "_check_view_auth", "check_permissions"): "check_view_auth",
    # Async ``check_permissions``: awaited on the loop; an ``async def`` is never wrapped.
    ("auth/core.py", "acheck_view_auth", "check_permissions"): None,
    # ``ModelAdmin.get_object(request, id)`` inside the admin views' own ``mount()``
    # (an ordinary lookup, under the mount step), not the view hook.
    ("admin_ext/forms.py", "mount", "get_object"): None,
    ("admin_ext/views.py", "mount", "get_object"): None,
}

_TEARDOWN_HOOKS = {"_cleanup_uploads", "_cleanup_on_unregister", "_on_sticky_unmount"}


def _reference_sites(
    names: Set[str], *, attribute_calls_only: bool
) -> Dict[Tuple[str, str, str], None]:
    found: Dict[Tuple[str, str, str], None] = {}
    for relative, tree in sources().items():
        parents = _parents(tree)
        for node in ast.walk(tree):
            name = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in names and not isinstance(node.func.value, ast.Call):
                    name = node.func.attr
            elif not attribute_calls_only:
                if isinstance(node, ast.Attribute) and node.attr in names:
                    name = node.attr
                elif isinstance(node, ast.Constant) and node.value in names:
                    name = node.value
            if name is None:
                continue
            chain = []
            current = parents.get(node)
            while current is not None:
                if isinstance(current, _FUNCTIONS):
                    chain.append(current.name)
                current = parents.get(current)
            found[(relative, chain[0] if chain else "<module>", name)] = None
    return found


def test_application_auth_hooks_are_called_from_exactly_the_audited_sites():
    found = {site for site in _reference_sites(_AUTH_SINKS, attribute_calls_only=True)}
    found = {site for site in found if site[0] != "live_view.py"}
    expected = set(EXPECTED_AUTH_SINKS)
    assert found == expected, "new: %s; gone: %s" % (
        sorted(found - expected),
        sorted(expected - found),
    )


def test_every_site_that_names_a_teardown_hook_goes_through_call_teardown_hook():
    """The three teardown hooks are looked up by name (getattr / a tuple of names), so
    the site is pinned by what it names: the function that names one must call
    ``call_teardown_hook``, except the hook's own definition and documentation."""
    index = function_index()
    problems = []
    for relative, function_name, name in sorted(
        _reference_sites(_TEARDOWN_HOOKS, attribute_calls_only=False)
    ):
        if function_name == "<module>":
            continue
        functions = [
            node
            for qualname, node in index.get(relative, {}).items()
            if node.name == function_name  # type: ignore[attr-defined]
        ]
        calls = any(
            isinstance(n, ast.Call) and _bare(n.func, {}) == "call_teardown_hook"
            for function in functions
            for n in ast.walk(function)
        )
        if not calls and (relative, function_name) not in TEARDOWN_NAME_ONLY:
            problems.append(
                "%s::%s names %s without call_teardown_hook" % (relative, function_name, name)
            )
    assert not problems, "\n".join(problems)


#: Functions that name a teardown hook without calling it (a definition, a check, a doc).
TEARDOWN_NAME_ONLY: Set[Tuple[str, str]] = set()


# ---------------------------------------------------------------------------
# The pin is load-bearing: each escape, in a file it has never seen, is reported
# ---------------------------------------------------------------------------

ESCAPES = {
    # the entry points the #3637 pin could not see (#3646 item 2)
    "bare @sync_to_async decorator": (
        "from asgiref.sync import sync_to_async\n"
        "@sync_to_async\n"
        "def read(view):\n"
        "    return view.get_object()\n"
    ),
    "@sync_to_async(...) decorator": (
        "from asgiref.sync import sync_to_async\n"
        "@sync_to_async(thread_sensitive=False)\n"
        "def read(view):\n"
        "    return view.get_object()\n"
    ),
    "@database_sync_to_async decorator": (
        "from channels.db import database_sync_to_async\n"
        "@database_sync_to_async\n"
        "def read(view):\n"
        "    return view.get_object()\n"
    ),
    "aliased import": (
        "from asgiref.sync import sync_to_async as s2a\n"
        "async def f(view):\n"
        "    await s2a(view.handle_restore)()\n"
    ),
    "module attribute": (
        "import asgiref.sync\n"
        "async def f(view):\n"
        "    await asgiref.sync.sync_to_async(view.handle_restore)()\n"
    ),
    "plain re-binding": (
        "from asgiref.sync import sync_to_async\n"
        "threaded = sync_to_async\n"
        "async def f(view):\n"
        "    await threaded(view.handle_restore)()\n"
    ),
    "SyncToAsync class": (
        "from asgiref.sync import SyncToAsync\n"
        "async def f(view):\n"
        "    await SyncToAsync(view.handle_restore)()\n"
    ),
    "executor.submit": "def f(view, executor):\n    executor.submit(view.handle_restore)\n",
    "threading.Thread": (
        "import threading\ndef f(view):\n    threading.Thread(target=view.handle_restore).start()\n"
    ),
    "aliased Thread": (
        "from threading import Thread as T\n"
        "def f(view):\n"
        "    T(target=view.handle_restore).start()\n"
    ),
    "Thread subclass": (
        "import threading\nclass Worker(threading.Thread):\n    def run(self):\n        pass\n"
    ),
    "threading.Timer": (
        "import threading\ndef f(view):\n    threading.Timer(1, view.handle_restore).start()\n"
    ),
    "anyio.to_thread.run_sync": (
        "import anyio\n"
        "async def f(view):\n"
        "    await anyio.to_thread.run_sync(view.handle_restore)\n"
    ),
    "anyio to_thread import": (
        "from anyio import to_thread\n"
        "async def f(view):\n"
        "    await to_thread.run_sync(view.handle_restore)\n"
    ),
    "anyio run_sync import": (
        "from anyio.to_thread import run_sync\n"
        "async def f(view):\n"
        "    await run_sync(view.handle_restore)\n"
    ),
    "asyncio.to_thread aliased": (
        "from asyncio import to_thread as in_thread\n"
        "async def f(view):\n"
        "    await in_thread(view.handle_restore)\n"
    ),
    # the shapes the #3637 pin already caught
    "raw attribute": "async def f(view):\n    await sync_to_async(view.handle_restore)(1)\n",
    "lambda": "async def f(view):\n    await sync_to_async(lambda: view.get_presence_key())()\n",
    "partial": (
        "async def f(view):\n    await sync_to_async(functools.partial(view.get_presence_key))()\n"
    ),
    "getattr": "async def f(view):\n    await sync_to_async(getattr(view, 'x'))()\n",
    "to_thread": "async def f(view):\n    await asyncio.to_thread(view.update_presence_heartbeat)\n",
    "run_in_executor": (
        "async def f(view, loop):\n    await loop.run_in_executor(None, view.handle_cursor_move)\n"
    ),
    "database_sync_to_async": (
        "async def f(view):\n    await database_sync_to_async(view.get_template)()\n"
    ),
    "unknown helper": "async def f(view):\n    await sync_to_async(some_new_helper)(view)\n",
    # a step the helper does not know
    "undeclared step": (
        "async def f(view):\n    await sync_to_async(sync_step(view, 'bogus', view.handle_restore))()\n"
    ),
    "step held in a variable": (
        "async def f(view, step):\n    await sync_to_async(sync_step(view, step, view.handle_restore))()\n"
    ),
}


@pytest.mark.parametrize("label", sorted(ESCAPES))
def test_the_pin_reports_every_escape(label):
    tree = ast.parse(ESCAPES[label])
    assert unaccounted(tree, "brand_new.py"), label


def test_the_pin_accepts_the_stepped_forms():
    tree = ast.parse(
        "from djust.sync_turn import sync_step as ss\n"
        "async def f(view):\n"
        "    await sync_to_async(sync_step(view, 'hook', view.handle_cursor_move))(1, 2)\n"
        "    await database_sync_to_async(guarded_step(view, 'auth', view.get_object))()\n"
        "    await asyncio.to_thread(sync_step(view, 'hook', view.get_presence_key))\n"
        "    await sync_to_async(ss(view, 'lifecycle', view.connected))()\n"
        "    call = sync_step(view, 'event', handler)\n"
        "    await sync_to_async(call)()\n"
        "    await sync_to_async(sync_step(view, _hook_step(fn), fn))()\n"
    )
    assert unaccounted(tree, "brand_new.py") == []


def test_the_allowlist_is_keyed_by_file_function_and_text_not_by_a_bare_name():
    """#3646 item 1: a nested helper named ``run`` / ``call`` / ``turn``, or a raw
    ``component.render``, hid behind a bare name on the old list."""
    source = (
        "class ViewRuntime:\n"
        "    async def _dispatch_mount_body(self):\n"
        # on ALLOWED for exactly this function
        "        await sync_to_async(view_instance._restore_listen_channels)()\n"
        "    async def brand_new(self):\n"
        # the same text in another function is not covered
        "        await sync_to_async(view_instance._restore_listen_channels)()\n"
        # a bare name that used to be on the list
        "        def run():\n"
        "            view.get_object()\n"
        "        await sync_to_async(run)()\n"
        "        await sync_to_async(component.render)()\n"
        "        await sync_to_async(view.render_with_diff)()\n"
    )
    problems = unaccounted(ast.parse(source), "runtime.py")
    assert len(problems) == 4, problems
    assert not any("_dispatch_mount_body" in p for p in problems), problems
    for expected in (
        "_restore_listen_channels",
        "(run)",
        "(component.render)",
        "(view.render_with_diff)",
    ):
        assert any(expected in p for p in problems), (expected, problems)


def test_a_leaf_name_is_not_a_free_pass_in_a_new_function():
    source = (
        "class ViewRuntime:\n"
        "    async def brand_new(self):\n"
        "        await sync_to_async(check_object_permission)(view, request)\n"
    )
    assert unaccounted(ast.parse(source), "runtime.py")


# ---- the HOOKS checks reject a false claim ------------------------------------------


@pytest.mark.parametrize(
    "key, claim, why",
    [
        ("LiveView.get_object", "stepped:tick", "wrong step"),
        ("LiveView.get_object", "stepped:bogus", "undeclared step"),
        (
            "LiveView.get_object",
            "inside:event@runtime.py::ViewRuntime.no_such_function",
            "no caller",
        ),
        (
            "LiveView.get_object",
            "inside:event@auth/core.py::check_event_auth",
            "caller does not name it",
        ),
        (
            "LiveView.get_object",
            "inside:bogus@auth/core.py::check_object_permission",
            "undeclared step",
        ),
        (
            "LiveCursorMixin.handle_cursor_move",
            "unwrapped:not-threaded:" + "x" * 30,
            "it IS threaded",
        ),
        ("LiveView.get_object", "unwrapped:async-def:" + "x" * 30, "it is not async"),
        ("TenantMixin.resolve_tenant", "unwrapped:scope-input:" + "x" * 30, "valid claim"),
        (
            "LiveView.get_object",
            "unwrapped:scope-input:" + "x" * 30,
            "no entry declares reaching it",
        ),
        ("LiveView.get_object", "unwrapped:whatever:" + "x" * 30, "unknown category"),
        ("LiveView.get_object", "unwrapped:not-threaded:short", "no reason"),
        ("NoSuchClass.method", "stepped:hook", "no such class"),
        ("LiveView.get_object", "mystery", "unknown form"),
    ],
)
def test_the_hook_checks_reject_a_false_claim(key, claim, why):
    problem = check_disposition(key, claim)
    if why == "valid claim":
        assert problem is None, problem
    else:
        assert problem, (key, claim, why)


def test_a_new_documented_override_in_any_class_needs_a_disposition():
    tree = ast.parse(
        "class Something:\n"
        "    def hook(self):\n"
        '        """Override this to customise the thing."""\n'
        "    def helper(self):\n"
        '        """A plain helper."""\n'
    )
    assert derived_override_points({"x.py": tree}) == {"Something.hook"}


def test_a_hook_reached_outside_any_step_is_reported():
    """The reach check: a plumbing hop that names a stepped hook raw is red."""
    hooks = _reach_hooks()
    raw = ast.parse("def hop(view):\n    return view.get_object()\n").body[0]
    stepped = ast.parse(
        "def hop(view):\n    return run_sync_step(view, 'auth', view.get_object)\n"
    ).body[0]
    by_string = ast.parse(
        "def hop(view):\n    fn = getattr(view, 'get_object', None)\n    return fn()\n"
    ).body[0]
    bound_and_stepped = ast.parse(
        "def hop(view):\n"
        "    fn = getattr(view, 'get_object', None)\n"
        "    return run_sync_step(view, 'auth', fn)\n"
    ).body[0]
    assert "get_object" in _reached("runtime.py", raw, hooks, set())
    assert "get_object" in _reached("runtime.py", by_string, hooks, set())
    assert _reached("runtime.py", stepped, hooks, set()) == set()
    assert _reached("runtime.py", bound_and_stepped, hooks, set()) == set()


def test_a_teardown_hook_called_without_the_helper_is_reported():
    """The teardown sink pin: a function that names a teardown hook must call the helper."""
    tree = ast.parse("def drop(view):\n    view._on_sticky_unmount()\n")
    sites = collections.defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            sites[node.func.attr].append(node)
    assert "_on_sticky_unmount" in sites
    names = _names_in(tree.body[0])
    assert "_on_sticky_unmount" in names
    assert not any(
        isinstance(n, ast.Call) and _bare(n.func, {}) == "call_teardown_hook"
        for n in ast.walk(tree)
    )
