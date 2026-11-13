"""What survives a reconnect, a restart and a second process: the table (#4000).

This module is the ONE copy of the published table. Every outcome cell names
the test that proves it, and that test asserts the cell's exact text through
``SurvivalRecorder.observe`` (``conftest.py``): a test that sees anything else
fails, a cell whose test never observes it fails, and a test that observes a
cell naming another test fails. ``test_survival_table.py`` checks that every
named test exists and that the docs page carries exactly what ``render()``
prints. Regenerate the page with::

    python -m tests.integration.multinode.survival_table --write

``NA`` marks a cell with no separate claim, and says why.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple, Union

ROOT = Path(__file__).resolve().parents[3]
DOC = ROOT / "docs" / "website" / "guides" / "state-survival.md"
TEST_MODULE = "tests/integration/multinode/test_state_survival.py"


@dataclass(frozen=True)
class Cell:
    outcome: str
    test: str
    gap: bool = False  # a known gap: pinned as it is, not as it should be


@dataclass(frozen=True)
class NA:
    reason: str


Entry = Union[Cell, NA]


@dataclass(frozen=True)
class Row:
    key: str
    label: str
    author: str


@dataclass(frozen=True)
class Table:
    name: str
    columns: Tuple[Tuple[str, str], ...]
    rows: Tuple[Row, ...]
    cells: Dict[Tuple[str, str], Entry]


# ---------------------------------------------------------------------------
# 1. Kinds of state and features, across processes
# ---------------------------------------------------------------------------

_T = "test_"
SAME, HTTP, WS, RESTART = "same", "http", "ws", "restart"

STATE_COLUMNS = (
    (SAME, "Same process, next event"),
    (HTTP, "HTTP fallback request on another process"),
    (WS, "WebSocket reconnect to another process"),
    (RESTART, "Process killed (SIGKILL), reconnect to another"),
)

STATE_ROWS = (
    Row(
        "persist_server",
        '`state(..., persist="server")` field',
        'Declare it `persist="server"`; use a session engine every process shares.',
    ),
    Row(
        "persist_client",
        '`state(..., persist="client", client=True)` field',
        "Nothing more over a WebSocket. Over the HTTP fallback it is not kept: "
        'use `persist="server"` for a value that must be.',
    ),
    Row(
        "plain_state",
        "`state()` without `persist`",
        "Nothing: it is saved automatically. Use a session engine every process shares.",
    ),
    Row(
        "plain_attr",
        "Ordinary public attribute holding plain data",
        "Nothing: it is saved automatically, and is not rendered unless declared.",
    ),
    Row(
        "not_saved",
        "`_private` attribute, `state(persist=False)`, or a value that is not plain data",
        "Rebuild it in `mount()` or `handle_restore()`; drop the underscore if it must survive.",
    ),
    Row(
        "handle_restore",
        "`handle_restore()` hook",
        "Use it to reload what the persisted fields point at.",
    ),
    Row(
        "render",
        "Render baseline (what the next update sends)",
        "Nothing: the framework re-sends the page.",
    ),
    Row(
        "url",
        "URL kwargs and the query string (`handle_params`)",
        "Nothing: the URL is read again; it wins over a persisted field of the same name.",
    ),
    Row(
        "form",
        "`FormMixin` input and errors, mid-validation",
        "Nothing: input and errors are saved, except password and other sensitive fields.",
    ),
    Row(
        "wizard",
        "`WizardMixin` step and answers",
        "Nothing: the step and the answers are saved.",
    ),
    Row(
        "legacy",
        '`exposure_policy = "legacy"` view attribute',
        "Nothing over the HTTP fallback. Over a socket it lives in the process: "
        "migrate the view to explicit exposure.",
    ),
    Row(
        "component",
        "`LiveComponent` state",
        "Persist the values on the view and build the component from them.",
    ),
    Row(
        "dropdown",
        "Interactive component (ADR-034 `DropdownMenu`) open state",
        "Nothing; a reconnect closes it. No HTTP-fallback support yet (#4046).",
    ),
    Row(
        "async",
        "`start_async()` work in flight",
        "Clear a persisted in-progress flag in `handle_restore()`; restart the work.",
    ),
    Row(
        "tick",
        "`tick_interval` / `handle_tick()`",
        "Nothing: a counter the tick changes is saved like any other state.",
    ),
    Row(
        "stream",
        "Rows added with `stream_insert()` after mount",
        "Load the rows in `mount()` from storage.",
    ),
    Row(
        "held_save",
        "An event whose save was still being written",
        "Nothing; the user's last shown state comes back, the event is lost.",
    ),
)

STATE_CELLS: Dict[Tuple[str, str], Entry] = {
    ("persist_server", SAME): Cell("kept", _T + "ws_events_on_one_process"),
    ("persist_server", HTTP): Cell(
        "kept (1, 2, 3 across A, B, A)", _T + "http_fallback_alternating_processes"
    ),
    ("persist_server", WS): Cell("restored", _T + "ws_reconnect_to_another_process"),
    ("persist_server", RESTART): Cell("restored (last completed save)", _T + "kill_after_save"),
    ("persist_client", SAME): Cell("kept", _T + "ws_events_on_one_process"),
    ("persist_client", HTTP): Cell(
        "lost: every request starts from the default", _T + "http_fallback_alternating_processes"
    ),
    ("persist_client", WS): Cell(
        "restored from the browser's signed token", _T + "ws_reconnect_to_another_process"
    ),
    ("persist_client", RESTART): Cell(
        "restored from the browser's signed token", _T + "kill_after_save"
    ),
    ("plain_state", SAME): Cell("kept", _T + "ws_events_on_one_process"),
    ("plain_state", HTTP): Cell(
        "kept (1, 2, 3 across A, B, A)", _T + "http_fallback_alternating_processes"
    ),
    ("plain_state", WS): Cell("restored", _T + "ws_reconnect_to_another_process"),
    ("plain_state", RESTART): Cell("restored (last completed save)", _T + "kill_after_save"),
    ("plain_attr", SAME): Cell("kept", _T + "ws_events_on_one_process"),
    ("plain_attr", HTTP): Cell(
        "kept (1, 2, 3 across A, B, A)", _T + "http_fallback_alternating_processes"
    ),
    ("plain_attr", WS): Cell("restored", _T + "ws_reconnect_to_another_process"),
    ("plain_attr", RESTART): Cell("restored (last completed save)", _T + "kill_after_save"),
    ("not_saved", SAME): Cell("kept", _T + "ws_events_on_one_process"),
    ("not_saved", HTTP): Cell(
        "lost: every request runs `mount()`", _T + "http_fallback_alternating_processes"
    ),
    ("not_saved", WS): Cell("lost: `mount()` value", _T + "ws_reconnect_to_another_process"),
    ("not_saved", RESTART): Cell("lost: `mount()` value", _T + "kill_after_save"),
    ("handle_restore", SAME): NA("no remount, so nothing to restore"),
    ("handle_restore", HTTP): Cell(
        "called on every request", _T + "http_fallback_alternating_processes"
    ),
    ("handle_restore", WS): Cell(
        "called after the restore", _T + "ws_reconnect_to_another_process"
    ),
    ("handle_restore", RESTART): Cell("called after the restore", _T + "kill_after_save"),
    ("render", SAME): Cell("patches", _T + "ws_events_on_one_process"),
    ("render", HTTP): Cell(
        "full HTML on every request", _T + "http_fallback_alternating_processes"
    ),
    ("render", WS): Cell(
        "full HTML in the mount, full HTML for the first event, then patches",
        _T + "ws_reconnect_to_another_process",
    ),
    ("render", RESTART): Cell(
        "full HTML in the mount, full HTML for the first event, then patches",
        _T + "kill_after_save",
    ),
    ("url", SAME): Cell("kept", _T + "url_kwargs_and_handle_params"),
    ("url", HTTP): Cell(
        "read from the request; persisted fields restored", _T + "url_kwargs_and_handle_params"
    ),
    ("url", WS): Cell(
        "read from the new URL (it wins); persisted fields restored",
        _T + "url_kwargs_and_handle_params",
    ),
    ("url", RESTART): NA("same mount path as the reconnect to another process"),
    ("form", SAME): Cell("kept", _T + "form_mid_validation"),
    ("form", HTTP): Cell("input and errors restored", _T + "form_mid_validation"),
    ("form", WS): Cell("input and errors restored", _T + "form_mid_validation"),
    ("form", RESTART): NA("same mount path as the reconnect to another process"),
    ("wizard", SAME): Cell("kept", _T + "wizard_step"),
    ("wizard", HTTP): Cell("kept: the step and the answers", _T + "wizard_step"),
    ("wizard", WS): Cell("restored: the step and the answers", _T + "wizard_step"),
    ("wizard", RESTART): NA("same mount path as the reconnect to another process"),
    ("legacy", SAME): Cell("kept", _T + "legacy_view"),
    ("legacy", HTTP): Cell("kept (1, 2 across A, B)", _T + "legacy_view"),
    ("legacy", WS): Cell("lost: `mount()` value", _T + "legacy_view"),
    ("legacy", RESTART): NA("same mount path as the reconnect to another process"),
    ("component", SAME): Cell("kept", _T + "components"),
    ("component", HTTP): Cell("event refused (Component not found)", _T + "components"),
    ("component", WS): Cell("lost: mounts fresh", _T + "components"),
    ("component", RESTART): NA("same mount path as the reconnect to another process"),
    ("dropdown", SAME): Cell("kept", _T + "components"),
    ("dropdown", HTTP): Cell("event refused (Component not found)", _T + "components"),
    ("dropdown", WS): Cell("lost: closed, with a new component id", _T + "components"),
    ("dropdown", RESTART): NA("same mount path as the reconnect to another process"),
    ("async", SAME): Cell("result delivered", _T + "async_work_in_flight"),
    ("async", HTTP): NA("the HTTP fallback waits for the work inside the request"),
    ("async", WS): Cell(
        "result lost; a persisted in-progress flag stays set", _T + "async_work_in_flight"
    ),
    ("async", RESTART): NA("the work dies with its process; same as the reconnect"),
    ("tick", SAME): Cell("keeps ticking", _T + "tick"),
    ("tick", HTTP): NA("ticks run only on a connection"),
    ("tick", WS): Cell(
        "restarts on the new process; the counters continue",
        _T + "tick",
    ),
    ("tick", RESTART): NA("same mount path as the reconnect to another process"),
    ("stream", SAME): Cell("kept", _T + "stream_rows"),
    ("stream", HTTP): NA("not measured"),
    ("stream", WS): Cell("lost: the mount renders only `mount()`'s rows", _T + "stream_rows"),
    ("stream", RESTART): NA("same mount path as the reconnect to another process"),
    ("held_save", SAME): NA("the event's own answer waits for its save"),
    ("held_save", HTTP): NA("not measured"),
    ("held_save", WS): NA("see the restart column"),
    ("held_save", RESTART): Cell(
        "event lost; the previous saved state restored", _T + "kill_while_save_held"
    ),
}

# ---------------------------------------------------------------------------
# 2. Sessions, sockets and identity across processes
# ---------------------------------------------------------------------------

SESSION_COLUMNS = (
    (SAME, "Same process"),
    (WS, "Other process"),
)

SESSION_ROWS = (
    Row(
        "login",
        "Log in through one process, then use another",
        "Use a session engine every process shares.",
    ),
    Row(
        "logout",
        "Log out through one process while a socket is open on another",
        "Nothing: the open socket refuses its next event.",
    ),
    Row(
        "tenant",
        "Same session, another tenant",
        "Nothing: persisted state is bound to the tenant.",
    ),
    Row(
        "two_sockets_same_route",
        "Two sockets of one session on the same route",
        "Expect last write wins; keep shared data in your own storage.",
    ),
    Row(
        "two_sockets_other_route",
        "Two sockets of one session on different routes",
        "Nothing: state is per route.",
    ),
    Row(
        "push_redis",
        "`push_to_view()` with a Redis channel layer",
        "Use a channel layer every process shares.",
    ),
    Row(
        "push_memory",
        "`push_to_view()` with an in-memory channel layer",
        "Do not run more than one process with it.",
    ),
    Row(
        "presence_redis",
        "Presence (Redis backend and channel layer)",
        'Set `PRESENCE_BACKEND = "redis"` and a shared channel layer.',
    ),
    Row(
        "presence_memory",
        "Presence (memory backend, in-memory channel layer)",
        "Do not run more than one process with it.",
    ),
    Row(
        "clock_skew",
        "Persisted state read by a process whose clock is behind the writer's",
        "Keep the processes' clocks in sync.",
    ),
)

SESSION_CELLS: Dict[Tuple[str, str], Entry] = {
    ("login", SAME): Cell("authenticated", _T + "login_then_other_process"),
    ("login", WS): Cell("authenticated (HTTP and WebSocket)", _T + "login_then_other_process"),
    ("logout", SAME): NA("Django's own logout"),
    ("logout", WS): Cell(
        "next event refused, socket closed 4403", _T + "logout_while_socket_open_elsewhere"
    ),
    ("tenant", SAME): NA("same binding rule; measured across processes"),
    ("tenant", WS): Cell(
        "other tenant mounts fresh; own tenant restored", _T + "tenant_scoping_across_processes"
    ),
    ("two_sockets_same_route", SAME): Cell(
        "last write wins (a stale socket overwrites)", _T + "two_sockets_same_route"
    ),
    ("two_sockets_same_route", WS): Cell(
        "last write wins (a stale socket overwrites)", _T + "two_sockets_same_route"
    ),
    ("two_sockets_other_route", SAME): NA("same binding rule; measured across processes"),
    ("two_sockets_other_route", WS): Cell("independent", _T + "two_sockets_other_route"),
    ("push_redis", SAME): NA("measured across processes only"),
    ("push_redis", WS): Cell("delivered", _T + "push_to_view_across_processes"),
    ("push_memory", SAME): Cell("delivered", _T + "push_with_in_memory_layer"),
    ("push_memory", WS): Cell("not delivered", _T + "push_with_in_memory_layer", gap=True),
    ("presence_redis", SAME): NA("measured across processes only"),
    ("presence_redis", WS): Cell(
        "join and leave seen by the other process", _T + "presence_across_processes"
    ),
    ("presence_memory", SAME): NA("measured across processes only"),
    ("presence_memory", WS): Cell(
        "each process counts only its own", _T + "presence_with_memory_backend", gap=True
    ),
    ("clock_skew", SAME): NA("one clock"),
    ("clock_skew", WS): Cell(
        "discarded: the view mounts fresh", _T + "clock_skew_between_processes", gap=True
    ),
}

# ---------------------------------------------------------------------------
# 3. Session engines, two processes sharing one store
# ---------------------------------------------------------------------------

ENGINE_COLUMNS = (
    (HTTP, "HTTP fallback on another process"),
    (WS, "WebSocket reconnect to another process"),
)

ENGINE_ROWS = (
    Row("db", "`db` (a database both processes use)", "Supported."),
    Row("file", "`file` (a directory both processes use)", "Only on storage every process shares."),
    Row(
        "cache_file",
        "`cache`, `FileBasedCache` in a shared directory",
        "Only with a cache every process shares.",
    ),
    Row("cache_redis", "`cache`, Django's `RedisCache`", "A Redis with persistence."),
    Row("cached_db", "`cached_db`, `RedisCache` plus the database", "Supported."),
    Row(
        "cache_locmem",
        "`cache`, `LocMemCache` (one per process)",
        "Never with more than one process.",
    ),
)

ENGINE_CELLS: Dict[Tuple[str, str], Entry] = {}
for _engine in ("db", "file", "cache_file", "cache_redis", "cached_db"):
    ENGINE_CELLS[(_engine, HTTP)] = Cell("kept", _T + "session_engine_across_processes")
    ENGINE_CELLS[(_engine, WS)] = Cell("restored", _T + "session_engine_across_processes")
ENGINE_CELLS[("cache_locmem", HTTP)] = Cell(
    "500 error, the session cookie is deleted; the next request starts a new session",
    _T + "session_engine_across_processes",
    gap=True,
)
ENGINE_CELLS[("cache_locmem", WS)] = Cell(
    "mount fails with an error", _T + "session_engine_across_processes", gap=True
)

# ---------------------------------------------------------------------------
# 4. Session reads and writes per event (db engine), counted not timed
# ---------------------------------------------------------------------------

COST_COLUMNS = (
    ("loads", "Session loads"),
    ("saves", "Session saves"),
    ("selects", "`SELECT`s on `django_session`"),
    ("updates", "`UPDATE`s on `django_session`"),
)

COST_ROWS = (
    Row("ws_event", "WebSocket event on a mounted view", ""),
    Row("http_event", "HTTP fallback event (page POST)", ""),
)

COST_CELLS: Dict[Tuple[str, str], Entry] = {
    ("ws_event", "loads"): Cell("1", _T + "session_cost_per_event"),
    ("ws_event", "saves"): Cell("1", _T + "session_cost_per_event"),
    ("ws_event", "selects"): Cell("2", _T + "session_cost_per_event"),
    ("ws_event", "updates"): Cell("1", _T + "session_cost_per_event"),
    ("http_event", "loads"): Cell("1", _T + "session_cost_per_event"),
    ("http_event", "saves"): Cell("2", _T + "session_cost_per_event"),
    ("http_event", "selects"): Cell("1", _T + "session_cost_per_event"),
    ("http_event", "updates"): Cell("2", _T + "session_cost_per_event"),
}

TABLES = (
    Table("state", STATE_COLUMNS, STATE_ROWS, STATE_CELLS),
    Table("sessions", SESSION_COLUMNS, SESSION_ROWS, SESSION_CELLS),
    Table("engines", ENGINE_COLUMNS, ENGINE_ROWS, ENGINE_CELLS),
    Table("cost", COST_COLUMNS, COST_ROWS, COST_CELLS),
)

ALL_CELLS: Dict[Tuple[str, str, str], Entry] = {
    (table.name, row, col): entry for table in TABLES for (row, col), entry in table.cells.items()
}


def cells_of_test(test: str) -> List[Tuple[str, str, str]]:
    return [
        key for key, entry in ALL_CELLS.items() if isinstance(entry, Cell) and entry.test == test
    ]


# ---------------------------------------------------------------------------
# Rendering into the docs page
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    return text.replace("|", "\\|")


def _cell_text(entry: Entry) -> str:
    if isinstance(entry, NA):
        return "n/a (%s)" % entry.reason
    text = entry.outcome + (" **(known gap)**" if entry.gap else "")
    return "%s<br><sub>`%s`</sub>" % (text, entry.test)


def render_table(table: Table) -> str:
    with_author = any(row.author for row in table.rows)
    header = ["", *[label for _, label in table.columns]]
    if with_author:
        header.append("What the app author must do")
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for row in table.rows:
        cells = [row.label]
        for key, _ in table.columns:
            cells.append(_cell_text(table.cells[(row.key, key)]))
        if with_author:
            cells.append(row.author)
        lines.append("| " + " | ".join(_escape(c) for c in cells) + " |")
    return "\n".join(lines)


def _region(name: str) -> Tuple[str, str]:
    return (
        "<!-- BEGIN GENERATED survival-%s: python -m tests.integration.multinode.survival_table"
        " --write -->" % name,
        "<!-- END GENERATED survival-%s -->" % name,
    )


def render_doc(text: str) -> str:
    for table in TABLES:
        begin, end = _region(table.name)
        pattern = re.compile(re.escape(begin) + r"\n(?:.*?\n)?" + re.escape(end), re.S)
        if not pattern.search(text):
            raise ValueError("%s has no region for table %r" % (DOC, table.name))
        body = begin + "\n" + render_table(table) + "\n" + end
        text = pattern.sub(lambda _m: body, text, count=1)
    return text


def main(argv: List[str]) -> int:
    text = DOC.read_text(encoding="utf-8")
    rendered = render_doc(text)
    if "--write" in argv:
        if rendered != text:
            DOC.write_text(rendered, encoding="utf-8")
        return 0
    if rendered != text:
        sys.stderr.write("%s is out of date; run with --write\n" % DOC)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
