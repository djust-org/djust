#!/usr/bin/env python3
"""Verify Makefile, pyproject.toml, and on-disk test directories agree.

Three-way consistency check:
1. Every on-disk test directory must be collected by the Makefile's
   ``test-python`` target (the original direction; catches Makefile-misses-paths).
2. Every ``[tool.pytest.ini_options].testpaths`` entry in ``pyproject.toml``
   must be covered by some Makefile path (prefix-match allowed — a Makefile
   ``tests/`` covers ``tests/unit``).
3. Every Makefile path must correspond to (or be a parent of) some pyproject
   ``testpaths`` entry (catches "removed from pyproject but still in Makefile").

Exit 0: all three directions agree. Exit 1: any direction fails.
"""

import re
import sys
from pathlib import Path

# tomllib is stdlib in 3.11+; fall back to tomli for 3.10
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        raise ImportError(
            "Python 3.10 requires the 'tomli' package for TOML parsing. "
            "Install it with: pip install tomli"
        )

REPO = Path(__file__).resolve().parent.parent
MAKEFILE = REPO / "Makefile"
PYPROJECT = REPO / "pyproject.toml"


def parse_makefile_dirs() -> set[str]:
    """Parse the test-python target in the Makefile for test directories."""
    text = MAKEFILE.read_text()
    # Capture the pytest line within the test-python target
    m = re.search(r"^test-python:.*\n(?:\s+@.*\n)*\s+@.*pytest\s+(.+)$", text, re.MULTILINE)
    if not m:
        print("ERROR: Could not parse test-python target from Makefile")
        sys.exit(2)
    return {d.rstrip("/") for d in m.group(1).split() if "/" in d}


def parse_pyproject_testpaths() -> set[str]:
    """Parse [tool.pytest.ini_options].testpaths from pyproject.toml."""
    cfg = tomllib.loads(PYPROJECT.read_text())
    try:
        paths = cfg["tool"]["pytest"]["ini_options"]["testpaths"]
    except KeyError:
        print("ERROR: Could not find [tool.pytest.ini_options].testpaths in pyproject.toml")
        sys.exit(2)
    return {p.rstrip("/") for p in paths}


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


def _covered_by(path: str, parents: set[str]) -> bool:
    """True iff path equals some parent in parents, or has a parent prefix in parents."""
    return any(path == p or path.startswith(p + "/") for p in parents)


def main() -> int:
    makefile_dirs = parse_makefile_dirs()
    pyproject_dirs = parse_pyproject_testpaths()
    test_dirs = find_test_dirs()

    failed = False

    # Direction 1: every on-disk test directory must be collected by Makefile.
    missing_from_makefile = sorted(td for td in test_dirs if not _covered_by(td, makefile_dirs))
    if missing_from_makefile:
        failed = True
        print("Test directories NOT collected by Makefile test-python target:")
        for td in missing_from_makefile:
            print(f"  {td}")
        print()
        print("FAIL: Some test directories are not collected by CI.")
        print("Add missing directories to the test-python target in the Makefile.")
        print()

    # Direction 2: every pyproject testpath must be covered by Makefile.
    pyproject_not_in_makefile = sorted(
        pp for pp in pyproject_dirs if not _covered_by(pp, makefile_dirs)
    )
    if pyproject_not_in_makefile:
        failed = True
        print("pyproject.toml testpaths NOT covered by Makefile test-python target:")
        for pp in pyproject_not_in_makefile:
            print(f"  {pp}")
        print()
        print("FAIL: pyproject.toml lists testpaths that the Makefile does not collect.")
        print(
            "Either add them to the Makefile's test-python target, or remove them from pyproject.toml."
        )
        print()

    # Direction 3: every Makefile path must correspond to (or parent) a pyproject testpath.
    makefile_not_in_pyproject = sorted(
        md
        for md in makefile_dirs
        if not any(pp == md or pp.startswith(md + "/") for pp in pyproject_dirs)
    )
    if makefile_not_in_pyproject:
        failed = True
        print("Makefile test-python paths NOT referenced by pyproject.toml testpaths:")
        for md in makefile_not_in_pyproject:
            print(f"  {md}")
        print()
        print("FAIL: Makefile collects paths that pyproject.toml does not list.")
        print(
            "Either add them to [tool.pytest.ini_options].testpaths, or remove them from the Makefile."
        )
        print()

    if failed:
        return 1

    print("All test directories agree across Makefile, pyproject.toml, and on-disk layout.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
