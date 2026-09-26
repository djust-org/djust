#!/usr/bin/env python3
"""Reject multi-line ``{# ... #}`` comments in Django templates.

Django's ``{# #}`` comment syntax is **single-line only**. A ``{#`` whose
closing ``#}`` is on a later line is not a comment at all: the lexer emits it as
TEXT, so the comment is rendered verbatim on the page, and any template tag
written inside it is *executed*.

Three ways it has actually bitten, all in one session:

1. A five-line ``{# ... #}`` at the top of a page template printed the
   comment — including its backticks — above the masthead, on every page.
2. A comment mentioning ``{% block view_class %}`` registered a second block of
   that name, and the page 500'd with
   ``'block' tag with name 'view_class' appears more than once``.
3. A comment naming ``html[data-theme="dark"]`` was autoescaped to
   ``&quot;`` and rendered as body text.

The failure is silent in the common case — the page still renders, so nothing
raises and no test fails — and it is invisible to a reader of the source, since
the comment *looks* like a comment. That is exactly the class of thing a
mechanical check should own.

Multi-line comments belong in ``{% comment %} ... {% endcomment %}``, which the
codebase already uses correctly in several places.

Templates that a command *writes* live in Python string literals, not ``.html``
files, so the scan also reads the string constants of every ``.py`` file under a
``management/`` or ``scaffolding/`` directory. #3141: ``djust_theme init
--with-examples`` wrote a two-line ``{# #}`` whose quoted ``{% end_theme_card %}``
then raised ``TemplateSyntaxError``.

Usage:
    python3 scripts/check-template-comments.py
    python3 scripts/check-template-comments.py --path python/djust

Exit code:
    0 — no multi-line ``{# #}`` found
    1 — at least one found
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: ``{#`` … ``#}`` with a newline anywhere between. Non-greedy, DOTALL, so the
#: shortest span wins and two adjacent single-line comments are not merged into
#: one "multi-line" match.
_MULTILINE_COMMENT_RE = re.compile(r"\{#(.*?)#\}", re.DOTALL)


#: Directories whose ``.py`` files hold templates as string literals: the
#: management commands and scaffolders that write templates into a project.
_EMBEDDED_TEMPLATE_DIRS = ("management", "scaffolding")


def _findings_in_text(source: str, first_line: int = 1) -> list[tuple[int, str]]:
    findings = []
    for match in _MULTILINE_COMMENT_RE.finditer(source):
        body = match.group(1)
        if "\n" not in body:
            continue  # single-line: Django handles it correctly
        line = first_line + source[: match.start()].count("\n")
        first = body.strip().splitlines()[0][:60] if body.strip() else ""
        findings.append((line, first))
    return findings


def find_multiline_comments(path: Path) -> list[tuple[int, str]]:
    """``[(line_number, first_line_of_comment), ...]`` for one template."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    return _findings_in_text(source)


def find_multiline_comments_in_python(path: Path) -> list[tuple[int, str]]:
    """``[(line_number, first_line_of_comment), ...]`` across a module's string literals.

    Only string constants are scanned, so a ``{#`` in a Python ``#`` comment
    is ignored. Line numbers count from the line where the literal starts.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        return []
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            findings.extend(_findings_in_text(node.value, node.lineno))
    return sorted(findings)


def iter_embedded_template_modules(root: Path):
    """``.py`` files under a ``management/`` or ``scaffolding/`` directory of ``root``."""
    for module in sorted(root.rglob("*.py")):
        parts = module.relative_to(root).parts[:-1]
        if any(part in _EMBEDDED_TEMPLATE_DIRS for part in parts):
            yield module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        default="python/djust",
        help="Directory to scan, relative to the repo root (default: python/djust).",
    )
    args = parser.parse_args(argv)

    root = ROOT / args.path
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    total = 0
    for template in sorted(root.rglob("*.html")):
        for line, preview in find_multiline_comments(template):
            rel = template.relative_to(ROOT)
            print(f"{rel}:{line}: multi-line {{# #}} renders as page text -> {preview}…")
            total += 1
    for module in iter_embedded_template_modules(root):
        for line, preview in find_multiline_comments_in_python(module):
            rel = module.relative_to(ROOT)
            print(f"{rel}:{line}: multi-line {{# #}} in a generated template -> {preview}…")
            total += 1

    if total:
        print(
            f"\n{total} multi-line {{# #}} comment(s) found. Django's `{{# #}}` is "
            "single-line only — a multi-line one is emitted as TEXT (and any tag "
            "inside it is executed). Use `{% comment %} … {% endcomment %}`.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
