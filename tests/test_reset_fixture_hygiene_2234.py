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
COMMENT_LINE = re.compile(r"^\s*#")
_DELIMS = ('"""', "'''")


def _strip_prose(text: str) -> str:
    """Blank out comments and triple-quoted blocks, preserving line count.

    The earlier version excluded only `#` lines, so a DOCSTRING naming
    deactivate_all tripped the guard — and the files that legitimately explain
    the difference survived only because they happened to write it without the
    `translation.` prefix. That is luck, not an exemption, and the stated rule
    ("a file may still EXPLAIN the difference") was not the rule enforced.
    """
    out, delim = [], None
    for line in text.splitlines():
        stripped = line.strip()
        if delim is not None:
            out.append("")
            if delim in stripped:
                delim = None
            continue
        if COMMENT_LINE.match(line):
            out.append("")
            continue
        opened = next((d for d in _DELIMS if stripped.startswith(d)), None)
        if opened is not None:
            # A one-line docstring opens and closes on the same line.
            if not (len(stripped) > 2 * len(opened) and stripped.endswith(opened)):
                delim = opened
            line = ""
        out.append(line)
    return "\n".join(out)


def test_no_test_calls_deactivate_all():
    """`deactivate()`, never `deactivate_all()`, in a test.

    Comments and docstrings are excluded so a file may still EXPLAIN the
    difference — which several do, and which is worth keeping.
    """
    offenders = []
    for p in _test_files():
        if DEACTIVATE_ALL.search(_strip_prose(p.read_text())):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], (
        "these call deactivate_all(), which leaves get_language() as None and "
        "zeroes NUMBER_GROUPING for the rest of the worker. Use deactivate(), "
        f"which restores LANGUAGE_CODE: {offenders}"
    )


def test_the_guard_catches_both_aliasing_shapes_and_spares_prose():
    """Empirical canary (#1459) — a lint reporting nothing looks like a broken one.

    Each shape is checked against the real matcher rather than trusted from the
    regex's appearance. Both aliased forms passed the earlier version.
    """
    caught = [
        "translation.deactivate_all()",
        "deactivate_all()",
        "trans.deactivate_all()",
        "    deactivate_all()",
        "x = 1; deactivate_all()",
        "foo()  # trailing comment\ndeactivate_all()",
    ]
    for src in caught:
        assert DEACTIVATE_ALL.search(_strip_prose(src)), f"missed: {src!r}"

    spared = [
        "# never call translation.deactivate_all() here",
        "    # deactivate_all() leaks",
        '"""' + "Explains why deactivate_all() is wrong." + '"""',
        '"""' + "\nProse about deactivate_all().\n" + '"""',
    ]
    for src in spared:
        assert not DEACTIVATE_ALL.search(_strip_prose(src)), f"false alarm: {src!r}"


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
    missing = []
    for root in TEST_ROOTS:
        conftest = ROOT / root / "conftest.py"
        if not conftest.exists() or "reset_djust_globals" not in conftest.read_text():
            missing.append(root)
    assert missing == [], (
        "these roots are scanned by the guards above but have no autouse "
        f"reset_djust_globals fixture, so nothing protects them: {missing}"
    )
