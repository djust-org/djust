"""ADR-032 S1 (#2917): ``RustLiveView.patch_component_subtree`` patches one
bound component's subtree without a page render.

The oracle is ``render_with_diff`` itself: after a scoped patch the view must
be in the state a full render would have left it in — same HTML, a no-op
next diff, ids that do not collide, the partial-render fragment cache dropped
and the text fast-path baseline refreshed — and every ``None`` must leave the
view untouched so the caller's full render is still exact (D6).
"""

from __future__ import annotations

import json
import re

import pytest

try:
    from djust._rust import RustLiveView, diff_html
except ImportError:  # pragma: no cover
    RustLiveView = None  # type: ignore[assignment]
    diff_html = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(RustLiveView is None, reason="Rust extension not built")

TEMPLATE = '<div dj-root><p>{{ other }}</p><div class="wrap">{{ nav }}</div></div>'
# Two top-level template nodes (a text node, then the root element): the
# partial-render fragment cache holds one fragment per top-level node, so the
# element fragment — the one holding the component markup — can be REUSED
# when only ``other`` changes. That is the leak D3 closes by dropping the cache.
CACHE_TEMPLATE = "{{ other }}" + TEMPLATE


def comp(active: str, *, extra: str = "", cid: str = "nav") -> str:
    return (
        f'<div data-component-id="{cid}">'
        '<nav><button dj-click="select" dj-value="billing">Billing</button></nav>'
        f"<p>{active}<b>{cid}</b></p>{extra}</div>"
    )


def make_view(template: str = TEMPLATE, nav: str = comp("overview")) -> RustLiveView:
    view = RustLiveView(template)
    view.update_state({"other": "A", "nav": nav})
    view.mark_safe_keys(["nav"])
    return view


def dj_ids(html: str) -> list[str]:
    return re.findall(r'dj-id="([^"]+)"', html)


def strip_ids(html: str) -> str:
    return re.sub(r' dj-id="[^"]+"', "", html)


@pytest.fixture
def view() -> RustLiveView:
    v = make_view()
    v.render_with_diff()
    return v


# ---------------------------------------------------------------------------
# The triple, the paths, the version
# ---------------------------------------------------------------------------


def test_returns_the_render_with_diff_triple_with_a_bumped_version(view):
    _, _, base_version = view.render_with_diff()
    result = view.patch_component_subtree("nav", comp("billing"))
    assert result is not None
    html, patches_json, version = result
    assert version == base_version + 1
    patches = json.loads(patches_json)
    assert patches, "a text change inside the component must produce patches"
    assert all(p["type"] == "SetText" for p in patches), patches
    # Byte-identical to a page render with the new component HTML.
    fresh = make_view(nav=comp("billing"))
    full_html, _, _ = fresh.render_with_diff()
    assert html == full_html


def test_patch_paths_are_prefixed_with_the_component_node_path(view):
    old, new = comp("overview"), comp("billing", extra="<i>new</i>")
    _, patches_json, _ = view.patch_component_subtree("nav", new)
    scoped = json.loads(patches_json)
    local = json.loads(diff_html(old, new))
    assert len(scoped) == len(local) >= 2
    # <div dj-root><p/><div class="wrap"><div data-component-id="nav"> → [1, 0]
    prefix = [1, 0]
    for s, loc in zip(scoped, local):
        assert s["type"] == loc["type"]
        assert s["path"] == prefix + loc["path"], (s, loc)


def test_inserted_nodes_get_ids_past_the_page_high_water_mark(view):
    before, _, _ = view.render_with_diff()
    html, patches_json, _ = view.patch_component_subtree(
        "nav", comp("overview", extra="<i>new</i><u>more</u>")
    )
    ids = dj_ids(html)
    assert len(ids) == len(set(ids)), f"duplicate dj-ids after a scoped patch: {ids}"
    inserted = [p for p in json.loads(patches_json) if p["type"] == "InsertChild"]
    assert inserted
    for p in inserted:
        new_id = p["node"]["attrs"]["dj-id"]
        assert new_id not in dj_ids(before), new_id
    # Survivors keep the ids the client DOM holds.
    assert dj_ids(before) == [i for i in ids if i in dj_ids(before)]


# ---------------------------------------------------------------------------
# The view is left as a full render would have left it
# ---------------------------------------------------------------------------


def test_state_is_updated_so_the_next_full_render_is_a_noop(view):
    html, _, version = view.patch_component_subtree("nav", comp("billing"))
    html2, patches2, version2 = view.render_with_diff()
    assert json.loads(patches2) == []
    assert html2 == html
    assert version2 == version + 1


def test_partial_render_fragment_cache_is_dropped():
    """The top-level-node fragment cache still holds the OLD component
    markup; a later partial render (changed_keys on another key) must not
    re-collect it (D3 step "clear node_html_cache")."""
    view = make_view(template=CACHE_TEMPLATE)
    view.render_with_diff()
    view.update_state({"other": "A2"})
    view.set_changed_keys(["other"])
    view.render_with_diff()  # populates the fragment cache on the partial path
    assert view.patch_component_subtree("nav", comp("billing")) is not None
    view.update_state({"other": "B"})
    view.set_changed_keys(["other"])
    html, _, _ = view.render_with_diff()
    assert "<p>B</p>" in strip_ids(html)
    assert "billing<b>" in strip_ids(html) and "overview<b>" not in html, html


def test_the_next_render_diffs_against_what_the_page_shows(view):
    """``last_html`` is rebuilt from the spliced tree, so the text fast paths
    on the NEXT renders compare against the patched markup, not the
    pre-patch render."""
    view.patch_component_subtree("nav", comp("billing"))
    for value in ("B", "C", "D"):
        view.update_state({"other": value})
        html, patches_json, _ = view.render_with_diff()
        patches = json.loads(patches_json)
        assert [p["type"] for p in patches] == ["SetText"], patches
        assert patches[0]["text"] == value
        assert "billing<b>" in strip_ids(html)
    assert view.get_render_timing()["fast_path"] == 2.0, "the text-region fast path is back"


@pytest.mark.parametrize(
    "template",
    [
        '<div dj-root><div dj-update="ignore">{{ nav }}</div></div>',
        '<div dj-root><ul dj-virtual><li dj-key="1">{{ nav }}</li></ul></div>',
    ],
    ids=["dj-update-ignore", "dj-virtual"],
)
def test_none_under_an_ignored_or_virtual_ancestor(template):
    """The full path leaves a ``dj-update="ignore"`` region alone and
    addresses ``[dj-virtual]`` rows by key; a scoped patch there would not
    be what the full path emits."""
    view = make_view(template=template)
    before = view.render_with_diff()
    assert view.patch_component_subtree("nav", comp("billing")) is None
    _assert_untouched(view, before)


def test_dj_update_ignore_inside_the_component_keeps_the_old_children(view):
    view = make_view(nav=comp("overview", extra='<div dj-update="ignore"><s>keep</s></div>'))
    view.render_with_diff()
    html, patches_json, _ = view.patch_component_subtree(
        "nav", comp("billing", extra='<div dj-update="ignore"><s>replaced</s></div>')
    )
    assert "<s>keep</s>" in strip_ids(html) and "replaced" not in html
    assert all("replaced" not in json.dumps(p) for p in json.loads(patches_json))


def test_safe_html_is_not_escaped_on_either_path(view):
    html, _, _ = view.patch_component_subtree("nav", comp("billing"))
    assert "&lt;nav" not in html and "<nav" in html
    html2, _, _ = view.render_with_diff()
    assert "&lt;nav" not in html2


def test_timing_reports_the_component_fast_path(view):
    view.patch_component_subtree("nav", comp("billing"))
    timing = view.get_render_timing()
    assert timing["fast_path"] == 3.0
    assert timing["render_ms"] == 0.0


# ---------------------------------------------------------------------------
# None — and untouched — whenever the scoped path cannot be exact
# ---------------------------------------------------------------------------


def _assert_untouched(view: RustLiveView, before: tuple[str, str, int]) -> None:
    html, _, version = view.render_with_diff()
    assert version == before[2] + 1, "a None must not bump the version"
    assert html == before[0], "a None must not change the tree"


def test_none_before_the_first_render():
    view = make_view()
    assert view.patch_component_subtree("nav", comp("billing")) is None


def test_none_when_the_node_is_missing(view):
    before = view.render_with_diff()
    assert view.patch_component_subtree("zzz", comp("billing", cid="zzz")) is None
    _assert_untouched(view, before)


def test_none_when_the_node_is_duplicated():
    view = make_view(template="<div>{{ nav }}{{ nav }}</div>")
    before = view.render_with_diff()
    assert view.patch_component_subtree("nav", comp("billing")) is None
    _assert_untouched(view, before)


def test_none_when_the_fragment_has_two_roots(view):
    before = view.render_with_diff()
    assert view.patch_component_subtree("nav", comp("billing") + "<p>stray</p>") is None
    _assert_untouched(view, before)


def test_none_when_the_fragment_root_carries_another_id(view):
    before = view.render_with_diff()
    assert view.patch_component_subtree("nav", comp("billing", cid="other")) is None
    _assert_untouched(view, before)


@pytest.mark.parametrize(
    ("template", "markup"),
    [
        (
            "<div dj-root><table>{{ nav }}</table></div>",
            '<div data-component-id="nav"><table><tr><td>{v}</td></tr></table></div>',
        ),
    ],
    ids=["table-in-table"],
)
def test_none_when_the_page_parse_relocated_part_of_the_component(template, markup):
    """Review of #2920 🟡2: the page parse foster-parents a nested ``<table>``
    out of the wrapper; a fragment parse of the same markup does not. The wrapper in the page then holds
    less than the markup says, so a scoped patch would leave the relocated
    part stale. Re-parsing the old markup and diffing it against the node the
    page holds catches it — the scoped path is not exact, the page renders."""
    view = make_view(template=template, nav=markup.format(v="a"))
    before = view.render_with_diff()
    assert view.patch_component_subtree("nav", markup.format(v="b")) is None
    _assert_untouched(view, before)
