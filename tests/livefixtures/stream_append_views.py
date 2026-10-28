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

    @property
    def pk(self):
        """Carried on every row as ``data-pk``, so a row that ends up with another
        row's attributes is visible in the replayed DOM."""
        return self.id * 10


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
        '    <li id="rows-{{ r.id }}" data-pk="{{ r.pk }}">{{ r.body }}</li>\n'
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


class BannerRowsView(_TemporaryRowsView):
    """A block before the list appears in the same event that appends a row, so
    the container sits one position later than where the stored tree has it."""

    template = (
        '<div class="ws-root">\n'
        '  {% if banner %}<p class="banner">notice</p>{% endif %}\n'
        '  <ul id="list" dj-update="append">\n'
        "    {% for r in rows %}\n"
        '    <li id="rows-{{ r.id }}" data-pk="{{ r.pk }}">{{ r.body }}</li>\n'
        "    {% endfor %}\n"
        "  </ul>\n"
        "  <p>count {{ k }}</p>\n"
        "</div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.banner = False

    @event_handler()
    def banner_and_add(self, **kwargs):
        self.banner = not self.banner
        self.k += 1
        self.rows = [Row(100 + self.k, f"new {self.k}")]


class MixedView(LiveView):
    """``temporary_assigns`` for something else; the ``dj-update`` list is an
    ordinary attribute, re-rendered whole, so a removal or an edit must still
    reach the page."""

    temporary_assigns = {"flash": []}
    template = (
        '<div class="ws-root">\n'
        '  <ul id="list" dj-update="append">\n'
        "    {% for r in items %}\n"
        '    <li id="rows-{{ r.id }}" data-pk="{{ r.pk }}">{{ r.body }}</li>\n'
        "    {% endfor %}\n"
        "  </ul>\n"
        "  <p>{{ flash }}</p>\n"
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.items = rows(4)
        self.flash = []

    @event_handler()
    def remove_first(self, **kwargs):
        self.items = self.items[1:]

    @event_handler()
    def edit_first(self, **kwargs):
        self.items = [Row(self.items[0].id, "edited"), *self.items[1:]]

    @event_handler()
    def add_item(self, **kwargs):
        self.items = [*self.items, Row(50 + len(self.items), "added")]


# ---------------------------------------------------------------------------
# #3647: the limits of per-container accumulation
# ---------------------------------------------------------------------------


@dataclass
class TaggedRow:
    """A row whose ``id`` can repeat; ``pk`` tells two rows with one ``id`` apart."""

    id: int
    body: str
    pk: str


class DuplicateIdsPrependView(LiveView):
    """A prepend container whose mount rows, and then a batch, repeat an ``id``."""

    temporary_assigns = {"rows": []}
    template = _append_template("prepend")

    def mount(self, request, **kwargs):
        self.rows = [
            TaggedRow(0, "first", "a"),
            TaggedRow(0, "second", "b"),
            TaggedRow(1, "third", "c"),
        ]
        self.k = 0

    @event_handler()
    def add(self, **kwargs):
        self.k += 1
        self.rows = [TaggedRow(100 + self.k, f"new {self.k}", f"n{self.k}")]

    @event_handler()
    def add_pair(self, **kwargs):
        """Two rows of one batch share an ``id``."""
        self.k += 1
        base = 500 + 10 * self.k
        self.rows = [
            TaggedRow(base, "pair one", f"p{self.k}a"),
            TaggedRow(base, "pair two", f"p{self.k}b"),
            TaggedRow(base + 1, "pair three", f"p{self.k}c"),
        ]

    @event_handler()
    def bump(self, **kwargs):
        self.k += 1


class UnkeyedStoredPrependView(PrependRowsView):
    """The container was not recognised when the page was first rendered (the
    generator hides it), so the stored rows carry no keys; it is recognised from the
    first event on, as after a template edit that brings a container into the set."""


class AliasedRowsView(_TemporaryRowsView):
    """The container is fed through an alias made OUTSIDE it."""

    template = (
        '<div class="ws-root">\n'
        "  {% with shown=rows %}\n"
        '  <ul id="list" dj-update="append">\n'
        "    {% for r in shown %}\n"
        '    <li id="rows-{{ r.id }}" data-pk="{{ r.pk }}">{{ r.body }}</li>\n'
        "    {% endfor %}\n"
        "  </ul>\n"
        "  {% endwith %}\n"
        "  <p>count {{ k }}</p>\n"
        "</div>"
    )


class RenamedRowsView(_TemporaryRowsView):
    """``get_context_data`` hands the rows to the template as ``feed``."""

    template = (
        '<div class="ws-root">\n'
        '  <ul id="list" dj-update="append">\n'
        "    {% for r in feed %}\n"
        '    <li id="rows-{{ r.id }}" data-pk="{{ r.pk }}">{{ r.body }}</li>\n'
        "    {% endfor %}\n"
        "  </ul>\n"
        "  <p>count {{ k }}</p>\n"
        "</div>"
    )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["feed"] = self.rows
        return context


class NestedContainersView(_TemporaryRowsView):
    """The outer list is an ordinary attribute; only the inner one reads ``rows``."""

    template = (
        '<div class="ws-root">\n'
        '  <div id="outer" dj-update="append">\n'
        '    <ul id="inner" dj-update="append">\n'
        "      {% for r in rows %}\n"
        '      <li id="rows-{{ r.id }}" data-pk="{{ r.pk }}">{{ r.body }}</li>\n'
        "      {% endfor %}\n"
        "    </ul>\n"
        "    {% for r in items %}\n"
        '    <p id="items-{{ r.id }}" data-pk="{{ r.pk }}">{{ r.body }}</p>\n'
        "    {% endfor %}\n"
        "  </div>\n"
        "  <p>count {{ k }}</p>\n"
        "</div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.items = rows(3)

    @event_handler()
    def remove_item(self, **kwargs):
        self.items = self.items[1:]

    @event_handler()
    def edit_item(self, **kwargs):
        self.items = [Row(self.items[0].id, "edited"), *self.items[1:]]
