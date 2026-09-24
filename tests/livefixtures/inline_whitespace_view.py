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


# --- {% if %} / {% for %} shapes next to kept spaces (#2999 review H1/H2) ---
#
# Each shape is a template over a small generic state (``tags``: list of
# {"name", "visible"}; flags ``a``/``b``/``admin``; ``user``; ``error``;
# ``sep``; counter ``n``). The generator drives random state changes through
# ``set_state`` and records every real patch batch.
IF_FOR_SHAPES = {
    # The review's H2(a) repro: a conditional item in a loop, the usual way.
    "badges_multiline": """<div class="ws-root">
<p class="tags">
  {% for tag in tags %}
    {% if tag.visible %}<span class="badge">{{ tag.name }}</span>{% endif %}
  {% endfor %}
</p>
<em>{{ n }}</em>
</div>""",
    "badges_single_line": (
        '<div class="ws-root"><p>{% for t in tags %}{% if t.visible %}<span>{{ t.name }}</span>'
        "{% endif %} {% endfor %}<u>{{ n }}</u></p></div>"
    ),
    "keyed_if_items": (
        '<div class="ws-root"><p>{% for t in tags %}{% if t.visible %}<b dj-key="{{ t.name }}">'
        "{{ t.name }}</b>{% endif %} {% endfor %}<u>{{ n }}</u></p></div>"
    ),
    "if_else_in_loop": (
        '<div class="ws-root"><p>{% for t in tags %}<span>{{ t.name }}</span>{% if t.visible %} '
        "<em>new</em>{% else %} <s>old</s>{% endif %} {% endfor %}<u>{{ n }}</u></p></div>"
    ),
    "block_or_inline_items": (
        '<div class="ws-root"><div>{% for t in tags %}{% if t.visible %}<div>{{ t.name }}</div>'
        "{% else %}<span>{{ t.name }}</span>{% endif %} {% endfor %}</div><u>{{ n }}</u></div>"
    ),
    # The review's H2(b) repro and its multi-line "logged in as" form.
    "nested_if": (
        '<div class="ws-root"><p>{% if a %}<b>A</b> {% if b %}<i>I</i>{% endif %} <u>U</u>{% endif %}'
        "<em>{{ n }}</em></p></div>"
    ),
    "nested_if_multiline": """<div class="ws-root">
<p class="who">
  {% if user %}
    <strong>{{ user }}</strong>
    {% if admin %}<em>(admin)</em>{% endif %}
    <a href="#">Log out</a>
  {% endif %}
</p>
<u>{{ n }}</u>
</div>""",
    "two_ifs_between_inline": (
        '<div class="ws-root"><p><b>A</b> {% if a %}<i>I</i>{% endif %} {% if b %}<s>S</s>{% endif %} '
        "<u>U</u> <em>{{ n }}</em></p></div>"
    ),
    # The review's H1 repro: an error message that clears and comes back.
    "error_message": (
        '<div class="ws-root"><form><input name="email"> <span class="error">{{ error }}</span>'
        "</form><u>{{ n }}</u></div>"
    ),
    "separator_variable": (
        '<div class="ws-root"><p><b>A</b>{{ sep }}<i>B</i> <u>{{ n }}</u></p></div>'
    ),
}


def state_view(template, initial):
    """A LiveView over ``template`` whose state is ``initial``'s keys, with a
    generic ``set_state`` event handler."""
    from djust.decorators import event_handler

    class _StateView(LiveView):
        pass

    _StateView.template = template

    def mount(self, request, **kwargs):
        for k, v in initial.items():
            setattr(self, k, v)

    def get_context_data(self, **kwargs):
        ctx = LiveView.get_context_data(self, **kwargs)
        for k in initial:
            ctx[k] = getattr(self, k)
        return ctx

    @event_handler
    def set_state(self, **state):
        for k, v in state.items():
            setattr(self, k, v)

    _StateView.mount = mount
    _StateView.get_context_data = get_context_data
    _StateView.set_state = set_state
    return _StateView
