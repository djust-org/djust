"""Shared source-scanning primitives for the repo's structural pins (#2238).

Structural pins in this repo grep module source to count call sites or to
assert a pattern is present. Every one that greps the RAW source has the same
two defects, in both directions:

* **False positive** — prose (a comment or a docstring) that names the pattern
  is counted as a call site. Observed in #2237/#2215: a docstring explaining an
  argument-evaluation defect contained the literal text
  ``version=self._next_version_armed(html)`` and the #1817 pin went red with
  "expected 13 ... found 14". The workaround was to reword the prose, which is
  backwards — the code was correct and the checker was wrong.
* **False negative** — a call site inside a commented-out block still counts,
  so genuinely deleting the path the pin exists to protect leaves it green.
  Nothing catches this direction, which makes it the worse half.

Both functions here blank the offending tokens with ``tokenize`` so a pin sees
only what it means to see. They are deliberately **layout-preserving**: each
dropped token's text is replaced with spaces in place and newlines are kept, so
line and column offsets are identical to the input. That matters because
several pins distinguish shapes by spacing — ``version=self.f(`` (a keyword
argument) versus ``x = self.f(`` (an assignment) — a distinction a
token-joining strip would collapse, silently counting one as the other.

Two functions, because "prose" and "not executable" are different lines:

* :func:`without_prose` drops comments and docstrings only. A string literal is
  CODE — ``getattr(self, "_last_sent_version", 0)`` in
  ``LiveViewConsumer._arm_recovery`` is the whole point of the assertion that
  greps for that name, and dropping it makes the pin assert nothing. This is
  the right function for a pin over production source.
* :func:`code_only` additionally drops string literals, for a pin whose subject
  is a call and where naming it in a message string must NOT count.

Lifted from ``tests/test_reset_fixture_hygiene_2234.py::_code_only`` (#1077),
which now delegates here rather than keeping a second copy (#1646). That
version's hard-won details are preserved below.

**PYTHON SOURCE ONLY.** Both functions run CPython's ``tokenize``, so they
understand ``#`` comments and Python string literals and nothing else. They do
NOT strip Rust ``//`` / ``/* */`` comments, JS comments, or template syntax —
a ``.rs`` file fed in here comes back unchanged via the does-not-parse
fallback, silently, which is the failure mode this module exists to prevent.
A pin over non-Python source needs its own language-appropriate stripper.
#2247's two guards over ``crates/djust_templates/src/filters.rs`` are the live
example, and they show the blindness has a direction that depends on the
assertion's shape: the ``".replace(" not in body`` guard goes RED on a comment
merely explaining the ban, while the ``count("json_string_body(") == 3`` guard
stays GREEN when a real call site is deleted and its text left in a comment.
Both measured; tracked at #2249.
"""

from __future__ import annotations

import io
import textwrap
import tokenize as _tok

__all__ = ["code_only", "without_prose"]


def _fstring_types() -> frozenset[int]:
    """The PEP 701 f-string token types, or empty before Python 3.12.

    Python 3.12 splits an f-string into ``FSTRING_START`` / ``FSTRING_MIDDLE`` /
    ``FSTRING_END`` rather than one ``STRING`` token, so the literal text inside
    it is NOT type ``STRING`` and leaks through a naive filter. The canary in
    ``test_reset_fixture_hygiene_2234`` caught that on its first run, which is
    the entire argument for having a canary rather than trusting that "tokenize
    handles strings". ``getattr`` because those names do not exist before 3.12.
    """
    types = set()
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        tok_type = getattr(_tok, name, None)
        if tok_type is not None:
            types.add(tok_type)
    return frozenset(types)


_FSTRING = _fstring_types()
_STRINGISH = frozenset({_tok.STRING}) | _FSTRING
# Tokens that carry no text and do not end a logical line.
_TRIVIA = frozenset({_tok.NL, _tok.COMMENT, _tok.INDENT, _tok.DEDENT})


def _tokenize(source: str):
    """Tokenize, or return ``None`` if the source does not parse.

    ``inspect.getsource`` of a method returns an indented block, which is an
    ``IndentationError`` to ``tokenize``; the source is dedented first so a
    method source scans the same as a module source.

    Source that does not parse is left alone by the callers — a syntax error is
    somebody else's failure, and silently exempting it would be the same
    blindness in a new costume.
    """
    text = textwrap.dedent(source)
    try:
        return text, list(_tok.generate_tokens(io.StringIO(text).readline))
    except (_tok.TokenError, SyntaxError, IndentationError):
        return text, None


def _docstring_indices(toks: list) -> set[int]:
    """Indices of the string tokens that are bare string STATEMENTS.

    That is a module / class / function docstring, or any other string on a
    line by itself — prose in every case the pins care about. A string used as
    a value (``getattr(self, "_x")``, ``MSG = "..."``) is not included, because
    it is code.

    Implicit concatenation (``"a" "b"`` on one statement) is handled by taking
    the whole run.
    """
    found: set[int] = set()
    at_stmt_start = True
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok.type in _TRIVIA:
            i += 1
            continue
        if tok.type == _tok.NEWLINE:
            at_stmt_start = True
            i += 1
            continue
        if at_stmt_start and tok.type in _STRINGISH:
            run = []
            j = i
            while j < len(toks) and (toks[j].type in _STRINGISH or toks[j].type in _TRIVIA):
                if toks[j].type in _STRINGISH:
                    run.append(j)
                j += 1
            # A bare string statement is terminated by NEWLINE. Anything else
            # (an operator, a call) means the string was an operand, not a
            # statement — e.g. `"a" + b` or a bare string used as an argument.
            if j < len(toks) and toks[j].type == _tok.NEWLINE:
                found.update(run)
            i = j
            continue
        at_stmt_start = False
        i += 1
    return found


def _blank(text: str, toks: list, drop: set[int]) -> str:
    """Replace the text of the tokens at ``drop`` with spaces, in place."""
    lines = text.splitlines(keepends=True)
    for idx in drop:
        tok = toks[idx]
        (srow, scol), (erow, ecol) = tok.start, tok.end
        for row in range(srow, erow + 1):
            line_idx = row - 1
            if line_idx >= len(lines):
                break
            line = lines[line_idx]
            body = line.rstrip("\r\n")
            terminator = line[len(body) :]
            start = min(scol if row == srow else 0, len(body))
            end = min(ecol if row == erow else len(body), len(body))
            lines[line_idx] = body[:start] + " " * (end - start) + body[end:] + terminator
    return "".join(lines)


def without_prose(source: str) -> str:
    """Return ``source`` with comments and docstrings blanked to spaces.

    String literals are KEPT: they are code, and a pin that greps for a name
    passed as a string (``getattr(self, "_last_sent_version", 0)``) must still
    see it. Layout is preserved exactly — same lines, same columns — so a regex
    written against the original source keeps working unchanged.

    ``tokenize`` knows the difference between a quote that opens a string and
    one that closes it, handles CRLF, f-strings, raw strings and nested quotes,
    and cannot desynchronise — unlike the hand-rolled line scanners this repo
    keeps re-inventing. #2213 measured 1318 live code lines blinded across 42
    files by one of those, because it tracked a triple-quote delimiter across
    lines and treated everything between a docstring's opening quotes and the
    next occurrence as prose.
    """
    text, toks = _tokenize(source)
    if toks is None:
        return text
    drop = {i for i, t in enumerate(toks) if t.type == _tok.COMMENT}
    drop |= _docstring_indices(toks)
    return _blank(text, toks, drop)


def code_only(source: str) -> str:
    """Return ``source`` with comments AND every string literal blanked.

    Use when naming the subject inside a message string must not count — the
    ``deactivate_all()`` guard in ``test_reset_fixture_hygiene_2234`` wants
    ``MSG = "deactivate_all() is wrong"`` spared. Prefer :func:`without_prose`
    for a pin over production source, where string literals are usually load
    bearing.
    """
    text, toks = _tokenize(source)
    if toks is None:
        return text
    drop = {i for i, t in enumerate(toks) if t.type == _tok.COMMENT or t.type in _STRINGISH}
    return _blank(text, toks, drop)
