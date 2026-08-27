"""No ``type_name ==`` special case may sit below an extract that swallows it (#2214).

The general rule that #2212 was one instance of
--------------------------------------------------

#2212: ``extract::<i64>()`` above an ``extract::<bool>()`` arm made the bool arm
dead, because PyO3 extracts a Python ``True`` as ``i64`` ``1``.

#2214: ``extract::<f64>()`` above ``if type_name == "Decimal"`` makes the Decimal
branch dead, because PyO3's ``f64`` extraction goes through ``PyFloat_AsDouble``,
which honours ``Decimal.__float__``.

Same shape, different type pair — *a permissive extraction placed above a
narrower special case*. Neither is a compile error (the arms have different
types, so not ``unreachable_patterns``) and clippy flags neither, verified
against mutated builds. So the class is invisible to every tool the repo runs,
and the only thing that catches it is a structural sweep.

``test_bool_before_int_converters_2212.py`` sweeps for the i64/bool pair
specifically and is blind to the Decimal one. This file is the generalisation
the #2214 issue asked for, and #2212's guard stays because the two answer
different questions: that one is about which of two EXTRACTS comes first, this
one about an extract shadowing a NAMED TYPE.

Why the capture table is measured rather than declared
------------------------------------------------------
Deciding whether ``extract::<f64>()`` swallows a ``Decimal`` means knowing which
Python types PyO3's extraction accepts. Hardcoding that as a comment is how the
original bug shipped: the code *says* Decimal converts to a string, and it does
not (#1867).

So the table below is **built by running Python** — for each type named in the
Rust source, an instance is constructed and ``float()`` / ``int()`` / ``str()``
are tried. The guard therefore re-derives its own premise on every run, and a
future Python or PyO3 that changes ``Decimal.__float__`` would move the table
rather than silently invalidate a comment (#1459 empirical canary).
"""

from __future__ import annotations

import functools
import operator
import re
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

FN_START = re.compile(
    r"^\s*(?:pub\s*(?:\([^)]*\)\s*)?)?(?:const\s+)?(?:async\s+)?(?:unsafe\s+)?"
    r'(?:extern\s+"[^"]*"\s+)?fn\s+(\w+)'
)
LINE_COMMENT = re.compile(r"//.*$", re.M)
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

TYPE_NAME_CHECK = re.compile(r'type_name\s*==\s*"([A-Za-z_][A-Za-z0-9_]*)"')
EXTRACT_CALL = re.compile(r"extract::<([A-Za-z0-9_]+)>\(\)")

#: How to build one instance of each type the Rust source names, so the capture
#: table can be measured. A type named in Rust but absent here fails loudly
#: rather than being skipped — an unknown type is exactly when the guard must
#: not quietly pass.
SPECIMENS = {
    "Decimal": lambda: Decimal("19.99"),
    "UUID": lambda: __import__("uuid").UUID(int=1),
    "datetime": lambda: __import__("datetime").datetime(2026, 8, 22, 12, 0),
    "date": lambda: __import__("datetime").date(2026, 8, 22),
    "time": lambda: __import__("datetime").time(12, 0),
    "QuerySet": None,  # needs Django app state; no numeric protocol either way
    # Not a Rust type_name — present so the capture table can assert the
    # #2212 pair (a Python True IS i64-extractable) alongside the #2214 one.
    "bool_specimen": lambda: True,
}

#: Rust extract targets, and the Python coercion PyO3 performs for each.
#:
#: Two of these are easy to get wrong, and getting either wrong makes the guard
#: cry wolf on branches that are perfectly reachable — which is worse than not
#: having it, because a false alarm trains people to ignore the real one.
#:
#: * ``i64`` is ``operator.index``, **not** ``int()``. PyO3 goes through
#:   ``__index__``; ``int()`` also accepts ``__int__``/``__trunc__``. The
#:   difference is not academic: ``int(Decimal("19.99"))`` succeeds (19) and
#:   ``int(UUID(int=1))`` succeeds (1), while ``operator.index`` rejects both.
#:   The first draft of this table used ``int`` and reported two dead branches
#:   that are not dead.
#: * ``String`` is **not** ``str()``. PyO3 requires an actual ``str`` and never
#:   calls ``__str__``, which is why a Decimal survives an
#:   ``extract::<String>()`` above it but not an ``extract::<f64>()``.
COERCIONS = {
    "f64": float,
    "i64": operator.index,
    "bool": None,  # strict: PyO3 casts to PyBool, no __bool__ coercion
    "String": None,  # strict: requires a real str
}


def _strip_comments(text: str) -> str:
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", text))


def _body_from(lines: list[str], start: int) -> str:
    """Body ended by BRACE BALANCE, not by the next signature match.

    Lifted from the #2212 guard, where ending at the next regex match
    mis-attributed a body whenever the following signature was a form the regex
    missed (#2213 review).
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
def _swallowed() -> tuple[tuple[str, str, str, str], ...]:
    """Every (file, fn, type_name, extract) where the extract shadows the check."""
    hits = []
    for path in sorted(ROOT.glob("crates/**/*.rs")):
        if "tests" in path.parts:
            continue
        lines = path.read_text().splitlines()
        for i, line in enumerate(lines):
            m = FN_START.match(line)
            if not m:
                continue
            body = _body_from(lines, i)
            extracts = [(mm.start(), mm.group(1)) for mm in EXTRACT_CALL.finditer(body)]
            if not extracts:
                continue
            for tm in TYPE_NAME_CHECK.finditer(body):
                type_name = tm.group(1)
                for pos, target in extracts:
                    if pos >= tm.start():
                        continue  # the extract is BELOW the check — no shadow
                    if _captures(target, type_name):
                        hits.append((str(path.relative_to(ROOT)), m.group(1), type_name, target))
    return tuple(hits)


def _captures(extract_target: str, type_name: str) -> bool:
    """Would ``extract::<target>()`` succeed on an instance of ``type_name``?

    Measured, not declared. Returns False for anything unrepresentable here so
    an exotic type cannot make the guard fabricate a violation; the
    ``SPECIMENS`` completeness test below is what stops that from becoming a
    silent skip.
    """
    if extract_target not in COERCIONS:
        return False
    coerce = COERCIONS[extract_target]
    if coerce is None:
        return False  # strict extraction: no coercion protocol to exploit
    build = SPECIMENS.get(type_name)
    if build is None:
        return False
    try:
        coerce(build())
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# The guard's own premises, measured.
# ---------------------------------------------------------------------------


def test_every_type_named_in_rust_has_a_specimen() -> None:
    """An unnamed type must fail loudly, not be skipped.

    Without this, adding ``type_name == "Money"`` to the Rust source would leave
    the sweep silently unable to judge it — the guard would pass while the new
    special case sat dead below an ``extract::<f64>()``.
    """
    named = set()
    for path in ROOT.glob("crates/**/*.rs"):
        if "tests" in path.parts:
            continue
        named.update(TYPE_NAME_CHECK.findall(path.read_text()))
    missing = named - set(SPECIMENS)
    assert not missing, (
        f"these types are special-cased in Rust but have no specimen here, so the "
        f"guard cannot judge them: {sorted(missing)}. Add one to SPECIMENS."
    )


def test_the_measured_capture_table_matches_what_pyo3_does() -> None:
    """The premise the whole guard rests on, re-derived rather than trusted.

    ``float(Decimal(...))`` succeeding is *why* #2214 exists; if a future Python
    changed it, this guard would silently stop protecting anything. Asserting it
    here means that change breaks the test rather than the protection.
    """
    assert _captures("f64", "Decimal"), (
        "float(Decimal) no longer succeeds — the premise of #2214. Re-check "
        "whether extract::<f64>() still swallows a Decimal before relaxing "
        "anything here."
    )
    assert not _captures("f64", "UUID"), "UUID is not float-convertible"
    assert not _captures("f64", "datetime"), "datetime is not float-convertible"
    # The two that the first draft of the table got wrong. `int()` accepts both
    # — `int(Decimal("19.99"))` is 19 and `int(UUID(int=1))` is 1 — but PyO3
    # goes through `__index__`, which rejects both. Modelling i64 as `int()`
    # reported two reachable branches as dead.
    assert not _captures("i64", "Decimal"), (
        "PyO3's i64 extraction uses __index__, which a Decimal does not "
        "implement — modelling it as int() is what produced a false positive"
    )
    assert not _captures("i64", "UUID"), (
        "same: int(UUID) succeeds via __int__, operator.index(UUID) does not"
    )
    # And the pair #2212 is about, which is observable end to end: PyO3 really
    # does extract a Python True as i64 1.
    assert _captures("i64", "bool_specimen"), "bool must still be i64-extractable"
    assert not _captures("String", "Decimal"), (
        "PyO3's String extraction must stay strict — it does not call __str__, "
        "which is why a Decimal survives an extract::<String>() above it"
    )
    assert not _captures("bool", "Decimal"), "bool extraction is a strict PyBool cast"


# ---------------------------------------------------------------------------
# The sweep.
# ---------------------------------------------------------------------------


def test_no_special_case_sits_below_an_extract_that_swallows_it() -> None:
    """No dead special cases. The `xfail` this carried is gone (#2214 fixed).

    It was `strict=True` precisely so fixing the bug would fail the build rather
    than leave a stale marker asserting a defect that no longer exists — and it
    did, as an `XPASS(strict)`, which is what sent the fix back here.
    """
    swallowed = _swallowed()
    assert not swallowed, "\n".join(
        [f"{len(swallowed)} dead special-case branch(es):"]
        + [
            f'  {p}::{fn} — `type_name == "{t}"` is unreachable because '
            f"extract::<{x}>() above it accepts a {t}"
            for p, fn, t, x in swallowed
        ]
    )


def test_the_sweep_currently_finds_exactly_the_known_dead_branch() -> None:
    """Pin what IS dead today, as a set (#1125).

    The xfail above says "something is wrong"; this says *what*, so a second
    instance appearing does not hide behind the first — an xfail is satisfied by
    one failure and would stay green if a new dead branch joined it.
    """
    actual = {(p, fn, t, x) for p, fn, t, x in _swallowed()}
    assert actual == set(), (
        "a dead special-case branch appeared.\n"
        f"  found: {sorted(actual)}\n"
        "The set has been empty since #2214. Anything here is a fresh instance "
        "of the #2212/#2214 class — a `type_name ==` special case placed below "
        "an `extract::<T>()` that accepts the same type, which neither rustc nor "
        "clippy can see — and wants its own issue."
    )


# ---------------------------------------------------------------------------
# The behavioural half: what the dead branch actually costs.
# ---------------------------------------------------------------------------


def test_the_decimal_precision_loss_is_fixed() -> None:
    """The flip side of the pin this used to be (#2214).

    It asserted the CURRENT (wrong) values on purpose — an xfail is satisfied by
    any failure, including an unrelated one, while pinning the exact bytes means
    a fix arrives as a diff that says what changed. This is that diff: `float`
    became `str`, and the 29-digit value stopped collapsing to
    `1.2345678901234567e+19`.

    Behavioural coverage lives in `test_decimal_precision_2214.py`, which is a
    differential against real Django. This stays because it is the counterpart
    of the structural sweep above, in the same file, and reads as the before/after
    of the same finding.
    """
    import json

    import django
    from django.conf import settings

    if not settings.configured:  # pragma: no cover - the suite configures it
        pytest.skip("django settings not configured")
    django.setup()

    from djust._rust import serialize_context

    out = serialize_context(
        {
            "price": Decimal("19.99"),
            "huge": Decimal("12345678901234567890.123456789"),
            "uid": __import__("uuid").UUID(int=1),
        }
    )
    parsed = json.loads(out) if isinstance(out, (str, bytes)) else out

    # A JSON string now, matching `DjangoJSONEncoder`. `19.99` read the same
    # either way — the readable case was never the damaging one, which is why
    # "a price arrives as a float" both understated and overstated the bug.
    assert isinstance(parsed["price"], str)
    assert parsed["price"] == "19.99"

    # The damage, gone: 29 significant digits do not fit in a binary double, so
    # this used to arrive as 1.2345678901234567e+19.
    assert isinstance(parsed["huge"], str)
    assert parsed["huge"] == "12345678901234567890.123456789"

    # UUID shared the branch and was never affected — not float-convertible, so
    # it always reached the check. Unchanged by the split.
    assert parsed["uid"] == str(__import__("uuid").UUID(int=1))
    assert parsed["huge"] != Decimal("12345678901234567890.123456789")

    # UUID reaches the same branch correctly, because it is not
    # float-convertible — which is what makes the Decimal case a shadowing bug
    # rather than a missing branch.
    assert parsed["uid"] == "00000000-0000-0000-0000-000000000001"
