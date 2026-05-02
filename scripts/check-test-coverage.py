#!/usr/bin/env python3
"""Verify all test directories are collected by the Makefile test-python target.

Exit 0: all directories covered. Exit 1: gap found (missing directory).
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MAKEFILE = REPO / "Makefile"


def parse_makefile_dirs() -> set[str]:
    """Parse the test-python target in the Makefile for test directories."""
    text = MAKEFILE.read_text()
    # Capture the pytest line within the test-python target
    m = re.search(r"^test-python:.*\n(?:\s+@.*\n)*\s+@.*pytest\s+(.+)$", text, re.MULTILINE)
    if not m:
        print("ERROR: Could not parse test-python target from Makefile")
        sys.exit(2)
    return set(d.rstrip("/") for d in m.group(1).split() if "/" in d)


def find_test_dirs() -> set[str]:
    """Find all directories containing test files."""
    test_dirs = set()
    for root, dirs, files in REPO.walk():
        # Skip hidden and venv dirs
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        # Only look in known test locations
        root_str = str(root.relative_to(REPO))
        if not any(
            root_str == prefix or root_str.startswith(prefix + "/")
            for prefix in ("python/tests", "python/djust/tests", "tests")
        ):
            continue
        for f in files:
            if f.endswith(".py") and (f.startswith("test_") or f.endswith("_test.py")):
                rel_dir = str(root.relative_to(REPO))
                test_dirs.add(rel_dir)
                break
    return test_dirs


def main() -> int:
    makefile_dirs = parse_makefile_dirs()
    test_dirs = find_test_dirs()

    missing = []
    for td in sorted(test_dirs):
        covered = any(td == md or td.startswith(md + "/") for md in makefile_dirs)
        if not covered:
            missing.append(td)

    if missing:
        print("Test directories NOT collected by CI:")
        for td in missing:
            print(f"  {td}")
        print()
        print("FAIL: Some test directories are not collected by CI.")
        print("Add missing directories to the test-python target in the Makefile.")
        return 1

    print("All test directories are collected by CI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
