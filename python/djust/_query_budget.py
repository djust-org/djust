"""``LiveView.query_budget``: parsing it, and measuring it at run time (#3686, #3701).

A view declares what its phases may spend on the database::

    class ReportView(LiveView):
        query_budget = {"mount": 3, "render": 1, "events": {"refresh": 2}}

* ``query_budget = 5``: ``mount()`` plus the first render, together.
* ``"mount"``: ``mount()`` and ``handle_params()``. ``"render"``: one
  ``get_context_data()`` pass (where the JIT evaluates the view's QuerySets).
* ``"event"``: every ``@event_handler`` of the view; ``"events": {name: n}``
  names a handler's own number, which wins over ``"event"``.

Four things read it, and they read it the same way, through
:func:`declared_budget` (which is :func:`parse_budget` plus the checks that need
the view class: a handler named in ``events`` must exist and must be one a
budget can be enforced on). ``tests/test_query_budget_review_3722.py`` pins that
nothing else reads the attribute:

* ``LiveViewSmokeTest`` (``djust.testing``) measures each phase under test and
  fails when one is over; an invalid declaration is a ``ValueError``;
* at run time, :func:`hop` meters each sync step djust runs for a view that
  declares a budget, in ``DEBUG`` or under pytest (or whenever
  ``LIVEVIEW_CONFIG["query_budget"]`` is ``"warn"`` / ``"error"``), and reports a
  phase over its number as a log warning, or raises
  :class:`QueryBudgetExceeded` in ``"error"`` mode; an invalid declaration is
  logged once and not measured;
* ``manage.py check`` reports an invalid declaration as V024 and an invalid
  ``LIVEVIEW_CONFIG["query_budget"]`` as C029, at startup;
* ``djust_audit`` lists the declaration, or the reason it is invalid.

The run-time meter hooks ``djust.sync_turn`` (``run_sync_step`` and the
functions built on it), which every sync hop of every transport already goes
through, on the thread that runs the hop. That is the place a query counter has
to be: Django connections are per thread, and a sync handler or ``mount()`` runs
on a ``sync_to_async`` worker whose connection a counter installed on the event
loop would never see. A view that declares no ``query_budget`` pays one class
attribute read per hop and installs nothing.

What is not measured, and therefore never accepted for a handler named in
``events``: ``async def`` handlers (they run on the event loop, where their own
``sync_to_async`` calls are not hops of this view) and ``@background`` handlers
(the work runs later, in ``start_async``). Also not measured: ``start_async`` /
``handle_async_result`` hops, ticks, the authorization hops (``check_permissions``,
``get_object``, ``has_object_permission``), the ``on_mount`` hooks and
``connected()`` / ``disconnected()`` (the ``"lifecycle"`` step), and a step that
runs inside another step on the same thread, such as an embedded
``{% live_render %}`` child's ``get_context_data()`` (it is part of the parent's
step, and the parent is the one judged). Only the default database connection is
counted, as in ``LiveViewSmokeTest``, and an event is the handler's own queries,
in both.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Mapping
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

logger = logging.getLogger(__name__)

#: The keys of the dict form.
BUDGET_KEYS = ("mount", "render", "event", "events")

#: The ``sync_turn`` steps that belong to a phase. A step not listed is not metered.
_STEP_PHASE = {"mount": "mount", "handle_params": "mount", "render": "render", "event": "event"}

#: ``LIVEVIEW_CONFIG["query_budget"]`` values.
MODES = ("off", "warn", "error")


class QueryBudgetExceeded(Exception):
    """A phase ran more queries than its ``query_budget`` (``LIVEVIEW_CONFIG["query_budget"] = "error"``)."""


def _shown(value: Any) -> str:
    """``value`` for an error message: bounded, and never raises (a hostile ``__repr__``,
    an int of more than 4300 digits, a huge container)."""
    try:
        text = repr(value)
    except Exception:  # noqa: BLE001 - this only builds a message
        return "<%s object>" % type(value).__name__
    return text if len(text) <= 80 else text[:77] + "..."


class Budget(NamedTuple):
    """A parsed ``query_budget``. ``None`` means the phase has no number of its own."""

    total: Optional[int] = None  # the int form: mount + first render
    mount: Optional[int] = None
    render: Optional[int] = None
    event: Optional[int] = None
    events: Mapping = {}

    def declared(self) -> Any:
        """The budget as a JSON-safe value: an int, or a dict of the keys that are set."""
        if self.total is not None:
            return self.total
        out: Dict[str, Any] = {}
        for key in ("mount", "render", "event"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        if self.events:
            out["events"] = dict(self.events)
        return out


def valid_count(value: Any, label: str) -> int:
    """``value`` as a budget number; ``ValueError`` when it is not a non-negative ``int``."""
    # bool is an int subclass: ``query_budget = True`` is a mistake, not 1.
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative int, got {_shown(value)}")
    return value


def parse_budget(raw: Any, label: str = "query_budget") -> Budget:
    """Parse a declared ``query_budget``; ``ValueError`` for one nothing could enforce.

    An unenforced budget reads as protection and gives none, so a key outside
    :data:`BUDGET_KEYS`, a bool, a negative number or a malformed ``events``
    raises instead of being ignored.
    """
    if raw is None:
        return Budget()
    if not isinstance(raw, dict):
        return Budget(total=valid_count(raw, label))
    unsupported = sorted(set(raw) - set(BUDGET_KEYS), key=_shown)
    if unsupported:
        raise ValueError(
            f"{label} key {_shown(unsupported[0])} is not supported: the keys are "
            "'mount', 'render', 'event' and 'events'. Use an int, or a dict with those keys."
        )
    events = raw.get("events", {})
    if not isinstance(events, dict):
        raise ValueError(
            f"{label}['events'] must be a dict of handler name to a number, got {_shown(events)}"
        )
    for name, value in events.items():
        if not isinstance(name, str):
            raise ValueError(f"{label}['events'] keys must be handler names, got {_shown(name)}")
        valid_count(value, f"{label}['events'][{name!r}]")
    return Budget(
        mount=valid_count(raw["mount"], f"{label}['mount']") if "mount" in raw else None,
        render=valid_count(raw["render"], f"{label}['render']") if "render" in raw else None,
        event=valid_count(raw["event"], f"{label}['event']") if "event" in raw else None,
        events=dict(events),
    )


def is_declared(cls: Any) -> bool:
    """Whether the view class declares a ``query_budget`` at all (valid or not)."""
    return getattr(cls, "query_budget", None) is not None


def handler_problem(function: Any) -> Optional[str]:
    """Why a budget cannot be enforced on this event handler, or ``None`` when it can."""
    from ._view_hooks import is_async_hook

    if getattr(function, "_djust_decorators", {}).get("background"):
        return "a @background handler (its work runs after the handler returns, in start_async)"
    if is_async_hook(function):
        return "an async def handler (it runs on the event loop, outside the metered sync steps)"
    return None


def event_handlers(cls: Any) -> Dict[str, Any]:
    """``{name: function}`` for the ``@event_handler`` methods dispatch resolves on ``cls``."""
    from ._parameter_metadata import declared_handlers

    return {handler.name: handler.function for handler in declared_handlers(cls)}


def declared_budget(cls: Any) -> Budget:
    """The one reader of ``cls.query_budget``: parsed, and checked against the class.

    ``ValueError`` for a declaration nothing could enforce: the shapes
    :func:`parse_budget` rejects, a handler named in ``events`` that the view does
    not have, and one a budget cannot be enforced on (``async def``,
    ``@background``). The smoke test, the run-time meter, the system check and the
    audit all call this, so they cannot give one declaration four verdicts.
    """
    parsed = parse_budget(getattr(cls, "query_budget", None))
    if not parsed.events:
        return parsed
    handlers = event_handlers(cls)
    for name in parsed.events:
        function = handlers.get(name)
        if function is None:
            raise ValueError(
                f"query_budget['events'] names {name!r}, which is not an @event_handler of "
                f"{cls.__name__} (handlers: {', '.join(sorted(handlers)) or 'none'})"
            )
        problem = handler_problem(function)
        if problem is not None:
            raise ValueError(
                f"query_budget['events'] names {name!r}, which cannot be measured: it is {problem}"
            )
    return parsed


def unmeasurable_handlers(cls: Any, parsed: Budget) -> List[Tuple[str, str]]:
    """``(name, why)`` of handlers only the broad ``event`` number covers, that it cannot measure."""
    if parsed.event is None:
        return []
    out = []
    for name, function in sorted(event_handlers(cls).items()):
        if name in parsed.events:
            continue
        problem = handler_problem(function)
        if problem is not None:
            out.append((name, problem))
    return out


def valid_mode(value: Any) -> bool:
    """Whether ``value`` is a legal ``LIVEVIEW_CONFIG["query_budget"]`` (``None`` is unset)."""
    return value is None or (isinstance(value, str) and value in MODES)


#: Whether the invalid-mode log line was written (once per process).
_mode_warned = False


def mode() -> str:
    """Whether and how run-time metering reports: ``"off"``, ``"warn"`` or ``"error"``.

    ``LIVEVIEW_CONFIG["query_budget"]`` unset means ``"warn"`` while ``DEBUG`` is on
    or under pytest, and ``"off"`` otherwise. An explicit ``"warn"`` or ``"error"``
    meters regardless of ``DEBUG`` (an explicit ``"error"`` in production turns an
    over-budget page into an error, which is what asking for it means). Any other
    value is a configuration mistake that ``manage.py check`` reports (C029); a
    request never fails over it: it is read as ``"warn"`` and said once.
    """
    global _mode_warned
    from django.conf import settings

    from .config import config

    configured = config.get("query_budget")
    if configured is None:
        from .apps import _running_under_pytest

        return "warn" if (getattr(settings, "DEBUG", False) or _running_under_pytest()) else "off"
    if not valid_mode(configured):
        if not _mode_warned:
            _mode_warned = True
            logger.warning(
                "LIVEVIEW_CONFIG['query_budget'] must be one of %s, got %s; using 'warn'.",
                ", ".join(repr(m) for m in MODES),
                _shown(configured),
            )
        return "warn"
    return configured


class _Tally:
    """What one view instance has spent in the mount phase it is in."""

    __slots__ = ("mount", "total", "open_total", "last", "reported")

    def __init__(self) -> None:
        self.mount = 0
        self.total = 0
        self.open_total = False
        self.last: Optional[str] = None
        self.reported: set = set()


#: Per view instance, weakly: nothing here outlives the view.
_tallies: "weakref.WeakKeyDictionary[Any, _Tally]" = weakref.WeakKeyDictionary()

#: ``{view class: (the declared object it was computed from, parsed budget or None)}``.
#: A class's declaration is parsed and checked once, and an invalid one is logged
#: once; re-read when the class attribute is rebound.
_class_state: "weakref.WeakKeyDictionary[type, Tuple[Any, Optional[Budget]]]" = (
    weakref.WeakKeyDictionary()
)

#: View classes whose instances cannot be weakly referenced, logged once each.
_unreferenceable: "weakref.WeakSet[type]" = weakref.WeakSet()


def _reset_for_tests() -> None:
    """Forget what was parsed and logged (tests build many classes and read the log)."""
    global _mode_warned
    _class_state.clear()
    _unreferenceable.clear()
    _tallies.clear()
    _mode_warned = False


def budgeted(view: Any) -> bool:
    """Whether ``view``'s class declares a ``query_budget`` (one attribute read)."""
    return getattr(type(view), "query_budget", None) is not None


def _budget_of(view: Any) -> Optional[Budget]:
    """The view class's checked budget, or ``None`` (invalid: logged once, not measured)."""
    cls = type(view)
    raw = getattr(cls, "query_budget", None)
    cached = _class_state.get(cls)
    if cached is not None and cached[0] is raw:
        return cached[1]
    parsed: Optional[Budget]
    try:
        parsed = declared_budget(cls)
    except ValueError as exc:
        parsed = None
        from ._exposure_diagnostics import log_failure_for

        log_failure_for(
            logger,
            (view,),
            exc,
            "%s declares an invalid query_budget and it is not measured: %s",
            cls.__name__,
            exc,
            level="warning",
        )
    _class_state[cls] = (raw, parsed)
    return parsed


def hop(view: Any, step: str, fn: Any) -> Optional["_Hop"]:
    """The meter for one sync hop of ``view``, or ``None`` when it is not metered.

    ``None`` is the usual answer: the view declares no budget, the step belongs
    to no phase, metering is off, or the declared budget is invalid (logged
    once; ``LiveViewSmokeTest`` raises on it).
    """
    if getattr(type(view), "query_budget", None) is None:
        return None
    phase = _STEP_PHASE.get(step)
    if phase is None:
        return None
    current = mode()
    if current == "off":
        return None
    parsed = _budget_of(view)
    if parsed is None:
        return None
    return _Hop(view, step, phase, parsed, current, getattr(fn, "__name__", ""))


class _Hop:
    """A context manager that counts the queries one sync hop runs, then settles the phase."""

    __slots__ = ("view", "step", "phase", "budget", "mode", "handler", "count", "_scope")

    def __init__(
        self, view: Any, step: str, phase: str, budget: Budget, mode: str, handler: str
    ) -> None:
        self.view = view
        self.step = step
        self.phase = phase
        self.budget = budget
        self.mode = mode
        self.handler = handler
        self.count = 0
        self._scope: Any = None

    def _count(self, execute: Any, sql: Any, params: Any, many: Any, context: Any) -> Any:
        try:
            return execute(sql, params, many, context)
        finally:
            self.count += 1

    def __enter__(self) -> "_Hop":
        from django.db import connection

        self._scope = connection.execute_wrapper(self._count)
        self._scope.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Optional[bool]:
        self._scope.__exit__(exc_type, exc, tb)
        try:
            self._settle(report=exc_type is None)
        finally:
            self._scope = None
        return None

    def _tally(self) -> Optional[_Tally]:
        try:
            tally = _tallies.get(self.view)
            if tally is None:
                tally = _tallies[self.view] = _Tally()
        except TypeError:  # a view that cannot be weakly referenced or hashed
            cls = type(self.view)
            if cls not in _unreferenceable:
                _unreferenceable.add(cls)
                logger.warning(
                    "%s instances cannot be weakly referenced or hashed, so its query_budget "
                    "is not measured",
                    cls.__name__,
                )
            return None
        return tally

    def _settle(self, *, report: bool) -> None:
        tally = self._tally()
        if tally is None:
            return
        budget, count = self.budget, self.count
        problems = []
        if self.phase == "mount":
            # ``mount()`` opens a mount phase; ``handle_params()`` joins it, and
            # opens one of its own when it runs alone (a ``url_change``).
            if self.step == "mount" or tally.last not in ("mount", "handle_params"):
                tally.mount = tally.total = 0
                tally.open_total = True
                tally.reported = set()
            tally.mount += count
            if tally.open_total:
                tally.total += count
            if budget.mount is not None and tally.mount > budget.mount:
                problems.append(("mount", tally.mount, budget.mount, "mount"))
            if budget.total is not None and tally.total > budget.total:
                problems.append(("total", tally.total, budget.total, "mount + first render"))
        elif self.phase == "render":
            if budget.render is not None and count > budget.render:
                problems.append(("render", count, budget.render, "render"))
            if tally.open_total:
                tally.total += count
                tally.open_total = False
                if budget.total is not None and tally.total > budget.total:
                    problems.append(("total", tally.total, budget.total, "mount + first render"))
        else:  # event
            tally.open_total = False
            limit = budget.events.get(self.handler, budget.event)
            if limit is not None and count > limit:
                problems.append(("event:" + self.handler, count, limit, "event " + self.handler))
        tally.last = self.step
        if not report:
            return
        for key, spent, limit, what in problems:
            # The mount phase spans several hops, so it is reported once; every
            # render pass and every event is its own occurrence.
            if key in ("mount", "total"):
                if key in tally.reported:
                    continue
                tally.reported.add(key)
            # A mount phase spans several hops and is reported the moment it first
            # goes over, so the figure is a floor: a later hop can add more.
            message = "%s: %s ran %s%d queries, over its query_budget of %d" % (
                type(self.view).__name__,
                what,
                "at least " if key in ("mount", "total") else "",
                spent,
                limit,
            )
            if self.mode == "error":
                raise QueryBudgetExceeded(message)
            logger.warning("%s", message)
