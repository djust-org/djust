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
import difflib
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

#: A tag whose harness passes fewer than this share of the parameters its
#: implementations can read is not measured, it is sampled: the class would say
#: nothing about the rest.
MIN_EXERCISED = 0.5


def is_escape_variant(name: str) -> bool:
    """``literal`` / ``safe`` and their per-parameter forms (``safe:title``)."""
    return name.split(":", 1)[0] in ESCAPE_VARIANTS


_XSS_MARKERS = ("<script>alert(1)</script>", "<img src=x onerror=alert(2)>")
_LITERAL_MARKERS = ("<i>b</i>", "<b>safe</b>")
_JS_URL = re.compile(r"""(?:href|src|action|formaction)\s*=\s*["']?\s*javascript:""")

_WS = re.compile(r"\s+")
_GAP = re.compile(r">\s+<")
_ADDR = re.compile(r"0x[0-9a-f]{6,}")
# The shapes the harness's own counting uuid4 produces (an int of a few bits):
# the hyphenated form, the 32-digit ``.hex`` and the truncated ``.hex[:n]``.
# NOT any run of hex digits: ``item-deadbeef`` and ``item-cafebabe`` differ.
_UUID = re.compile(
    r"0{8}-0{4}-0{4}-0{4}-0{9}[0-9a-f]{3}|(?<![0-9a-f])0{29}[0-9a-f]{3}(?![0-9a-f])"
    r"|(?<![0-9a-f])0{6,12}(?![0-9a-f])"
)
_TOKEN = re.compile(r"<[^>]*>|[^<]+")

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


def own_fragment(bare: str, full: str) -> str:
    """What a child tag contributed: ``full`` minus the same template without it.

    A tag that only renders inside a parent (``menu_item`` in ``dropdown_menu``)
    is compared on the markup it ADDED, so a difference in the parent's own
    markup is not charged to the child, and a child the parent hides is seen to
    contribute nothing. Token-level (tags and text runs), deterministic.
    """
    a, b = _TOKEN.findall(bare), _TOKEN.findall(full)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return "".join(
        "".join(b[j1:j2])
        for op, _i1, _i2, j1, j2 in matcher.get_opcodes()
        if op in ("insert", "replace")
    )


@dataclass(frozen=True)
class VariantResult:
    variant: str
    invalid_usage: bool  # a call the Python tag must reject (required args missing)
    cls: str
    aspects: tuple[str, ...]
    python_errored: bool
    rust_errored: bool
    #: the tag's OWN output appeared in at least one engine (a child tag: beyond its parent's)
    present: bool = True


def measure_variant(
    variant: str,
    invalid_usage: bool,
    py_status: str,
    py_out: str,
    rs_status: str,
    rs_out: str,
    present: bool = True,
) -> VariantResult:
    """``py_out`` / ``rs_out`` are what is compared: the output, or for a child tag its fragment."""
    cls = classify_variant(py_status, py_out, rs_status, rs_out)
    aspects: list[str] = []
    if cls == DIFFERENT:
        aspects = differing_aspects(py_out, rs_out)
    if cls in (IDENTICAL, WHITESPACE_ONLY, DIFFERENT):
        if set(raw_markers(py_out)) != set(raw_markers(rs_out)):
            aspects.append("escaping")
    return VariantResult(
        variant, invalid_usage, cls, tuple(aspects), py_status == "err", rs_status == "err", present
    )


@dataclass(frozen=True)
class TagRow:
    """One line of the baseline: what is known about one tag."""

    tag_class: str
    different: int = 0  # variants whose output differs
    aspects: str = ""  # "+"-joined sorted "<aspect>:<variants it differs in>"
    errors: str = ""  # "+"-joined sorted "<variant>:<engine>": one engine rejected the input
    compared: int = 0  # variants both engines rendered
    exercised: str = "1/1"  # "<parameters the harness passed>/<parameters the tag can read>"
    own_output: int = 0  # variants in which the tag's own output appeared

    def as_fields(self) -> tuple[str, ...]:
        return (
            self.tag_class,
            str(self.different),
            self.aspects,
            self.errors,
            str(self.compared),
            self.exercised,
            str(self.own_output),
        )

    @classmethod
    def from_fields(cls, fields: Sequence[str]) -> "TagRow":
        tag_class, different, aspects, errors, compared, exercised, own_output = fields
        return cls(
            tag_class, int(different), aspects, errors, int(compared), exercised, int(own_output)
        )

    def passed_readable(self) -> tuple[int, int]:
        passed, readable = self.exercised.split("/")
        return int(passed), int(readable)


def classify_tag(results: Sequence[VariantResult], passed: int = 1, readable: int = 1) -> TagRow:
    """Aggregate one tag's variants.

    ``passed`` / ``readable``: how many distinct parameters the generated calls
    pass, and how many the Python tag and the native handler can read between
    them. A tag whose own output never appeared, or that is exercised on under
    ``MIN_EXERCISED`` of what it reads, is ``UNEXERCISED``: neither
    ``IDENTICAL`` nor ``DIFFERENT`` would be a statement about the tag.
    """
    valid = [r for r in results if not r.invalid_usage]
    counts = Counter(r.cls for r in valid)
    core = [r for r in valid if not is_escape_variant(r.variant) and r.cls == DIFFERENT]
    escape_only = [r for r in valid if is_escape_variant(r.variant) and r.cls == DIFFERENT]
    own_output = sum(1 for r in results if r.present)
    unexercised = own_output == 0 or bool(readable and passed < MIN_EXERCISED * readable)
    if core:
        tag_class = DIFFERENT  # one proven difference is a statement about the tag
    elif unexercised:
        tag_class = "UNEXERCISED"  # but "no difference found" is not, if little was tried
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
    aspect_counts = Counter(a for r in valid for a in set(r.aspects))
    aspects = "+".join("%s:%d" % (a, aspect_counts[a]) for a in sorted(aspect_counts))
    errors = sorted(
        f"{r.variant}:{'python' if r.python_errored else 'rust'}"
        for r in results
        if r.cls == ERR_ONE_ENGINE
    )
    compared = sum(1 for r in results if r.cls in (IDENTICAL, WHITESPACE_ONLY, DIFFERENT))
    return TagRow(
        tag_class,
        counts[DIFFERENT],
        aspects,
        "+".join(errors),
        compared,
        "%d/%d" % (passed, readable),
        own_output,
    )


# --------------------------------------------------------------------------- #
# The baseline file


HEADER = ("tag", "class", "different", "aspects", "errors", "compared", "exercised", "own_output")


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
        tag = fields[0]
        if tag in rows:
            raise ValueError("baseline lists %s twice" % tag)
        if fields[1] not in TAG_CLASSES:
            raise ValueError("baseline row %s has unknown class %r" % (tag, fields[1]))
        rows[tag] = TagRow.from_fields(fields[1:])
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


def _counts(joined: str) -> dict[str, int]:
    out = {}
    for token in filter(None, joined.split("+")):
        name, _, n = token.rpartition(":")
        out[name] = int(n)
    return out


def _describe(row: TagRow) -> str:
    parts = [row.tag_class, "different=%d" % row.different]
    if row.aspects:
        parts.append("aspects=" + row.aspects)
    if row.errors:
        parts.append("errors=" + row.errors)
    parts.append(
        "compared=%d exercised=%s own_output=%d" % (row.compared, row.exercised, row.own_output)
    )
    return " ".join(parts)


_TO_REGENERATE = "run `make template-library-parity-baseline` and commit the new baseline"


def _compare_one(tag: str, now: TagRow, then: TagRow) -> Finding | None:
    if now == then:
        return None
    worse: list[str] = []
    better: list[str] = []

    def number(label: str, a: int, b: int, higher_is_worse: bool) -> None:
        if a == b:
            return
        (worse if (a > b) == higher_is_worse else better).append("%s %d -> %d" % (label, b, a))

    rank_now, rank_then = TAG_CLASSES.index(now.tag_class), TAG_CLASSES.index(then.tag_class)
    if rank_now != rank_then:
        (worse if rank_now > rank_then else better).append(
            "class %s -> %s" % (then.tag_class, now.tag_class)
        )
    number("differing variants", now.different, then.different, True)
    c_now, c_then = _counts(now.aspects), _counts(then.aspects)
    for aspect in sorted(set(c_now) | set(c_then)):
        a, b = c_now.get(aspect, 0), c_then.get(aspect, 0)
        if a != b:
            number("variants differing in %s" % aspect, a, b, True)
    added, removed = (
        _tokens(now.errors) - _tokens(then.errors),
        _tokens(then.errors) - _tokens(now.errors),
    )
    if added:
        worse.append("new one-engine errors: %s" % "+".join(sorted(added)))
    if removed:
        better.append("one-engine errors gone: %s" % "+".join(sorted(removed)))
    number("variants compared", now.compared, then.compared, False)
    number("variants showing the tag's own output", now.own_output, then.own_output, False)
    (p_now, r_now), (p_then, r_then) = now.passed_readable(), then.passed_readable()
    number("parameters exercised", p_now, p_then, False)
    number("parameters the tag can read", r_now, r_then, True)
    detail = "baseline [%s] now [%s]" % (_describe(then), _describe(now))
    if worse:
        return Finding(
            tag,
            WORSE,
            "parity regressed (%s); %s. Fix the divergence or restore the coverage; if it is "
            "deliberate, say so in review and %s." % ("; ".join(worse), detail, _TO_REGENERATE),
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
