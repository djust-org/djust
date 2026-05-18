#!/usr/bin/env python3
"""
Doc-snippet smoke test + mechanically-derivable claim assertions — #1500.

Two checks, run together, in one self-contained script (no network, no git,
no gh — CI-fast and deterministic, matching check-adr-status.py / docs-lint.py).

Part (a) — Fenced Python snippet AST/import smoke check
    Every ```python fenced block in README.md and QUICKSTART.md is parsed.
    - `ast.parse` failure → FAIL (a doc snippet that is not valid Python).
    - A block is a "complete module" if it has at least one TOP-LEVEL
      import statement AND at least one TOP-LEVEL `class`/`def`. Otherwise
      it is a "fragment".
    - Fragment   → AST-parse only (no name resolution; fragments legitimately
      reference undefined names like `Product.objects.filter(...)`).
    - Module     → additionally import-resolve every imported module and
      symbol. `importlib.import_module(X)` for each `import X`, plus a
      `getattr` for each `from X import a, b`. Run under the project's test
      settings (`DJANGO_SETTINGS_MODULE=tests.settings`, `django.setup()`)
      so `from djust import LiveView` resolves. This catches phantom imports
      and renamed/removed public symbols.

    Honest scope: AST + import-resolution does NOT execute snippets, so it
    cannot catch a phantom *method call* (e.g. `View.as_live_view()`). That
    is deferred to part (c) — see #1500 follow-up.

    Escape hatch: a `<!-- doc-snippet-check: skip -->` HTML comment on the
    line immediately before a ```python fence skips that block.

Part (b) — Mechanically-derivable claim assertions
    1. Django floor: the `Django>=X.Y...` line in pyproject.toml is the
       source of truth. Every `django-{ver}+` badge and `Django {ver}+`
       prose claim in README.md/QUICKSTART.md must state that same
       `major.minor`. Mismatch → FAIL.
    2. JS bundle size: `python/djust/static/djust/client.min.js.gz` size
       (bytes / 1024 = KB) is the source of truth. Every `~NN KB` gzipped
       client-size claim in README.md must fall within a +/-3 KB tolerance
       band of the measured value (the `~` is a rounded claim; the band
       lets `~53` match a measured 51.6 while still catching a regression
       to 29 KB or a bloat to 60 KB). If the bundle file is absent (a
       fresh checkout before build-client.sh ran), the size sub-check
       SKIPS with a warning — exit stays 0 for that sub-check.

Usage:
    python3 scripts/check-doc-snippets.py
    python3 scripts/check-doc-snippets.py --readme README.md --quickstart QUICKSTART.md
    python3 scripts/check-doc-snippets.py --pyproject pyproject.toml --bundle path/to/client.min.js.gz
    make check-doc-snippets

Exit code:
    0 — no drift (snippets parse/resolve; claims match; size warnings allowed)
    1 — drift found (>=1 bad snippet, version mismatch, or out-of-band size)
    2 — usage error (an explicitly-passed input file does not exist)
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# JS bundle-size tolerance band, in KB. The README states a `~`-rounded
# value; the band lets the rounded claim pass while still catching a real
# regression. If the bundle legitimately grows past the band, the README
# claim AND this constant must be updated together.
_SIZE_TOLERANCE_KB = 3.0

# HTML-comment escape hatch: when this exact marker is on the line directly
# before a ```python fence, that block is skipped.
_SKIP_MARKER = "<!-- doc-snippet-check: skip -->"

# Matches the `Django>=X.Y` floor in pyproject.toml's dependency list.
_DJANGO_FLOOR_RE = re.compile(r'"Django>=(\d+\.\d+)')

# Matches a `django-X.Y+` shields.io badge slug.
_DJANGO_BADGE_RE = re.compile(r"django-(\d+\.\d+)\+")

# Matches a `Django X.Y+` prose claim.
_DJANGO_PROSE_RE = re.compile(r"Django (\d+\.\d+)\+")

# Matches a `~NN KB` gzipped-client size claim. The `gz` / `gzip` context
# keyword must appear within the same line so we don't match unrelated KB
# numbers.
_SIZE_CLAIM_RE = re.compile(r"~\s*(\d+(?:\.\d+)?)\s*KB")


def extract_python_blocks(path: Path) -> list[tuple[int, str]]:
    """Extract every ```python fenced block from a markdown file.

    Returns a list of (start_line, code) tuples. `start_line` is the
    1-based line number of the opening fence. Blocks immediately preceded
    by the `<!-- doc-snippet-check: skip -->` marker line are omitted.
    """
    text = path.read_text()
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "```python":
            fence_lineno = i + 1  # 1-based
            # Skip-marker check: the line immediately before the fence.
            prev = lines[i - 1].strip() if i > 0 else ""
            body: list[str] = []
            j = i + 1
            while j < n and lines[j].strip() != "```":
                body.append(lines[j])
                j += 1
            if prev != _SKIP_MARKER:
                blocks.append((fence_lineno, "\n".join(body)))
            i = j + 1
        else:
            i += 1
    return blocks


def _imports_from_tree(tree: ast.Module) -> list[tuple[str, list[str]]]:
    """Collect TOP-LEVEL imports from a parsed module.

    Returns a list of (module_name, symbols) tuples. For `import X` /
    `import X.Y`, symbols is empty. For `from X import a, b`, symbols is
    ['a', 'b']. Relative imports (`from . import x`) are skipped — they
    cannot be resolved out of a package context.
    """
    imports: list[tuple[str, list[str]]] = []
    for node in tree.body:  # TOP-LEVEL only
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or not node.module:
                continue  # relative import — unresolvable standalone
            symbols = [a.name for a in node.names if a.name != "*"]
            imports.append((node.module, symbols))
    return imports


def _is_module(tree: ast.Module) -> bool:
    """A block is a 'complete module' if it has >=1 top-level import AND
    >=1 top-level class/def. Otherwise it is a 'fragment'."""
    has_import = any(
        isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body
    )
    has_def = any(
        isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        for n in tree.body
    )
    return has_import and has_def


def _resolve_imports(imports: list[tuple[str, list[str]]]) -> list[str]:
    """Attempt to import each module + getattr each symbol.

    Returns a list of human-readable error strings (empty if all resolve).
    """
    import importlib

    errors: list[str] = []
    for module_name, symbols in imports:
        try:
            mod = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 — surface every failure
            errors.append(
                f"unresolvable import `{module_name}` ({type(exc).__name__})"
            )
            continue
        for sym in symbols:
            if not hasattr(mod, sym):
                errors.append(
                    f"`{module_name}` has no attribute `{sym}` "
                    f"(renamed or removed public symbol?)"
                )
    return errors


def _setup_django() -> None:
    """Configure Django so `from djust import ...` resolves.

    Best-effort: a missing settings module degrades gracefully (the
    import-resolution sub-check will then fail loudly on djust imports,
    which is the correct signal).
    """
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.settings")
    try:
        import django

        django.setup()
    except Exception:  # noqa: BLE001 — degrade gracefully
        pass


def check_snippets(readme: Path, quickstart: Path) -> list[str]:
    """Part (a): AST/import smoke-check every ```python block.

    Returns a list of error strings (empty if all snippets are clean).
    """
    errors: list[str] = []
    for doc in (readme, quickstart):
        for start_line, code in extract_python_blocks(doc):
            loc = f"{doc.name}:{start_line}"
            try:
                tree = ast.parse(code)
            except SyntaxError as exc:
                errors.append(f"{loc} — syntax error: {exc.msg}")
                continue
            if _is_module(tree):
                imports = _imports_from_tree(tree)
                for err in _resolve_imports(imports):
                    errors.append(f"{loc} — {err}")
    return errors


def check_django_floor(
    pyproject: Path, readme: Path, quickstart: Path
) -> list[str]:
    """Part (b.1): every stated Django version must match the pyproject floor.

    Returns a list of error strings (empty if all claims match).
    """
    errors: list[str] = []
    pp_text = pyproject.read_text()
    m = _DJANGO_FLOOR_RE.search(pp_text)
    if not m:
        errors.append(
            f"{pyproject.name} — could not find a `Django>=X.Y` "
            f"dependency line to derive the version floor from"
        )
        return errors
    floor = m.group(1)  # e.g. "4.2"

    for doc in (readme, quickstart):
        text = doc.read_text()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for claim_re, label in (
                (_DJANGO_BADGE_RE, "badge"),
                (_DJANGO_PROSE_RE, "prose"),
            ):
                for cm in claim_re.finditer(line):
                    stated = cm.group(1)
                    if stated != floor:
                        errors.append(
                            f"{doc.name}:{lineno} — Django {label} claims "
                            f"`{stated}+` but pyproject floor is `{floor}` "
                            f"(`Django>={floor}`)"
                        )
    return errors


def check_js_size(bundle: Path, readme: Path) -> tuple[list[str], list[str]]:
    """Part (b.2): every `~NN KB` client-size claim must be within band.

    Returns (errors, warnings). A missing bundle file yields a warning
    (sub-check skipped), not an error.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not bundle.is_file():
        warnings.append(
            f"bundle {bundle} not found — JS size check skipped "
            f"(run scripts/build-client.sh to generate it)"
        )
        return errors, warnings

    measured_kb = bundle.stat().st_size / 1024
    low = measured_kb - _SIZE_TOLERANCE_KB
    high = measured_kb + _SIZE_TOLERANCE_KB

    text = readme.read_text()
    for lineno, line in enumerate(text.splitlines(), start=1):
        lower = line.lower()
        if "gz" not in lower and "gzip" not in lower:
            continue  # only size claims qualified with a gzip context
        for cm in _SIZE_CLAIM_RE.finditer(line):
            stated = float(cm.group(1))
            if not (low <= stated <= high):
                errors.append(
                    f"{readme.name}:{lineno} — client-size claim "
                    f"`~{cm.group(1)} KB` is outside the tolerance band "
                    f"[{low:.1f}, {high:.1f}] KB "
                    f"(measured {measured_kb:.1f} KB, "
                    f"+/-{_SIZE_TOLERANCE_KB:.0f} KB)"
                )
    return errors, warnings


def run(
    readme: Path, quickstart: Path, pyproject: Path, bundle: Path
) -> tuple[int, str]:
    """Core logic exposed for testing.

    Runs part (a) + part (b) against the given paths. Returns
    (exit_code, message).
    """
    _setup_django()

    all_errors: list[str] = []
    all_warnings: list[str] = []

    all_errors.extend(check_snippets(readme, quickstart))
    all_errors.extend(check_django_floor(pyproject, readme, quickstart))
    size_errors, size_warnings = check_js_size(bundle, readme)
    all_errors.extend(size_errors)
    all_warnings.extend(size_warnings)

    lines: list[str] = []
    for w in all_warnings:
        lines.append(f"WARNING: {w}")

    if all_errors:
        lines.append(
            f"Found {len(all_errors)} doc-snippet/claim issue(s):"
        )
        for e in all_errors:
            lines.append(f"  {e}")
        return 1, "\n".join(lines)

    lines.append(
        "OK — doc snippets parse/resolve and version/size claims match"
        + (f" ({len(all_warnings)} warning(s))" if all_warnings else "")
    )
    return 0, "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument(
        "--readme",
        default=None,
        help="Path to README.md (default: <repo>/README.md)",
    )
    p.add_argument(
        "--quickstart",
        default=None,
        help="Path to QUICKSTART.md (default: <repo>/QUICKSTART.md)",
    )
    p.add_argument(
        "--pyproject",
        default=None,
        help="Path to pyproject.toml (default: <repo>/pyproject.toml)",
    )
    p.add_argument(
        "--bundle",
        default=None,
        help=(
            "Path to the gzipped minified client bundle "
            "(default: <repo>/python/djust/static/djust/client.min.js.gz)"
        ),
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Currently a no-op; reserved for parity with other linters",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    readme = Path(args.readme) if args.readme else (ROOT / "README.md")
    quickstart = (
        Path(args.quickstart) if args.quickstart else (ROOT / "QUICKSTART.md")
    )
    pyproject = (
        Path(args.pyproject) if args.pyproject else (ROOT / "pyproject.toml")
    )
    bundle = (
        Path(args.bundle)
        if args.bundle
        else (ROOT / "python/djust/static/djust/client.min.js.gz")
    )

    # An explicitly-passed input file that does not exist is a usage error.
    # The bundle is intentionally exempt — its absence is a graceful skip.
    for label, path, was_explicit in (
        ("README", readme, args.readme is not None),
        ("QUICKSTART", quickstart, args.quickstart is not None),
        ("pyproject", pyproject, args.pyproject is not None),
    ):
        if not path.is_file():
            if was_explicit:
                print(f"ERROR: {label} file not found: {path}")
                sys.exit(2)
            # A missing default file is also a usage error — the repo is
            # malformed.
            print(f"ERROR: {label} file not found: {path}")
            sys.exit(2)

    exit_code, msg = run(readme, quickstart, pyproject, bundle)
    print(msg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
