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
    cannot catch a phantom *method call* (e.g. `View.as_live_view()`).

    Guides (#1707): every ```python fenced block in `docs/website/guides/*.md`
    is run through part (a) ONLY (AST-parse + import/symbol resolution). This
    is the guard that would have caught #1559/#1699's ~10 hallucinated
    `djust.tenants` symbols. Guides are NOT subject to parts (b) (the
    Django/JS-size claim assertions are README/QUICKSTART-specific) or (c)
    (the security/style lint would false-positive on the legitimate demo
    `print()` calls that pepper the guides — out of #1707 scope). Disable
    with `--no-guides`; point elsewhere with `--guides-dir`.

    Escape hatch: a `<!-- doc-snippet-check: skip -->` HTML comment on the
    line immediately before a ```python fence skips that block from ALL
    checks (parts a + b + c). Use it for intentionally-illustrative guide
    blocks — partial fragments indented under a markdown list item (which
    fail `ast.parse` on the leading whitespace), bare API-doc signatures,
    or imports of external libs / placeholder app names.

Part (c) — Doc-example security/style lint (#1509)
    An AST walker re-encodes djust's auto-reject Security Rules and applies
    them to every fenced Python snippet. Five hard triggers (each → exit 1):
      1. `print(...)`               — CLAUDE.md rule 6 (no print in prod code)
      2. `print(f"...")`            — subset of #1, reported as the f-string form
      3. `mark_safe(f"...{x}...")`  — CLAUDE.md rule 1 (interpolating mark_safe);
         a constant f-string with no interpolation is NOT flagged
      4. bare `except: pass`        — CLAUDE.md rule 5 (also `except E: pass`,
         softer message); a body that does anything but `pass` is fine
      5. `logger.X(f"...")`         — CLAUDE.md rule 4 (f-string logging on a
         `logger`/`logging`/`log` receiver); a constant f-string is not flagged
    Plus one soft advisory (WARNING only, never exit 1): a `@csrf_exempt`
    decorator — legitimate with documented justification, so surfaced for a
    human, never a build failure.

    Escape hatch: a `<!-- doc-snippet-check: anti-pattern -->` HTML comment
    on the line immediately before a ```python fence opts that block out of
    the part-(c) style verdict ONLY — it is still syntax/import-checked by
    part (a). Use it for deliberately-wrong "❌ Wrong" examples.

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

This audit is mechanical and self-contained — it does NOT call git, gh,
or the network (keeps it CI-fast and deterministic).

Usage:
    python3 scripts/check-doc-snippets.py
    python3 scripts/check-doc-snippets.py --readme README.md --quickstart QUICKSTART.md
    python3 scripts/check-doc-snippets.py --pyproject pyproject.toml --bundle path/to/client.min.js.gz
    make check-doc-snippets

Exit code:
    0 — no drift (snippets parse/resolve; claims match; size/style warnings allowed)
    1 — drift found (>=1 bad snippet, version mismatch, out-of-band size,
        or a part-(c) security/style trigger)
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
# before a ```python fence, that block is skipped from ALL checks.
_SKIP_MARKER = "<!-- doc-snippet-check: skip -->"

# HTML-comment escape hatch (part c only): when this exact marker is on the
# line directly before a ```python fence, the block is still syntax/import
# checked (part a) but is opted OUT of the part-(c) security/style verdict.
# Use it for deliberately-wrong "❌ Wrong" anti-pattern examples.
_ANTIPATTERN_MARKER = "<!-- doc-snippet-check: anti-pattern -->"

# Logger-call receiver names. A doc snippet has no logger config to
# introspect, so f-string logging is keyed on these conventional receiver
# names (djust's convention is `logger = logging.getLogger(__name__)`).
_LOGGER_NAMES = frozenset({"logger", "logging", "log"})

# Logging method names that take a message as their first positional arg.
_LOGGER_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)

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


def extract_python_blocks(path: Path) -> list[tuple[int, str, str | None]]:
    """Extract every ```python fenced block from a markdown file.

    Returns a list of (start_line, code, marker) tuples. `start_line` is
    the 1-based line number of the opening fence. `marker` is:
      - ``None``           — no escape-hatch comment precedes the block.
      - ``"anti-pattern"`` — the block is preceded by the
        `<!-- doc-snippet-check: anti-pattern -->` comment; part (c)
        skips it but parts (a)/(b) still process it.
    Blocks immediately preceded by the `<!-- doc-snippet-check: skip -->`
    marker line are omitted entirely.
    """
    text = path.read_text()
    lines = text.splitlines()
    blocks: list[tuple[int, str, str | None]] = []

    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.strip() == "```python":
            fence_lineno = i + 1  # 1-based
            # Escape-hatch check: the line immediately before the fence.
            prev = lines[i - 1].strip() if i > 0 else ""
            body: list[str] = []
            j = i + 1
            while j < n and lines[j].strip() != "```":
                body.append(lines[j])
                j += 1
            if prev == _SKIP_MARKER:
                marker: str | None = None  # dropped below
            elif prev == _ANTIPATTERN_MARKER:
                marker = "anti-pattern"
            else:
                marker = None
            if prev != _SKIP_MARKER:
                blocks.append((fence_lineno, "\n".join(body), marker))
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
            if hasattr(mod, sym):
                continue
            # `from X import Y` resolves Y as an attribute OR a submodule.
            # A genuine submodule (e.g. `from django.db import migrations`)
            # is not an attribute of the parent package until imported, so
            # `hasattr` alone yields a false positive — try importing the
            # dotted submodule before declaring the symbol missing.
            try:
                importlib.import_module(f"{module_name}.{sym}")
            except Exception:  # noqa: BLE001 — not a submodule either → real miss
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


def check_snippets(*docs: Path) -> list[str]:
    """Part (a): AST/import smoke-check every ```python block in `docs`.

    Returns a list of error strings (empty if all snippets are clean).
    Accepts any number of doc paths so the same logic covers
    README/QUICKSTART and the `docs/website/guides/*.md` set (#1707).
    """
    errors: list[str] = []
    for doc in docs:
        for start_line, code, _marker in extract_python_blocks(doc):
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


def collect_guides(guides_dir: Path) -> list[Path]:
    """Return the sorted list of `*.md` guide files under `guides_dir`.

    A missing directory yields an empty list (the guide sub-check then
    no-ops) — callers decide whether that is an error.
    """
    if not guides_dir.is_dir():
        return []
    return sorted(guides_dir.glob("*.md"))


def _joinedstr_interpolates(node: ast.AST) -> bool:
    """True if `node` is an f-string with >=1 interpolated value.

    A `JoinedStr` whose parts are all `Constant` is a constant f-string
    with no interpolation — not a violation for mark_safe/logging.
    """
    return isinstance(node, ast.JoinedStr) and any(
        isinstance(part, ast.FormattedValue) for part in node.values
    )


def _func_name(func: ast.AST) -> str | None:
    """Return the bare callable name for `ast.Name`/`ast.Attribute`, else None."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _walk_security_style(code: str) -> tuple[list[str], list[str]]:
    """Walk one snippet's AST for the part-(c) triggers.

    Returns (errors, warnings) as lists of `lineN: message` strings.
    Assumes `code` already parses (caller handles SyntaxError).
    """
    errors: list[str] = []
    warnings: list[str] = []
    tree = ast.parse(code)

    for node in ast.walk(tree):
        # --- Triggers 1 + 2: print(...) / print(f"...") ---
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "print":
                fstring = bool(node.args) and _joinedstr_interpolates(
                    node.args[0]
                )
                if fstring:
                    errors.append(
                        f"line {node.lineno}: f-string `print(f\"...\")` — "
                        f"use the logging module (CLAUDE.md rules 4 + 6)"
                    )
                else:
                    errors.append(
                        f"line {node.lineno}: `print(...)` in example code — "
                        f"use the logging module (CLAUDE.md rule 6)"
                    )

        # --- Trigger 3: mark_safe(f"...{x}...") with interpolation ---
        if isinstance(node, ast.Call):
            if _func_name(node.func) == "mark_safe":
                if node.args and _joinedstr_interpolates(node.args[0]):
                    errors.append(
                        f"line {node.lineno}: interpolating `mark_safe(f\"...\")` "
                        f"— use `format_html()` or `escape()` (CLAUDE.md rule 1)"
                    )

        # --- Trigger 5: f-string logging — logger.X(f"...") ---
        if isinstance(node, ast.Call) and isinstance(
            node.func, ast.Attribute
        ):
            attr = node.func
            base = attr.value
            if (
                attr.attr in _LOGGER_METHODS
                and isinstance(base, ast.Name)
                and base.id in _LOGGER_NAMES
                and node.args
                and _joinedstr_interpolates(node.args[0])
            ):
                errors.append(
                    f"line {node.lineno}: f-string logging "
                    f"`{base.id}.{attr.attr}(f\"...\")` — use %s-style "
                    f"formatting (CLAUDE.md rule 4)"
                )

        # --- Trigger 4: bare `except: pass` / `except E: pass` ---
        if isinstance(node, ast.ExceptHandler):
            body_is_solely_pass = len(node.body) == 1 and isinstance(
                node.body[0], ast.Pass
            )
            if body_is_solely_pass:
                if node.type is None:
                    errors.append(
                        f"line {node.lineno}: bare `except: pass` — always "
                        f"log or re-raise (CLAUDE.md rule 5)"
                    )
                else:
                    errors.append(
                        f"line {node.lineno}: silenced `except ...: pass` — "
                        f"log or re-raise (CLAUDE.md rule 5)"
                    )

        # --- Soft advisory: @csrf_exempt decorator ---
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if _func_name(target) == "csrf_exempt":
                    warnings.append(
                        f"line {node.lineno}: `@csrf_exempt` in an example — "
                        f"verify it carries a documented justification "
                        f"(CLAUDE.md rule 3; advisory only)"
                    )

    return errors, warnings


def check_security_style(*docs: Path) -> tuple[list[str], list[str]]:
    """Part (c) — #1509: AST-walk every ```python block for djust's
    auto-reject security/style triggers.

    Blocks marked `<!-- doc-snippet-check: anti-pattern -->` are skipped
    (deliberately-wrong examples). Returns (errors, warnings).

    Applied to README/QUICKSTART only — NOT the guides (#1707): guides
    legitimately use `print()` in demo / management-command examples, so
    the style verdict would false-positive there. Guides get part (a) only.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for doc in docs:
        for start_line, code, marker in extract_python_blocks(doc):
            if marker == "anti-pattern":
                continue  # opted out of the style verdict (still parsed by part a)
            loc = f"{doc.name}:{start_line}"
            try:
                snippet_errors, snippet_warnings = _walk_security_style(code)
            except SyntaxError:
                # An unparseable snippet is part (a)'s failure to report,
                # not part (c)'s — skip it here.
                continue
            for e in snippet_errors:
                errors.append(f"{loc} — {e}")
            for w in snippet_warnings:
                warnings.append(f"{loc} — {w}")
    return errors, warnings


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
    readme: Path,
    quickstart: Path,
    pyproject: Path,
    bundle: Path,
    guides: list[Path] | None = None,
) -> tuple[int, str]:
    """Core logic exposed for testing.

    Runs part (a) + part (b) + part (c) against README/QUICKSTART and
    part (a) ONLY against `guides` (the `docs/website/guides/*.md` set —
    #1707). Returns (exit_code, message).
    """
    _setup_django()

    all_errors: list[str] = []
    all_warnings: list[str] = []

    # Part (a): README + QUICKSTART + every guide.
    all_errors.extend(check_snippets(readme, quickstart, *(guides or [])))
    # Part (c): README + QUICKSTART only (style lint — see check_security_style).
    style_errors, style_warnings = check_security_style(readme, quickstart)
    all_errors.extend(style_errors)
    all_warnings.extend(style_warnings)
    # Part (b): README + QUICKSTART only (claim assertions).
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
        "--guides-dir",
        default=None,
        help=(
            "Directory of guide markdown files to part-(a) check "
            "(default: <repo>/docs/website/guides)"
        ),
    )
    p.add_argument(
        "--no-guides",
        action="store_true",
        help="Skip the docs/website/guides/*.md part-(a) scan (#1707)",
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
    guides_dir = (
        Path(args.guides_dir)
        if args.guides_dir
        else (ROOT / "docs/website/guides")
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

    # An explicitly-passed --guides-dir that does not exist is a usage error;
    # the default dir is allowed to be absent (graceful no-op for a partial
    # checkout). --no-guides disables the scan entirely.
    if args.no_guides:
        guides: list[Path] = []
    else:
        if args.guides_dir is not None and not guides_dir.is_dir():
            print(f"ERROR: guides dir not found: {guides_dir}")
            sys.exit(2)
        guides = collect_guides(guides_dir)

    exit_code, msg = run(readme, quickstart, pyproject, bundle, guides)
    print(msg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
