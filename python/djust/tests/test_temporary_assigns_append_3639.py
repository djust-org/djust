"""``temporary_assigns`` + ``dj-update="append"`` over the patch path (#3639, shape 2).

``temporary_assigns = {"rows": []}`` with a container marked
``dj-update="append"`` is the documented way to send only the NEW rows. Over a
real WebSocket the page never got them:

* ``self.k += 1; self.rows = [new]`` replied ``patch`` with NO row. The key was
  left out of the changed keys ("they are reset to new objects after each
  render"), so the node reading ``rows`` was not re-rendered.
* ``self.rows = [new]`` as the ONLY change replied with patches that rewrote the
  first row to the new one and removed the other four: the differ diffs the
  container against the stored tree like any other list, and nothing on the
  patch path honours ``dj-update="append"`` (the client implements it only for a
  full ``html`` frame, ``applyDjUpdateElements``).

So putting the key back in the changed keys alone would trade a missing row for
a vanished history. The fix has two halves, one per file:

* ``mixins/rust_bridge.py`` leaves a temporary key out of the changed keys only
  while it still holds its reset default, so an ASSIGNED key re-renders its node.
* the differ treats ``dj-update="append"`` / ``"prepend"`` as accumulating on a
  view that declares ``temporary_assigns``: the new children join the stored
  ones (``splice_ignore_subtrees``), so the patch is an ``InsertChild`` and the
  rows already on the page are left alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from djust import LiveView
from djust.decorators import event_handler

from .test_stream_only_handlers_3639 import drive as _drive_over_a_real_socket

pytestmark = [pytest.mark.django_db]

MODULE = __name__


@dataclass
class Row:
    id: int
    body: str


ROWS = [Row(i, f"BODY{i}") for i in range(5)]


def _tpl(view, mode="append", extra="|{{ k }}"):
    update = f' dj-update="{mode}"' if mode else ""
    return (
        f'<div dj-view="{MODULE}.{view}" dj-id="0"><div id="list"{update}>'
        '{% for r in rows %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        f"</div>{extra}</div>"
    )


class Temp(LiveView):
    temporary_assigns = {"rows": []}
    template = _tpl("Temp")

    def mount(self, request, **kwargs):
        self.rows = list(ROWS)
        self.k = 0

    @event_handler()
    def add(self, **kwargs):
        """The pattern in LiveView's docstring, next to another change."""
        self.k += 1
        self.rows = [Row(100 + self.k, f"NEW{self.k}")]

    @event_handler()
    def add_only(self, **kwargs):
        """The row assignment is the ONLY change (BEST_PRACTICES' chat view)."""
        self.rows = [Row(200, "ONLY")]

    @event_handler()
    def add_two(self, **kwargs):
        self.rows = [Row(301, "T1"), Row(302, "T2")]

    @event_handler()
    def bump(self, **kwargs):
        """Changes something else; the rows must be left exactly as they are."""
        self.k += 1

    @event_handler()
    def idle(self, **kwargs):
        """Changes nothing."""

    @event_handler()
    def clear_rows(self, **kwargs):
        """Assigning the default is not a change."""
        self.rows = []


class TempNoK(Temp):
    """The template reads nothing but ``rows`` (the issue's second example)."""

    template = _tpl("TempNoK", extra="")


class TempPrepend(Temp):
    template = _tpl("TempPrepend", mode="prepend")


class TempNoAppend(Temp):
    """``temporary_assigns`` WITHOUT ``dj-update``: the template is authoritative."""

    template = _tpl("TempNoAppend", mode="")


class TempStream(LiveView):
    """``temporary_assigns`` empties the stream items after every render too
    (``_reset_temporary_assigns`` -> ``_reset_streams``), so the stream container
    needs ``dj-update="append"`` for the stored rows to stay."""

    temporary_assigns = {"unused": []}
    template = (
        f'<div dj-view="{MODULE}.TempStream" dj-id="0">'
        '<div id="list" dj-stream="rows" dj-update="append">'
        '{% for r in streams.rows %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div></div>"
    )

    def mount(self, request, **kwargs):
        self.stream("rows", ROWS)

    @event_handler()
    def ins(self, **kwargs):
        self.stream_insert("rows", Row(100, "NEW"))

    @event_handler()
    def idle(self, **kwargs):
        """Changes nothing."""


class AppendNoTemp(LiveView):
    """``dj-update="append"`` on a view that declares no ``temporary_assigns``:
    the full list is re-rendered every time, so a removal must still reach the
    page as before."""

    template = _tpl("AppendNoTemp")

    def mount(self, request, **kwargs):
        self.rows = list(ROWS)
        self.k = 0

    @event_handler()
    def drop_first(self, **kwargs):
        self.rows = self.rows[1:]

    @event_handler()
    def add(self, **kwargs):
        self.k += 1
        self.rows = [*self.rows, Row(100 + self.k, f"NEW{self.k}")]


async def drive(view, events):
    return await _drive_over_a_real_socket(view, events, module=MODULE)


def kinds(frame):
    return [p["type"] for p in frame.get("patches") or []]


@pytest.mark.asyncio
class TestAssignedRowsReachThePage:
    async def test_a_row_assigned_next_to_another_change_is_appended(self):
        (frame,) = await drive("Temp", ["add"])
        assert frame["type"] == "patch", frame
        inserts = [p for p in frame["patches"] if p["type"] == "InsertChild"]
        assert len(inserts) == 1 and inserts[0]["index"] == len(ROWS), frame
        assert "RemoveChild" not in kinds(frame), frame
        assert "NEW1" in str(inserts[0]), frame

    async def test_the_rows_already_on_the_page_are_not_touched(self):
        (frame,) = await drive("Temp", ["add"])
        assert set(kinds(frame)) <= {"InsertChild", "SetText"}, frame

    async def test_consecutive_assignments_keep_appending(self):
        first, second = await drive("Temp", ["add", "add"])
        assert [p["index"] for p in first["patches"] if p["type"] == "InsertChild"] == [5]
        assert [p["index"] for p in second["patches"] if p["type"] == "InsertChild"] == [6]
        assert "RemoveChild" not in kinds(second), second

    async def test_a_row_assigned_as_the_only_change_is_appended_not_swapped_in(self):
        """Before: the first row was rewritten to the new one and the rest removed."""
        (frame,) = await drive("Temp", ["add_only"])
        assert frame["type"] == "patch", frame
        assert kinds(frame) == ["InsertChild"], frame
        assert frame["patches"][0]["index"] == len(ROWS), frame

    async def test_a_template_that_reads_only_the_rows_gets_the_row(self):
        """The issue's second example: ``patches: []`` before."""
        (frame,) = await drive("TempNoK", ["add"])
        assert frame["type"] == "patch", frame
        assert kinds(frame) == ["InsertChild"], frame

    async def test_two_assigned_rows_are_both_appended_in_order(self):
        (frame,) = await drive("Temp", ["add_two"])
        inserts = [p for p in frame["patches"] if p["type"] == "InsertChild"]
        assert [p["index"] for p in inserts] == [5, 6], frame
        assert "T1" in str(inserts[0]) and "T2" in str(inserts[1]), frame

    async def test_a_prepend_container_grows_by_one_row_and_loses_none(self):
        """``prepend`` is correct but not minimal: the differ pairs children by
        position (the same as for a prepended plain list), so the new row is
        written over the first one and the stored rows shift down, the last of
        them arriving as the one ``InsertChild``. Nothing is removed."""
        (frame,) = await drive("TempPrepend", ["add"])
        assert kinds(frame).count("InsertChild") == 1, frame
        assert "RemoveChild" not in kinds(frame), frame
        assert "rows-101" in str(frame["patches"]), frame


@pytest.mark.asyncio
class TestTheResetIsStillNotAChange:
    async def test_an_unrelated_event_leaves_the_rows_alone(self):
        (frame,) = await drive("Temp", ["bump"])
        assert kinds(frame) == ["SetText"], frame

    async def test_an_unrelated_event_after_an_append_leaves_the_rows_alone(self):
        _add, bump = await drive("Temp", ["add", "bump"])
        assert kinds(bump) == ["SetText"], bump

    async def test_an_event_that_changes_nothing_is_a_noop(self):
        (frame,) = await drive("Temp", ["idle"])
        assert frame["type"] == "noop", frame

    async def test_assigning_the_default_back_changes_nothing_on_the_page(self):
        (frame,) = await drive("Temp", ["clear_rows"])
        assert "RemoveChild" not in kinds(frame), frame
        assert "InsertChild" not in kinds(frame), frame


@pytest.mark.asyncio
class TestWhatTheGateLeavesAlone:
    async def test_without_dj_update_the_template_is_authoritative(self):
        """No ``dj-update``: the container shows what was assigned, as for any list."""
        (frame,) = await drive("TempNoAppend", ["add_only"])
        assert "RemoveChild" in kinds(frame), frame

    async def test_append_without_temporary_assigns_still_propagates_a_removal(self):
        (frame,) = await drive("AppendNoTemp", ["drop_first"])
        assert "RemoveChild" in kinds(frame), frame

    async def test_append_without_temporary_assigns_still_appends_a_new_row(self):
        (frame,) = await drive("AppendNoTemp", ["add"])
        assert [p["index"] for p in frame["patches"] if p["type"] == "InsertChild"] == [5], frame


@pytest.mark.asyncio
class TestStreamsOnAViewWithTemporaryAssigns:
    async def test_a_stream_insert_alone_is_appended_after_the_stored_rows(self):
        (frame,) = await drive("TempStream", ["ins"])
        assert frame["type"] == "patch", frame
        assert kinds(frame) == ["InsertChild"], frame
        assert frame["patches"][0]["index"] == len(ROWS), frame

    async def test_an_idle_event_after_it_is_still_a_noop(self):
        _ins, idle = await drive("TempStream", ["ins", "idle"])
        assert idle["type"] == "noop", idle
