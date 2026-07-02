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
