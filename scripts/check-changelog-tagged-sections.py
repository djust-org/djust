#!/usr/bin/env python3
"""Pin already-shipped ``CHANGELOG.md`` sections against the newest release tag.

Once a version's section is superseded by a newer release, it is *frozen* —
a later branch merge must never rewrite it. But a 3-way merge of ``CHANGELOG.md``
across branches that diverged around a release cut can do exactly that with ZERO
conflicts (git's diff3 has no notion that a version heading is immutable),
silently moving genuinely-unreleased content into an already-shipped section —
see the v1.1.0rc5 consolidation incident (CLAUDE.md "Process canonicalizations
from the v1.1.0rc5 retro").

Neither ``check-changelog-test-counts.py`` nor ``make check-adr-status`` catches
this class — both validate the diff's own numeric/version-line claims, not
whether an already-shipped section changed at all.

**Why not pin each section against its OWN tag?** This repo uses rolling-rc
sections: a ``## [X.Y.ZrcN]`` heading keeps accumulating entries *after*
``vX.Y.ZrcN`` is tagged, until the next rc/release renames ``[Unreleased]``. So
a section is NOT frozen at its own tag — it's frozen once a *newer* release
supersedes it. The authoritative frozen record of every superseded section is
therefore the **newest release tag's** ``CHANGELOG.md``.

The check: find the newest release (the top-most ``## [X.Y.Z]`` section in the
working tree whose tag ``vX.Y.Z`` exists — the CHANGELOG is maintained
newest-first). For every section BELOW it that also appears in that tag's
``CHANGELOG.md``, assert the working-tree body is byte-identical to the tag's.
The newest tagged section itself (may still be accumulating) and any newer,
not-yet-tagged sections above it are skipped.

Exits 0 on match or when there's nothing to check (no tagged section, or git
unavailable). Exits 1 with a per-section diff on any mismatch.

Usage::

    python scripts/check-changelog-tagged-sections.py [path/to/CHANGELOG.md]

Closes #2028.
"""

from __future__ import annotations

import difflib
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANGELOG = REPO_ROOT / "CHANGELOG.md"

# A section heading: "## [X.Y.Z]" or "## [X.Y.Z] - 2026-06-30" etc. Captures the
# version token inside the brackets. "[Unreleased]" is intentionally mutable.
_HEADING_RE = re.compile(r"^## \[(?P<version>[^\]]+)\]")


def _split_sections(text: str) -> "list[tuple[str, str]]":
    """Return ``[(version, section_text), ...]`` in file order (newest first),
    where section_text runs from the heading line through the line before the
    next ``## [`` heading. ``[Unreleased]`` is skipped.
    """
    sections: list[tuple[str, str]] = []
    current: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        if current is not None and current.lower() != "unreleased":
            sections.append((current, "".join(buf)))

    for line in text.splitlines(keepends=True):
        m = _HEADING_RE.match(line)
        if m:
            _flush()
            current = m.group("version").strip()
            buf = [line]
        elif current is not None:
            buf.append(line)
    _flush()
    return sections


def _git(*args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True
        )
        return proc.returncode, proc.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _tag_exists(tag: str) -> bool:
    code, _ = _git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
    return code == 0


def check_changelog(changelog_path: Path) -> int:
    if not changelog_path.exists():
        print(f"CHANGELOG not found: {changelog_path}", file=sys.stderr)
        return 0  # nothing to check — don't block

    if _git("rev-parse", "--is-inside-work-tree")[0] != 0:
        return 0  # not a git work tree — skip silently

    working = _split_sections(changelog_path.read_text(encoding="utf-8"))

    # Anchor = the newest release: the first (top-most) section whose tag exists.
    anchor_ver: str | None = None
    anchor_index = -1
    for i, (ver, _) in enumerate(working):
        if _tag_exists(f"v{ver}"):
            anchor_ver = ver
            anchor_index = i
            break
    if anchor_ver is None:
        return 0  # no shipped section yet — nothing frozen to pin

    code, snapshot_text = _git("show", f"v{anchor_ver}:CHANGELOG.md")
    if code != 0:
        return 0  # anchor tag had no CHANGELOG.md — can't pin
    snapshot = dict(_split_sections(snapshot_text))

    mismatches: list[str] = []
    checked = 0
    # Everything BELOW the anchor (older, superseded) is frozen in the anchor's
    # snapshot. The anchor itself + anything above it (newer/untagged/Unreleased)
    # is skipped.
    for ver, body in working[anchor_index + 1 :]:
        if ver not in snapshot:
            continue  # not present in the anchor snapshot — can't pin
        checked += 1
        if body != snapshot[ver]:
            diff = "".join(
                difflib.unified_diff(
                    snapshot[ver].splitlines(keepends=True),
                    body.splitlines(keepends=True),
                    fromfile=f"v{anchor_ver}:CHANGELOG.md  [## [{ver}] as shipped]",
                    tofile=f"working CHANGELOG.md  [## [{ver}] now]",
                )
            )
            mismatches.append(
                f"\n✗ Section '## [{ver}]' was rewritten after it shipped "
                f"(differs from the frozen copy in v{anchor_ver}).\n"
                f"  An already-shipped CHANGELOG section is immutable — this is "
                f"almost certainly a stray branch\n"
                f"  merge rewriting shipped history (see #2028). Restore '[{ver}]' "
                f"to match v{anchor_ver}.\n{diff}"
            )

    if mismatches:
        print("CHANGELOG shipped-section pin FAILED:", file=sys.stderr)
        for m in mismatches:
            print(m, file=sys.stderr)
        return 1

    if checked:
        print(
            f"OK: {checked} shipped CHANGELOG section(s) match the newest "
            f"release tag v{anchor_ver}."
        )
    return 0


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CHANGELOG
    return check_changelog(path)


if __name__ == "__main__":
    raise SystemExit(main())
