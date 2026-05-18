#!/usr/bin/env python3
"""
Audit ADR Status/version-line consistency — closes #1501.

Catches the drift class that #1492 reconciled by hand: an ADR whose
`**Status**:` line was flipped to "Accepted — shipped ..." while its
`**Target version**:` metadata line still names a future target. A line
literally labelled "Target version" on an already-shipped ADR is
semantically stale even when the number is right.

This audit is mechanical and self-contained — it does NOT call git, gh,
or the network (keeps it CI-fast and deterministic, matching
docs-lint.py's no-network design).

Rule 1 (hard — sets exit 1):
    Status/version-line consistency invariant.
    - Status begins with "Accepted"          → version line, if present,
      must be labelled `**Shipped in**:` (NOT `**Target version**:`).
    - Status begins with "Deferred"          → version line, if present,
      must contain a `post-1.0` or `deferred` token.
    - Status begins with "Partially Accepted" → version line must mention
      both a shipped-version token (`vX.Y`) and a `deferred`/`post-1.0`
      token (it straddles both).
    ADRs with no version line are fine (the 001/009/010/011 shape).

Rule 2 (soft — WARNING only, does NOT set exit 1):
    Proposed-but-shipped heuristic. If Status is exactly "Proposed" but
    the ADR body contains a shipped-marker phrase (`shipped`, `closes #`,
    `PR #`), emit a WARNING line. Heuristic, so it never fails the build.

Usage:
    python3 scripts/check-adr-status.py
    python3 scripts/check-adr-status.py --adr-dir docs/adr
    make check-adr-status

Exit code:
    0 — no drift (Rule 1 clean; Rule 2 warnings allowed)
    1 — drift found (>=1 ADR fails Rule 1)
    2 — usage error (adr dir missing)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Recognized version-metadata line keys. `Shipped in` is the fact-line for
# a shipped feature; `Target version` is the predictive line; `Milestone`
# is the ADR-012 fact-line style.
_VERSION_KEYS = ("Shipped in", "Target version", "Milestone")

# Header is parsed from the first N lines of the ADR file.
_HEADER_LINES = 12

# Tokens that mark a deferral (Deferred / Partially Accepted version lines).
_DEFER_TOKENS = ("post-1.0", "deferred")

# Heuristic shipped-marker phrases for Rule 2.
_SHIPPED_MARKERS = ("shipped", "closes #", "pr #")

# Matches a `vX.Y` shipped-version token.
_VERSION_TOKEN_RE = re.compile(r"v\d+\.\d+")


def _strip_meta_prefix(line: str) -> str:
    """Strip a leading `- ` bullet (ADR-012 header style) and whitespace."""
    stripped = line.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    return stripped


def parse_header(path: Path) -> dict:
    """Parse an ADR file's header into {status, status_word, version_key,
    version_line, body}.

    `status` is the full text after `**Status**:`. `status_word` is the
    leading classification (Accepted / Deferred / Partially Accepted /
    Proposed) extracted from the prefix before any ` — ` em-dash suffix.
    `version_key` is one of _VERSION_KEYS or None. `version_line` is the
    full text after the version key. `body` is the full file text
    (lower-cased) for the Rule 2 heuristic.
    """
    text = path.read_text()
    lines = text.splitlines()[:_HEADER_LINES]

    status = None
    version_key = None
    version_line = None

    for raw in lines:
        line = _strip_meta_prefix(raw)
        if status is None:
            m = re.match(r"\*\*Status\*\*:\s*(.+)", line)
            if m:
                status = m.group(1).strip()
                continue
        if version_key is None:
            for key in _VERSION_KEYS:
                m = re.match(rf"\*\*{re.escape(key)}\*\*:\s*(.+)", line)
                if m:
                    version_key = key
                    version_line = m.group(1).strip()
                    break

    status_word = None
    if status:
        # The classification is the leading word(s) before an em-dash
        # suffix. "Partially Accepted" is two words; everything else is
        # one. Match the longest known prefix.
        head = re.split(r"\s+—\s+|\s+-\s+", status, maxsplit=1)[0].strip()
        for known in ("Partially Accepted", "Accepted", "Deferred", "Proposed"):
            if head == known or head.startswith(known):
                status_word = known
                break

    return {
        "status": status,
        "status_word": status_word,
        "version_key": version_key,
        "version_line": version_line,
        "body": text.lower(),
    }


def _check_one(adr_id: str, header: dict) -> tuple[list[str], list[str]]:
    """Apply Rule 1 + Rule 2 to a single parsed ADR header.

    Returns (errors, warnings).
    """
    errors: list[str] = []
    warnings: list[str] = []

    status_word = header["status_word"]
    version_key = header["version_key"]
    version_line = header["version_line"] or ""
    version_lc = version_line.lower()

    # --- Rule 1 (hard) ---
    if status_word == "Accepted":
        if version_key == "Target version":
            errors.append(
                f"{adr_id} is Accepted but still has a `Target version:` "
                f"line — rename to `Shipped in:` or remove."
            )
    elif status_word == "Deferred":
        if version_key == "Target version":
            if not any(tok in version_lc for tok in _DEFER_TOKENS):
                errors.append(
                    f"{adr_id} is Deferred but its `Target version:` line "
                    f"names a concrete target — it must contain a "
                    f"`post-1.0` or `deferred` token (see Status)."
                )
    elif status_word == "Partially Accepted":
        if version_key is not None:
            has_shipped = bool(_VERSION_TOKEN_RE.search(version_line))
            has_defer = any(tok in version_lc for tok in _DEFER_TOKENS)
            if not (has_shipped and has_defer):
                errors.append(
                    f"{adr_id} is Partially Accepted — its version line "
                    f"must mention both a shipped-version token (vX.Y) "
                    f"and a `deferred`/`post-1.0` token."
                )

    # --- Rule 2 (soft) ---
    if status_word == "Proposed" and header["status"] == "Proposed":
        if any(marker in header["body"] for marker in _SHIPPED_MARKERS):
            warnings.append(
                f"{adr_id} is Proposed but its body contains a shipped "
                f"marker — verify the feature has not already shipped "
                f"(soft heuristic; not a failure)."
            )

    return errors, warnings


def run(adr_paths) -> tuple[int, str]:
    """Core logic exposed for testing.

    `adr_paths` is an iterable of ADR file Paths. Returns
    (exit_code, message).
    """
    adr_paths = sorted(adr_paths)
    all_errors: list[str] = []
    all_warnings: list[str] = []

    for path in adr_paths:
        adr_id = path.stem.split("-")[0]
        adr_label = f"ADR-{adr_id}" if adr_id.isdigit() else path.name
        header = parse_header(path)
        errors, warnings = _check_one(adr_label, header)
        all_errors.extend(errors)
        all_warnings.extend(warnings)

    lines: list[str] = []
    for w in all_warnings:
        lines.append(f"WARNING: {w}")

    if all_errors:
        lines.append(
            f"Found {len(all_errors)} ADR status/version-line "
            f"inconsistencies:"
        )
        for e in all_errors:
            lines.append(f"  {e}")
        return 1, "\n".join(lines)

    lines.append(
        f"OK — {len(adr_paths)} ADRs scanned, "
        f"0 status/version-line inconsistencies"
        + (f", {len(all_warnings)} warning(s)" if all_warnings else "")
    )
    return 0, "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument(
        "--adr-dir",
        default=None,
        help="Directory of ADR .md files to scan (default: docs/adr/)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Currently a no-op; reserved for parity with other linters",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    adr_dir = Path(args.adr_dir) if args.adr_dir else (ROOT / "docs/adr")
    if not adr_dir.is_dir():
        print(f"ERROR: ADR directory not found: {adr_dir}")
        sys.exit(2)

    adr_paths = sorted(adr_dir.glob("*.md"))
    exit_code, msg = run(adr_paths)
    print(msg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
