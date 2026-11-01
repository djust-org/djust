#!/usr/bin/env python3
"""Lint a PR body for unresolved placeholders and dangling references (#3713).

``docs/patterns/unverified-claim.md`` is the pattern; this is its narrow mechanical
half. Three of three agent PR bodies in v1.3.0-15 broke the prose rule at writing
time, one of them with a placeholder count and links to comments that did not exist
yet (#3708). The check covers only what a string can answer; it does NOT cover
claims about code, which stay with the differential-test habit.

It fails on:

(a) **unresolved placeholder tokens**: ``TBD``, ``XXX``, ``<N>``, ``N tests``,
    ``#NNN`` (``#N``/``#TBD`` too), and
(b) **a reference that does not resolve in this repo**: ``#<number>``, a
    ``github.com/<this repo>/(issues|pull)/<number>`` URL, and an ``issuecomment-<id>``
    (bare, or in a URL for this repo).

What it deliberately leaves alone, so a clean body stays clean:

* fenced code blocks (``` and ~~~, an unterminated one runs to the end), inline code,
  and HTML comments (the PR template's own prompts);
* ``#`` inside any URL or anchor, the text of a link (``[#363](url)``,
  ``<a href=..>#363</a>``: the target is checked, not the label; Dependabot bodies are
  full of other repos' numbers this way), and ``owner/repo#N`` (another repo's number), a
  word glued to the ``#`` (``C#12``), and HTML entities (``&#35;``);
* a line carrying ``pr-body-lint: ignore`` (for prose that means another repo's ``#N``).

``Closes #N`` lines are ordinary references: they must resolve.

The logic is a pure function over the text with the resolver injected
(``check_body``); ``main`` wires the real ``gh api`` resolver. A lookup that could not
be made (network, rate limit) is reported under ``unchecked`` and does NOT fail the
run: an outage must not look like a bad body.

CLI::

    python scripts/check_pr_body.py --repo OWNER/REPO (--pr N | --body-file FILE | --body-env VAR)

Exit status: 0 clean (or only unchecked), 1 findings, 2 usage / could not read the body.
Not covered: review-comment links (``#discussion_r<id>``, ``#pullrequestreview-<id>``).
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

#: ("number" | "comment", id) -> True (resolves), False (does not), None (could not tell)
Resolver = Callable[[str, str], "bool | None"]

IGNORE_MARKER = "pr-body-lint: ignore"

PLACEHOLDERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("TBD", re.compile(r"\bTBD\b")),
    ("XXX", re.compile(r"\bXXX+\b")),
    ("<N>", re.compile(r"<N+>")),
    ("N tests", re.compile(r"\bN tests?\b")),
    ("#NNN", re.compile(r"#N+\b")),
)

# a ``#123`` that stands alone: not glued to a word, ``/`` (owner/repo#N), ``&`` (entity) or
# another ``#``; not the head of a longer token (``#123abc``, ``#12-3`` is still ``#12``).
_NUMBER_REF = re.compile(r"(?<![\w/&#])#(\d+)\b")
_COMMENT_REF = re.compile(r"\bissuecomment-(\d+)\b")
_URL = re.compile(r"https?://[^\s<>\[\]()\"']+")
_FENCE_OPEN = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.DOTALL)
_MD_LINK_TEXT = re.compile(r"\[([^\]\n]*)\](?=\()")
_HTML_LINK = re.compile(r"(<a\b[^>]*>)(.*?)(</a\s*>)", re.DOTALL | re.IGNORECASE)
_HTML_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


@dataclasses.dataclass(frozen=True)
class Finding:
    kind: str  # "placeholder" | "dangling-number" | "dangling-comment"
    line: int
    token: str
    message: str

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}"


@dataclasses.dataclass
class Report:
    findings: list[Finding] = dataclasses.field(default_factory=list)
    unchecked: list[Finding] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _blank(text: str) -> str:
    """Replace every non-newline character with a space (keeps offsets and line numbers)."""
    return re.sub(r"[^\n]", " ", text)


def _mask_fences(text: str) -> str:
    out: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.split("\n"):
        if fence is None:
            opened = _FENCE_OPEN.match(line)
            if opened:
                fence = (opened.group(1)[0], len(opened.group(1)))
                out.append(_blank(line))
            else:
                out.append(line)
            continue
        out.append(_blank(line))
        closing = re.match(r"^[ ]{0,3}([`~]{%d,})[ \t]*$" % fence[1], line)
        if closing and closing.group(1)[0] == fence[0]:
            fence = None
    return "\n".join(out)


def mask(text: str) -> str:
    """The body with fenced code, inline code and HTML comments blanked out."""
    text = _mask_fences(text)
    text = _INLINE_CODE.sub(lambda m: _blank(m.group(0)), text)
    return _HTML_COMMENT.sub(lambda m: _blank(m.group(0)), text)


def _blank_link_text(text: str) -> str:
    """Blank the TEXT of ``[text](url)`` and ``<a href=..>text</a>``: the target is the reference.

    Dependabot bodies are full of ``<a href="https://github.com/other/repo/issues/363">#363</a>``;
    ``#363`` there names the other repo's issue, and the URL (checked separately) is what counts.
    """
    text = _MD_LINK_TEXT.sub(lambda m: "[" + _blank(m.group(1)) + "]", text)
    return _HTML_LINK.sub(lambda m: m.group(1) + _blank(m.group(2)) + m.group(3), text)


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _ignored_lines(original: str) -> set[int]:
    return {i for i, line in enumerate(original.split("\n"), 1) if IGNORE_MARKER in line}


def check_body(body: str, resolve: Resolver, *, repo: str = "") -> Report:
    """Lint ``body``. ``repo`` is ``owner/name``: URLs to it are checked, others are not."""
    report = Report()
    ignored = _ignored_lines(body)
    visible = mask(body)
    repo_url = (
        re.compile(rf"https?://(?:www\.)?github\.com/{re.escape(repo)}/(?:issues|pull)/(\d+)", re.I)
        if repo
        else None
    )
    cache: dict[tuple[str, str], bool | None] = {}

    def lookup(kind: str, ident: str) -> bool | None:
        if (kind, ident) not in cache:
            cache[(kind, ident)] = resolve(kind, ident)
        return cache[(kind, ident)]

    def reference(kind: str, ident: str, line: int, token: str, what: str) -> None:
        if line in ignored:
            return
        verdict = lookup(kind, ident)
        if verdict is True:
            return
        finding = Finding(
            f"dangling-{kind}",
            line,
            token,
            f"{what} does not resolve in {repo or 'this repo'}"
            if verdict is False
            else f"{what} could not be checked",
        )
        (report.findings if verdict is False else report.unchecked).append(finding)

    # URLs first (their ``#`` and digits are references, never placeholders or ``#N`` prose),
    # then blank them so the plain-text passes below cannot re-read an anchor.
    plain = visible
    for url in _URL.finditer(visible):
        line = _line_of(visible, url.start())
        text = url.group(0)
        if repo_url and (m := repo_url.match(text)):
            reference("number", m.group(1), line, f"#{m.group(1)}", f"{text}")
            tail = text[m.end() :]
            comment = _COMMENT_REF.search(tail)
            if comment:
                reference(
                    "comment", comment.group(1), line, comment.group(0), f"comment link {text}"
                )
        plain = plain[: url.start()] + _blank(text) + plain[url.end() :]

    for name, pattern in PLACEHOLDERS:
        for m in pattern.finditer(plain):
            line = _line_of(plain, m.start())
            if line in ignored:
                continue
            report.findings.append(
                Finding("placeholder", line, m.group(0), f"unresolved placeholder {name!r}")
            )
    prose = _blank_link_text(plain)
    for m in _NUMBER_REF.finditer(prose):
        reference("number", m.group(1), _line_of(prose, m.start()), m.group(0), m.group(0))
    for m in _COMMENT_REF.finditer(prose):
        reference("comment", m.group(1), _line_of(prose, m.start()), m.group(0), m.group(0))

    report.findings.sort(key=lambda f: (f.line, f.token))
    report.unchecked.sort(key=lambda f: (f.line, f.token))
    return report


# --------------------------------------------------------------------------- the real resolver


def gh_resolver(repo: str) -> Resolver:
    """Resolve through ``gh api``: 404 is False, any other failure is None (unchecked)."""

    def resolve(kind: str, ident: str) -> bool | None:
        path = (
            f"repos/{repo}/issues/{ident}"
            if kind == "number"
            else f"repos/{repo}/issues/comments/{ident}"
        )
        try:
            proc = subprocess.run(
                ["gh", "api", path, "--silent"], capture_output=True, text=True, timeout=60
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode == 0:
            return True
        return False if "404" in proc.stderr or "Not Found" in proc.stderr else None

    return resolve


def render(report: Report) -> str:
    lines = [f"  FAIL  {f}" for f in report.findings]
    lines += [f"  skip  {f}" for f in report.unchecked]
    lines.append(
        f"pr-body-lint: {len(report.findings)} finding(s), {len(report.unchecked)} unchecked"
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, resolver: Resolver | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", required=True, help="OWNER/REPO the references must resolve in")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pr", type=int, help="read the body of this PR with gh")
    source.add_argument("--body-file", type=Path, help="read the body from a file ('-' = stdin)")
    source.add_argument("--body-env", help="read the body from this environment variable")
    args = parser.parse_args(argv)

    if args.pr is not None:
        proc = subprocess.run(
            [
                "gh",
                "pr",
                "view",
                str(args.pr),
                "--repo",
                args.repo,
                "--json",
                "body",
                "-q",
                ".body",
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            print(
                f"pr-body-lint: could not read PR {args.pr}: {proc.stderr.strip()}", file=sys.stderr
            )
            return 2
        body = proc.stdout
    elif args.body_file is not None:
        body = sys.stdin.read() if str(args.body_file) == "-" else args.body_file.read_text()
    else:
        if args.body_env not in os.environ:
            print(f"pr-body-lint: ${args.body_env} is not set", file=sys.stderr)
            return 2
        body = os.environ[args.body_env]

    report = check_body(body, resolver or gh_resolver(args.repo), repo=args.repo)
    print(render(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
