#!/usr/bin/env python3
"""Mutate the parity ratchet, and the code it measures; each mutation must turn the test red (#3663).

A ratchet that survives a mutation of the thing it guards is a decoration. Each
entry below breaks one guarantee; the test file is run against it, and a
mutation that leaves the file GREEN is reported and fails this script. Rules
(the same as ``doc-examples-gateoff.py``): the mutation text must be found and
must change the source; HUNG and ERROR are reported as themselves and are not
a pass; ``__pycache__`` is cleared before every run so a stale ``.pyc`` cannot
certify a mutation that never ran; the original is restored with ``write_text``
(never ``copy2``, which keeps the mtime and lets a build reuse the mutant), even
on failure.

Run: ``python scripts/template-library-parity-gateoff.py [--list]``. About ten
seconds per mutation. ``python/tests/test_template_library_parity_3663.py``
pins that every mutation text below still exists, so the list cannot rot.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts/lib/template_library_parity"
HANDLERS = ROOT / "python/djust/components/rust_handlers.py"
TAGS = ROOT / "python/djust/components/templatetags/djust_components.py"
TEST = "python/tests/test_template_library_parity_3663.py"

MUTATIONS: list[tuple[str, pathlib.Path, str, str]] = [
    # --- the ratchet's own logic
    (
        "compare: an improvement no longer fails",
        PKG / "classify.py",
        "    return Finding(\n        tag,\n        BETTER,",
        "    return None if True else Finding(\n        tag,\n        BETTER,",
    ),
    (
        "compare: a regression no longer fails",
        PKG / "classify.py",
        "    if worse:\n        return Finding(",
        "    if worse and False:\n        return Finding(",
    ),
    (
        "compare: a tag with no baseline row is ignored",
        PKG / "classify.py",
        "            if not partial:\n                findings.append(\n                    Finding(\n"
        "                        tag,\n                        NEW,",
        "            if False:\n                findings.append(\n                    Finding(\n"
        "                        tag,\n                        NEW,",
    ),
    (
        "compare: a row for a vanished tag is ignored",
        PKG / "classify.py",
        "    if not partial:\n        for tag in sorted(set(baseline) - set(now)):",
        "    if False:\n        for tag in sorted(set(baseline) - set(now)):",
    ),
    (
        "classify_variant: DIFFERENT reported IDENTICAL",
        PKG / "classify.py",
        '        return DIFFERENT\n    if py_status == "err"',
        '        return IDENTICAL\n    if py_status == "err"',
    ),
    (
        "classify_tag: a tag the harness barely exercised is called IDENTICAL",
        PKG / "classify.py",
        "    unexercised = own_output == 0 or bool(readable and passed < MIN_EXERCISED * readable)",
        "    unexercised = False",
    ),
    # --- the harness
    (
        "harness: the planted difference is a no-op",
        PKG / "harness.py",
        "        node.render = lambda context: inner(context) + PLANT_MARKER",
        "        node.render = lambda context: inner(context)",
    ),
    (
        "harness: the process-global tooltip counter is not reset",
        PKG / "harness.py",
        "    djust_components._tooltip_id_counter = itertools.count(1)\n",
        "    pass\n",
    ),
    (
        "harness: uuids left real",
        PKG / "harness.py",
        "    uuid.uuid4 = fake_uuid4  # type: ignore[assignment]",
        "    pass",
    ),
    (
        "harness: the clock is not frozen",
        PKG / "harness.py",
        "    datetime.date = FrozenDate  # type: ignore[misc]",
        "    pass",
    ),
    (
        "harness: the Rust engine is silently replaced by Django",
        PKG / "harness.py",
        '    rust_engine = python_engine if self_compare else engines["djust"]',
        "    rust_engine = python_engine",
    ),
    (
        "harness: discovery stops at the entry point (does not follow render_* helpers)",
        PKG / "harness.py",
        "            queue.extend((c, depth + 1) for c in _callees(node, obj))",
        "            pass",
    ),
    (
        "variants: no per-parameter safe/evil calls",
        PKG / "variants.py",
        '    for mode in ("safe", "evil"):\n        for n in names:',
        "    for mode in ():\n        for n in names:",
    ),
    (
        "variants: a child tag is rendered inside a CLOSED parent",
        PKG / "variants.py",
        '    parent_args = "open=True" if parent_info and "open" in parent_info.readable() else ""',
        '    parent_args = ""',
    ),
    # --- the code under measurement: each must move the table
    (
        "native KbdHandler renders an extra span",
        HANDLERS,
        "        return str(_k(*keys))",
        "        return str(_k(*keys)) + '<span>extra</span>'",
    ),
    (
        "Python audit_log renders an extra span",
        TAGS,
        "            f'<table class=\"dj-audit-log__table\">{thead}{tbody}</table></div>'",
        "            f'<table class=\"dj-audit-log__table\">{thead}{tbody}</table><span>x</span></div>'",
    ),
    (
        "native ConfirmDialogHandler drops message, title and confirm_label",
        HANDLERS,
        "        return str(render_confirm_dialog(kw, body))",
        "        drop = ('message', 'title', 'confirm_label')\n"
        "        return str(render_confirm_dialog({k: v for k, v in kw.items() if k not in drop}, body))",
    ),
    (
        "native DropdownMenuHandler drops label and align",
        HANDLERS,
        "        return str(render_dropdown_menu(_parse_args(args, context), content))",
        "        kw = {k: v for k, v in _parse_args(args, context).items() if k not in ('label', 'align')}\n"
        "        return str(render_dropdown_menu(kw, content))",
    ),
    (
        "native MenuItemHandler loses its safe wrapper (markup is escaped a second time)",
        HANDLERS,
        "        return _safe(render_menu_item(_parse_args(args, context)))",
        "        return render_menu_item(_parse_args(args, context))",
    ),
    (
        "Python kbd escapes a mark_safe key",
        TAGS,
        "    parts = [f'<kbd class=\"kbd\">{conditional_escape(k)}</kbd>' for k in keys]",
        "    from django.utils.html import escape\n"
        "    parts = [f'<kbd class=\"kbd\">{escape(k)}</kbd>' for k in keys]",
    ),
    (
        "native CardHandler gains a role attribute (a new aspect inside an already DIFFERENT tag)",
        HANDLERS,
        "            f'<div class=\"card card-{variant} {extra_class}\">'",
        '            f\'<div role="region" class="card card-{variant} {extra_class}">\'',
    ),
]


def clear_pycache() -> None:
    for top in (ROOT / "scripts", ROOT / "python/djust/components", ROOT / "python/tests"):
        for cache in top.rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def run_tests() -> tuple[str, str]:
    """``(outcome, first failing test)``; outcome is GREEN, RED, HUNG or ERROR."""
    clear_pycache()
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", TEST],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired:
        return "HUNG", ""
    out = done.stdout + done.stderr
    first = next((ln for ln in out.splitlines() if ln.startswith(("FAILED", "ERROR "))), "")
    # Only pytest's own summary line decides: test output can say "error" too.
    summary = next(
        (ln for ln in reversed(out.splitlines()) if re.search(r"\d+ (passed|failed|error)", ln)), ""
    )
    if done.returncode == 0 and "passed" in summary and "failed" not in summary:
        return "GREEN", ""
    if "failed" in summary and not re.search(r"\d+ error", summary):
        return "RED", first
    return "ERROR", first  # a collection error or a crash is not evidence of anything


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for label, path, _old, _new in MUTATIONS:
            print("%-80s %s" % (label, path.relative_to(ROOT)))
        return 0
    control, _ = run_tests()
    print("control (unmutated): %s" % control)
    if control != "GREEN":
        return 2
    survivors = 0
    for label, path, old, new in MUTATIONS:
        original = path.read_text(encoding="utf-8")
        assert old in original, "MUTATION TEXT NOT FOUND for %s" % label
        mutated = original.replace(old, new, 1)
        assert mutated != original, "NO-OP MUTATION for %s" % label
        try:
            path.write_text(mutated, encoding="utf-8")
            outcome, first = run_tests()
        finally:
            path.write_text(original, encoding="utf-8")
        assert path.read_text(encoding="utf-8") == original
        survivors += outcome != "RED"
        print("%-6s %s\n         %s" % (outcome, label, first.replace(TEST + "::", "")[:150]))
    control, _ = run_tests()
    print("control (restored): %s" % control)
    print("%d of %d mutations turned the test red" % (len(MUTATIONS) - survivors, len(MUTATIONS)))
    return 1 if survivors or control != "GREEN" else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
