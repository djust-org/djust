"""What one connected view holds, who holds it, and how it grows (#3998).

Each open WebSocket keeps a consumer, a view and a Rust renderer, so a
process's memory is open sockets times what one of them holds. These tests
measure that with the accounting in ``_view_memory`` (a ``sys.getsizeof`` walk
on the Python side, ``RustLiveView.retained_bytes()`` on the Rust side) over a
real ``WebsocketCommunicator``. No test reads a resident size or a clock: the
numbers are counts of bytes that do not depend on the machine, and the laws
are fits to four sizes. The Python side is measured as CPython 3.12 sizes
objects (``_view_memory.reference_sizeof``), whatever interpreter runs the
test: a tuple is 8 bytes larger from 3.14 and a free-threaded build adds 16
bytes to most objects, and the published numbers are stated for 3.12.

* the attribution: every owner, for a counter, a 50 KB static page, a
  1,000-row list, 20 components and a form;
* the reductions, each against the same view with its switch turned off
  (``_per_turn``): the compact fingerprint and the one shared tree;
* what is shared between two users' sockets (the template source text, and
  nothing that holds a value of either user), and what a change made outside
  a turn does to a fingerprint the socket kept;
* the growth laws, and the coefficients ``docs/website/guides/scaling.md``
  publishes, which must stay within 10% of what is measured here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

import pytest

from djust import _per_turn
from djust.tests import _per_turn_harness as harness
from djust.tests import _view_memory as vm
from djust.tests import _view_memory_scenarios as scenarios

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

KB = 1024

#: label, view, the one event sent after the mount.
SCENARIOS: List[Tuple[str, type, Tuple[str, Any]]] = [
    ("counter", scenarios.Counter, ("inc", None)),
    ("static_50kb", scenarios.static_page(50), ("inc", None)),
    ("rows_1000", scenarios.row_list(1000), ("touch", None)),
    ("components_20", scenarios.Components20, ("rename", {"value": "x"})),
    ("form", scenarios.FormView, ("validate_field", {"field": "name", "value": "TOOLONG"})),
]

STATIC_SIZES = (10, 25, 50, 100)
ROW_COUNTS = (125, 250, 500, 1000)

#: Owners whose size follows the PAGE (its nodes and its HTML).
PAGE_OWNERS = (
    "rust.vdom",
    "rust.last_html",
    "rust.node_html_cache",
    "rust.text_node_index",
    "rust.fragment_text_map",
    "rust.loop_render_cache",
    "py.recovery_html",
)
#: Owners whose size follows the STATE the view holds.
STATE_OWNERS = (
    "py.state",
    "py.dirty_baseline",
    "py.context_fingerprints",
    "rust.state",
)


async def measured(view_class: type, event: Tuple[str, Any]) -> Dict[str, int]:
    return await vm.measure(
        view_class, [event], module=scenarios.MODULE, sizeof=vm.reference_sizeof
    )


def page_part(report: Dict[str, int]) -> int:
    return sum(report[owner] for owner in PAGE_OWNERS)


def state_part(report: Dict[str, int]) -> int:
    return sum(report[owner] for owner in STATE_OWNERS)


# --- the attribution ------------------------------------------------------------------

TABLE: Dict[str, Dict[str, int]] = {}


@pytest.mark.parametrize("label,view_class,event", SCENARIOS, ids=[s[0] for s in SCENARIOS])
async def test_every_byte_has_an_owner(label, view_class, event):
    report = await measured(view_class, event)
    TABLE[label] = report
    assert report["total"] == sum(report[owner] for owner in vm.OWNERS)
    assert report["total"] == report["py.total"] + report["rust.total"]
    # Each side holds something, and the page was rendered into a tree.
    assert report["py.state"] > 0 and report["py.view_other"] > 0
    assert report["rust.vdom"] > 0 and report["rust.vdom_nodes"] >= 3
    # The source text is shared by every view of the class: a view owns none.
    assert report["rust.template_source"] == 0


async def test_a_counter_is_tens_of_kilobytes():
    """The floor: a view with two numbers and a five-node page."""
    report = await measured(scenarios.Counter, ("inc", None))
    assert 20 * KB < report["total"] < 80 * KB, report


async def test_the_tree_is_most_of_a_static_page():
    """A 50 KB page of markup: the Rust tree is the largest owner by far, and
    the state-driven owners are what a counter has."""
    report = await measured(scenarios.static_page(50), ("inc", None))
    counter = await measured(scenarios.Counter, ("inc", None))
    assert report["rust.vdom"] > 0.5 * report["total"]
    assert state_part(report) < state_part(counter) + KB
    # Three more copies of the page text, each about one page long.
    for owner in ("rust.last_html", "rust.node_html_cache", "py.recovery_html"):
        assert 0.9 < report[owner] / report["page_bytes"] < 1.1, owner


async def test_the_tree_and_the_state_copy_are_most_of_a_list_page():
    report = await measured(scenarios.row_list(1000), ("touch", None))
    assert report["rust.vdom"] + report["rust.state"] > 0.6 * report["total"]
    # The Rust copy of the rows is about three times the Python rows.
    assert 2.5 < report["rust.state"] / report["py.state"] < 3.5


# --- the reductions, each against its switch turned off -------------------------------


async def with_switch_off(
    monkeypatch: Any, view_class: type, event: Tuple[str, Any], *off: str
) -> Dict[str, int]:
    with harness.shortcuts(monkeypatch, *off):
        for name in off:
            assert not _per_turn.on(name), f"{name} was not turned off"
        return await measured(view_class, event)


def fingerprints(report: Dict[str, int]) -> int:
    return report["py.dirty_baseline"] + report["py.context_fingerprints"]


async def test_the_fingerprints_of_a_list_are_a_seventh_of_what_they_were(monkeypatch):
    """Both reductions together, on the issue's shape: the rows changed once
    since mount, so the baseline and the last render differ by one row."""
    view_class = scenarios.row_list(500)
    before = await with_switch_off(
        monkeypatch, view_class, ("touch", None), "compact_fingerprints", "shared_fingerprints"
    )
    after = await measured(view_class, ("touch", None))
    assert fingerprints(after) * 6 < fingerprints(before)
    # Two equal trees before, each larger than the rows themselves (the issue).
    assert 0.9 < before["py.dirty_baseline"] / before["py.context_fingerprints"] < 1.1
    assert before["py.dirty_baseline"] > 2 * before["py.state"]
    # Nothing else moved.
    for owner in vm.OWNERS:
        if owner not in ("py.dirty_baseline", "py.context_fingerprints"):
            assert after[owner] == pytest.approx(before[owner], rel=0.01, abs=64), owner


async def test_compact_alone_and_shared_alone(monkeypatch):
    view_class = scenarios.row_list(500)
    neither = await with_switch_off(
        monkeypatch, view_class, ("touch", None), "compact_fingerprints", "shared_fingerprints"
    )
    wide = await with_switch_off(monkeypatch, view_class, ("touch", None), "compact_fingerprints")
    apart = await with_switch_off(monkeypatch, view_class, ("touch", None), "shared_fingerprints")
    both = await measured(view_class, ("touch", None))
    # Shared, still wide: one wide tree. Compact, not shared: two compact trees.
    assert fingerprints(wide) < 0.55 * fingerprints(neither)
    assert fingerprints(apart) < 0.34 * fingerprints(neither)
    assert fingerprints(both) < 0.55 * fingerprints(apart)
    assert fingerprints(both) < 0.34 * fingerprints(wide)


async def test_an_unchanged_list_is_one_tree_and_the_same_object(monkeypatch):
    recorded = vm.Recorded()
    key = await harness.new_session_key_async()
    with harness.ws_settings(scenarios.MODULE):
        comm, _mount = await vm.mount_recorded(scenarios.row_list(125), key, recorded)
        try:
            view = recorded.consumers[-1].view_instance
            for _ in range(3):
                await harness.event(comm, "retitle")
                baseline = view._dirty_baseline["items"]
                held = view._prev_context_fingerprints["items"]
                assert baseline[0] == "c" and baseline[2] is held
            await harness.event(comm, "touch")
            baseline = view._dirty_baseline["items"]
            held = view._prev_context_fingerprints["items"]
            assert baseline[2] is not held and baseline[2] != held
            same = [a is b for a, b in zip(held[1], baseline[2][1])]
            assert same.count(False) == 1 and not same[0], "all rows but the changed one"
            assert "items" in view.changed_fields and view.is_dirty
            # mark_clean takes a new baseline: equal to the held one, so it IS it.
            view.mark_clean()
            assert view._dirty_baseline["items"][2] is view._prev_context_fingerprints["items"]
            assert "items" not in view.changed_fields
        finally:
            await comm.disconnect()


async def test_a_baseline_taken_mid_turn_is_of_the_state_as_it_is_now():
    """``mark_clean()`` in a handler runs before the turn's render, so the
    fingerprint the last render left describes the rows BEFORE the handler
    changed them. The new baseline takes that object only when it is equal."""
    recorded = vm.Recorded()
    key = await harness.new_session_key_async()
    with harness.ws_settings(scenarios.MODULE):
        comm, _mount = await vm.mount_recorded(scenarios.row_list(8), key, recorded)
        try:
            view = recorded.consumers[-1].view_instance
            await harness.event(comm, "retitle")
            stale = view._prev_context_fingerprints["items"]
            view.items[0]["name"] = "changed, then saved"
            view.mark_clean()
            assert view._dirty_baseline["items"][2] is not stale
            assert view._dirty_baseline["items"][2] != stale
            assert "items" not in view.changed_fields
            view.items[0]["name"] = "changed again"
            assert "items" in view.changed_fields
        finally:
            await comm.disconnect()


# --- a change made outside a turn, per shape ------------------------------------------


def _append(view: Any) -> str:
    view.items.append({"id": 9001, "name": "OUTSIDE-append", "qty": 1, "active": True, "tags": []})
    return "OUTSIDE-append"


def _row_in_place(view: Any) -> str:
    view.items[2]["name"] = "OUTSIDE-row"
    return "OUTSIDE-row"


def _nested_in_place(view: Any) -> str:
    view.items[3]["tags"].append("OUTSIDE-tag")
    return "OUTSIDE-tag"


def _rebind(view: Any) -> str:
    view.items = scenarios.rows(4) + [
        {"id": 9002, "name": "OUTSIDE-rebind", "qty": 1, "active": True, "tags": []}
    ]
    return "OUTSIDE-rebind"


def _back_to_the_baseline(view: Any) -> str:
    # Changed in a turn first (below), then put back outside one: the value
    # equals the mount-time baseline again, which the socket still holds.
    view.items[0]["name"] = "item 0"
    return "item 0<"


OUTSIDE = [
    ("append", _append),
    ("row_in_place", _row_in_place),
    ("nested_in_place", _nested_in_place),
    ("rebind", _rebind),
    ("back_to_the_baseline", _back_to_the_baseline),
]


@pytest.mark.parametrize("label,mutate", OUTSIDE, ids=[shape[0] for shape in OUTSIDE])
@pytest.mark.parametrize("switches", ["on", "off"])
async def test_a_change_between_turns_reaches_the_next_frame(monkeypatch, switches, label, mutate):
    """A timer, a signal handler or a careless helper changes state while no
    turn is running. The next turn's snapshots are both taken after it, so the
    only thing that can see it is the fingerprint the socket kept from the
    last render. That fingerprint is now built from the baseline's rows, so
    this is where a shared tree that followed the state would show: the frame
    would be missing the change."""
    off = () if switches == "on" else ("compact_fingerprints", "shared_fingerprints")
    recorded = vm.Recorded()
    key = await harness.new_session_key_async()
    with harness.shortcuts(monkeypatch, *off), harness.ws_settings(scenarios.MODULE):
        comm, _mount = await vm.mount_recorded(scenarios.row_list(8), key, recorded)
        try:
            view = recorded.consumers[-1].view_instance
            await harness.event(comm, "retitle")
            await harness.event(comm, "touch")  # row 0 now differs from the baseline
            expected = mutate(view)
            frames = await harness.event_frames(comm, "retitle")
        finally:
            await comm.disconnect()
    text = "".join(str(frame) for frame in frames)
    assert frames[-1]["type"] in ("patch", "html_update"), frames
    assert expected.rstrip("<") in text, (label, text[:400])


# --- two users ------------------------------------------------------------------------


def _tuples(root: Any) -> Set[int]:
    """The ``id`` of every non-empty tuple under *root* (the empty one is a singleton)."""
    out: Set[int] = set()
    stack = [root]
    while stack:
        obj = stack.pop()
        if isinstance(obj, dict):
            stack.extend(obj.values())
        elif isinstance(obj, (list, tuple, frozenset)):
            if isinstance(obj, tuple) and obj:
                if id(obj) in out:
                    continue
                out.add(id(obj))
            stack.extend(obj)
    return out


async def test_two_users_share_the_template_text_and_nothing_of_each_other():
    # Two visitors, each with a session of their own.
    recorded = vm.Recorded()
    view_class = scenarios.row_list(8, mark="twosessions")  # a template no other test mounts
    with harness.ws_settings(scenarios.MODULE):
        comms = []
        try:
            for _visitor in ("ann", "bob"):
                key = await harness.new_session_key_async()
                comm, _mount = await vm.mount_recorded(view_class, key, recorded)
                comms.append(comm)
            first, second = (consumer.view_instance for consumer in recorded.consumers)
            first.items[1]["name"] = "ANN-PRIVATE"
            second.items[1]["name"] = "BOB-PRIVATE"
            ann_frames = await harness.event_frames(comms[0], "retitle")
            bob_frames = await harness.event_frames(comms[1], "retitle")

            # Shared: the template source text, one copy for both sockets.
            a = first._rust_view.retained_bytes()
            b = second._rust_view.retained_bytes()
            # ``holders`` counts the references to ONE allocation, so two
            # means both views read the same text. No address is reported.
            assert a["template_source_holders"] == b["template_source_holders"] == 2
            assert not any("address" in key for key in a)
            assert a["template_source"] == b["template_source"] == 0
            assert a["shared_template_source"] == len(view_class.template)

            # Not shared: anything that holds a value. No tuple of one view's
            # fingerprints is in the other's, and neither user's frames or
            # retained HTML carry the other's row.
            ann_tuples = _tuples(first._prev_context_fingerprints) | _tuples(first._dirty_baseline)
            bob_tuples = _tuples(second._prev_context_fingerprints) | _tuples(
                second._dirty_baseline
            )
            assert ann_tuples and bob_tuples and not (ann_tuples & bob_tuples)
            assert "ANN-PRIVATE" in str(ann_frames) and "BOB-PRIVATE" not in str(ann_frames)
            assert "BOB-PRIVATE" in str(bob_frames) and "ANN-PRIVATE" not in str(bob_frames)
            assert "BOB-PRIVATE" not in (recorded.consumers[0]._recovery_html or "")
            assert "ANN-PRIVATE" not in (recorded.consumers[1]._recovery_html or "")
            assert first.items is not second.items
        finally:
            for comm in comms:
                await comm.disconnect()


async def test_a_second_socket_of_the_same_page_adds_no_template_text():
    """One socket or two: the shared text is counted once, by neither."""
    view_class = scenarios.static_page(30)  # a size no other test mounts
    recorded = vm.Recorded()
    with harness.ws_settings(scenarios.MODULE):
        comms = []
        try:
            for _ in range(2):
                key = await harness.new_session_key_async()
                comm, _mount = await vm.mount_recorded(view_class, key, recorded)
                comms.append(comm)
            reports = [
                consumer.view_instance._rust_view.retained_bytes()
                for consumer in recorded.consumers
            ]
        finally:
            for comm in comms:
                await comm.disconnect()
    assert reports[0]["total"] == reports[1]["total"]
    assert reports[0]["shared_template_source"] > 30_000
    assert reports[0]["template_source_holders"] == 2


# --- the growth laws ------------------------------------------------------------------


async def static_series() -> Tuple[List[int], List[Dict[str, int]]]:
    reports = [await measured(scenarios.static_page(n), ("inc", None)) for n in STATIC_SIZES]
    return [r["page_bytes"] for r in reports], reports


async def row_series() -> Tuple[List[int], List[Dict[str, int]]]:
    reports = [await measured(scenarios.row_list(n), ("touch", None)) for n in ROW_COUNTS]
    return list(ROW_COUNTS), reports


async def test_a_static_page_costs_a_fixed_number_of_bytes_per_byte_of_page():
    pages, reports = await static_series()
    intercept, slope, worst = vm.fit(pages, [r["total"] for r in reports])
    assert worst < 0.03, (worst, [r["total"] for r in reports])
    # Linear: sixteen times the page is not more than sixteen times the cost.
    assert reports[-1]["total"] < 10.5 * reports[0]["total"]
    # What does not grow with the page is what a counter costs.
    counter = await measured(scenarios.Counter, ("inc", None))
    assert abs(intercept - counter["total"]) < 8 * KB, (intercept, counter["total"])
    assert 8 < slope < 25, slope


async def test_a_list_costs_a_fixed_number_of_bytes_per_row():
    rows, reports = await row_series()
    intercept, slope, worst = vm.fit(rows, [r["total"] for r in reports])
    assert worst < 0.03, (worst, [r["total"] for r in reports])
    for owner in ("rust.vdom", "rust.state", "py.state", "py.dirty_baseline"):
        _i, _s, owner_worst = vm.fit(rows, [r[owner] for r in reports])
        assert owner_worst < 0.03, (owner, owner_worst)
    # The second tree does not grow with the rows any more: one row and a
    # tuple of references (8 bytes a row).
    _i, per_row, _w = vm.fit(rows, [r["py.context_fingerprints"] for r in reports])
    assert per_row < 16, per_row


# --- the published model --------------------------------------------------------------

REPO = Path(__file__).resolve().parents[3]
GUIDE = REPO / "docs" / "website" / "guides" / "scaling.md"
PUBLISHED = re.compile(r"<!-- view-memory:([^>]*)-->")


def published() -> Dict[str, float]:
    match = PUBLISHED.search(GUIDE.read_text())
    assert match, "the scaling guide no longer carries the view-memory coefficients"
    return {
        key: float(value) for key, value in (item.split("=") for item in match.group(1).split())
    }


async def test_the_published_coefficients_are_what_is_measured():
    """``docs/website/guides/scaling.md`` states per-view memory as a formula.
    Its numbers are fitted here; a change that moves one by more than 10%
    fails until the guide is updated."""
    if not (REPO / "docs").is_dir():
        pytest.skip("installed without the repository's docs")
    stated = published()

    counter = await measured(scenarios.Counter, ("inc", None))
    pages, static = await static_series()
    rows, listed = await row_series()

    _i, static_per_page_byte, _w = vm.fit(pages, [page_part(r) for r in static])
    _i, table_per_page_byte, _w = vm.fit(
        [r["page_bytes"] for r in listed], [page_part(r) for r in listed]
    )
    _i, per_state_byte, _w = vm.fit(
        [r["py.state"] for r in listed], [state_part(r) for r in listed]
    )
    _i, per_row, _w = vm.fit(rows, [r["total"] for r in listed])
    _i, static_total_per_page_byte, _w = vm.fit(pages, [r["total"] for r in static])

    measured_now = {
        "base_kb": counter["total"] / KB,
        "static_page": static_per_page_byte,
        "table_page": table_per_page_byte,
        "state": per_state_byte,
        "row_kb": per_row / KB,
        "static_total": static_total_per_page_byte,
    }
    assert set(stated) == set(measured_now)
    drift = {
        key: (stated[key], round(value, 2))
        for key, value in measured_now.items()
        if abs(value - stated[key]) > 0.10 * stated[key]
    }
    assert drift == {}, f"published vs measured: {drift}"

    # The formula, applied to a size it was not fitted to, lands within 10%.
    check = await measured(scenarios.row_list(750), ("touch", None))
    predicted = (
        stated["base_kb"] * KB
        + stated["table_page"] * check["page_bytes"]
        + stated["state"] * check["py.state"]
    )
    assert abs(predicted - check["total"]) < 0.10 * check["total"], (predicted, check["total"])
    check = await measured(scenarios.static_page(75), ("inc", None))
    predicted = stated["base_kb"] * KB + stated["static_total"] * check["page_bytes"]
    assert abs(predicted - check["total"]) < 0.10 * check["total"], (predicted, check["total"])


#: The rows of the guide's "Who holds it" table: label -> the owners it sums.
TABLE_ROWS = {
    "Rust: last rendered tree": ("rust.vdom",),
    "Rust: copy of the state": ("rust.state",),
    "Rust: text-node index": ("rust.text_node_index",),
    "Rust: last HTML, per-node HTML cache, loop cache, fragment map": (
        "rust.last_html",
        "rust.node_html_cache",
        "rust.loop_render_cache",
        "rust.fragment_text_map",
    ),
    "Python: the view's state": ("py.state",),
    "Python: change-detection fingerprints": ("py.dirty_baseline", "py.context_fingerprints"),
    "Python: recovery HTML": ("py.recovery_html",),
    "Python: view and consumer, everything else": (
        "py.view_other",
        "py.consumer_other",
        "py.context_refs",
    ),
    "Rust: everything else": ("rust.other", "rust.template_source"),
}


async def test_the_published_table_is_what_is_measured():
    """Every cell of the guide's per-owner table, against the same five views.

    Within 10%, or 512 bytes for the cells that are a few hundred bytes (a
    Django or Python version moves those by a slot or two)."""
    if not (REPO / "docs").is_dir():
        pytest.skip("installed without the repository's docs")
    text = GUIDE.read_text()
    cells: Dict[str, List[int]] = {}
    for line in text.splitlines():
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if parts and parts[0] in TABLE_ROWS:
            cells[parts[0]] = [int(part.replace(",", "")) for part in parts[1:]]
    assert set(cells) == set(TABLE_ROWS), set(TABLE_ROWS) - set(cells)

    reports = [await measured(view_class, event) for _label, view_class, event in SCENARIOS]
    wrong = []
    for label, owners in TABLE_ROWS.items():
        assert len(cells[label]) == len(SCENARIOS), label
        for (scenario, _view, _event), report, stated in zip(SCENARIOS, reports, cells[label]):
            now = sum(report[owner] for owner in owners)
            if abs(now - stated) > max(0.10 * stated, 512):
                wrong.append((label, scenario, stated, now))
    assert wrong == [], wrong
    # The rows are every owner, once each.
    listed = [owner for owners in TABLE_ROWS.values() for owner in owners]
    assert sorted(listed) == sorted(vm.OWNERS)
