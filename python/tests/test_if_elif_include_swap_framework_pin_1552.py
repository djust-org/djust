"""#1552 framework-level invariant tests + the negative result that informs
the investigation strategy.

## The diagnostic finding

#1552 reporter reported (rc4 and verified again on rc7) that:

    {% if current_step_name == "claimant" %}
        {% include "intake/steps/mv_step_claimant.html" %}
    {% elif current_step_name == "vehicle" %}
        {% include "intake/steps/vpd_step_vehicle.html" %}
    {% endif %}

…produces BOTH step subtrees in the post-swap DOM. PR #1555 (commit
`8e4c40328`, merged 2026-05-20) shipped a real fix for dj-id counter
collisions across thread boundaries / msgpack round-trip, but the
reporter verified the user-visible symptom still reproduces on rc7.
The bug is real; PR #1555 fixed an adjacent issue, not this one.

This file's tests cover the THREE template shapes the reporter's
production code uses:

1. `{% if/elif %} + {% include %}` — the simplest include-swap shape.
2. `{% if/elif %} + {% include %}` + cross-thread msgpack round-trip —
   the same shape that broke for dj-id collisions in PR #1555 (which
   PR fixed; this test pins it stays fixed).
3. `{% extends %} + {% block %} + {% if/elif %} + {% include %}` —
   the reporter's full template-inheritance shape.

**All three tests PASS on main.** This is the empirically-honest
finding (per CLAUDE.md Bug-report triage rule #1: "trust the symptom,
not the cited path"): the framework differ produces correct patches
(5 RemoveChild patches for the old subtree + Insert/Replace patches
for the new subtree) for every template shape the reporter described.
The user-visible bug must live in another layer — candidates:

- The WS-event save block (PR #1466 / commit `a5e2c50c`): `last_vdom`
  persists on every WS event; if save + load produce divergent VDOM
  trees, the diff is computed against stale state.
- Sticky-child persistence (ADR-018 / iters 18a/18b/18c).
- The JS-side patch-application path under specific DOM conditions
  the reporter's app produces.
- Some interaction we haven't sampled.

**Why these tests stay in the suite anyway:** they're a framework-level
regression backstop for the template combinations the reporter
described. If a future change in the differ regresses any of these
shapes, the tests catch it fast. That's the value — pins the framework
SIDE of the bug at "behaves correctly," so the next investigation knows
to look at the other layers, not re-trace the differ.

**Next-step request to the reporter** (posted as a #1552 comment after
this PR lands): share a BugCapture URL (the iter A feature shipped in
PR #1563 on this same day) capturing `state_before` + `state_after` +
`vdom_patches` at the moment of the broken transition. The bug-capture
artifact contains the exact patches THEIR deploy produces — which we
can compare against the patches my framework reproducers produce. If
they diverge, the gap is identified. See
`docs/website/guides/bug-capture.md` for the workflow.

## Important: this test file is NOT a successful-fix victory lap.

PR #1555 also added tests and called them a fix. Those tests passed
on main with the bug still in production because they covered a
different shape. Future readers: take "test passes on main" as
evidence about THE FRAMEWORK behaving correctly, NOT as evidence about
the user's bug being resolved. The bug stays OPEN until the reporter
verifies their production symptom is gone, with bug-capture data
linking their actual patches to a specific framework change.
"""

from __future__ import annotations

import json
import queue
import tempfile
import threading
from pathlib import Path

import pytest

try:
    from djust._rust import RustLiveView
except ImportError:  # pragma: no cover
    RustLiveView = None


pytestmark = pytest.mark.skipif(RustLiveView is None, reason="Rust extensions not built")


# The reporter's exact shape: parent template containing {% if/elif %}
# whose bodies are `{% include %}` statements.
_PARENT_TEMPLATE_WITH_INCLUDE = """<div class="wizard-content">
  {% if step == "claimant" %}
    {% include "step_claimant.html" %}
  {% elif step == "vehicle" %}
    {% include "step_vehicle.html" %}
  {% endif %}
</div>"""

# Step 1 — the OLD content that should be REMOVED after the swap.
_STEP_CLAIMANT_HTML = """<h2>About You</h2>
<h3>Your Information</h3>
<input type="text" name="first_name" />
<input type="text" name="last_name" />
<input type="text" name="address" />
<input type="text" name="city" />"""

# Step 2 — the NEW content that should be INSERTED after the swap.
_STEP_VEHICLE_HTML = """<h2>What Happened</h2>
<input type="text" name="incident_date" />
<input type="text" name="incident_time" />
<input type="text" name="incident_location" />
<input type="text" name="borough" />"""


def _run_cross_thread_include_render(
    parent_template: str,
    includes: dict[str, str],
    state_1: dict,
    state_2: dict,
) -> tuple[str, str, list]:
    """Variant of _run_cross_thread_render that writes ``includes`` to a
    temp dir, points the rust view at it via ``set_template_dirs``, then
    runs the same cross-thread render-then-rerender pattern.

    Returns (html_1, html_2, patches_list). ``includes`` is a dict of
    filename -> template body — written into the temp dir verbatim.
    """
    with tempfile.TemporaryDirectory() as td:
        tdpath = Path(td)
        for name, body in includes.items():
            (tdpath / name).write_text(body, encoding="utf-8")

        q_serialized: "queue.Queue[bytes]" = queue.Queue()
        q_h1: "queue.Queue[str]" = queue.Queue()
        q_result: "queue.Queue[tuple[str, str]]" = queue.Queue()

        def producer():
            v = RustLiveView(parent_template)
            v.set_template_dirs([str(tdpath)])
            v.update_state(state_1)
            h1, _patches_1, _version = v.render_with_diff()
            q_h1.put(h1)
            q_serialized.put(v.serialize_msgpack())

        def consumer():
            serialized = q_serialized.get()
            restored = RustLiveView.deserialize_msgpack(serialized)
            restored.set_template_dirs([str(tdpath)])
            restored.update_state(state_2)
            h2, p2, _version = restored.render_with_diff()
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
# The load-bearing test — must FAIL on main before any fix.
# --------------------------------------------------------------------------


def test_framework_include_swap_emits_correct_remove_then_insert_1552():
    """Framework-level invariant pin (does NOT reproduce reporter's bug):
    {% if %} + {% include %} swap correctly removes old subtree.

    PASSES on main. Documents that THE FRAMEWORK produces correct
    Remove+Insert patches for this template shape. The reporter's
    user-visible symptom must come from another layer (WS save block /
    sticky-child / JS patch application / etc.). See module docstring
    for the investigation strategy.

    If this test ever STARTS failing, the framework has regressed —
    file a P0 immediately. PR #1555's adjacent fix is part of why this
    test currently passes; counter-monotonicity invariants are
    load-bearing for the include-swap shape.
    """
    h1, h2, patches = _run_cross_thread_include_render(
        _PARENT_TEMPLATE_WITH_INCLUDE,
        includes={
            "step_claimant.html": _STEP_CLAIMANT_HTML,
            "step_vehicle.html": _STEP_VEHICLE_HTML,
        },
        state_1={"step": "claimant"},
        state_2={"step": "vehicle"},
    )

    # H1 sanity: the first render must contain the claimant heading.
    assert "About You" in h1, (
        f"Pre-condition failed: step-1 render missing claimant heading. h1={h1!r}"
    )

    # The bit-exact symptom assertion. The reporter saw BOTH headings
    # after the swap. Correct behavior is the NEW heading present + the
    # OLD heading absent.
    assert "What Happened" in h2, (
        f"Post-swap render missing the NEW step heading 'What Happened'. "
        f"h2={h2!r} | patches={json.dumps(patches, indent=2)}"
    )
    assert "About You" not in h2, (
        "BUG REPRODUCED — post-swap render still contains OLD step heading "
        "'About You'. This is the #1552 user-visible symptom: "
        "{% if %} + {% include %} branch swap inserts new subtree but "
        "does not remove the old one. " + f"h2={h2!r} | patches={json.dumps(patches, indent=2)}"
    )


# --------------------------------------------------------------------------
# Diagnostic tests — surface WHICH patches the differ produced so the
# Stage 5 trace has data to work with. Always emit, never assert.
# --------------------------------------------------------------------------


def _patch_op_counts(patches: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in patches:
        op = p.get("type", "<unknown>")
        counts[op] = counts.get(op, 0) + 1
    return counts


def test_diagnostic_patch_op_summary_1552_include_swap():
    """Diagnostic — emits the patch-op histogram for the include-swap
    so a Stage 5 tracer can see at a glance what the differ produced.
    Always passes. Reading the captured output is the point."""
    _h1, _h2, patches = _run_cross_thread_include_render(
        _PARENT_TEMPLATE_WITH_INCLUDE,
        includes={
            "step_claimant.html": _STEP_CLAIMANT_HTML,
            "step_vehicle.html": _STEP_VEHICLE_HTML,
        },
        state_1={"step": "claimant"},
        state_2={"step": "vehicle"},
    )
    counts = _patch_op_counts(patches)
    print(f"\n[1552 include-swap] patch op counts: {counts}")
    print(f"[1552 include-swap] full patches:\n{json.dumps(patches, indent=2)}")
    # Always pass — informational only.
    assert True


# --------------------------------------------------------------------------
# Bit-exact reporter shape variant — adds {% extends %} + {% block %} on top
# of {% if/elif %} + {% include %}. The reporter's wizard base template
# inherits from a layout template and emits the if/elif INSIDE a block.
# Three dimensions, not two.
# --------------------------------------------------------------------------

_LAYOUT_TEMPLATE = """<html>
<body>
  <div class="layout-shell">
    {% block wizard_step_content %}{% endblock %}
  </div>
</body>
</html>"""

_WIZARD_CHILD_TEMPLATE = """{% extends "layout.html" %}
{% block wizard_step_content %}
<div class="wizard-content">
  {% if step == "claimant" %}
    {% include "step_claimant.html" %}
  {% elif step == "vehicle" %}
    {% include "step_vehicle.html" %}
  {% endif %}
</div>
{% endblock %}"""


def test_framework_include_swap_with_extends_and_block_1552():
    """Framework-level invariant pin (does NOT reproduce reporter's bug):
    full template-inheritance shape works correctly.

    {% extends %} + {% block %} + {% if/elif %} + {% include %}. PASSES
    on main. Companion to test_framework_include_swap_emits_correct_remove_then_insert_1552
    covering the additional inheritance dimension the reporter's wizard
    templates use.
    """
    with tempfile.TemporaryDirectory() as td:
        tdpath = Path(td)
        (tdpath / "layout.html").write_text(_LAYOUT_TEMPLATE, encoding="utf-8")
        (tdpath / "step_claimant.html").write_text(_STEP_CLAIMANT_HTML, encoding="utf-8")
        (tdpath / "step_vehicle.html").write_text(_STEP_VEHICLE_HTML, encoding="utf-8")

        q_serialized: "queue.Queue[bytes]" = queue.Queue()
        q_h1: "queue.Queue[str]" = queue.Queue()
        q_result: "queue.Queue[tuple[str, str]]" = queue.Queue()

        def producer():
            v = RustLiveView(_WIZARD_CHILD_TEMPLATE)
            v.set_template_dirs([str(tdpath)])
            v.update_state({"step": "claimant"})
            h1, _p, _v = v.render_with_diff()
            q_h1.put(h1)
            q_serialized.put(v.serialize_msgpack())

        def consumer():
            serialized = q_serialized.get()
            restored = RustLiveView.deserialize_msgpack(serialized)
            restored.set_template_dirs([str(tdpath)])
            restored.update_state({"step": "vehicle"})
            h2, p2, _v = restored.render_with_diff()
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

        # Pre-condition: step-1 render must contain claimant content.
        assert "About You" in h1, f"Pre-condition failed: h1={h1!r}"

        # Symptom assertion — same as the include-swap reproducer but
        # with the extends/block layer added.
        assert "What Happened" in h2, (
            "Post-swap render missing NEW step heading. "
            + f"h2={h2!r} | patches={json.dumps(patches, indent=2)}"
        )
        assert "About You" not in h2, (
            "BUG REPRODUCED — post-swap render still contains OLD step heading "
            "with extends+block+if/elif+include shape. "
            + f"h2={h2!r} | patches={json.dumps(patches, indent=2)}"
        )
