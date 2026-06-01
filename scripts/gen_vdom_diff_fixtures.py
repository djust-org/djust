#!/usr/bin/env python
"""Generate committed JSON fixtures for the client-faithful differential VDOM
harness (``tests/js/vdom_client_faithful_diff.test.js``).

For each scenario this drives a real LiveView through ``LiveViewTestClient``,
capturing — exactly as production would send to the client:

  * ``initial_html``   — the mount render, run through the client-egress strip
                         (``_strip_comments_and_whitespace``: comments removed
                         EXCEPT dj-if markers, whitespace normalized).
  * for each step:      the ``render_with_diff`` patch batch + the fresh full
                         render of the new state (also egress-stripped) as the
                         ``expected_html`` the client DOM must structurally
                         equal after applying the patches.

Output is committed JSON so the JS test has no Python/Rust dependency at run
time. Regeneration is idempotent (deterministic dj-ids); a pre-commit hook
re-runs this and ``git add``s the result so fixtures can't go stale.

Run:  .venv/bin/python scripts/gen_vdom_diff_fixtures.py
"""

import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "examples" / "demo_project"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

import django  # noqa: E402

django.setup()

# Register djust component tag handlers ({% kanban_board %}, {% empty_state %})
# with the Rust engine — the #1678 fixture's active tab body is a nested
# conditional over these component tags (the live djust_pm shape).
from djust.components.rust_handlers import register_with_rust_engine  # noqa: E402

register_with_rust_engine()

from djust.testing import LiveViewTestClient  # noqa: E402

from tests.livefixtures.kanban_tabs_view import KanbanTabsView  # noqa: E402

FIXTURE_DIR = REPO / "tests" / "js" / "fixtures"


def _patch_type_counts(patches):
    out = {}
    for p in patches:
        out[p["type"]] = out.get(p["type"], 0) + 1
    return out


def gen_kanban_tabs_1678():
    """#1678: 8 sibling {% if active_tab==N %} blocks; mount tab0, switch to the
    kanban tab (tab 3) — which emits MoveSubtree for the shifted later blocks —
    then move a card across columns (the positional count-badge SetText)."""
    c = LiveViewTestClient(KanbanTabsView)
    c.mount(active_tab=0)
    egress = c.view_instance._strip_comments_and_whitespace

    initial_html, _, _ = c.render_with_patches()  # establishes diff baseline (tab0)
    initial_html = egress(initial_html)

    steps = []

    c.send_event("switch_tab", tab=3)
    html, patches, _ = c.render_with_patches()
    steps.append(
        {"label": "switch tab0 -> tab3 (kanban)", "patches": patches, "expected_html": egress(html)}
    )

    c.send_event("move_card", card_id="c1", to_column="done", to_index=0)
    html, patches, _ = c.render_with_patches()
    steps.append(
        {
            "label": "move card c1 todo -> done",
            "patches": patches,
            "expected_html": egress(html),
        }
    )

    return {
        "scenario": "kanban_tabs_1678",
        "description": (
            "8-tab dashboard, tab 3 keyed kanban. Mount tab0 -> switch to tab3 "
            "(MoveSubtree for shifted blocks) -> cross-column card move."
        ),
        "root_selector": ".tabs-content",
        "initial_html": initial_html,
        "steps": steps,
    }


SCENARIOS = {
    "vdom_diff_kanban_tabs_1678.json": gen_kanban_tabs_1678,
}


def main():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for filename, gen in SCENARIOS.items():
        fixture = gen()
        path = FIXTURE_DIR / filename
        path.write_text(json.dumps(fixture, indent=2) + "\n")
        print(f"wrote {path.relative_to(REPO)}")
        for i, step in enumerate(fixture["steps"], 1):
            print(f"  step {i} [{step['label']}]: {_patch_type_counts(step['patches'])}")


if __name__ == "__main__":
    main()
