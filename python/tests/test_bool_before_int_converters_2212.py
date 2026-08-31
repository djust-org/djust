"""No Python->value converter may check `i64` before `bool` (#2212).

PyO3 0.29 extracts a Python ``True`` as ``i64`` ``1`` (its own ``test_i64_bool``
asserts this, at ``src/conversions/std/num.rs:1221``). So in any function that
tries ``i64`` first, the ``bool`` arm is **dead code** and a bool silently
becomes an integer.

Checked structurally rather than behaviourally because the failure is invisible
by construction: a dead ``if let`` arm is not a compile error, and clippy does
not flag it either (the arms have different types, so it is not
``unreachable_patterns`` — verified against a mutated build).

**This guard is the only protection for five of the six converters.** A review
gate-off re-introduced the #2211 bug in ``python_to_value`` and the entire suite
stayed green — 10,278 passed, 0 failed. Only ``serialize_python_value`` has
behavioural coverage (``test_template_serialization.py::TestBoolRoundTrip2212``).
That is why the parser below is defensive rather than quick: when a guard is the
sole protection, its own defects are the real risk.

Three parser defects found in review and fixed here:

* ``pub(crate) fn`` / ``pub(super) fn`` / ``const fn`` / ``unsafe fn`` were not
  matched at all — three such functions already exist in the tree, and a
  synthetic ``pub(crate) fn`` carrying the exact #2212 bug produced **zero
  rows**, leaving the guard green on a real violation.
* Bodies ended at the next signature match rather than at a balanced brace, so
  renaming a function to ``pub(crate)`` absorbed its body into the *preceding*
  one. The count held at six and the self-check passed while protection had
  silently moved to the wrong function.
* Comments were not stripped, so a correct converter whose comment merely
  *mentions* ``extract::<i64>()`` was reported as a violation. Not hypothetical:
  #2212's own fix adds a comment block directly above the bool arm.
"""

from __future__ import annotations

import functools
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FN_START = re.compile(
    r"^\s*(?:pub\s*(?:\([^)]*\)\s*)?)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
    r'(?:extern\s+"[^"]*"\s+)?fn\s+(\w+)'
)

LINE_COMMENT = re.compile(r"//.*$", re.M)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# The exact converters extracting both types, as of #2212. Pinned as a SET, not
# a floor (#1125): a floor tolerates one silently disappearing, which is exactly
# the failure the brace-balancing fix addresses.
EXPECTED = {
    ("crates/djust_core/src/lib.rs", "extract"),
    # The dict-KEY converter (#2339). Added here because this sweep flagged it
    # the moment it landed — which is the net working: a Python `bool` IS an
    # `int`, so an `i64`-first arm order would swallow `True` and
    # `{{ {True: 1} }}` would print `{1: 1}`.
    ("crates/djust_core/src/lib.rs", "py_object_key"),
    # The `Value::Encoded` predicate (#2477/#2489). It probes every arm of
    # `impl FromPyObject for Value` in that impl's own order, so the same
    # bool-before-i64 rule applies to it for the same reason: a Python `bool`
    # IS an `int`, and an `i64`-first probe would answer "not a scalar" for
    # `True` and let the normalizer carry it. Flagged here the moment it
    # landed, which is the net working.
    ("crates/djust_core/src/lib.rs", "crosses_as_encoded"),
    ("crates/djust_live/src/lib.rs", "python_to_json_value"),
    ("crates/djust_live/src/lib.rs", "python_to_value"),
    ("crates/djust_live/src/lib.rs", "serialize_python_value"),
    ("crates/djust_live/src/lib.rs", "python_to_json"),
    ("crates/djust_live/src/model_serializer.rs", "python_to_json"),
}


def _strip_comments(text: str) -> str:
    """Comments must not decide the verdict — only real code does."""
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))


def _body_from(lines: list[str], start: int) -> str:
    """The function body, ended by BRACE BALANCE rather than the next signature.

    Ending at the next regex match mis-attributes a body whenever the following
    signature is a form the regex misses — which is how a renamed function's
    arms were silently absorbed into its predecessor.
    """
    depth, started, out = 0, False, []
    for line in lines[start:]:
        stripped = _strip_comments(line)
        out.append(stripped)
        depth += stripped.count("{") - stripped.count("}")
        if "{" in stripped:
            started = True
        if started and depth <= 0:
            break
    return "\n".join(out)


@functools.cache
def _converters() -> tuple[tuple[str, str, int, bool], ...]:
    """Every Rust fn extracting BOTH i64 and bool, with whether bool comes first."""
    found = []
    for path in sorted(ROOT.glob("crates/**/*.rs")):
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            m = FN_START.match(line)
            if not m:
                continue
            body = _body_from(lines, i)
            if "extract::<i64>()" in body and "extract::<bool>()" in body:
                bool_first = body.index("extract::<bool>()") < body.index("extract::<i64>()")
                found.append((str(path.relative_to(ROOT)), m.group(1), i + 1, bool_first))
    return tuple(found)


def test_the_sweep_finds_exactly_the_known_converters() -> None:
    """Guard the guard, by SET rather than by count.

    A floor (`>= 5`) passes while a converter silently drops out — demonstrated
    in review, where a legal `pub(crate)` rename removed one and the count still
    read six. An exact set catches a disappearance AND an unexpected new
    converter, which is the thing actually worth knowing about.
    """
    actual = {(p, n) for p, n, _, _ in _converters()}
    assert actual == EXPECTED, (
        "the set of converters extracting both i64 and bool changed.\n"
        f"  missing (parser broke, or the fn was removed): {EXPECTED - actual}\n"
        f"  unexpected (a NEW converter — check its arm order, then add it here): "
        f"{actual - EXPECTED}"
    )


def test_the_parser_matches_non_plain_signatures() -> None:
    """`pub(crate) fn` and friends must be matched.

    Not matching them is a SILENT miss: the converter never appears at all, so a
    real violation inside one leaves the suite green.
    """
    for sig in (
        "fn a() {",
        "pub fn b() {",
        "    pub(crate) fn c() {",
        "pub(super) fn d() {",
        "pub(in crate::x) fn e() {",
        "const fn f() {",
        "pub const fn g() {",
        "async fn h() {",
        "pub async fn i() {",
        "unsafe fn j() {",
        'pub extern "C" fn k() {',
    ):
        assert FN_START.match(sig), f"signature not matched: {sig.strip()!r}"


def test_a_comment_mentioning_the_wrong_order_is_not_a_violation() -> None:
    """Comments must not decide the verdict.

    #2212's own fix puts a long comment directly above the bool arm; had it
    spelled the literal `extract::<i64>()`, an un-stripped parser would report
    correct code as broken.
    """
    body = _strip_comments(
        "\n".join(
            [
                "fn documented_but_correct() {",
                "    // Checked BEFORE extract::<i64>() on purpose.",
                "    if let Ok(b) = value.extract::<bool>() { }",
                "    if let Ok(i) = value.extract::<i64>() { }",
                "}",
            ]
        )
    )
    assert body.index("extract::<bool>()") < body.index("extract::<i64>()")


@pytest.mark.parametrize(
    "path,name,line",
    [(p, n, ln) for p, n, ln, _ in _converters()],
    ids=[f"{Path(p).name}::{n}" for p, n, _, _ in _converters()],
)
def test_bool_is_checked_before_i64(path: str, name: str, line: int) -> None:
    match = [c for c in _converters() if (c[0], c[1], c[2]) == (path, name, line)]
    assert match, f"{name} vanished from the sweep"
    assert match[0][3], (
        f"{path}:{line} `{name}` extracts i64 BEFORE bool.\n"
        f"PyO3 extracts a Python True as i64 1, so the bool arm below it is dead "
        f"code and a bool silently becomes an integer. Move the bool check above "
        f"the i64 check (#2212)."
    )
