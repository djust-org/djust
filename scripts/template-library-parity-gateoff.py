#!/usr/bin/env python3
"""Mutate the parity ratchet, and the code it measures; each mutation must turn the test red (#3663).

A ratchet that survives a mutation of the thing it guards is a decoration. Each
entry below breaks one guarantee; the test file is run against it, and a
mutation that leaves the file GREEN is reported and fails this script.

**The mutations are applied to a throwaway copy of the tree, never to this
checkout.** (The first version edited the real files in place: anyone reading
the tree meanwhile measured a mutant, and a SIGTERM mid-run left one behind.)
The copy holds what the test needs (the package, the tests' conftests, the
demo project, the script and the baseline); the compiled extension is
symlinked, not copied. It is deleted in a ``finally``, and SIGTERM / SIGINT /
SIGHUP are turned into ``SystemExit`` so the ``finally`` runs. The real tree is
never written, so there is nothing to restore; ``--selftest`` and
``TestGateOffs`` prove that by hashing it before, during and after a mutation
that is killed.

Rules (the same as ``doc-examples-gateoff.py``): the mutation text must be found
and must change the source; HUNG and ERROR are reported as themselves and are
not a pass; the copy starts without ``__pycache__`` so a stale ``.pyc`` cannot
certify a mutation that never ran. ``test_every_source_mutation_of_the_gateoff_
script_still_applies`` is deselected from the runs (it fails under ANY mutation,
which would otherwise look like a catch).

Run: ``python scripts/template-library-parity-gateoff.py [--list] [--only TEXT]
[--no-control]``. About ten seconds per mutation.
``python/tests/test_template_library_parity_3663.py`` pins that every mutation
text below still exists, so the list cannot rot.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager

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


#: What the test needs from the tree, relative to the root.
_COPY_TREES = ("python/djust", "examples/demo_project", "scripts/lib/template_library_parity")
_COPY_FILES = (
    "pyproject.toml",
    "conftest.py",
    "python/tests/conftest.py",
    "python/tests/differential_corpus_2723.py",
    TEST,
    "scripts/template-library-parity.py",
    "scripts/template-library-parity-baseline.csv",
    "scripts/template-library-parity-gateoff.py",
)
_SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "_rust*.so", "*.sqlite3")
_DESELECT = "python/tests/test_template_library_parity_3663.py::TestGateOffs::test_every_source_mutation_of_the_gateoff_script_still_applies"


def tree_digest(paths: list[pathlib.Path]) -> str:
    """One hash over the bytes of ``paths`` (the files a mutation touches)."""
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        digest.update(str(path).encode() + b"\0" + path.read_bytes())
    return digest.hexdigest()


def make_copy(work: pathlib.Path) -> None:
    for tree in _COPY_TREES:
        src = ROOT / tree
        dst = work / tree
        if tree == "python/djust":  # the package's own tests are not needed and are large
            shutil.copytree(src, dst, ignore=_SKIP)
        else:
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for ext in src.glob("_rust*.so"):  # symlink: a second copy of a mapped .so is a hazard
            (dst / ext.name).symlink_to(ext)
    for rel in _COPY_FILES:
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, work / rel)
    # ``tests/`` helpers the root conftest imports (not the test files themselves)
    (work / "tests").mkdir()
    for helper in (ROOT / "tests").glob("*.py"):
        if not helper.name.startswith("test_"):
            shutil.copyfile(helper, work / "tests" / helper.name)


@contextmanager
def workdir() -> Iterator[pathlib.Path]:
    """A throwaway copy of the tree; deleted on exit, including on SIGTERM / SIGINT / SIGHUP."""

    def die(signum: int, _frame: object) -> None:
        raise SystemExit(128 + signum)

    previous = {s: signal.signal(s, die) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    work = pathlib.Path(tempfile.mkdtemp(prefix="parity-gateoff-")).resolve()
    try:
        make_copy(work)
        yield work
    finally:
        shutil.rmtree(work, ignore_errors=True)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def run_tests(work: pathlib.Path) -> tuple[str, str]:
    """``(outcome, first failing test)``; outcome is GREEN, RED, HUNG or ERROR."""
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(work / "python"), str(work)]),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("DJANGO_SETTINGS_MODULE", None)
    try:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-q",
                "-x",
                "-p",
                "no:cacheprovider",
                "--deselect",
                _DESELECT,
                TEST,
            ],
            cwd=work,
            env=env,
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
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    chosen = [m for m in MUTATIONS if only is None or only in m[0]]
    if not chosen:
        print("no mutation matches %r" % only)
        return 2
    real = [m[1] for m in MUTATIONS]
    before = tree_digest(real)
    survivors = 0
    with workdir() as work:
        print("workdir: %s" % work, flush=True)
        if "--no-control" not in argv:
            control, _ = run_tests(work)
            print("control (unmutated): %s" % control, flush=True)
            if control != "GREEN":
                return 2
        for label, path, old, new in chosen:
            target = work / path.relative_to(ROOT)
            original = target.read_text(encoding="utf-8")
            assert old in original, "MUTATION TEXT NOT FOUND for %s" % label
            mutated = original.replace(old, new, 1)
            assert mutated != original, "NO-OP MUTATION for %s" % label
            target.write_text(mutated, encoding="utf-8")
            print("mutating: %s" % label, flush=True)
            outcome, first = run_tests(work)
            target.write_text(original, encoding="utf-8")
            survivors += outcome != "RED"
            print("%-6s %s\n         %s" % (outcome, label, first.replace(TEST + "::", "")[:150]))
        if "--no-control" not in argv:
            control, _ = run_tests(work)
            print("control (after the last mutation): %s" % control)
            survivors += control != "GREEN"
    assert tree_digest(real) == before, "the real tree changed during the run"
    print("%d of %d mutations turned the test red" % (len(chosen) - survivors, len(chosen)))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
