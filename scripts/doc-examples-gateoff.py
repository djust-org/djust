#!/usr/bin/env python3
"""Revert each harness layer in turn and show a test goes red (ADR-037 D2).

Each mutation must apply and change the source; the suite runs under a
timeout; HUNG and ERROR are reported as themselves, never as a pass; the
original is restored with write_text (never copy2) even on failure.
"""

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "python/djust/tests/_doc_examples.py"
TESTS = [
    "python/djust/tests/test_doc_examples.py",
    "python/djust/tests/test_doc_example_drift.py",
]
MUTATIONS = [
    (
        "extractor",
        "        while k >= 0 and _COMMENT.match(lines[k].strip()):\n",
        "        while False:\n",
    ),
    (
        "pairing",
        'b.language == "html" and b.line > block.line',
        'b.language == "html" and b.line < block.line',
    ),
    (
        "scenario driving",
        "        self.view = self.view_class()\n        response = self.view.post(request)\n",
        "        return 200\n",
    ),
    ("drift", "            if not example.marked:\n", "            if False:\n"),
]


def run():
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", *TESTS],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )


def main():
    original = TARGET.read_text()
    rows = []
    try:
        for label, old, new in MUTATIONS:
            assert old in original, "MUTATION TEXT NOT FOUND for %s" % label
            mutated = original.replace(old, new, 1)
            assert mutated != original, "NO-OP MUTATION for %s" % label
            TARGET.write_text(mutated)
            try:
                result = run()
                tail = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
                if re.search(r"\d+ error", tail) or result.returncode in (2, 3, 4):
                    outcome = "ERROR (the mutation broke collection): " + tail
                elif result.returncode == 0:
                    outcome = "GREEN — NOT CAUGHT: " + tail
                else:
                    outcome = "RED: " + tail
            except subprocess.TimeoutExpired:
                outcome = "HUNG (timeout 900s)"
            finally:
                TARGET.write_text(original)
            rows.append((label, outcome))
    finally:
        TARGET.write_text(original)
    sys.stdout.write("| Layer | Outcome |\n|---|---|\n")
    for label, outcome in rows:
        sys.stdout.write("| %s | %s |\n" % (label, outcome))
    return 0 if all(o.startswith("RED") for _l, o in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
