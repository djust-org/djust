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
time. Regeneration is idempotent (deterministic dj-ids), so the committed
fixtures must match a fresh regen exactly. Freshness is enforced by CI: the
``python-tests`` job in ``.github/workflows/test.yml`` re-runs this generator
and fails on any diff under ``tests/js/fixtures/`` (#1979). When a differ /
template / component / fixture-view change alters the captured patch stream,
regenerate and commit the result — otherwise the guard would keep pinning stale
patch output (as happened before #1979, when this docstring wrongly claimed a
pre-commit hook kept it fresh and none existed).

Run:  make gen-vdom-fixtures      # or: .venv/bin/python scripts/gen_vdom_diff_fixtures.py
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "examples" / "demo_project"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "demo_project.settings")

import django  # noqa: E402

django.setup()

# The LiveViewTestClient mount/event path persists to the session
# (`django_session`), so the generator needs a migrated schema. Point the
# default DB at an isolated throwaway file and migrate it HERE, so the generator
# is self-contained: it works on a fresh CI runner (which has no migrated
# db.sqlite3 — the #1980 CI failure) AND on a fresh dev checkout, WITHOUT
# touching the developer's real demo db.sqlite3. `run_syncdb` is fine for this
# test-only tool (not a deploy path, so the #1637 caveat doesn't apply).
from django.conf import settings  # noqa: E402
from django.core.management import call_command  # noqa: E402
from django.db import connections  # noqa: E402

# mkdtemp gives a secure 0700 dir we own (no filename-generation race), removed on exit.
_TMP_DB_DIR = tempfile.mkdtemp(prefix="djust_vdom_fixtures_")
settings.DATABASES["default"]["NAME"] = os.path.join(_TMP_DB_DIR, "fixtures.sqlite3")
connections.close_all()  # drop any connection bound to the previous NAME
atexit.register(lambda: shutil.rmtree(_TMP_DB_DIR, ignore_errors=True))
call_command("migrate", run_syncdb=True, verbosity=0, interactive=False)

# Register djust component tag handlers ({% kanban_board %}, {% empty_state %})
# with the Rust engine — the #1678 fixture's active tab body is a nested
# conditional over these component tags (the live djust_pm shape).
from djust.components.rust_handlers import register_with_rust_engine  # noqa: E402

register_with_rust_engine()

from djust.testing import LiveViewTestClient  # noqa: E402

from tests.livefixtures.inline_whitespace_view import (  # noqa: E402
    KEYED_LIST_TEMPLATES,
    InlineWhitespaceView,
    keyed_list_view,
)
from tests.livefixtures.kanban_tabs_view import KanbanTabsView  # noqa: E402

FIXTURE_DIR = REPO / "tests" / "js" / "fixtures"


def _patch_type_counts(patches):
    out = {}
    for p in patches:
        out[p["type"]] = out.get(p["type"], 0) + 1
    return out


def gen_kanban_tabs_1678():
    """#1678: 8 sibling {% if active_tab==N %} blocks; mount tab0, switch to the
    kanban tab (tab 3), then move a card across columns.

    The guarded core is the tab switch: it tears down tab0's body and inserts
    tab3's NESTED-conditional kanban body (`RemoveChild` + `InsertSubtree`), the
    marker-preservation + nested-InsertSubtree path that was the actual #1678
    kanban `html_recovery` bug. It no longer emits `MoveSubtree` — since #1826
    (`bd1c1f53`) the move decision keys on RELATIVE (non-boundary-sibling)
    position, and the adjacent tab blocks keep their relative order across a
    switch, so the earlier absolute-offset MoveSubtrees were spurious and are
    correctly gone (regenerated for freshness in #1979).

    Step 2 (cross-column card move) emits a real targeted diff (RemoveChild +
    InsertChild + count-badge SetText): `move_card` does an IMMUTABLE update so
    change detection fires. (#1981 fixed this — an earlier in-place mutation
    captured 0 patches, a vacuous guard.)"""
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
            "(RemoveChild tab0 body + InsertSubtree tab3's nested-conditional "
            "kanban body; no MoveSubtree since #1826) -> cross-column card move."
        ),
        "root_selector": ".tabs-content",
        "initial_html": initial_html,
        "steps": steps,
    }


def gen_inline_whitespace_2999():
    """#2999: the space between two inline siblings is a kept `" "` VDOM node.

    Each step is a real event on ``InlineWhitespaceView`` whose patch batch has
    to land on the right node with those spaces counted: text changes AFTER a
    kept space, inline siblings inserted/removed next to one (unkeyed list),
    a keyed inline list reordered (moves across `" "` separators), an
    ``{% if %}`` between two spaces toggled off and on, and ``|safe`` HTML
    swapped."""
    c = LiveViewTestClient(InlineWhitespaceView)
    c.mount()
    egress = c.view_instance._strip_comments_and_whitespace

    initial_html, _, _ = c.render_with_patches()
    initial_html = egress(initial_html)

    steps = []

    def step(step_label, event, **params):
        c.send_event(event, **params)
        html, patches, _ = c.render_with_patches()
        steps.append({"label": step_label, "patches": patches, "expected_html": egress(html)})

    step("text after a kept space", "set_texts", label="Note:", code="y = 2", tail="later")
    step("insert inline sibling in the middle", "set_tags", tags=["alpha", "NEW", "beta", "gamma"])
    step("append inline sibling", "set_tags", tags=["alpha", "NEW", "beta", "gamma", "delta"])
    step("remove first inline sibling", "set_tags", tags=["NEW", "beta", "gamma", "delta"])
    step("remove down to one", "set_tags", tags=["beta"])
    step("keyed swap", "set_chips", chips=["two", "one", "three", "four"])
    step("keyed rotate", "set_chips", chips=["three", "four", "two", "one"])
    step("keyed reverse", "set_chips", chips=["one", "two", "four", "three"])
    step("keyed insert + remove", "set_chips", chips=["zero", "one", "four", "five"])
    step("if between spaces -> off", "toggle")
    step("if between spaces -> on", "toggle")
    step(
        "swap |safe html",
        "set_md",
        md=(
            "<li><strong>Admin.</strong> <code>DjustModelAdmin</code> <em>new</em></li>\n"
            "<li><strong>Packaging:</strong> <code>collectstatic</code></li>"
        ),
    )

    return {
        "scenario": "inline_whitespace_2999",
        "description": (
            "Spaces between inline siblings (#2999): text after a kept space, "
            "unkeyed inline insert/remove, keyed inline reorder across space "
            "separators, an {% if %} between spaces, |safe HTML swap."
        ),
        "root_selector": ".ws-root",
        "initial_html": initial_html,
        "steps": steps,
    }


def gen_keyed_list_fuzz_2999():
    """#2999: random reorders / inserts / removes of a keyed list, in four
    shapes (block items; inline items separated by " ", by ", ", and by
    nothing). Seeded, so regeneration is deterministic. Each step's real patch
    batch must reproduce a fresh render in jsdom."""
    import random

    rng = random.Random(2999)
    pool = [f"k{i}" for i in range(12)]
    shapes = {}
    for shape in KEYED_LIST_TEMPLATES:
        c = LiveViewTestClient(keyed_list_view(shape))
        c.mount()
        egress = c.view_instance._strip_comments_and_whitespace
        initial_html, _, _ = c.render_with_patches()
        items = ["k0", "k1", "k2", "k3", "k4"]
        steps = []
        for _ in range(40):
            op = rng.choice(["shuffle", "rotate", "swap", "insert", "remove", "mixed"])
            new = list(items)
            if op == "shuffle":
                rng.shuffle(new)
            elif op == "rotate" and new:
                n = rng.randrange(1, len(new) + 1)
                new = new[n:] + new[:n]
            elif op == "swap" and len(new) > 1:
                i, j = rng.sample(range(len(new)), 2)
                new[i], new[j] = new[j], new[i]
            elif op == "insert":
                free = [k for k in pool if k not in new]
                if free:
                    new.insert(rng.randrange(len(new) + 1), rng.choice(free))
            elif op == "remove" and new:
                new.pop(rng.randrange(len(new)))
            else:
                if new:
                    new.pop(rng.randrange(len(new)))
                free = [k for k in pool if k not in new]
                for _k in range(rng.randrange(0, 3)):
                    if free:
                        new.insert(rng.randrange(len(new) + 1), free.pop(rng.randrange(len(free))))
                rng.shuffle(new)
            if not new:
                new = [rng.choice(pool)]
            c.send_event("set_items", items=new)
            html, patches, _ = c.render_with_patches()
            steps.append(
                {
                    "label": f"{op}: {' '.join(items)} -> {' '.join(new)}",
                    "patches": patches,
                    "expected_html": egress(html),
                }
            )
            items = new
        shapes[shape] = {"initial_html": egress(initial_html), "steps": steps}
    return {
        "scenario": "keyed_list_fuzz_2999",
        "description": (
            "Seeded random keyed-list reorders/inserts/removes in four shapes "
            "(block; inline with ' ', ', ', or no separator)."
        ),
        "root_selector": ".ws-root",
        "shapes": shapes,
    }


SCENARIOS = {
    "vdom_diff_kanban_tabs_1678.json": gen_kanban_tabs_1678,
    "vdom_diff_inline_whitespace_2999.json": gen_inline_whitespace_2999,
    "vdom_diff_keyed_list_fuzz_2999.json": gen_keyed_list_fuzz_2999,
}


def main():
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for filename, gen in SCENARIOS.items():
        fixture = gen()
        path = FIXTURE_DIR / filename
        path.write_text(json.dumps(fixture, indent=2) + "\n")
        print(f"wrote {path.relative_to(REPO)}")
        for i, step in enumerate(fixture.get("steps", []), 1):
            print(f"  step {i} [{step['label']}]: {_patch_type_counts(step['patches'])}")
        for shape, sub in fixture.get("shapes", {}).items():
            counts = {}
            for step in sub["steps"]:
                for k, v in _patch_type_counts(step["patches"]).items():
                    counts[k] = counts.get(k, 0) + v
            print(f"  shape {shape}: {len(sub['steps'])} steps, {counts}")


if __name__ == "__main__":
    main()
