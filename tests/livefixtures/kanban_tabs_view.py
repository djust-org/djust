"""Live fixture view for the #1678 differential VDOM harness.

Reproduces the exact production shape that triggered the #1678 kanban
``html_recovery`` storm: a ``.tabs-content`` container with EIGHT sibling
``{% if active_tab == N %}`` blocks (so the Rust differ emits keyed dj-if
boundaries and, on a tab switch, ``MoveSubtree`` patches to relocate the
marker spans of the blocks whose absolute index shifted). The active tab
renders a keyed kanban (columns + cards carry ``dj-key``) so a cross-column
card move produces the positional count-badge ``SetText`` deep inside the
active block — the patch that fails when the client's marker positions have
drifted from the server's ``last_vdom``.

Not shipped — lives under ``tests/`` purely to generate the committed JSON
fixtures consumed by ``tests/js/vdom_client_faithful_diff.test.js``.
"""

from djust import LiveView

# One {% if active_tab == N %} block per tab. Tab 3 (KANBAN_TAB) renders a
# keyed kanban; the others render a trivial element-bearing block so every
# block emits a real dj-if marker pair (the renderer only emits markers when a
# branch is element-bearing). Tab 3 sits in the MIDDLE so that switching to it
# shifts the absolute index of the later blocks (4-7) -> multiple MoveSubtree
# patches, the multi-move case that exposes the client index-basis bug.
KANBAN_TAB = 3
NUM_TABS = 8

# The kanban tab (#1678): the active block's body is a NESTED {% if %} whose
# branches are COMPONENT TAGS ({% kanban_board %} / {% empty_state %}) — there
# is NO literal HTML element directly in the outer conditional. The Rust
# renderer's `nodes_contain_elements` only counts literal element nodes, so it
# does NOT see the component-tag branches as element-bearing; that drives the
# malformed dj-if boundary indexing where the differ addresses the kanban one
# significant-child earlier than the client's flat marker count → the count
# `SetText` lands on the nested dj-if open marker → html_recovery. This is the
# exact live djust_pm shape ({% if active_tab=="ideas" %}{% if has_ideas %}
# {% kanban_board %}{% else %}{% empty_state %}{% endif %}{% endif %}).
_KANBAN_BLOCK = (
    "{% if active_tab == 3 %}"
    "{% if has_cards %}"
    '{% kanban_board columns=columns move_event="move_card" %}'
    "{% else %}"
    '{% empty_state title="No cards" %}'
    "{% endif %}"
    "{% endif %}"
)


# A "rich" tab whose body contains NESTED element-bearing {% if %} blocks (like
# the live overview tab). When this tab is active it emits its nested marker
# pairs; when it empties on a tab switch those nested boundaries must be torn
# down. A stray leftover nested marker drifts every following sibling by one —
# exactly the #1678 client/last_vdom parity off-by-one. ``detail``/``extra``
# flags keep the nested branches element-bearing (so they emit real markers).
def _rich_block(n):
    return (
        "{% if active_tab == " + str(n) + " %}"
        '<div class="tab tab' + str(n) + '">'
        "<p>Tab " + str(n) + " intro</p>"
        '{% if detail %}<div class="detail">Detail for ' + str(n) + "</div>{% endif %}"
        '{% if extra %}<div class="extra"><span>x</span>{% if deep %}<b>deep</b>{% endif %}</div>{% endif %}'
        "<p>Tab " + str(n) + " outro</p>"
        "</div>"
        "{% endif %}"
    )


def _simple_block(n):
    return (
        "{% if active_tab == " + str(n) + " %}"
        '<div class="tab tab' + str(n) + '">Tab ' + str(n) + " content</div>"
        "{% endif %}"
    )


# Tabs 0-2 are rich (nested conditionals), tab 3 is the kanban, 4-7 are simple.
def _block(n):
    if n == KANBAN_TAB:
        return _KANBAN_BLOCK
    if n < KANBAN_TAB:
        return _rich_block(n)
    return _simple_block(n)


_TAB_BLOCKS = "".join(_block(n) for n in range(NUM_TABS))


def _initial_columns():
    # kanban_board component shape: cards use "title" (not "text").
    return [
        {
            "id": "todo",
            "title": "To Do",
            "cards": [
                {"id": "c1", "title": "Alpha"},
                {"id": "c2", "title": "Bravo"},
            ],
        },
        {
            "id": "doing",
            "title": "Doing",
            "cards": [{"id": "c3", "title": "Charlie"}],
        },
        {"id": "done", "title": "Done", "cards": []},
    ]


class KanbanTabsView(LiveView):
    """8-tab dashboard, tab 3 = keyed kanban. State: ``active_tab``, ``columns``."""

    template = '<div class="tabs-content">' + _TAB_BLOCKS + "</div>"

    def mount(self, request, active_tab=0):
        self.active_tab = int(active_tab)
        self.columns = _initial_columns()
        # Nested-conditional flags for the rich tabs (all element-bearing so the
        # nested {% if %} blocks emit real dj-if marker pairs).
        self.detail = True
        self.extra = True
        self.deep = True

    @property
    def has_cards(self):
        return any(c["cards"] for c in self.columns)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.active_tab
        ctx["detail"] = self.detail
        ctx["extra"] = self.extra
        ctx["deep"] = self.deep
        ctx["has_cards"] = self.has_cards
        ctx["columns"] = self.columns  # kanban_board component shape
        return ctx

    # --- event handlers ---

    def switch_tab(self, tab):
        self.active_tab = int(tab)

    def move_card(self, card_id, to_column, to_index=0):
        """Move a card to ``to_column`` at ``to_index`` (cross-column move).

        Uses an IMMUTABLE update — new column dicts + new ``cards`` lists — so
        djust's change detection sees the change and emits a targeted VDOM diff.
        An in-place mutation (``col["cards"].pop()/insert()``) would share the
        previous render's objects and produce ZERO patches (the deliberate
        no-deepcopy trade-off; #1981). When in-place is unavoidable, call
        ``self.set_changed_keys("columns")`` instead — see
        ``docs/website/guides/state-primitives.md``.
        """
        to_index = int(to_index)
        # Remove the card by building NEW cards lists (no in-place pop).
        moved = None
        stripped = []
        for col in self.columns:
            new_cards = []
            for card in col["cards"]:
                if card["id"] == card_id:
                    moved = card
                else:
                    new_cards.append(card)
            stripped.append({**col, "cards": new_cards})
        if moved is None:
            return
        # Insert into the target column, again building a NEW cards list, and
        # reassign ``self.columns`` to a NEW list (immutable update).
        self.columns = [
            {**col, "cards": col["cards"][:to_index] + [moved] + col["cards"][to_index:]}
            if col["id"] == to_column
            else col
            for col in stripped
        ]
