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


DEACTIVATE_ALL = re.compile(r"^(?!\s*#).*\btranslation\.deactivate_all\s*\(", re.MULTILINE)


def test_no_test_calls_translation_deactivate_all():
    """``deactivate()``, never ``deactivate_all()``, in a test.

    Comment lines are excluded so a file may still EXPLAIN the difference —
    which several do, and which is worth keeping.
    """
    offenders = []
    for p in _test_files():
        if DEACTIVATE_ALL.search(p.read_text()):
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], (
        "these call translation.deactivate_all(), which leaves get_language() "
        "as None and zeroes NUMBER_GROUPING for the rest of the worker. Use "
        f"translation.deactivate(), which restores LANGUAGE_CODE: {offenders}"
    )


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
