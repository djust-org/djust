"""The cached-subtree fast paths respect the VDOM nesting limit.

Two fast paths parse a fragment on its own and graft it into the page's tree:
``patch_component_subtree`` (a re-rendered component) and the loop-item parse
cache (reordered loop items). A fragment can be as deep as a page, so grafted
at depth ``d`` it could pass ``MAX_VDOM_DEPTH``. Both decline in that case and
the full path, which parses the whole page and applies the limit there, runs
instead. The pins: at the limit the fast path is taken, one level past it it is
declined, and the page the declined path produces is the one a full render
gives (cache on equals cache off).
"""

from __future__ import annotations

import re

import pytest

from djust import _rust
from djust._rust import RustLiveView

MAX = getattr(_rust, "MAX_VDOM_DEPTH", 256)

pytestmark = pytest.mark.skipif(RustLiveView is None, reason="Rust extension not built")

# Release lines differ in which fast paths exist (1.1 has no component-subtree
# patch, 1.0 no loop cache); each is pinned where it exists.
needs_component_path = pytest.mark.skipif(
    not hasattr(RustLiveView, "patch_component_subtree"), reason="no component-subtree fast path"
)
needs_loop_cache = pytest.mark.skipif(
    not hasattr(RustLiveView, "set_loop_render_cache_enabled"), reason="no loop render cache"
)


def _depth(html: str) -> int:
    """Deepest `<span>` / `<li>` / `<div>` / `<ul>` nesting of a rendered page."""
    depth = deepest = 0
    for tag in re.finditer(r"<(/?)(span|li|div|ul|nav|p|b)\b[^>]*>", html):
        depth += -1 if tag.group(1) else 1
        deepest = max(deepest, depth)
    return deepest


# ---------------------------------------------------------------------------
# patch_component_subtree
# ---------------------------------------------------------------------------

PAGE = '<div dj-root><p>{{ other }}</p><div class="wrap">{{ nav }}</div></div>'


def _component(active: str, chain: int = 0) -> str:
    deep = "<span>" * chain + "</span>" * chain
    return (
        '<div data-component-id="nav">'
        f"<nav><button>Billing</button></nav><p>{active}</p>{deep}</div>"
    )


def _view(nav: str) -> RustLiveView:
    view = RustLiveView(PAGE)
    view.update_state({"other": "A", "nav": nav})
    view.mark_safe_keys(["nav"])
    view.render_with_diff()
    return view


# The component sits at path [1, 0] (two levels under the root); its own tree
# is `chain + 1` levels deep, so the grafted tree reaches level chain + 3.
@needs_component_path
@pytest.mark.parametrize(
    "chain, taken",
    [(MAX - 4, True), (MAX - 3, True), (MAX - 2, False), (MAX + 40, False)],
)
def test_the_component_fast_path_is_taken_up_to_the_limit_and_declined_past_it(chain, taken):
    view = _view(_component("overview"))
    result = view.patch_component_subtree("nav", _component("billing", chain))
    assert (result is not None) is taken


@needs_component_path
def test_a_declined_component_patch_leaves_the_view_for_the_full_render():
    view = _view(_component("overview"))
    assert view.patch_component_subtree("nav", _component("billing", MAX + 40)) is None
    view.update_state({"nav": _component("billing", MAX + 40)})
    html, _patches, _version = view.render_with_diff()
    assert _depth(html) <= MAX
    assert "billing" in html


# ---------------------------------------------------------------------------
# loop-item parse cache
# ---------------------------------------------------------------------------

LOOP = '<ul>{% for x in xs %}<li dj-key="{{ x.id }}">{{ x.name|safe }}</li>{% endfor %}</ul>'


def _items(order, chain: int):
    deep = "<span>" * chain + "</span>" * chain
    return [{"id": i, "name": f"n{i}{deep}"} for i in order]


def _run(enabled: bool, chain: int):
    view = RustLiveView(LOOP)
    view.set_loop_render_cache_enabled(enabled)
    pages = []
    for step, order in enumerate(([1, 2, 3], [3, 1, 2])):
        if step:
            view.set_changed_keys(["xs"])
        view.update_state({"xs": _items(order, chain)})
        pages.append(view.render_with_diff()[0])
    return pages


# `<ul>` is level 1 and `<li>` level 2, so an item with `chain` spans reaches
# level chain + 2 once grafted.
@needs_loop_cache
@pytest.mark.parametrize("chain", [MAX - 3, MAX - 2, MAX - 1, MAX + 40])
def test_the_loop_cache_gives_the_page_a_full_parse_gives(chain):
    on = _run(True, chain)
    off = _run(False, chain)
    assert on == off
    assert all(_depth(page) <= MAX for page in on)


@needs_loop_cache
def test_the_loop_cache_still_hits_at_the_limit():
    view = RustLiveView(LOOP)
    view.set_loop_render_cache_enabled(True)
    view.update_state({"xs": _items([1, 2, 3], MAX - 2)})
    view.render_with_diff()
    view.set_changed_keys(["xs"])
    view.update_state({"xs": _items([3, 1, 2], MAX - 2)})
    view.render_with_diff()
    assert view.loop_parse_cache_hits() == 3
