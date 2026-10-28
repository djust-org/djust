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
* the differ treats a ``dj-update="append"`` / ``"prepend"`` container as
  accumulating when its rows come from a ``temporary_assigns`` key (found in the
  template by ``_accumulating_containers``, and located in the stored tree by
  its ``id``): the new children join the stored ones
  (``splice_accumulating_subtrees``), so the patch is an ``InsertChild`` and the
  rows already on the page are left alone. Any other ``dj-update`` container is
  re-rendered whole.
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
        '{% for r in rows %}<div id="rows-{{ r.id }}" data-pk="pk{{ r.id }}">{{ r.body }}</div>{% endfor %}'
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

    async def test_a_prepend_container_gets_one_insert_at_the_front_per_event(self):
        """Several events: each is a single insert at index 0 whose node carries
        its own ``id`` and ``data-pk``. Paired by position, the stored rows used to
        shift down one slot and take the attributes (and ``dj-id``) of the row above."""
        frames = await drive("TempPrepend", ["add", "add", "add", "bump", "add"])
        for n, frame in enumerate([f for i, f in enumerate(frames) if i != 3], start=1):
            assert kinds(frame).count("InsertChild") == 1, (n, frame)
            assert not {"SetAttr", "RemoveChild", "MoveChild"} & set(kinds(frame)), (n, frame)
            (insert,) = [p for p in frame["patches"] if p["type"] == "InsertChild"]
            assert insert["index"] == 0, (n, frame)
            row = int(insert["node"]["attrs"]["id"].removeprefix("rows-"))
            assert insert["node"]["attrs"]["data-pk"] == f"pk{row}", (n, frame)
        assert kinds(frames[3]) == ["SetText"], frames[3]

    async def test_row_dj_ids_stay_unique_across_prepends(self):
        frames = await drive("TempPrepend", ["add", "add", "add"])
        seen = set()
        for frame in frames:
            (insert,) = [p for p in frame["patches"] if p["type"] == "InsertChild"]
            djust_id = insert["node"]["djust_id"]
            assert djust_id not in seen, frames
            seen.add(djust_id)


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


class TempBanner(Temp):
    """A block before the list appears in the same event that appends a row."""

    template = (
        f'<div dj-view="{MODULE}.TempBanner" dj-id="0">{{% if banner %}}<p>notice</p>{{% endif %}}'
        '<div id="list" dj-update="append">'
        '{% for r in rows %}<div id="rows-{{ r.id }}" data-pk="pk{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div>|{{ k }}</div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.banner = False

    @event_handler()
    def banner_and_add(self, **kwargs):
        self.banner = not self.banner
        self.k += 1
        self.rows = [Row(100 + self.k, f"NEW{self.k}")]


class MixedList(LiveView):
    """``temporary_assigns`` for something else; the ``dj-update`` list is an
    ordinary attribute and is re-rendered whole."""

    temporary_assigns = {"flash": []}
    template = (
        f'<div dj-view="{MODULE}.MixedList" dj-id="0"><div id="list" dj-update="append">'
        '{% for r in items %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div>|{{ flash }}</div>"
    )

    def mount(self, request, **kwargs):
        self.items = list(ROWS)
        self.flash = []

    @event_handler()
    def remove_first(self, **kwargs):
        self.items = self.items[1:]

    @event_handler()
    def edit_first(self, **kwargs):
        self.items = [Row(self.items[0].id, "EDITED"), *self.items[1:]]

    @event_handler()
    def add_item(self, **kwargs):
        self.items = [*self.items, Row(50, "ADDED")]


class NoIdRows(Temp):
    template = (
        f'<div dj-view="{MODULE}.NoIdRows" dj-id="0"><div id="list" dj-update="append">'
        "{% for r in rows %}<div>{{ r.body }}</div>{% endfor %}"
        "</div>|{{ k }}</div>"
    )


@pytest.mark.asyncio
class TestOnlyAContainerFedByATemporaryKeyAccumulates:
    """The gate is per container, found from the template: ``temporary_assigns``
    on a view must not change how another ``dj-update`` list behaves."""

    async def test_a_plain_list_in_a_view_with_temporary_assigns_propagates_a_removal(self):
        (frame,) = await drive("MixedList", ["remove_first"])
        assert kinds(frame).count("RemoveChild") >= 1, frame

    async def test_a_plain_list_in_such_a_view_propagates_an_edit(self):
        (frame,) = await drive("MixedList", ["edit_first"])
        assert "EDITED" in str(frame["patches"]), frame

    async def test_a_plain_list_in_such_a_view_still_appends(self):
        (frame,) = await drive("MixedList", ["add_item"])
        assert kinds(frame) == ["InsertChild"], frame


@pytest.mark.asyncio
class TestAContainerThatMovesKeepsItsRows:
    async def test_a_block_appearing_before_the_list_does_not_wipe_the_stored_rows(self):
        (frame,) = await drive("TempBanner", ["banner_and_add"])
        assert frame["type"] == "patch", frame
        # Before: three RemoveChild for the stored rows and nothing re-added.
        removed_rows = [
            p for p in frame["patches"] if p["type"] == "RemoveChild" and p["path"] != []
        ]
        assert not removed_rows, frame
        assert "NEW1" in str(frame["patches"]), frame


@pytest.mark.asyncio
class TestTemporaryAssignsWithoutDjUpdate:
    async def test_an_unrelated_event_leaves_the_rows_alone(self):
        """The branch of ``_unassigned_temporary_keys`` that leaves a key still
        holding its reset default out of the changed keys: without it the
        container is re-rendered empty and every row is removed."""
        (frame,) = await drive("TempNoAppend", ["bump"])
        assert kinds(frame) == ["SetText"], frame


@pytest.mark.asyncio
class TestRowsWithoutAnIdAreSaidSo:
    async def test_a_debug_warning_names_the_view_once(self, caplog):
        import logging

        from django.test import override_settings

        with (
            override_settings(DEBUG=True),
            caplog.at_level(logging.WARNING, logger="djust.mixins.rust_bridge"),
        ):
            await drive("NoIdRows", ["add", "add", "add"])
        messages = [r.getMessage() for r in caplog.records if "have no id" in r.getMessage()]
        assert len(messages) == 1, messages
        assert "NoIdRows" in messages[0]

    async def test_no_warning_outside_debug(self, caplog):
        import logging

        from django.test import override_settings

        with (
            override_settings(DEBUG=False),
            caplog.at_level(logging.WARNING, logger="djust.mixins.rust_bridge"),
        ):
            await drive("NoIdRows", ["add", "add"])
        assert not [r for r in caplog.records if "have no id" in r.getMessage()]
