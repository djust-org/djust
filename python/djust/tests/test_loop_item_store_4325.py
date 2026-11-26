"""Differential fuzz for the index-keyed per-item loop store (#4325).

``RustLiveView`` keeps, per ``{% for %}``, each item's output by index and
serves it again when the item at that index hashes the same and nothing else
the body reads changed (``loop_cache::LoopItemStore``). The oracle is a
second view fed the same state with the store off and its caches cleared
before every render, so it renders every item and runs the full parse and
diff. Both must produce the same HTML and the same patches after every random
edit, across templates that exercise what the store must refuse or account
for: nested ifs and fors, ``forloop`` counters and ``parentloop``,
``{% cycle %}``, ``{% ifchanged %}``, ``{% regroup %}``, ``{% include %}``,
filters over the whole list (``|length``, ``|dictsort``), outer variables,
tuple unpacking, ``reversed``, safety marks, template changes and switching
the store off and on. A deliberately broken store must fail the same fuzz.
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from djust._rust import RustLiveView
from djust.tests.test_multi_text_region_4325 import _canonical as _canonical_text


def _canonical(prev_html: str, html: str, patches: str) -> tuple:
    """``_canonical_text``, plus the ids inside a patch's embedded ``html``
    (``InsertSubtree``), which are fresh ids drawn from the shared counter."""
    html_c, patches_c = _canonical_text(prev_html, html, patches)
    names = {}
    for match in re.finditer(r'dj-id="([^"]*)"', prev_html + html):
        names.setdefault(match.group(1), "id%d" % len(names))

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {
                k: (
                    re.sub(
                        r'dj-id="([^"]*)"', lambda m: 'dj-id="%s"' % names.get(m.group(1), "?"), v
                    )
                    if k == "html" and isinstance(v, str)
                    else walk(v)
                )
                for k, v in node.items()
            }
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return html_c, json.dumps(walk(json.loads(patches_c)))


ROW = (
    "<li>{% if r.on %}<b>{{ r.a }}</b>{% else %}{{ r.a|upper }}{% endif %}"
    " {{ forloop.counter }}/{{ forloop.revcounter }}"
    "{% if forloop.first %}F{% endif %}{% if forloop.last %}L{% endif %}</li>"
)

TEMPLATES: Dict[str, str] = {
    "if_forloop": "<div dj-root><ul>{% for r in items %}" + ROW + "{% endfor %}</ul></div>",
    "nested": (
        "<div dj-root>{% for r in items %}<ul>{% for t in r.tags %}<li>{% if t %}{{ t }}"
        "{% endif %}{{ forloop.parentloop.counter }}.{{ forloop.counter }}</li>"
        "{% empty %}<li>none</li>{% endfor %}</ul>{% endfor %}</div>"
    ),
    "cycle": (
        "<div dj-root><table>{% for r in items %}<tr class=\"{% cycle 'x' 'y' %}\">"
        "<td>{% if r.on %}on{% endif %}{{ r.a }}</td></tr>{% endfor %}</table></div>"
    ),
    "ifchanged": (
        "<div dj-root>{% for r in items %}{% ifchanged r.g %}<h3>{{ r.g }}</h3>"
        "{% endifchanged %}<p>{% if r.on %}{{ r.a }}{% endif %}</p>{% endfor %}</div>"
    ),
    "regroup": (
        "<div dj-root>{% regroup items by g as groups %}{% for grp in groups %}"
        "<h4>{{ grp.grouper }}</h4>{% for r in grp.list %}<i>{% if r.on %}{{ r.a }}"
        "{% endif %}</i>{% endfor %}{% endfor %}</div>"
    ),
    "include": (
        '<div dj-root><ul>{% for r in items %}{% include "row_4325.html" %}{% endfor %}</ul></div>'
    ),
    "outer": (
        "<div dj-root><p>{{ items|length }}</p><ul>{% for r in items %}<li>{% if r.on %}"
        "{{ prefix }}{% endif %}{{ r.a }}</li>{% endfor %}</ul>"
        '<ol>{% for r in items|dictsort:"a" %}<li>{% if r.on %}{{ r.a }}{% endif %}'
        "{{ forloop.counter0 }}</li>{% endfor %}</ol></div>"
    ),
    "unpack": (
        "<div dj-root><ul>{% for a, b in pairs reversed %}<li>{% if a %}{{ a }}{% endif %}"
        '={{ b|default:"-" }}{{ forloop.revcounter0 }}</li>{% endfor %}</ul></div>'
    ),
    "filters": (
        "<div dj-root>{% for r in items %}{% with x=r.a|lower %}<li>{% if x %}"
        '{{ x|truncatechars:4 }}{% endif %}{{ r.n|add:2 }}|{{ r.tags|join:"," }}|'
        '{{ r.tags|length }}|{% firstof r.a "z" %}|{% widthratio r.n 10 100 %}</li>'
        "{% endwith %}{% endfor %}</div>"
    ),
    "two_loops": (
        "<div dj-root>{% if show %}<ul>{% for r in items %}<li>{% if r.on %}{{ r.a }}"
        "{% endif %}</li>{% endfor %}</ul>{% endif %}<ol>{% for r in items %}<li>"
        "{% if not r.on %}{{ r.n }}{% endif %}{{ r.a }}</li>{% endfor %}</ol></div>"
    ),
    # An inner loop whose variable the body never reads, beside an if: the
    # inner loop's content cache emits parse-cache placeholders that must
    # never be stored with the outer item.
    "nested_unread": (
        "<div dj-root>{% for r in items %}<section>{% for t in r.tags %}<div>-</div>"
        "{% endfor %}{% if r.on %}{{ r.a }}{% endif %}</section>{% endfor %}</div>"
    ),
    # Inline ifs whose trailing filter argument is outer context (refused:
    # `prefix` is a read), and a second loop whose arguments are the item
    # and literals (served).
    "inline_if": (
        "<div dj-root><ul>{% for r in items %}<li>{{ r.a if r.on else r.a|add:prefix }}"
        '/{{ r.n if r.on else "z"|add:prefix }}{% if r.on %}!{% endif %}</li>'
        "{% endfor %}</ul><ol>{% for r in items %}<li>{{ r.a if r.on else r.g|add:r.n }}"
        '/{{ r.n if r.on else "z"|add:"!" }}{% if r.on %}!{% endif %}</li>'
        "{% endfor %}</ol></div>"
    ),
    # #4325 WP4: nested loops the store keeps. Three levels, an inner
    # `{% empty %}`, the parent loop's counters, an if around the inner loop,
    # and an inner loop whose items are block-level (the content cache's
    # parse placeholders, which must never be stored with the outer item).
    "nested_deep": (
        "<div dj-root>{% for r in items %}<section>{% if r.on %}<ol>{% for t in r.tags %}"
        "<li>{{ forloop.parentloop.counter0 }}{% for ch in t %}{% if ch == '<' %}lt"
        "{% else %}{{ ch }}{% endif %}{% endfor %}</li>{% empty %}<li>{{ r.a }}</li>"
        "{% endfor %}</ol>{% endif %}{{ r.n }}</section>{% endfor %}</div>"
    ),
    "nested_blocks": (
        "<div dj-root>{% for r in items %}<article>{% for t in r.tags %}<div>{{ t }}</div>"
        "{% endfor %}<p>{{ r.a }}</p></article>{% endfor %}</div>"
    ),
    # Refused: the inner loop reads outer context.
    "nested_outer": (
        "<div dj-root>{% for r in items %}<ul>{% for t in r.tags %}<li>{{ prefix }}{{ t }}"
        "</li>{% endfor %}</ul>{% endfor %}</div>"
    ),
    "dict": (
        "<div dj-root><ul>{% for k, v in d.items %}<li>{% if v %}{{ k }}={{ v }}"
        "{% endif %}</li>{% endfor %}</ul><ul>{% for r in items %}<li>{% if r.on %}"
        "{{ r.a }}{% endif %}</li>{% endfor %}</ul></div>"
    ),
}

INCLUDED = "<li>{% if r.on %}{{ r.a }}{% else %}-{% endif %}{{ forloop.counter }}</li>"

VALUES: List[Any] = [
    "x",
    "longer value",
    "",
    "<b>markup</b>",
    "a & b",
    'q"uote',
    "↑",
    "été",
    0,
    7,
    -0.0,
    0.0,
    1.5,
    None,
    True,
    False,
]


def _row(rng: random.Random, i: int) -> Dict[str, Any]:
    return {
        "a": rng.choice(VALUES),
        "on": rng.random() < 0.5,
        "g": rng.choice(["g1", "g2", "g3"]),
        "n": rng.choice([0, 1, 5, 25, 100]),
        "tags": rng.sample(["t1", "t2", "", "<t>"], rng.randint(0, 3)),
        "id": i,
    }


def _state(rng: random.Random, n: int) -> Dict[str, Any]:
    return {
        "items": [_row(rng, i) for i in range(n)],
        "pairs": [[rng.choice(VALUES), rng.choice(VALUES)] for _ in range(n)],
        "d": {"k%d" % i: rng.choice(VALUES) for i in range(4)},
        "prefix": "P",
        "show": True,
    }


class _Pair:
    """Three views fed the same state in lockstep.

    ``fast`` has the store on. ``control`` is the same pipeline with the
    store off (same partial renders, row deltas and text fast paths), so its
    HTML AND patches must equal ``fast``'s exactly: the store may change how
    the bytes are produced, never which. ``oracle`` clears its caches before
    every render (every item rendered, full parse and diff); its HTML must
    equal ``fast``'s too. Its patches are not compared: the text fast paths
    may send a no-op ``SetText`` when an unchanged row's raw bytes change but
    its decoded text does not (a safety mark moving), which a full diff omits.
    """

    def __init__(
        self,
        template: str,
        dirs: List[str],
        state: Dict[str, Any],
        content_cache: bool,
        sabotage: int = 0,
        nested: bool = True,
    ) -> None:
        self.fast = RustLiveView(template, dirs)
        self.control = RustLiveView(template, dirs)
        self.oracle = RustLiveView(template, dirs)
        for view in (self.fast, self.control):
            view.set_loop_render_cache_enabled(content_cache)
        self.oracle.set_loop_render_cache_enabled(False)
        self.control.set_loop_item_store_enabled(False)
        self.oracle.set_loop_item_store_enabled(False)
        self.fast.set_loop_item_store_nested(nested)
        if sabotage:
            self.fast._set_loop_item_store_sabotage(sabotage)
        self.views = (self.fast, self.control, self.oracle)
        self.html: Dict[int, str] = {}
        self.hits = 0
        self.mismatch: Optional[str] = None
        for view in self.views:
            view.update_state(state)
            self.html[id(view)] = view.render_with_diff()[0]

    def step(self, updates: Dict[str, Any], keys: List[str], marks: List[str], delta: Any) -> bool:
        for view in (self.fast, self.control):
            if delta is not None and view.update_state_items("items", delta[0], delta[1]):
                view.update_state({k: v for k, v in updates.items() if k != "items"})
            else:
                view.update_state(updates)
            view.set_changed_keys(keys)
        self.oracle.update_state(updates)
        self.oracle.clear_fragment_cache()
        out = {}
        for view in self.views:
            view.mark_safe_keys(marks)
            html, patches, _ = view.render_with_diff()
            out[id(view)] = _canonical(self.html[id(view)], html, patches or "[]")
            self.html[id(view)] = html
        self.hits += self.fast.loop_item_store_hits()
        assert self.control.loop_item_store_hits() == self.oracle.loop_item_store_hits() == 0
        fast, control, oracle = (out[id(v)] for v in self.views)
        if fast[0] != oracle[0]:
            self.mismatch = "html (oracle)"
        elif fast[0] != control[0]:
            self.mismatch = "html (control)"
        elif json.loads(fast[1]) != json.loads(control[1]):
            self.mismatch = "patches (control)"
        return self.mismatch is None

    def update_template(self, template: str) -> None:
        for view in self.views:
            view.update_template(template)


def _edit(rng: random.Random, state: Dict[str, Any]) -> tuple:
    """One random edit; returns (updates, keys, in-place row delta or None)."""
    items = state["items"]
    choice = rng.random()
    delta = None
    if choice < 0.45 and items:
        i = rng.randrange(len(items))
        row = dict(items[i])
        field = rng.choice(["a", "a", "on", "g", "n", "tags"])
        row[field] = {
            "a": lambda: rng.choice(VALUES),
            "on": lambda: not row["on"],
            "g": lambda: rng.choice(["g1", "g2", "g3"]),
            "n": lambda: rng.choice([0, 1, 5, 25, 100]),
            "tags": lambda: rng.sample(["t1", "t2", "", "<t>"], rng.randint(0, 3)),
        }[field]()
        items = list(items)
        items[i] = row
        delta = ([i], [row])
    elif choice < 0.55:
        items = list(items)
        items.insert(rng.randrange(len(items) + 1), _row(rng, len(items)))
    elif choice < 0.62 and len(items) > 1:
        items = list(items)
        items.pop(rng.randrange(len(items)))
    elif choice < 0.70 and len(items) > 1:
        items = list(items)
        i, j = rng.randrange(len(items)), rng.randrange(len(items))
        items[i], items[j] = items[j], items[i]
        delta = ([i, j], [items[i], items[j]]) if i != j else None
    elif choice < 0.75:
        items = list(items)
        rng.shuffle(items)
    elif choice < 0.82:
        key = rng.choice(["prefix", "show"])
        state[key] = (not state["show"]) if key == "show" else rng.choice(["P", "Q", "<p>"])
        return {key: state[key]}, [key], None
    elif choice < 0.90:
        pairs = [list(p) for p in state["pairs"]]
        if pairs:
            pairs[rng.randrange(len(pairs))][rng.randrange(2)] = rng.choice(VALUES)
        state["pairs"] = pairs
        return {"pairs": pairs}, ["pairs"], None
    else:
        d = dict(state["d"])
        d[rng.choice(list(d))] = rng.choice(VALUES)
        state["d"] = d
        return {"d": d}, ["d"], None
    state["items"] = items
    return {"items": items}, ["items"], delta


def _run(
    seed: int,
    name: str,
    tmp: Path,
    sabotage: int = 0,
    steps: int = 30,
    switch: bool = True,
    nested: bool = True,
) -> _Pair:
    rng = random.Random("%s-%d" % (name, seed))
    (tmp / "row_4325.html").write_text(INCLUDED)
    state = _state(rng, rng.randint(1, 12))
    pair = _Pair(TEMPLATES[name], [str(tmp)], state, rng.random() < 0.5, sabotage, nested)
    marks: List[str] = []
    for step in range(steps):
        updates, keys, delta = _edit(rng, state)
        roll = rng.random()
        if roll < 0.12:
            # A mark changes only with its value in a real sync (a SafeString
            # has its own fingerprint), so the key is reported changed.
            marks = (
                ["items.%d.a" % rng.randrange(max(1, len(state["items"])))] if roll < 0.08 else []
            )
            if "items" not in keys:
                keys = keys + ["items"]
                updates = dict(updates, items=state["items"])
        elif roll < 0.15 and switch:
            other = rng.choice(list(TEMPLATES))
            pair.update_template(TEMPLATES[other])
        elif roll < 0.18:
            pair.fast.set_loop_item_store_enabled(rng.random() < 0.5)
        elif roll < 0.19 and switch:
            pair.fast.set_loop_item_store_nested(rng.random() < 0.5)
        elif roll < 0.20:
            (tmp / "row_4325.html").write_text(INCLUDED.replace("-", "~" * (step % 3 + 1)))
        if not pair.step(updates, keys, marks, delta):
            return pair
    return pair


SEEDS = range(int(__import__("os").environ.get("DJUST_FUZZ_4325_SEEDS", "8")))


@pytest.mark.parametrize("name", sorted(TEMPLATES))
@pytest.mark.parametrize("seed", SEEDS)
def test_random_edits_match_the_full_render(tmp_path: Path, name: str, seed: int) -> None:
    pair = _run(seed, name, tmp_path)
    assert pair.mismatch is None, "seed %d, template %s: %s differ" % (seed, name, pair.mismatch)


NESTED = ("nested", "nested_unread", "nested_deep", "nested_blocks")


def test_the_store_serves_items_in_the_fuzz(tmp_path: Path) -> None:
    hits = {
        name: sum(_run(s, name, tmp_path, switch=False).hits for s in range(4))
        for name in TEMPLATES
    }
    for name in ("if_forloop", "filters", "two_loops", "unpack", "dict", "inline_if") + NESTED:
        assert hits[name] > 0, hits
    # Refused bodies: cycle and ifchanged carry state across items, include
    # reads the context, and so does the inner loop of nested_outer.
    for name in ("cycle", "ifchanged", "include", "nested_outer"):
        assert hits[name] == 0, hits


def test_switched_off_nested_bodies_are_rendered(tmp_path: Path) -> None:
    """``loop_item_nested`` off (#4325 WP4): a body with a nested loop is
    refused, as before it, and the fuzz still matches."""
    for name in NESTED:
        for seed in range(2):
            pair = _run(seed, name, tmp_path, switch=False, nested=False)
            assert pair.mismatch is None, (name, seed, pair.mismatch)
            assert pair.hits == 0, (name, seed)


@pytest.mark.parametrize("mode", [1, 2, 3])
def test_a_broken_store_fails_the_fuzz(tmp_path: Path, mode: int) -> None:
    """Sabotage 1 serves a stored item whatever its value; 2 ignores length
    and environment; 3 hashes a row's inner lists by kind only, so a nested
    loop over a changed inner list is served stale (#4325 WP4). The fuzz
    must see all three, and 3 on the nested templates."""
    names = NESTED if mode == 3 else TEMPLATES
    found = [
        name
        for name in names
        for seed in range(4)
        if _run(seed, name, tmp_path, sabotage=mode).mismatch is not None
    ]
    assert found, "a broken store passed the fuzz"


def test_a_marked_row_is_rendered_not_served() -> None:
    """A safety mark under the iterable bypasses the store: no item is served
    from it, and the page equals a fresh render of the same state and marks."""
    template = TEMPLATES["if_forloop"]
    state = {"items": [{"a": "<i>x</i>", "on": True} for _ in range(3)]}
    view = RustLiveView(template, None)
    view.update_state(state)
    view.render_with_diff()
    view.mark_safe_keys(["items.1.a"])
    view.set_changed_keys(["items"])
    html, _patches, _ = view.render_with_diff()
    assert view.loop_item_store_hits() == 0
    fresh = RustLiveView(template, None)
    fresh.update_state(state)
    fresh.mark_safe_keys(["items.1.a"])
    strip = re.compile(r' dj-id="[^"]*"')
    assert strip.sub("", html) == strip.sub("", fresh.render_with_diff()[0])
