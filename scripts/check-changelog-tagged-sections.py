#!/usr/bin/env python3
"""Pin already-shipped ``CHANGELOG.md`` sections against the newest release tag.

A tagged section is frozen, including the newest release. Compare every
section at or below the newest tagged heading with that tag's CHANGELOG.
Unreleased and newer untagged sections remain editable. Comparing with one
snapshot also preserves the historical rolling-RC sections as they stood
when the latest release shipped.

Also detects *absence* (#2854): the newest release is the top-most
``## [X.Y.Z]`` section whose tag exists, so a tag with no section at all
used to be invisible — v1.1.3 shipped to PyPI with no section and its
fragment unfolded while the gate reported OK against v1.1.2. Every
release-version tag reachable from HEAD that sorts *above* the anchor must
therefore have a working-tree section; a missing one fails by name.

Also detects *deletion* (#2862): the pinned sections are the union of the
working tree's sections at or below the anchor and the anchor snapshot's
sections. Iterating only the tree's own sections — as #2028 first did —
never visits a shipped section that was deleted from the tree, so its
removal passed silently. Every section the anchor tag shipped must still be
present in the working tree; a missing one fails by name (a distinct
message from a rewritten body, because the operator's next action differs:
restore the section vs. revert the edit).

Exits 0 on match or when there's nothing to check (no tagged section, or git
unavailable). Exits 1 with a per-section diff on any mismatch (deleted
sections are named without a diff — there is no working copy to diff
against).

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

# A release tag / version token: vX.Y.Z with an optional a/b/rc pre-release
# suffix (0.2.0a1, 1.2.0rc7). Non-release tags don't demand CHANGELOG sections.
_RELEASE_TAG_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:(?P<pre>a|b|rc)(?P<prenum>\d+))?$"
)
_PRE_PHASE = {"a": 0, "b": 1, "rc": 2}  # final releases rank above all of these


def _parse_version(token: str) -> "tuple[int, int, int, int, int] | None":
    """Sort key for a release tag/version, or ``None`` if not a release.

    Final releases rank above their own pre-releases: ``1.2.0rc7 < 1.2.0``.
    """
    m = _RELEASE_TAG_RE.match(token)
    if not m:
        return None
    if m.group("pre"):
        phase, prenum = _PRE_PHASE[m.group("pre")], int(m.group("prenum"))
    else:
        phase, prenum = 3, 0
    return (int(m.group("major")), int(m.group("minor")), int(m.group("patch")), phase, prenum)


def _missing_higher_release_sections(anchor_ver: str, working_versions: "set[str]") -> "list[str]":
    """Release tags above the anchor whose version has no working section.

    Only tags reachable from HEAD are considered: in a multi-branch repo,
    ``main`` never carried the ``1.1.x`` maintenance sections and the ``1.1``
    branch never carried main's ``1.2.0rc*`` sections — demanding both would
    be a permanent false positive on whichever branch the check runs on.
    """
    anchor_key = _parse_version(anchor_ver)
    if anchor_key is None:
        return []
    code, out = _git("tag", "--list", "v*", "--sort=-v:refname", "--merged", "HEAD")
    if code != 0:
        return []  # cannot enumerate tags — fail open, like every git path here
    missing: list[str] = []
    for tag in out.split():
        key = _parse_version(tag)
        if key is None or key <= anchor_key:
            continue
        ver = tag[1:]  # the list pattern guarantees the leading 'v'
        if ver not in working_versions:
            missing.append(ver)
    return missing


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
        proc = subprocess.run(["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True)
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

    # Absence detection (#2854): a tag above the anchor with no section is the
    # defect itself — the gate's anchor silently fell back to the previous tag
    # and shipped v1.1.3 sectionless.
    missing = _missing_higher_release_sections(anchor_ver, {ver for ver, _ in working})

    mismatches: list[str] = []
    deleted: list[str] = []
    checked = 0
    working_versions = {ver for ver, _ in working}
    # The anchor itself is shipped too. Only newer untagged sections may change.
    for ver, body in working[anchor_index:]:
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
    # Deletion detection (#2862): iterate the snapshot's sections too. The
    # loop above only visits sections the working tree still has, so a
    # shipped section deleted from the tree was never compared and its
    # removal passed silently. Every snapshot section predates the anchor
    # tag, so demanding its presence cannot touch newer untagged sections.
    for ver in snapshot:
        if ver not in working_versions:
            deleted.append(ver)

    if mismatches or deleted or missing:
        print("CHANGELOG shipped-section pin FAILED:", file=sys.stderr)
        for m in mismatches:
            print(m, file=sys.stderr)
        for ver in deleted:
            print(
                f"\n✗ Section '## [{ver}]' shipped in v{anchor_ver}'s CHANGELOG "
                f"but is missing from the\n  working tree — it was deleted after "
                f"it shipped (see #2028/#2862; the realistic route is\n  a "
                f"cross-branch CHANGELOG merge resolved toward the branch without "
                f"the section).\n  Restore it verbatim: 'git show "
                f"v{anchor_ver}:CHANGELOG.md' and re-insert the '## [{ver}]'\n"
                f"  section.",
                file=sys.stderr,
            )
        for ver in missing:
            print(
                f"\n✗ Release tag 'v{ver}' exists on this branch's history but "
                f"CHANGELOG.md has no '## [{ver}]' section.\n"
                f"  A tagged release must ship with its CHANGELOG section already "
                f"committed: v1.1.3 went\n  to PyPI with no section and its "
                f"changelog.d/ fragment unfolded (#2854). Fold the fragment /\n"
                f"  rename '[Unreleased]' to '[{ver}]', commit, then re-tag.",
                file=sys.stderr,
            )
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
