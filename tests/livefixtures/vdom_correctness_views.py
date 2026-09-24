"""Fixture views for the v1.2.1-8 VDOM correctness scenarios.

Driven by ``scripts/gen_vdom_diff_fixtures.py``; the recorded patch batches are
replayed in jsdom by ``tests/js/vdom_correctness_v1_2_1_8.test.js``.

- #2997: a 69-item keyed list filtered to a subset and restored, and re-sorted
  and restored (the djust.org /themes/ flow).
- #2898: text values carrying characters HTML escapes (``&``, ``<``, quotes),
  plain and ``|safe``, changing through the parse-skipping text fast paths.
- #3012: text patches whose path runs through whitespace-only text inside
  ``<pre>``/``<code>``/``<textarea>``, which the server counts.
"""

from djust import LiveView

# --- #2997 keyed filter / restore ------------------------------------------

KEYED_FILTER_TEMPLATES = {
    "block": (
        '<div class="ws-root"><ul class="presets">{% for i in items %}\n'
        '  <li dj-key="{{ i }}"><a href="/t/{{ i }}/">{{ i }}</a></li>'
        "{% endfor %}\n</ul></div>"
    ),
    "block_tight": (
        '<div class="ws-root"><ul>{% for i in items %}<li dj-key="{{ i }}">{{ i }}</li>'
        "{% endfor %}</ul></div>"
    ),
}

ALL_ITEMS = [f"item{n:02d}" for n in range(69)]
SUBSET = [ALL_ITEMS[n] for n in (7, 8, 30, 45, 60, 66)]
# "Most loved": a deterministic re-sort unrelated to the natural order.
RESORTED = sorted(ALL_ITEMS, key=lambda s: ((int(s[4:]) * 37) % 69, s))


def keyed_filter_view(shape):
    class _KeyedFilterView(LiveView):
        template = KEYED_FILTER_TEMPLATES[shape]

        def mount(self, request, **kwargs):
            self.items = list(ALL_ITEMS)

        def get_context_data(self, **kwargs):
            ctx = super().get_context_data(**kwargs)
            ctx["items"] = self.items
            return ctx

        def set_items(self, items):
            self.items = list(items)

    _KeyedFilterView.__name__ = f"KeyedFilterView_{shape}"
    return _KeyedFilterView


# --- #2898 escaped text through the fast paths ------------------------------

ESCAPED_TEXT_TEMPLATE = (
    '<div class="ws-root">'
    '<p class="plain">{{ plain }}</p>'
    '<p class="around">Value: {{ plain }} (end)</p>'
    '<div class="safe">{{ safe|safe }}</div>'
    '<ul class="loop">{% for r in rows %}<li>{{ r }}</li>{% endfor %}</ul>'
    "</div>"
)

# --- #3012 whitespace-only text inside pre / code / textarea ------------------

PRESERVE_SHAPES = {
    "pre_newline_between_inline": (
        '<div class="ws-root"><pre><code>{{ a }}</code>\n<b>{{ b }}</b>\n<i>{{ c }}</i></pre></div>'
    ),
    "code_leading_space_runs": (
        '<div class="ws-root"><p><code>  <span>{{ a }}</span>\t<em>{{ b }}</em>  </code></p></div>'
    ),
    "pre_block_of_lines": (
        '<div class="ws-root"><pre>{% for l in lines %}<span class="ln">{{ l }}</span>\n{% endfor %}'
        "<b>{{ c }}</b></pre></div>"
    ),
    "textarea_and_code": (
        '<div class="ws-root"><div><textarea>{{ a }}</textarea>\n  <code>\n<i>{{ b }}</i>\n</code>'
        "\n  <b>{{ c }}</b></div></div>"
    ),
    "nested_inline_in_pre": (
        '<div class="ws-root"><pre><span>\n</span><b>{{ a }}</b>\n<span>  <i>{{ b }}</i></span></pre></div>'
    ),
}
