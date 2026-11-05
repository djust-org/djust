"""The safe-markup baseline, as a merge gate (#3848).

``python/tests/test_safe_markup_audit_3774.py`` holds this check next to a payload sweep that takes
minutes, so it is not on ``make merge-gates``. This is the cheap half: the guard over ``python/djust``
(about 6 s) against ``scripts/lib/safe_markup_audit.py``. It reads SOURCE, not the test tree, but it
is a cross-file gate all the same: two PRs that each pass alone can break it together (one adds
``_safe(f"<b>{x}</b>")``, the other adds the entry that was meant to cover the old count, or a class
that ``setattr``s its keywords and turns class-attribute trust off for a file the other relied on).
"""

from scripts.lib import safe_markup_audit as audit


def test_every_flagged_site_is_audited_and_every_entry_is_current():
    problems = audit.reconcile(audit.scan(), audit.AUDITED)
    assert not any(problems.values()), problems


def test_the_evidence_is_still_live_code():
    assert audit.missing_evidence(audit.AUDITED) == []
