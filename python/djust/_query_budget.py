"""``LiveView.query_budget``: parsing it, and measuring it at run time (#3686, #3701).

A view declares what its phases may spend on the database::

    class ReportView(LiveView):
        query_budget = {"mount": 3, "render": 1, "events": {"refresh": 2}}

* ``query_budget = 5``: ``mount()`` plus the first render, together.
* ``"mount"``: ``mount()`` and ``handle_params()``. ``"render"``: one
  ``get_context_data()`` pass (where the JIT evaluates the view's QuerySets).
* ``"event"``: every ``@event_handler`` of the view; ``"events": {name: n}``
  names a handler's own number, which wins over ``"event"``.

Two things read it, and they read it the same way (:func:`parse_budget`):

* ``LiveViewSmokeTest`` (``djust.testing``) measures each phase under test and
  fails when one is over;
* at run time, :func:`hop` meters each sync step djust runs for a view that
  declares a budget, in ``DEBUG`` or under pytest (or whenever
  ``LIVEVIEW_CONFIG["query_budget"]`` is ``"warn"`` / ``"error"``), and reports a
  phase over its number as a log warning, or raises
  :class:`QueryBudgetExceeded` in ``"error"`` mode.

The run-time meter hooks ``djust.sync_turn`` (``run_sync_step`` and the
functions built on it), which every sync hop of every transport already goes
through, on the thread that runs the hop. That is the place a query counter has
to be: Django connections are per thread, and a sync handler or ``mount()`` runs
on a ``sync_to_async`` worker whose connection a counter installed on the event
loop would never see. A view that declares no ``query_budget`` pays one class
attribute read per hop and installs nothing.

What is not measured: ``async def`` handlers and hooks (they run on the event
loop, where a query cannot run), the ``start_async`` / ``handle_async_result``
hops, ticks, and the authorization and lifecycle hops. Only the default database
connection is counted, as in ``LiveViewSmokeTest``.
"""

from __future__ import annotations

import logging
import weakref
from collections.abc import Mapping
from typing import Any, NamedTuple, Optional

logger = logging.getLogger(__name__)

#: The keys of the dict form.
BUDGET_KEYS = ("mount", "render", "event", "events")

#: The ``sync_turn`` steps that belong to a phase. A step not listed is not metered.
_STEP_PHASE = {"mount": "mount", "handle_params": "mount", "render": "render", "event": "event"}

#: ``LIVEVIEW_CONFIG["query_budget"]`` values.
MODES = ("off", "warn", "error")


class QueryBudgetExceeded(Exception):
    """A phase ran more queries than its ``query_budget`` (``LIVEVIEW_CONFIG["query_budget"] = "error"``)."""


class Budget(NamedTuple):
    """A parsed ``query_budget``. ``None`` means the phase has no number of its own."""

    total: Optional[int] = None  # the int form: mount + first render
    mount: Optional[int] = None
    render: Optional[int] = None
    event: Optional[int] = None
    events: Mapping = {}


def valid_count(value: Any, label: str) -> int:
    """``value`` as a budget number; ``ValueError`` when it is not a non-negative ``int``."""
    # bool is an int subclass: ``query_budget = True`` is a mistake, not 1.
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative int, got {value!r}")
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
    unsupported = sorted(set(raw) - set(BUDGET_KEYS), key=repr)
    if unsupported:
        raise ValueError(
            f"{label} key {unsupported[0]!r} is not supported: the keys are "
            "'mount', 'render', 'event' and 'events'. Use an int, or a dict with those keys."
        )
    events = raw.get("events", {})
    if not isinstance(events, dict):
        raise ValueError(
            f"{label}['events'] must be a dict of handler name to a number, got {events!r}"
        )
    for name, value in events.items():
        if not isinstance(name, str):
            raise ValueError(f"{label}['events'] keys must be handler names, got {name!r}")
        valid_count(value, f"{label}['events'][{name!r}]")
    return Budget(
        mount=valid_count(raw["mount"], f"{label}['mount']") if "mount" in raw else None,
        render=valid_count(raw["render"], f"{label}['render']") if "render" in raw else None,
        event=valid_count(raw["event"], f"{label}['event']") if "event" in raw else None,
        events=dict(events),
    )


def mode() -> str:
    """Whether and how run-time metering reports: ``"off"``, ``"warn"`` or ``"error"``.

    ``LIVEVIEW_CONFIG["query_budget"]`` unset means ``"warn"`` while ``DEBUG`` is on
    or under pytest, and ``"off"`` otherwise. An explicit ``"warn"`` or ``"error"``
    meters regardless of ``DEBUG`` (an explicit ``"error"`` in production turns an
    over-budget page into an error, which is what asking for it means).
    """
    from django.conf import settings
    from django.core.exceptions import ImproperlyConfigured

    from .config import config

    configured = config.get("query_budget")
    if configured is None:
        from .apps import _running_under_pytest

        return "warn" if (getattr(settings, "DEBUG", False) or _running_under_pytest()) else "off"
    if configured not in MODES:
        raise ImproperlyConfigured(
            "LIVEVIEW_CONFIG['query_budget'] must be one of %s, got %r."
            % (", ".join(repr(m) for m in MODES), configured)
        )
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

#: View classes whose invalid budget was already logged, so each is named once.
_invalid_warned: set = set()


def budgeted(view: Any) -> bool:
    """Whether ``view``'s class declares a ``query_budget`` (one attribute read)."""
    return getattr(type(view), "query_budget", None) is not None


def hop(view: Any, step: str, fn: Any) -> Optional["_Hop"]:
    """The meter for one sync hop of ``view``, or ``None`` when it is not metered.

    ``None`` is the usual answer: the view declares no budget, the step belongs
    to no phase, metering is off, or the declared budget is invalid (logged
    once; ``LiveViewSmokeTest`` raises on it).
    """
    budget = getattr(type(view), "query_budget", None)
    if budget is None:
        return None
    phase = _STEP_PHASE.get(step)
    if phase is None:
        return None
    current = mode()
    if current == "off":
        return None
    try:
        parsed = parse_budget(budget)
    except ValueError as exc:
        cls = type(view)
        if cls not in _invalid_warned:
            _invalid_warned.add(cls)
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
