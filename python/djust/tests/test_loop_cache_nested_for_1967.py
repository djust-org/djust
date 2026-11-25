"""The #1967 loop cache with nested ``{% for %}`` bodies, checked differentially.

The outer body of a nested loop is never cached (a nested ``For`` is a
position-dependent construct), but the INNER loop's body is its own ``For``
and is cached whenever it reads nothing but its own loop variable. This file
pins that the cache is right for nested loops: inner loops over an outer
item's field, over outer context, inner bodies that read the outer item or
outer context, ``forloop.parentloop``, ``{% empty %}``, three levels, and
equal inner items under different outer rows.

Each template runs a seeded random edit sequence through two views, the
cache on and the cache off (same pipeline, the per-item store off in both so
only #1967 is under test). HTML and patches must match after id
canonicalization, and the HTML must match a fresh view rendering the same
state from scratch.
"""

from __future__ import annotations

import copy
import random
import re
from typing import Any, Dict, List

import pytest

from djust._rust import RustLiveView
from djust.tests.test_loop_item_store_4325 import _canonical

TEMPLATES: Dict[str, str] = {
    # Inner loop over a field of the outer item; inner body reads only `t`.
    "item_field": (
        "<div dj-root>{% for r in rows %}<ul>{% for t in r.tags %}<li>{{ t }}</li>"
        "{% endfor %}</ul>{% endfor %}</div>"
    ),
    # Inner loop over outer context.
    "outer_context": (
        "<div dj-root><table>{% for r in rows %}<tr><th>{{ r.name }}</th>"
        "{% for c in cols %}<td>{{ c }}</td>{% endfor %}</tr>{% endfor %}</table></div>"
    ),
    # Inner body reads the outer item and outer context.
    "reads_outer": (
        "<div dj-root>{% for r in rows %}<p>{% for t in r.tags %}"
        "<i>{{ prefix }}{{ r.name }}:{{ t }}</i>{% endfor %}</p>{% endfor %}</div>"
    ),
    # Inner body reads forloop and forloop.parentloop.
    "parentloop": (
        "<div dj-root>{% for r in rows %}<p>{% for t in r.tags %}"
        "<i>{{ forloop.parentloop.counter }}.{{ forloop.counter }} {{ t }}</i>"
        "{% endfor %}</p>{% endfor %}</div>"
    ),
    # Dict inner items, {% empty %}, filters.
    "empty_dicts": (
        "<div dj-root>{% for r in rows %}<ol>{% for t in r.items %}"
        "<li>{{ t.label|upper }} {{ t.n|add:1 }}</li>{% empty %}<li>none</li>"
        "{% endfor %}</ol>{% endfor %}</div>"
    ),
    # Three levels, the innermost over the middle item's field.
    "three_levels": (
        "<div dj-root>{% for r in rows %}<section>{% for t in r.items %}<ul>"
        "{% for w in t.words %}<li>{{ w }}</li>{% endfor %}</ul>{% endfor %}"
        "</section>{% endfor %}</div>"
    ),
    # An {% if %} inside the inner body: both bodies are position-dependent.
    "inner_if": (
        "<div dj-root>{% for r in rows %}<p>{% for t in r.tags %}"
        "{% if t %}<b>{{ t }}</b>{% else %}-{% endif %}{% endfor %}</p>{% endfor %}</div>"
    ),
    # Inner loop over the outer context, inner body reads only the inner var,
    # and a sibling inner loop over the item field.
    "siblings": (
        "<div dj-root>{% for r in rows %}<p>{% for c in cols %}<b>{{ c }}</b>{% endfor %}"
        "{% for t in r.tags %}<b>{{ t }}</b>{% endfor %}</p>{% endfor %}</div>"
    ),
}

WORDS = ["a", "b", "c", "x", "<y>", ""]


def _row(rng: random.Random, n: int) -> Dict[str, Any]:
    return {
        "name": "r%d" % n,
        "tags": [rng.choice(WORDS) for _ in range(rng.randint(0, 3))],
        "items": [
            {
                "label": rng.choice(WORDS),
                "n": rng.randint(0, 3),
                "words": [rng.choice(WORDS) for _ in range(rng.randint(0, 2))],
            }
            for _ in range(rng.randint(0, 3))
        ],
    }


def _edit(rng: random.Random, state: Dict[str, Any], counter: List[int]) -> List[str]:
    rows = state["rows"]
    op = rng.randrange(9)
    if op == 0 and rows:
        rows[rng.randrange(len(rows))]["tags"] = [
            rng.choice(WORDS) for _ in range(rng.randint(0, 3))
        ]
    elif op == 1 and rows:
        r = rows[rng.randrange(len(rows))]
        if r["items"]:
            it = r["items"][rng.randrange(len(r["items"]))]
            it[rng.choice(["label", "n", "words"])] = (
                rng.randint(0, 3)
                if rng.random() < 0.3
                else [rng.choice(WORDS)]
                if rng.random() < 0.5
                else rng.choice(WORDS)
            )
            for item in r["items"]:
                if not isinstance(item["words"], list):
                    item["words"] = [str(item["words"])]
                if not isinstance(item["n"], int):
                    item["n"] = 0
    elif op == 2:
        counter[0] += 1
        rows.insert(rng.randint(0, len(rows)), _row(rng, counter[0]))
    elif op == 3 and rows:
        rows.pop(rng.randrange(len(rows)))
    elif op == 4:
        rng.shuffle(rows)
    elif op == 5:
        state["cols"] = [rng.choice(WORDS) for _ in range(rng.randint(0, 3))]
        return ["cols"]
    elif op == 6:
        state["prefix"] = rng.choice(["", "P-", "Q-"])
        return ["prefix"]
    elif op == 7 and rows:
        rows[rng.randrange(len(rows))]["name"] = rng.choice(WORDS) + "n"
    elif op == 8 and len(rows) >= 2:
        i, j = rng.sample(range(len(rows)), 2)
        rows[i]["tags"], rows[j]["tags"] = list(rows[j]["tags"]), list(rows[i]["tags"])
    return ["rows"]


def _strip_ids(html: str) -> str:
    """A new node's dj-id comes from a counter the views share, so two views
    name the same node differently; the patches are compared after
    canonicalization, which checks that each view's ids are consistent."""
    return re.sub(r' dj-id="[^"]*"', "", html)


def _view(src: str, cache: bool) -> RustLiveView:
    view = RustLiveView(src)
    view.set_loop_render_cache_enabled(cache)
    view.set_loop_item_store_enabled(False)
    return view


def _run(name: str, seed: int, steps: int = 25) -> int:
    rng = random.Random(seed)
    src = TEMPLATES[name]
    counter = [0]
    state: Dict[str, Any] = {
        "rows": [_row(rng, i) for i in range(rng.randint(0, 5))],
        "cols": ["c1", "c2"],
        "prefix": "",
    }
    counter[0] = len(state["rows"])
    on, off = _view(src, True), _view(src, False)
    prev_on = prev_off = ""
    hits = 0
    for step in range(steps):
        keys = list(state) if step == 0 else _edit(rng, state, counter)
        results = []
        for view in (on, off):
            if step:
                view.set_changed_keys(keys)
            view.update_state(copy.deepcopy({k: state[k] for k in keys}))
            results.append(view.render_with_diff())
        hits += on.loop_render_cache_hits()
        (html_on, patches_on, _), (html_off, patches_off, _) = results
        fresh = _view(src, False)
        fresh.update_state(copy.deepcopy(state))
        html_fresh = fresh.render_with_diff()[0]
        where = "%s seed %d step %d" % (name, seed, step)
        assert _strip_ids(html_on) == _strip_ids(html_off), where
        assert _strip_ids(html_on) == _strip_ids(html_fresh), where
        assert _canonical(prev_on, html_on, patches_on or "[]") == _canonical(
            prev_off, html_off, patches_off or "[]"
        ), where
        prev_on, prev_off = html_on, html_off
    return hits


@pytest.mark.parametrize("seed", range(40))
@pytest.mark.parametrize("name", sorted(TEMPLATES))
def test_nested_loops_match_with_the_cache_off_and_a_fresh_render(name: str, seed: int) -> None:
    _run(name, seed)


@pytest.mark.parametrize(
    "name, served",
    [
        ("item_field", True),
        ("outer_context", True),
        ("empty_dicts", True),
        ("three_levels", True),
        ("siblings", True),
        ("reads_outer", False),
        ("parentloop", False),
        ("inner_if", False),
    ],
)
def test_only_inner_bodies_that_read_their_own_var_are_served(name: str, served: bool) -> None:
    """The cache does take inner bodies (so the fuzz above exercises it), and
    never one that reads the outer item, outer context or forloop."""
    hits = sum(_run(name, seed) for seed in range(5))
    assert (hits > 0) == served, hits
