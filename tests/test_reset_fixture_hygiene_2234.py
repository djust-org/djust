"""Test-suite hygiene guards for global-state leaks (#2234).

Filed after a leak shipped in the PR that added the number-localization tests:
its reset fixture called ``translation.deactivate_all()``, which leaves
``get_language()`` returning ``None`` so ``get_format`` falls back to
``global_settings`` — where ``NUMBER_GROUPING`` is ``0``. Number grouping was
silently off for every later test in that worker, and it poisoned a test two
PRs later (#2233).

A leaky reset fixture is doubly quiet: it is written to PREVENT pollution, so
nobody suspects the cleanup. Hence structural guards rather than trusting
review.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOTS = ("tests", "python/tests", "python/djust/tests")
SELF = Path(__file__).name


def _test_files():
    for root in TEST_ROOTS:
        for p in sorted((ROOT / root).rglob("*.py")):
            if p.name != SELF:
                yield p


# ---------------------------------------------------------------------------
# The behaviour that makes the guards worth having.
# ---------------------------------------------------------------------------


def test_deactivate_all_really_does_break_number_grouping():
    """Pin the premise, so the guard below cannot outlive its reason.

    A rule banning a function is only as good as the claim about what that
    function does. If a future Django makes ``deactivate_all()`` harmless, this
    fails and the ban should be reconsidered — rather than the ban quietly
    persisting as folklore.
    """
    from django.test import override_settings
    from django.utils import formats, translation

    with override_settings(USE_I18N=True, LANGUAGE_CODE="en-us", USE_THOUSAND_SEPARATOR=True):
        try:
            translation.deactivate()
            baseline = formats.get_format("NUMBER_GROUPING")

            translation.deactivate_all()
            after_all = formats.get_format("NUMBER_GROUPING")

            translation.deactivate()
            restored = formats.get_format("NUMBER_GROUPING")
        finally:
            translation.deactivate()

    assert baseline != 0, "expected real grouping under a normal active language"
    assert after_all == 0, (
        "deactivate_all() no longer zeroes NUMBER_GROUPING — the premise of the "
        "ban below has changed; re-evaluate it rather than keeping the rule"
    )
    assert restored == baseline, "deactivate() must restore the settings default"


def test_reset_djust_globals_normalises_the_active_language_and_timezone():
    """The systemic cure: a test that forgets cannot poison its neighbours.

    Fixtures in individual files are the belt; this is the braces, and it is
    what makes the class non-recurring rather than fixed-once.
    """
    from django.conf import settings
    from django.utils import timezone, translation

    from djust.test_isolation import reset_djust_globals

    translation.activate("de")
    timezone.activate("Asia/Tokyo")
    assert translation.get_language() == "de"

    reset_djust_globals()

    assert translation.get_language() == settings.LANGUAGE_CODE
    assert timezone.get_current_timezone_name() == settings.TIME_ZONE


def test_reset_djust_globals_also_undoes_deactivate_all():
    # The specific shape that leaked, not just the obvious `activate()` case.
    from django.conf import settings
    from django.utils import translation

    from djust.test_isolation import reset_djust_globals

    translation.deactivate_all()
    assert translation.get_language() is None

    reset_djust_globals()
    assert translation.get_language() == settings.LANGUAGE_CODE


# ---------------------------------------------------------------------------
# Structural guards.
# ---------------------------------------------------------------------------


# Matches the bare name, so BOTH aliasing shapes are caught:
#
#     from django.utils.translation import deactivate_all;  deactivate_all()
#     from django.utils import translation as trans;        trans.deactivate_all()
#
# An earlier version anchored on the literal `translation.deactivate_all(` and
# both of those passed clean — the first being the shape most likely to
# reintroduce #2222, since it is what an editor auto-import produces.
DEACTIVATE_ALL = re.compile(r"\bdeactivate_all\s*\(")


def _code_only(text: str) -> str:
    """Return only the CODE tokens, dropping comments and string literals.

    Uses ``tokenize`` rather than scanning lines. The hand-rolled scanner this
    replaces had the failure mode hand-rolled parsers in this repo keep having
    (#2213): it tracked a triple-quote delimiter across lines and therefore
    treated everything between a docstring's `\"\"\"` and the next occurrence as
    prose — which, whenever a file's docstrings were counted oddly, blanked
    **real code**. The Stage 15 re-review measured 1318 live code lines blinded
    across 42 files and proved it end to end with a `deactivate_all()` call
    that passed the guard clean.

    It also fixed the wrong half of the false-positive: a `#` comment *after*
    real code on the same line still tripped the old line-oriented version,
    because that line does not start with `#`.

    ``tokenize`` knows the difference between a delimiter and a string, handles
    CRLF, f-strings, raw strings and nested quotes, and cannot desynchronise.
    A file that does not parse is returned unchanged — a syntax error is
    somebody else's failure, and silently exempting it would be the same
    blindness in a new costume.
    """
    import io
    import tokenize as _tok

    try:
        toks = list(_tok.generate_tokens(io.StringIO(text).readline))
    except (_tok.TokenError, SyntaxError, IndentationError):
        return text
    # Python 3.12 (PEP 701) splits an f-string into FSTRING_START /
    # FSTRING_MIDDLE / FSTRING_END rather than one STRING token, so the literal
    # text inside it is NOT type STRING and leaks through a naive filter. The
    # canary below caught this on the first run, which is the entire argument
    # for having a canary rather than trusting that "tokenize handles strings".
    # `getattr` because those names do not exist before 3.12.
    drop = {_tok.COMMENT, _tok.STRING, _tok.NL}
    for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
        tok_type = getattr(_tok, name, None)
        if tok_type is not None:
            drop.add(tok_type)
    return " ".join(t.string for t in toks if t.type not in drop)


def test_no_test_calls_deactivate_all():
    """`deactivate()`, never `deactivate_all()`, in a test.

    Comments and docstrings are excluded so a file may still EXPLAIN the
    difference — which several do, and which is worth keeping.
    """
    offenders = []
    for p in _test_files():
        if DEACTIVATE_ALL.search(_code_only(p.read_text())):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], (
        "these call deactivate_all(), which leaves get_language() as None and "
        "zeroes NUMBER_GROUPING for the rest of the worker. Use deactivate(), "
        f"which restores LANGUAGE_CODE: {offenders}"
    )


def test_the_guard_catches_both_aliasing_shapes_and_spares_prose():
    """Empirical canary (#1459) — a lint reporting nothing looks like a broken one.

    Each shape is checked against the real matcher rather than trusted from the
    regex's appearance. Both aliased forms passed the earliest version, and the
    trailing-comment shape passed the second.
    """
    caught = [
        "translation.deactivate_all()",
        "deactivate_all()",
        "trans.deactivate_all()",
        "    deactivate_all()",
        "x = 1; deactivate_all()",
        "foo()  # trailing comment\ndeactivate_all()",
        # A `#` AFTER real code on the same line: the line-oriented version
        # blanked nothing here (it does not start with `#`) yet still matched
        # the text in the comment.
        "deactivate_all()  # a trailing comment",
        # Real code following a multi-line docstring — the shape that proved
        # the hand-rolled scanner was blinding live lines.
        '"""doc\nabout deactivate_all\n"""\ndeactivate_all()',
    ]
    for src in caught:
        assert DEACTIVATE_ALL.search(_code_only(src)), f"missed: {src!r}"

    spared = [
        "# never call translation.deactivate_all() here",
        "x = 1  # mentions deactivate_all() in a trailing comment",
        'MSG = "deactivate_all() is wrong"',
        'MSG = f"do not use deactivate_all() here"',
        'MSG = r"deactivate_all()"',
        "    # deactivate_all() leaks",
        '"""' + "Explains why deactivate_all() is wrong." + '"""',
        '"""' + "\nProse about deactivate_all().\n" + '"""',
    ]
    for src in spared:
        assert not DEACTIVATE_ALL.search(_code_only(src)), f"false alarm: {src!r}"


def test_every_override_settings_enable_has_a_matching_disable():
    """``enable()`` without ``disable()`` leaks the override for the session.

    Found a real one: ``tests/unit/test_live_view.py`` enabled a ``TEMPLATES``
    override pointing at a pytest tmp directory and never disabled it, so the
    template loader kept pointing at a **deleted** path for the rest of the
    worker. The ``settings`` fixture in that test's signature did not undo it —
    it restores only the settings it was itself asked to change.

    Counting is deliberately crude: it cannot tell WHICH disable pairs with
    which enable, only that a file has no unbalanced enable. That is enough to
    catch the omission, and a false alarm is a five-second read.
    """
    offenders = []
    for p in _test_files():
        t = p.read_text()
        enables = len(re.findall(r"\.enable\(\)", t))
        # `addfinalizer(ctx.disable)` and `ctx.disable()` both count.
        disables = len(re.findall(r"\.disable\b", t))
        if enables > disables:
            offenders.append(f"{p.relative_to(ROOT)} (enable={enables}, disable={disables})")
    assert offenders == [], (
        "these enable an override_settings context without a matching disable, "
        f"which leaks the override for the rest of the worker: {offenders}"
    )


@pytest.mark.parametrize("call", ["translation.activate", "timezone.activate"])
def test_activating_a_locale_or_zone_is_paired_with_a_reset(call):
    """A file that activates must also reset — belt as well as braces.

    ``reset_djust_globals`` now normalises both thread-locals before every
    test, so a forgotten reset can no longer poison a NEIGHBOUR. It can still
    poison a LATER TEST IN THE SAME FILE, because the autouse fixture runs
    per-test and a mid-test activate persists to the end of that test only —
    but an activate in a module-scoped fixture or at import time outlives it.
    Cheap to require, and it keeps the intent visible at the call site.
    """
    act = re.compile(rf"\b{re.escape(call)}\s*\(")
    reset = re.compile(r"\b(?:translation|timezone|dj_timezone)\.deactivate\w*\s*\(")
    offenders = []
    for p in _test_files():
        t = p.read_text()
        # Files import these under several names; normalise before matching.
        t_norm = t.replace("dj_timezone.", "timezone.")
        if act.search(t_norm) and not reset.search(t_norm):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], (
        f"these call {call}() with no deactivate anywhere in the file: {offenders}"
    )


def test_every_scanned_test_root_actually_has_the_autouse_reset():
    """The guards scan three roots; all three must be protected by one (#2234).

    `python/tests/` had no conftest at all, so the autouse reset wired into the
    other two roots since #1883 never ran for its 133 files — the guards
    *scanned* a root that nothing *protected*. Caught by the Stage 11 review of
    the PR that added those guards.

    Pins the invariant rather than the fix: adding a fourth scanned root
    without a conftest fails here.
    """
    # A substring check is not enough — the Stage 15 re-review showed a conftest
    # whose only mention is `# TODO: add the reset_djust_globals fixture` passes
    # it while the root stays unprotected. That is the decorative-pin shape
    # (#1859). Require an autouse fixture AND an actual call.
    missing = []
    for root in TEST_ROOTS:
        conftest = ROOT / root / "conftest.py"
        if not conftest.exists():
            missing.append(f"{root} (no conftest.py)")
            continue
        text = conftest.read_text()
        if "autouse=True" not in text:
            missing.append(f"{root} (conftest has no autouse fixture)")
        elif "reset_djust_globals()" not in text:
            missing.append(f"{root} (conftest never CALLS reset_djust_globals)")
    assert missing == [], (
        "these roots are scanned by the guards above but have no autouse "
        f"reset_djust_globals fixture, so nothing protects them: {missing}"
    )
