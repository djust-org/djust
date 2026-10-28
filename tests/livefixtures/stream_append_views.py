"""Fixture views for #3639: stream-only handlers and ``temporary_assigns`` + ``dj-update``.

Driven by ``scripts/gen_vdom_diff_fixtures.py``; the recorded patch batches are
replayed in jsdom by ``tests/js/vdom_correctness_v1_2_1_8.test.js`` through the
real client's ``applyPatches``.

The templates carry the inter-element whitespace a real page has (a ``{% for %}``
body on its own lines), because a client DOM built without it hides the
position bugs these paths are prone to (CLAUDE.md, reproduction fidelity).
"""

from dataclasses import dataclass

from djust import LiveView
from djust.decorators import event_handler


@dataclass
class Row:
    id: int
    body: str


def rows(count):
    return [Row(i, f"row {i}") for i in range(count)]


class StreamFeedView(LiveView):
    """A ``dj-stream`` container driven only by stream operations."""

    template = (
        '<div class="ws-root">\n'
        '  <ul id="feed" dj-stream="rows">\n'
        "    {% for r in streams.rows %}\n"
        '    <li id="rows-{{ r.id }}">{{ r.body }}</li>\n'
        "    {% endfor %}\n"
        "  </ul>\n"
        "  <p>{{ tail }}</p>\n"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.stream("rows", rows(4))
        self.tail = "tail"

    @event_handler()
    def ins(self, **kwargs):
        self.stream_insert("rows", Row(100, "inserted"))

    @event_handler()
    def ins_front(self, **kwargs):
        self.stream_insert("rows", Row(101, "front"), at=0)

    @event_handler()
    def dele(self, **kwargs):
        self.stream_delete("rows", Row(1, "row 1"))

    @event_handler()
    def prune(self, **kwargs):
        self.stream_prune("rows", 2, edge="top")

    @event_handler()
    def reset(self, **kwargs):
        self.stream_reset("rows", [Row(200, "fresh")])


def _append_template(mode):
    return (
        '<div class="ws-root">\n'
        f'  <ul id="list" dj-update="{mode}">\n'
        "    {% for r in rows %}\n"
        '    <li id="rows-{{ r.id }}">{{ r.body }}</li>\n'
        "    {% endfor %}\n"
        "  </ul>\n"
        "  <p>count {{ k }}</p>\n"
        "</div>"
    )


class _TemporaryRowsView(LiveView):
    temporary_assigns = {"rows": []}

    def mount(self, request, **kwargs):
        self.rows = rows(3)
        self.k = 0

    @event_handler()
    def add(self, **kwargs):
        """The LiveView docstring pattern: one new row next to another change."""
        self.k += 1
        self.rows = [Row(100 + self.k, f"new {self.k}")]

    @event_handler()
    def add_only(self, **kwargs):
        """The row assignment is the only change."""
        self.rows = [Row(200, "only")]

    @event_handler()
    def add_two(self, **kwargs):
        self.rows = [Row(301, "two a"), Row(302, "two b")]

    @event_handler()
    def bump(self, **kwargs):
        self.k += 1


class AppendRowsView(_TemporaryRowsView):
    template = _append_template("append")


class PrependRowsView(_TemporaryRowsView):
    template = _append_template("prepend")
