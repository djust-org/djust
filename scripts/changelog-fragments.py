#!/usr/bin/env python3
"""``changelog.d/`` fragments: one file per PR, folded into ``CHANGELOG.md`` at release.

Why: every PR used to append to ``## [Unreleased]`` in ``CHANGELOG.md``. In a
multi-PR drain that is N sequential merge conflicts on one file — and a PR
whose merge ref cannot be built gets NO ``pull_request`` CI run at all. With
fragments, each PR adds a NEW file under ``changelog.d/`` and nothing ever
edits the same lines.

Shape (see ``changelog.d/README.md``)::

    changelog.d/<issue-or-slug>.<section>.md

``<section>`` is one of ``added``, ``changed``, ``fixed``, ``security``,
``documentation``, ``removed``, ``deprecated``. The body is exactly the bullet
that would have gone under that ``### Section`` — starting with ``- **…**``,
multi-paragraph allowed.

Subcommands:

``check``
    Every fragment has a valid section suffix and a non-empty bullet body, and
    its numeric test-count claims hold (same rules as
    ``scripts/check-changelog-test-counts.py``, whose logic is imported). With
    ``--cached`` or ``--range A..B`` it also refuses a change that edits the
    ``[Unreleased]`` body of ``CHANGELOG.md`` directly, unless the same change
    deletes fragments (i.e. it IS the release-cut compile) or
    ``--allow-release-cut`` is passed. A merge in progress (``MERGE_HEAD``) is
    skipped: merging ``main`` legitimately brings in ``[Unreleased]`` lines.

``compile``
    Fold every fragment into ``## [Unreleased]`` under the right
    ``### Section`` (headings created in canonical order, fragments sorted by
    filename), then delete the fragments. Idempotent: with no fragments it
    writes nothing. ``--dry-run`` prints the resulting section and touches
    nothing.

``preview``
    Print the ``[Unreleased]`` section as ``compile`` would write it.

The release cut runs ``compile`` BEFORE renaming ``## [Unreleased]`` →
``## [X.Y.Z] - date`` (see the ``release`` target in the Makefile), so the
Keep-a-Changelog output is byte-for-byte what a hand-edited section would be.

Usage::

    python scripts/changelog-fragments.py check [--cached | --range A..B] [--allow-release-cut]
    python scripts/changelog-fragments.py compile [--dry-run]
    python scripts/changelog-fragments.py preview

All subcommands accept ``--changelog PATH``; the fragments directory is
``changelog.d/`` next to that file (so fixtures work in tests).
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CHANGELOG = REPO_ROOT / "CHANGELOG.md"
FRAGMENT_DIR_NAME = "changelog.d"

# Canonical ``### Section`` order inside ``[Unreleased]`` — the order the
# hand-maintained sections have used (Added, Changed, Fixed, Security,
# Documentation, Removed), with Deprecated last. Lower-case key → heading.
SECTIONS: dict[str, str] = {
    "added": "Added",
    "changed": "Changed",
    "fixed": "Fixed",
    "security": "Security",
    "documentation": "Documentation",
    "removed": "Removed",
    "deprecated": "Deprecated",
}
_SECTION_RANK = {name: i for i, name in enumerate(SECTIONS)}

_FRAGMENT_NAME_RE = re.compile(r"^(?P<slug>[^.][^/]*?)\.(?P<section>[a-z]+)\.md$")
_UNRELEASED_RE = re.compile(r"^##\s*\[Unreleased\]", re.IGNORECASE)
_VERSION_HEADING_RE = re.compile(r"^##\s+\[[^\]]+\]")
_SUBHEADING_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$")


class FragmentError(ValueError):
    """A fragment that cannot be compiled."""


@dataclass(frozen=True)
class Fragment:
    path: Path
    slug: str
    section: str  # lower-case key into SECTIONS
    body: str  # stripped bullet text, no trailing newline

    @property
    def multi_paragraph(self) -> bool:
        return "\n\n" in self.body


# --------------------------------------------------------------------------
# Fragments
# --------------------------------------------------------------------------


def fragment_dir_for(changelog_path: Path) -> Path:
    return changelog_path.resolve().parent / FRAGMENT_DIR_NAME


def list_fragment_paths(fragment_dir: Path) -> list[Path]:
    """Every file in the directory except ``README.md`` and dotfiles, sorted by name.

    Non-``.md`` files are returned too so ``check`` refuses them (a stray
    ``2510.fixed.txt`` would otherwise be silently skipped at compile time).
    """
    if not fragment_dir.is_dir():
        return []
    return sorted(
        p
        for p in fragment_dir.iterdir()
        if p.is_file() and p.name != "README.md" and not p.name.startswith(".")
    )


def parse_fragment(path: Path) -> Fragment:
    """Validate the filename + body and return a :class:`Fragment`.

    Raises :class:`FragmentError` with a one-line reason on any problem.
    """
    m = _FRAGMENT_NAME_RE.match(path.name)
    if not m:
        raise FragmentError(
            f"{path.name}: expected `<issue-or-slug>.<section>.md` "
            f"(section ∈ {', '.join(SECTIONS)})"
        )
    section = m.group("section")
    if section not in SECTIONS:
        raise FragmentError(
            f"{path.name}: unknown section `{section}` — use one of {', '.join(SECTIONS)}"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FragmentError(f"{path.name}: unreadable ({exc})") from exc
    body = raw.strip("\n").rstrip()
    if not body.strip():
        raise FragmentError(f"{path.name}: empty body")
    if not body.startswith("- "):
        raise FragmentError(
            f"{path.name}: body must be a bullet — start with `- **…**` "
            f"(got {body.splitlines()[0][:40]!r})"
        )
    return Fragment(path=path, slug=m.group("slug"), section=section, body=body)


def load_fragments(fragment_dir: Path) -> tuple[list[Fragment], list[str]]:
    """Return ``(fragments, errors)``; a bad fragment is reported, not raised."""
    fragments: list[Fragment] = []
    errors: list[str] = []
    for p in list_fragment_paths(fragment_dir):
        try:
            fragments.append(parse_fragment(p))
        except FragmentError as exc:
            errors.append(str(exc))
    return fragments, errors


# --------------------------------------------------------------------------
# CHANGELOG [Unreleased] model
# --------------------------------------------------------------------------


@dataclass
class Subsection:
    heading: str  # e.g. "### Added"
    lines: list[str] = field(default_factory=list)  # body lines, verbatim

    @property
    def name(self) -> str:
        m = _SUBHEADING_RE.match(self.heading)
        return m.group("name") if m else self.heading

    @property
    def rank(self) -> int:
        # Unknown headings ("### Tests") sort after every canonical one so a
        # new canonical heading is inserted before them.
        return _SECTION_RANK.get(self.name.lower(), len(SECTIONS))


@dataclass
class Unreleased:
    heading: str
    preamble: list[str]  # lines between the heading and the first "###"
    subsections: list[Subsection]

    def get(self, name: str) -> Subsection | None:
        for s in self.subsections:
            if s.name.lower() == name.lower():
                return s
        return None

    def ensure(self, section_key: str) -> Subsection:
        """Return the subsection for ``section_key``, creating it in canonical order."""
        existing = self.get(SECTIONS[section_key])
        if existing is not None:
            return existing
        new = Subsection(heading=f"### {SECTIONS[section_key]}")
        # Insert after the last existing subsection whose canonical rank is
        # lower; unknown headings rank last, so a canonical one goes before them.
        insert_at = 0
        for i, s in enumerate(self.subsections):
            if s.rank <= new.rank and s.rank < len(SECTIONS):
                insert_at = i + 1
        self.subsections.insert(insert_at, new)
        return new

    def render(self) -> str:
        """Heading through the last subsection, one blank line between blocks."""
        out: list[str] = [self.heading]
        pre = _strip_blank_edges(self.preamble)
        if pre:
            out.append("")
            out.extend(pre)
        for s in self.subsections:
            out.append("")
            out.append(s.heading)
            body = _strip_blank_edges(s.lines)
            if body:
                out.append("")
                out.extend(body)
        return "\n".join(out) + "\n"


def _strip_blank_edges(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


@dataclass
class Changelog:
    before: str  # everything before the ``## [Unreleased]`` line, verbatim
    unreleased: Unreleased
    after: str  # from the next "## [" heading to EOF (verbatim)

    def render(self) -> str:
        sep = "\n" if self.after else ""
        return self.before + self.unreleased.render() + sep + self.after


def split_changelog(text: str) -> Changelog:
    """Split ``text`` into (before, Unreleased model, after) by character offsets.

    ``before`` is everything up to the ``## [Unreleased]`` line; ``after`` is
    everything from the next ``## [`` heading (verbatim, so the frozen
    shipped sections are never re-serialized — #2028 pins them).
    """
    heading_start: int | None = None
    section_end = len(text)
    pos = 0
    for line in text.splitlines(keepends=True):
        if heading_start is None:
            if _UNRELEASED_RE.match(line):
                heading_start = pos
        elif _VERSION_HEADING_RE.match(line):
            section_end = pos
            break
        pos += len(line)
    if heading_start is None:
        raise FragmentError("CHANGELOG has no `## [Unreleased]` heading")

    section_lines = text[heading_start:section_end].splitlines()
    preamble: list[str] = []
    subsections: list[Subsection] = []
    cur: Subsection | None = None
    for ln in section_lines[1:]:
        if _SUBHEADING_RE.match(ln):
            cur = Subsection(heading=ln.rstrip())
            subsections.append(cur)
        elif cur is None:
            preamble.append(ln)
        else:
            cur.lines.append(ln)
    return Changelog(
        before=text[:heading_start],
        unreleased=Unreleased(section_lines[0].rstrip(), preamble, subsections),
        after=text[section_end:],
    )


def unreleased_body(text: str) -> str:
    """The normalized ``[Unreleased]`` body (heading excluded), or "" when absent."""
    try:
        rendered = split_changelog(text).unreleased.render()
    except FragmentError:
        return ""
    return rendered.split("\n", 1)[1]


# --------------------------------------------------------------------------
# compile / preview
# --------------------------------------------------------------------------


def _append_bullet(sub: Subsection, frag: Fragment) -> None:
    body = _strip_blank_edges(sub.lines)
    frag_lines = frag.body.splitlines()
    if body:
        # Match the hand-maintained style: a blank line separates two bullets
        # when either is multi-paragraph; single-paragraph bullets are adjacent.
        prev_multi = _last_bullet_is_multi_paragraph(body)
        if prev_multi or frag.multi_paragraph:
            body.append("")
    body.extend(frag_lines)
    sub.lines = body


def _last_bullet_is_multi_paragraph(lines: list[str]) -> bool:
    # Walk back to the start of the last bullet; multi-paragraph iff a blank
    # line sits inside it.
    i = len(lines) - 1
    while i > 0 and not re.match(r"^-\s+", lines[i]):
        i -= 1
    return any(not ln.strip() for ln in lines[i:])


def compile_text(text: str, fragments: list[Fragment]) -> str:
    """Return ``text`` with ``fragments`` folded into ``[Unreleased]``."""
    if not fragments:
        return text
    cl = split_changelog(text)
    for frag in sorted(fragments, key=lambda f: f.path.name):
        _append_bullet(cl.unreleased.ensure(frag.section), frag)
    return cl.render()


def preview_text(text: str, fragments: list[Fragment]) -> str:
    """The rendered ``[Unreleased]`` section after compiling ``fragments``."""
    compiled = compile_text(text, fragments)
    return split_changelog(compiled).unreleased.render()


def cmd_compile(changelog_path: Path, *, dry_run: bool) -> int:
    fragment_dir = fragment_dir_for(changelog_path)
    fragments, errors = load_fragments(fragment_dir)
    if errors:
        _report_errors(errors)
        return 1
    if not fragments:
        print(f"changelog-fragments: nothing to compile ({fragment_dir} has no fragments)")
        return 0
    text = changelog_path.read_text(encoding="utf-8")
    try:
        new_text = compile_text(text, fragments)
    except FragmentError as exc:
        print(f"changelog-fragments: {exc}", file=sys.stderr)
        return 1
    if dry_run:
        sys.stdout.write(split_changelog(new_text).unreleased.render())
        print(
            f"\n(dry run) would fold {len(fragments)} fragment(s) into "
            f"{changelog_path.name} and delete them",
            file=sys.stderr,
        )
        return 0
    changelog_path.write_text(new_text, encoding="utf-8")
    for frag in fragments:
        frag.path.unlink()
    print(
        f"changelog-fragments: folded {len(fragments)} fragment(s) into "
        f"{changelog_path.name} [Unreleased]; deleted them from {fragment_dir.name}/"
    )
    return 0


def cmd_preview(changelog_path: Path) -> int:
    fragments, errors = load_fragments(fragment_dir_for(changelog_path))
    if errors:
        _report_errors(errors)
        return 1
    text = changelog_path.read_text(encoding="utf-8")
    try:
        sys.stdout.write(preview_text(text, fragments))
    except FragmentError as exc:
        print(f"changelog-fragments: {exc}", file=sys.stderr)
        return 1
    return 0


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def _load_test_count_checker():
    spec = importlib.util.spec_from_file_location(
        "check_changelog_test_counts",
        Path(__file__).resolve().parent / "check-changelog-test-counts.py",
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses resolve `from __future__` annotations here
    spec.loader.exec_module(mod)
    return mod


def test_count_mismatches(fragments: list[Fragment], repo_root: Path) -> list[str]:
    """Apply ``check-changelog-test-counts.py``'s claim check to each fragment body."""
    checker = _load_test_count_checker()
    out: list[str] = []
    for frag in fragments:
        for mm in checker.find_mismatches(frag.body, 1, repo_root):
            out.append(
                f"{frag.path.name}: claims {mm.claimed} ({mm.phrase!r}) for {mm.file} "
                f"but the file has {mm.actual}"
            )
    return out


def _git(repo_root: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(repo_root), capture_output=True, text=True, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return proc.returncode, proc.stdout


def _show(repo_root: Path, rev: str, rel: str) -> str | None:
    code, out = _git(repo_root, "show", f"{rev}:{rel}")
    return out if code == 0 else None


def _squash_ws(text: str) -> str:
    return " ".join(text.split())


def _body_line_delta(old_body: str, new_body: str) -> tuple[list[str], list[str]]:
    """(removed, added) non-blank lines between two ``[Unreleased]`` bodies.

    A multiset difference: a line removed from one place and re-added
    elsewhere is neither. ``###`` headings are excluded — emptying a section
    legitimately drops its heading.
    """
    old_lines = [ln for ln in old_body.splitlines() if ln.strip()]
    remaining = [ln for ln in new_body.splitlines() if ln.strip()]
    removed: list[str] = []
    for ln in old_lines:
        if ln in remaining:
            remaining.remove(ln)
        elif not ln.lstrip().startswith("#"):
            removed.append(ln)
    added = [ln for ln in remaining if not ln.lstrip().startswith("#")]
    return removed, added


def _touched_fragment_text(repo_root: Path, diff_args: list[str], new_rev: str) -> str:
    """The concatenated text of every fragment ADDED or MODIFIED by the change."""
    code, out = _git(repo_root, *diff_args, "--diff-filter=AM")
    if code != 0:
        return ""
    chunks: list[str] = []
    for ln in out.splitlines():
        rel = ln.split("\t", 1)[-1]
        if rel.startswith(f"{FRAGMENT_DIR_NAME}/") and rel.endswith(".md"):
            text = _show(repo_root, new_rev, rel)
            if text:
                chunks.append(text)
    return _squash_ws("\n".join(chunks))


def is_fragment_migration(
    repo_root: Path, old: str, new: str, diff_args: list[str], new_rev: str
) -> bool:
    """A removal-only ``[Unreleased]`` diff whose every removed line reappears
    verbatim in a fragment the same change adds or modifies (#2603).

    That is a pre-fragment entry MOVING into ``changelog.d/``, not a direct
    edit. Any added body line, or a removed line that no touched fragment
    carries, is still a direct edit.
    """
    removed, added = _body_line_delta(unreleased_body(old), unreleased_body(new))
    if added or not removed:
        return False
    corpus = _touched_fragment_text(repo_root, diff_args, new_rev)
    if not corpus:
        return False
    return all(_squash_ws(ln) in corpus for ln in removed)


def direct_edit_violation(
    repo_root: Path,
    *,
    cached: bool = False,
    rev_range: str | None = None,
    changelog_rel: str = "CHANGELOG.md",
) -> str | None:
    """Return a reason string when the change edits ``[Unreleased]`` directly.

    ``cached``: compare ``HEAD`` to the index. ``rev_range``: ``A..B`` (or a
    single rev, compared to its parent). Allowed: a change that also DELETES
    fragments (the release-cut compile), a merge in progress, or a
    removal-only diff whose lines reappear in a fragment the change adds
    (an entry migrating into ``changelog.d/``, #2603).
    """
    if cached:
        if _git(repo_root, "rev-parse", "-q", "--verify", "MERGE_HEAD")[0] == 0:
            return None  # a merge legitimately brings [Unreleased] lines in
        old_rev, new_rev = "HEAD", ""  # "" == the index for `git show :path`
        diff_args = ["diff", "--cached", "--name-status"]
    else:
        assert rev_range
        if ".." in rev_range:
            old_rev, new_rev = rev_range.split("..", 1)
        else:
            old_rev, new_rev = f"{rev_range}~1", rev_range
        diff_args = ["diff", "--name-status", f"{old_rev}..{new_rev}"]

    old = _show(repo_root, old_rev, changelog_rel)
    new = _show(repo_root, new_rev, changelog_rel)
    if old is None or new is None:
        return None  # CHANGELOG absent on one side — nothing to compare
    if unreleased_body(old) == unreleased_body(new):
        return None

    code, deleted = _git(repo_root, *diff_args, "--diff-filter=D")
    if code == 0 and any(
        ln.split("\t", 1)[-1].startswith(f"{FRAGMENT_DIR_NAME}/") for ln in deleted.splitlines()
    ):
        return None  # fragments are being compiled — the one legitimate editor

    if is_fragment_migration(repo_root, old, new, diff_args, new_rev):
        return None  # an entry moving INTO changelog.d/ (#2603), not an edit

    return (
        f"{changelog_rel}: the `[Unreleased]` body was edited directly. Write a "
        f"fragment instead: {FRAGMENT_DIR_NAME}/<issue-or-slug>.<section>.md "
        f"(see {FRAGMENT_DIR_NAME}/README.md). Only the release-cut compile "
        f"(`python scripts/changelog-fragments.py compile`) edits that section; "
        f"pass --allow-release-cut to override."
    )


def cmd_check(
    changelog_path: Path,
    *,
    cached: bool,
    rev_range: str | None,
    allow_release_cut: bool,
) -> int:
    repo_root = changelog_path.resolve().parent
    fragment_dir = fragment_dir_for(changelog_path)
    fragments, errors = load_fragments(fragment_dir)
    errors.extend(test_count_mismatches(fragments, repo_root))

    if (cached or rev_range) and not allow_release_cut:
        try:
            rel = str(changelog_path.resolve().relative_to(repo_root))
        except ValueError:
            rel = "CHANGELOG.md"
        reason = direct_edit_violation(
            repo_root, cached=cached, rev_range=rev_range, changelog_rel=rel
        )
        if reason:
            errors.append(reason)

    if errors:
        _report_errors(errors)
        return 1
    print(f"changelog-fragments: OK ({len(fragments)} fragment(s) valid)")
    return 0


def _report_errors(errors: list[str]) -> None:
    print("changelog-fragments: FAILED", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--changelog", type=Path, default=DEFAULT_CHANGELOG, help="path to CHANGELOG.md"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser(
        "check", help="validate fragments (+ refuse direct [Unreleased] edits)"
    )
    p_check.add_argument("--cached", action="store_true", help="compare HEAD to the index")
    p_check.add_argument("--range", dest="rev_range", help="git range A..B (or one rev)")
    p_check.add_argument(
        "--allow-release-cut",
        action="store_true",
        help="do not refuse a direct [Unreleased] edit (release cut / migration)",
    )

    p_compile = sub.add_parser("compile", help="fold fragments into [Unreleased] and delete them")
    p_compile.add_argument("--dry-run", action="store_true", help="print, write nothing")

    sub.add_parser("preview", help="print the compiled [Unreleased] section")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    changelog_path: Path = args.changelog
    if not changelog_path.exists():
        print(f"CHANGELOG not found at {changelog_path}", file=sys.stderr)
        return 1
    if args.command == "check":
        return cmd_check(
            changelog_path,
            cached=args.cached,
            rev_range=args.rev_range,
            allow_release_cut=args.allow_release_cut,
        )
    if args.command == "compile":
        return cmd_compile(changelog_path, dry_run=args.dry_run)
    if args.command == "preview":
        return cmd_preview(changelog_path)
    return 2  # pragma: no cover — argparse enforces the choice


if __name__ == "__main__":
    sys.exit(main())
