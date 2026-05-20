"""End-to-end regression tests for #1550 / #1552 ({% if %}/{% elif %}
branch swap producing a doubled subtree on the client).

## Root cause (canonized here so future regressions surface fast)

The thread-local djust_id counter (`crates/djust_vdom/src/lib.rs:55`)
generates monotonically-increasing ids during HTML parsing. Each
`RustLiveView` keeps its rendered tree in `last_vdom`, the ids in which
were assigned by the THREAD that did the parse. When `last_vdom` is
either:

1. Migrated across worker threads (Channels / asyncio thread-pool
   handoff), OR
2. Round-tripped through `serialize_msgpack` / `deserialize_msgpack`
   (`InMemoryStateBackend.get` does this on every cache lookup —
   `python/djust/state_backends/memory.py:118`),

the new thread's counter is independent of `last_vdom`'s ids. The next
`parse_html_continue` call may then generate ids 1..k that collide with
ids 1..k surviving in `last_vdom`. The resulting `InsertSubtree.html`
patch carries dj-ids that match other elements in the parent's child
list, and the client's id-first RemoveChild handler at
`12-vdom-patch.js:1778-1788` resolves `:scope > [dj-id=N]` to the
fresher (newly-inserted) element instead of the surviving OLD one —
removing the wrong subtree and leaving the OLD content visible.

Pre-#1538 (commit `0a119962`) the bug was masked because the msgpack
deserialize failed silently and the state-backend `get()` returned
None, forcing a full re-mount on every event with no diff path.
Post-#1538 the deserialize succeeds, the diff path runs, and the
collision shape became reachable in production.

The fix (this PR): before each `parse_html_continue` in
`render_with_diff` / `render_binary_diff`, walk `last_vdom`, compute
its highest djust_id, and advance the thread-local counter past it
via `djust_vdom::ensure_id_counter_at_least`. The counter becomes
effectively per-view, surviving thread handoff and msgpack roundtrip.
"""

import json
import re
import threading
import queue

import pytest

try:
    from djust._rust import RustLiveView
except ImportError:  # pragma: no cover
    RustLiveView = None


pytestmark = pytest.mark.skipif(RustLiveView is None, reason="Rust extensions not built")


_IF_ELIF_TEMPLATE = """<div class="wizard-content">
  {% if step == "claimant" %}
    <h2>About You</h2>
    <h3>Your Information</h3>
    <h4>Attorney Information</h4>
    <input type="text" name="first_name" />
    <input type="text" name="last_name" />
    <input type="text" name="address" />
    <input type="text" name="city" />
    <input type="text" name="state" />
  {% elif step == "vehicle" %}
    <h2>What Happened</h2>
    <input type="text" name="incident_date" />
    <input type="text" name="incident_time" />
    <input type="text" name="incident_location" />
    <input type="text" name="borough" />
  {% endif %}
</div>"""


_IF_ELSE_TEMPLATE = """<div class="root">
  {% if flag %}
    <h2>Branch A heading</h2>
    <input type="text" name="a_field" />
  {% else %}
    <h2>Branch B heading</h2>
    <input type="text" name="b_field" />
  {% endif %}
</div>"""


def _extract_djids(html: str) -> set[str]:
    """All dj-id values appearing as attributes in the given HTML."""
    return set(re.findall(r'dj-id="([^"]+)"', html))


def _run_cross_thread_render(template: str, state_1: dict, state_2: dict) -> tuple[str, str, list]:
    """Render `template` in thread A with `state_1`, serialize_msgpack,
    deserialize into thread B (fresh thread-local counter), apply
    `state_2`, render again. Returns (html_1, html_2, patches_list).

    This shape mirrors production's `InMemoryStateBackend.get` returning
    a deserialized clone on every event (`memory.py:118-148`), plus
    Channels' thread-pool handoff where the consumer's async loop and
    the renderer's `sync_to_async` worker may run on different threads.
    """
    q_serialized = queue.Queue()
    q_h1 = queue.Queue()
    q_result = queue.Queue()

    def producer():
        v = RustLiveView(template)
        v.set_template_dirs([])
        v.update_state(state_1)
        h1, _, _ = v.render_with_diff()
        q_h1.put(h1)
        q_serialized.put(v.serialize_msgpack())

    def consumer():
        serialized = q_serialized.get()
        restored = RustLiveView.deserialize_msgpack(serialized)
        restored.set_template_dirs([])
        restored.update_state(state_2)
        h2, p2, _ = restored.render_with_diff()
        q_result.put((h2, p2))

    t_a = threading.Thread(target=producer)
    t_a.start()
    t_a.join()
    t_b = threading.Thread(target=consumer)
    t_b.start()
    t_b.join()

    h1 = q_h1.get()
    h2, p2 = q_result.get()
    patches = json.loads(p2) if p2 else []
    return h1, h2, patches


# --------------------------------------------------------------------------
# #1552 — {% if/elif %} + multi-rooted bodies → doubled subtree.
# --------------------------------------------------------------------------


def test_no_djid_collision_between_last_vdom_and_insert_subtree_html_1552():
    """The pre-fix root cause empirically.

    After `RustLiveView.deserialize_msgpack` into a thread with a fresh
    counter, the next `parse_html_continue` MUST NOT generate dj-ids
    that already appear in `last_vdom`. Otherwise the resulting
    `InsertSubtree.html` collides with surviving old-tree elements and
    the client's `:scope > [dj-id=N]` querySelector returns the wrong
    element.
    """
    h1, _h2, patches = _run_cross_thread_render(
        _IF_ELIF_TEMPLATE,
        {"step": "claimant"},
        {"step": "vehicle"},
    )

    old_ids = _extract_djids(h1)

    insert_subtree_html = ""
    remove_child_ids = []
    for p in patches:
        if p.get("type") == "InsertSubtree":
            insert_subtree_html += p.get("html", "")
        elif p.get("type") == "RemoveChild":
            cid = p.get("child_d")
            if cid:
                remove_child_ids.append(cid)

    new_ids = _extract_djids(insert_subtree_html)

    collisions = old_ids & new_ids
    assert not collisions, (
        f"NEW InsertSubtree.html shares dj-ids with surviving OLD "
        f"tree — this is the #1550/#1552 root cause. "
        f"Collisions: {sorted(collisions)} | "
        f"OLD ids: {sorted(old_ids)} | "
        f"NEW ids: {sorted(new_ids)} | "
        f"RemoveChild child_d: {remove_child_ids} | "
        f"Patches: {json.dumps(patches, indent=2)}"
    )

    # Defensive: at least one RemoveChild patch must have been emitted
    # (otherwise this test would tautology-pass when the diff layer
    # itself regresses to "emits nothing").
    assert remove_child_ids, (
        f"Expected RemoveChild patches for the if/elif swap. "
        f"Patches: {json.dumps(patches, indent=2)}"
    )


def test_remove_child_targets_are_uniquely_resolvable_post_insert_1552():
    """Tighter assertion of the actual production failure mode.

    Even if collisions exist, the bug only manifests when a
    RemoveChild patch's `child_d` targets an id that ALSO appears in
    the InsertSubtree HTML — that's the case where the client's
    querySelector returns the (newer, leftmost) inserted element and
    removes IT instead of the surviving old element.
    """
    h1, _h2, patches = _run_cross_thread_render(
        _IF_ELIF_TEMPLATE,
        {"step": "claimant"},
        {"step": "vehicle"},
    )

    old_ids = _extract_djids(h1)

    insert_subtree_ids: set[str] = set()
    remove_child_ids: list[str] = []
    for p in patches:
        if p.get("type") == "InsertSubtree":
            insert_subtree_ids |= _extract_djids(p.get("html", ""))
        elif p.get("type") == "RemoveChild":
            cid = p.get("child_d")
            if cid:
                remove_child_ids.append(cid)

    # The combined "live DOM" view after the client applies
    # InsertSubtree patches: surviving OLD ids ∪ NEW ids from
    # InsertSubtree.html. A RemoveChild's `child_d` is unique
    # iff exactly one element in that combined set carries the id.
    overlap_targets = [c for c in remove_child_ids if c in insert_subtree_ids]
    assert not overlap_targets, (
        f"RemoveChild patches target dj-ids that ALSO appear in the "
        f"InsertSubtree.html — production JS would remove the wrong "
        f"(newer, leftmost) element via `:scope > [dj-id=N]`. "
        f"Overlap: {overlap_targets} | "
        f"OLD ids: {sorted(old_ids)} | "
        f"InsertSubtree ids: {sorted(insert_subtree_ids)} | "
        f"RemoveChild child_d: {remove_child_ids}"
    )


# --------------------------------------------------------------------------
# #1550 — {% if/else %} where the two branches differ in structure /
# element-count. Without the counter-monotonicity fix, on cross-thread
# renders the new branch's ids may collide with the old branch's ids,
# producing the "branch doesn't swap" symptom.
# --------------------------------------------------------------------------


def test_no_djid_collision_for_if_else_branch_swap_1550():
    """Companion to the #1552 case for the simpler {% if/else %} shape."""
    h1, _h2, patches = _run_cross_thread_render(
        _IF_ELSE_TEMPLATE,
        {"flag": True},
        {"flag": False},
    )

    old_ids = _extract_djids(h1)
    insert_html = ""
    for p in patches:
        if p.get("type") in ("InsertSubtree", "Replace"):
            insert_html += json.dumps(p)

    new_ids = _extract_djids(insert_html)
    collisions = old_ids & new_ids
    assert not collisions, (
        f"NEW Insert/Replace payload shares dj-ids with OLD tree: "
        f"{sorted(collisions)} | OLD: {sorted(old_ids)} | "
        f"NEW: {sorted(new_ids)}"
    )


# --------------------------------------------------------------------------
# Higher-level invariant test — applies regardless of branch shape.
# --------------------------------------------------------------------------


def test_post_msgpack_roundtrip_counter_advanced_past_last_vdom():
    """The counter-monotonicity invariant: after a `deserialize_msgpack`
    + a subsequent `render_with_diff` in a fresh thread, every dj-id
    generated by the new render must be strictly larger than every
    dj-id in the deserialized last_vdom. This is the invariant the fix
    establishes."""
    h1, h2, _patches = _run_cross_thread_render(
        _IF_ELIF_TEMPLATE,
        {"step": "claimant"},
        {"step": "vehicle"},
    )

    # Translate base62 ids to integers so we can compare numerically.
    def _b62(s: str) -> int:
        v = 0
        for c in s:
            if c.isdigit():
                v = v * 62 + (ord(c) - ord("0"))
            elif c.islower():
                v = v * 62 + (ord(c) - ord("a") + 10)
            else:
                v = v * 62 + (ord(c) - ord("A") + 36)
        return v

    old_ids = {_b62(s) for s in _extract_djids(h1)}
    h2_ids = {_b62(s) for s in _extract_djids(h2)}
    new_only = h2_ids - old_ids  # ids that appear only in the NEW render
    if not new_only:
        pytest.skip("All H2 ids carried over from H1 — no fresh ids to verify")

    old_max = max(old_ids) if old_ids else 0
    new_min = min(new_only)
    assert new_min > old_max, (
        f"New-render id {new_min} <= old-tree max {old_max} — "
        f"counter-monotonicity invariant broken; collisions possible. "
        f"OLD ids: {sorted(old_ids)} | NEW-only ids: {sorted(new_only)}"
    )
