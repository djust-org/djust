"""Live fixture view for the #2999 inline-whitespace parity harness.

Every block puts a space between two inline-level siblings — the space the
VDOM parser used to drop (``<b>A</b> <i>B</i>`` rendered "AB"). The events
change text AFTER such a space, insert and remove inline siblings next to one,
reorder a keyed inline list whose items are separated by spaces, toggle an
``{% if %}`` that sits between two spaces, and swap ``|safe`` markdown-style
HTML. The generator (``scripts/gen_vdom_diff_fixtures.py``) records the real
patch batches; ``tests/js/inline_whitespace_parity_2999.test.js`` applies them
in jsdom and checks both the significant-child tree and the rendered text.

Not shipped — lives under ``tests/`` purely to generate committed fixtures.
"""

from djust import LiveView

_TEMPLATE = """
<div class="ws-root">
  <p class="lead">
    <strong>{{ label }}</strong> <code>{{ code }}</code> <em>{{ tail }}</em>
  </p>
  <p class="tags">
    {% for t in tags %}<span class="tag">{{ t }}</span> {% endfor %}
  </p>
  <p class="chips">
    {% for c in chips %}<b dj-key="{{ c }}">{{ c }}</b> {% endfor %}
  </p>
  <p class="cond">
    <b>A</b> {% if show %}<i>maybe</i> {% endif %}<u>C</u>
  </p>
  <ul class="md">
    {{ md|safe }}
  </ul>
</div>
"""


class InlineWhitespaceView(LiveView):
    template = _TEMPLATE

    def mount(self, request, **kwargs):
        self.label = "Lead."
        self.code = "x = 1"
        self.tail = "done"
        self.tags = ["alpha", "beta", "gamma"]
        self.chips = ["one", "two", "three", "four"]
        self.show = True
        self.md = "<li><strong>Filters.</strong> <code>{% load app_tags %}</code></li>"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            label=self.label,
            code=self.code,
            tail=self.tail,
            tags=self.tags,
            chips=self.chips,
            show=self.show,
            md=self.md,
        )
        return ctx

    # --- event handlers (immutable updates so change detection fires) ---

    def set_texts(self, label, code, tail):
        self.label, self.code, self.tail = label, code, tail

    def set_tags(self, tags):
        self.tags = list(tags)

    def set_chips(self, chips):
        self.chips = list(chips)

    def toggle(self):
        self.show = not self.show

    def set_md(self, md):
        self.md = md


# --- keyed-list reorder fuzz (#2999) ---------------------------------------
#
# The same keyed list in four shapes. The inline shapes put text between the
# items — the " " the parser now keeps between inline siblings, or ", " — so
# the differ takes its mixed keyed/unkeyed path and emits a move for every
# displaced item. Before #2999 the client applied those moves one at a time
# against the live list and any forward move landed one slot early.
KEYED_LIST_TEMPLATES = {
    "block": (
        '<div class="ws-root"><ul>{% for k in items %}\n  <li dj-key="{{ k }}">{{ k }}</li>{% endfor %}\n</ul></div>'
    ),
    "inline_space": (
        '<div class="ws-root"><p>{% for k in items %}<b dj-key="{{ k }}">{{ k }}</b> {% endfor %}</p></div>'
    ),
    "inline_comma": (
        '<div class="ws-root"><p>{% for k in items %}<b dj-key="{{ k }}">{{ k }}</b>, {% endfor %}</p></div>'
    ),
    "inline_tight": (
        '<div class="ws-root"><p>{% for k in items %}<b dj-key="{{ k }}">{{ k }}</b>{% endfor %}</p></div>'
    ),
}


def keyed_list_view(shape):
    class _KeyedListView(LiveView):
        template = KEYED_LIST_TEMPLATES[shape]

        def mount(self, request, **kwargs):
            self.items = ["k0", "k1", "k2", "k3", "k4"]

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["items"] = self.items
            return ctx

        def set_items(self, items):
            self.items = list(items)

    _KeyedListView.__name__ = f"KeyedListView_{shape}"
    return _KeyedListView
