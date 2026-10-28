"""Classification, baseline file and ratchet comparison — pure, no Django (#3663).

Three layers, each testable on its own:

1. ``classify_variant`` — two rendered outputs (or two errors) to one of
   ``IDENTICAL`` / ``WHITESPACE-ONLY`` / ``DIFFERENT`` / ``BOTH-ERROR`` /
   ``ERR-ONE-ENGINE``.
2. ``classify_tag`` — all the variants of one tag to a ``TagRow``: the class,
   which structural aspects differ, which variants one engine rejected and how
   many variants both engines rendered.
3. ``compare`` — a measured table against the checked-in baseline.

The structural scan deliberately does NOT use ``html.parser``. Its handling of
malformed markup, ``<script>`` bodies and comments has changed in security
releases of 3.12, 3.13 and 3.14, and the CI matrix spans five interpreters; a
classification that moves with the interpreter is a flaky test. The scan below
is a few regular expressions over ``str`` and ``html.unescape``, whose results
do not depend on the Python version. The CLASS of a variant does not use the
scan at all (it is a byte comparison); only the ``aspects`` column does.
"""

from __future__ import annotations

import csv
import html
import io
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

IDENTICAL = "IDENTICAL"
WHITESPACE_ONLY = "WHITESPACE-ONLY"
DIFFERENT = "DIFFERENT"
BOTH_ERROR = "BOTH-ERROR"
ERR_ONE_ENGINE = "ERR-ONE-ENGINE"

# Tag-level classes, best first. The index is the rank the ratchet compares.
TAG_CLASSES = (
    IDENTICAL,
    WHITESPACE_ONLY,
    "DIFFERENT-escape-policy-only",
    "NOT-RENDERABLE-IN-ONE-ENGINE",
    DIFFERENT,
    "UNEXERCISED",
)

# A variant that feeds the tag a string literal / a ``mark_safe`` value. Django
# honours both; the Rust handlers receive plain strings and escape them. A tag
# whose ONLY differences are in these variants differs in escaping policy, not
# in markup.
ESCAPE_VARIANTS = ("literal", "safe")

_XSS_MARKERS = ("<script>alert(1)</script>", "<img src=x onerror=alert(2)>")
_LITERAL_MARKERS = ("<i>b</i>", "<b>safe</b>")
_JS_URL = re.compile(r"""(?:href|src|action|formaction)\s*=\s*["']?\s*javascript:""")

_WS = re.compile(r"\s+")
_GAP = re.compile(r">\s+<")
_ADDR = re.compile(r"0x[0-9a-f]{6,}")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){0,3}(?:-[0-9a-f]{12})?|\b[0-9a-f]{32}\b")

_SCRIPT_STYLE = re.compile(
    r"""(<(script|style)\b(?:[^>"']|"[^"]*"|'[^']*')*>).*?</\2\s*>""", re.I | re.S
)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_TAG = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9:-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
_ATTR = re.compile(r"""([^\s=/>"']+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
_ID_ATTRS = ("id", "for", "aria-controls", "aria-labelledby", "aria-describedby")


def norm_addr(text: str) -> str:
    """Replace memory addresses (``0x…``), which a repr leaks into HTML."""
    return _ADDR.sub("0xADDR", text)


def norm_ws(text: str) -> str:
    text = _WS.sub(" ", text)
    return _GAP.sub("><", text).strip()


def raw_markers(out: str) -> list[str]:
    """Dangerous or literal markers that appear UNESCAPED in ``out``."""
    found = [m for m in _XSS_MARKERS + _LITERAL_MARKERS if m in out]
    if _JS_URL.search(out):
        found.append("javascript:url")
    return found


@dataclass
class Scan:
    text: str
    tags: list[str]
    classes: list[str]
    a11y: list[tuple[str, str]]
    dj: list[tuple[str, str]]
    ids: list[tuple[str, str]]
    other: list[tuple[str, str]]


def scan(markup: str) -> Scan:
    """The structural facts the ``aspects`` column compares."""
    # Comments first (they may hold a ``<script>``), then script/style BODIES;
    # the start tag stays, as it did under ``html.parser``, so it still counts.
    body = _COMMENT.sub(" ", markup)
    body = _SCRIPT_STYLE.sub(r"\1", body)
    tags: list[str] = []
    classes: list[str] = []
    a11y: list[tuple[str, str]] = []
    dj: list[tuple[str, str]] = []
    ids: list[tuple[str, str]] = []
    other: list[tuple[str, str]] = []
    for closing, name, attrs in _TAG.findall(body):
        if closing:
            continue
        tags.append(name.lower())
        for m in _ATTR.finditer(attrs):
            key = m.group(1).lower()
            value = html.unescape(next((g for g in m.groups()[1:] if g is not None), ""))
            if key == "class":
                classes += value.split()
            elif key in ("role", "tabindex") or key.startswith("aria-"):
                a11y.append((key, value))
            elif key.startswith(("dj-", "data-", "hx-")):
                dj.append((key, value))
            if key in _ID_ATTRS:
                ids.append((key, _UUID.sub("UUID", value)))
            elif key not in ("class", "role", "tabindex") and not key.startswith(
                ("aria-", "dj-", "data-", "hx-")
            ):
                other.append((key, value))
    text = html.unescape(_TAG.sub(" ", body))
    return Scan(text, tags, classes, a11y, dj, ids, other)


def differing_aspects(a: str, b: str) -> list[str]:
    """Which structural aspects of two outputs differ, in a fixed order."""
    x, y = scan(a), scan(b)
    out = []
    if norm_ws(x.text) != norm_ws(y.text):
        out.append("text")
    if x.tags != y.tags:
        out.append("tags")
    if sorted(x.classes) != sorted(y.classes):
        out.append("classes")
    if sorted(x.a11y) != sorted(y.a11y):
        out.append("a11y")
    if sorted(x.dj) != sorted(y.dj):
        out.append("data/dj-attrs")
    if sorted(x.ids) != sorted(y.ids):
        out.append("ids")
    if sorted(x.other) != sorted(y.other):
        out.append("other-attrs")
    return out


def classify_variant(py_status: str, py_out: str, rs_status: str, rs_out: str) -> str:
    """``ok``/``err`` plus the output for each engine, to one class."""
    if py_status == "ok" and rs_status == "ok":
        if py_out == rs_out:
            return IDENTICAL
        if norm_ws(py_out) == norm_ws(rs_out):
            return WHITESPACE_ONLY
        return DIFFERENT
    if py_status == "err" and rs_status == "err":
        return BOTH_ERROR
    return ERR_ONE_ENGINE


@dataclass(frozen=True)
class VariantResult:
    variant: str
    invalid_usage: bool  # a call the Python tag must reject (required args missing)
    cls: str
    aspects: tuple[str, ...]
    python_errored: bool
    rust_errored: bool


def measure_variant(
    variant: str,
    invalid_usage: bool,
    py_status: str,
    py_out: str,
    rs_status: str,
    rs_out: str,
) -> VariantResult:
    cls = classify_variant(py_status, py_out, rs_status, rs_out)
    aspects: list[str] = []
    if cls == DIFFERENT:
        aspects = differing_aspects(py_out, rs_out)
    if cls in (IDENTICAL, WHITESPACE_ONLY, DIFFERENT):
        if set(raw_markers(py_out)) != set(raw_markers(rs_out)):
            aspects.append("escaping")
    return VariantResult(
        variant, invalid_usage, cls, tuple(aspects), py_status == "err", rs_status == "err"
    )


@dataclass(frozen=True)
class TagRow:
    """One line of the baseline: what is known about one tag."""

    tag_class: str
    aspects: str  # "+"-joined, sorted: the structural aspects that differ
    errors: str  # "+"-joined, sorted "<variant>:<engine>": one engine rejected the input
    compared: int  # variants both engines rendered

    def as_fields(self) -> tuple[str, str, str, str]:
        return (self.tag_class, self.aspects, self.errors, str(self.compared))


def classify_tag(results: Sequence[VariantResult]) -> TagRow:
    valid = [r for r in results if not r.invalid_usage]
    counts = Counter(r.cls for r in valid)
    core = [r for r in valid if r.variant not in ESCAPE_VARIANTS and r.cls == DIFFERENT]
    escape_only = [r for r in valid if r.variant in ESCAPE_VARIANTS and r.cls == DIFFERENT]
    if core:
        tag_class = DIFFERENT
    elif escape_only:
        tag_class = "DIFFERENT-escape-policy-only"
    elif counts[ERR_ONE_ENGINE]:
        tag_class = "NOT-RENDERABLE-IN-ONE-ENGINE"
    elif counts[WHITESPACE_ONLY]:
        tag_class = WHITESPACE_ONLY
    elif counts[IDENTICAL]:
        tag_class = IDENTICAL
    else:
        tag_class = "UNEXERCISED"
    aspects = sorted({a for r in valid for a in r.aspects})
    errors = sorted(
        f"{r.variant}:{'python' if r.python_errored else 'rust'}"
        for r in results
        if r.cls == ERR_ONE_ENGINE
    )
    compared = sum(1 for r in results if r.cls in (IDENTICAL, WHITESPACE_ONLY, DIFFERENT))
    return TagRow(tag_class, "+".join(aspects), "+".join(errors), compared)


# --------------------------------------------------------------------------- #
# The baseline file


HEADER = ("tag", "class", "aspects", "errors", "compared")


def render_baseline(rows: Mapping[str, TagRow]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(HEADER)
    for tag in sorted(rows):
        writer.writerow((tag, *rows[tag].as_fields()))
    return buf.getvalue()


def parse_baseline(text: str) -> dict[str, TagRow]:
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if tuple(header or ()) != HEADER:
        raise ValueError("baseline header is %r, expected %r" % (header, HEADER))
    rows: dict[str, TagRow] = {}
    for fields in reader:
        if len(fields) != len(HEADER):
            raise ValueError("baseline row has %d fields: %r" % (len(fields), fields))
        tag, tag_class, aspects, errors, compared = fields
        if tag in rows:
            raise ValueError("baseline lists %s twice" % tag)
        if tag_class not in TAG_CLASSES:
            raise ValueError("baseline row %s has unknown class %r" % (tag, tag_class))
        rows[tag] = TagRow(tag_class, aspects, errors, int(compared))
    return rows


def read_baseline(path: Path) -> dict[str, TagRow]:
    return parse_baseline(path.read_text(encoding="utf-8"))


def write_baseline(path: Path, rows: Mapping[str, TagRow]) -> None:
    path.write_text(render_baseline(rows), encoding="utf-8", newline="")


# --------------------------------------------------------------------------- #
# The ratchet

WORSE = "WORSE"
BETTER = "BETTER"
NEW = "NEW"
STALE = "STALE"


@dataclass(frozen=True)
class Finding:
    tag: str
    kind: str
    message: str

    def __str__(self) -> str:
        return "%s: %s: %s" % (self.tag, self.kind, self.message)


def _tokens(joined: str) -> set[str]:
    return set(filter(None, joined.split("+")))


def _describe(row: TagRow) -> str:
    parts = [row.tag_class]
    if row.aspects:
        parts.append("aspects=" + row.aspects)
    if row.errors:
        parts.append("errors=" + row.errors)
    parts.append("compared=%d" % row.compared)
    return " ".join(parts)


_TO_REGENERATE = "run `make template-library-parity-baseline` and commit the new baseline"


def _compare_one(tag: str, now: TagRow, then: TagRow) -> Finding | None:
    if now == then:
        return None
    worse: list[str] = []
    better: list[str] = []
    rank_now, rank_then = TAG_CLASSES.index(now.tag_class), TAG_CLASSES.index(then.tag_class)
    if rank_now > rank_then:
        worse.append("class %s -> %s" % (then.tag_class, now.tag_class))
    elif rank_now < rank_then:
        better.append("class %s -> %s" % (then.tag_class, now.tag_class))
    for label, a, b in (
        ("aspects", now.aspects, then.aspects),
        ("one-engine errors", now.errors, then.errors),
    ):
        added, removed = _tokens(a) - _tokens(b), _tokens(b) - _tokens(a)
        if added:
            worse.append("new %s: %s" % (label, "+".join(sorted(added))))
        if removed:
            better.append("%s gone: %s" % (label, "+".join(sorted(removed))))
    if now.compared < then.compared:
        worse.append("coverage lost: %d -> %d variants compared" % (then.compared, now.compared))
    elif now.compared > then.compared:
        better.append("coverage grew: %d -> %d variants compared" % (then.compared, now.compared))
    detail = "baseline [%s] now [%s]" % (_describe(then), _describe(now))
    if worse:
        return Finding(
            tag,
            WORSE,
            "parity regressed (%s); %s. Fix the divergence; if it is deliberate, say so in "
            "review and %s." % ("; ".join(worse), detail, _TO_REGENERATE),
        )
    return Finding(
        tag,
        BETTER,
        "parity improved (%s); %s. %s so the gain cannot silently regress."
        % ("; ".join(better), detail, _TO_REGENERATE.capitalize()),
    )


def compare(
    now: Mapping[str, TagRow], baseline: Mapping[str, TagRow], *, partial: bool = False
) -> list[Finding]:
    """The ratchet. Every difference from the baseline is a finding.

    Worse AND better both fail: a baseline that silently lags an improvement
    would let the tag slide back to the old level unnoticed, so the baseline is
    exact, not a ceiling. ``partial`` is for a run restricted to some tags: it
    compares those and skips the new/stale checks.
    """
    findings: list[Finding] = []
    for tag in sorted(now):
        if tag not in baseline:
            if not partial:
                findings.append(
                    Finding(
                        tag,
                        NEW,
                        "has a native handler but no baseline row (now [%s]); classify it: %s."
                        % (_describe(now[tag]), _TO_REGENERATE),
                    )
                )
            continue
        found = _compare_one(tag, now[tag], baseline[tag])
        if found is not None:
            findings.append(found)
    if not partial:
        for tag in sorted(set(baseline) - set(now)):
            findings.append(
                Finding(
                    tag,
                    STALE,
                    "is in the baseline but is no longer measured (tag removed, or it lost its "
                    "native handler); %s." % _TO_REGENERATE,
                )
            )
    return findings


def summarize(rows: Mapping[str, TagRow]) -> dict[str, int]:
    counts = Counter(row.tag_class for row in rows.values())
    return {name: counts[name] for name in TAG_CLASSES if counts[name]}
