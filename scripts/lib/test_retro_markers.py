"""Unit tests for ``scripts/lib/retro_markers.py``.

Filed as #1249 (extract retro-marker regex to shared constants module).
The regex was previously defined in two places (the audit script and the
Stage 14 ``subagent_prompt`` text in the pipeline templates). These
tests lock the canonical pattern's behavior so future template updates
that re-embed the regex literal can be caught by a CI grep.

Run with::

    pytest scripts/lib/test_retro_markers.py -v
"""

from __future__ import annotations

import re

from scripts.lib.retro_markers import RETRO_MARKER_REGEX


COMPILED = re.compile(RETRO_MARKER_REGEX, re.IGNORECASE)


# Positive cases — these should match the canonical retro-marker regex.
POSITIVE_CASES = [
    # Stage 14 retro shapes from real PRs.
    "## Retrospective\n\nQuality: 5/5 — went well.",
    "Quality: 4 — lessons learned about kwargs forwarding.",
    "Lessons learned: always grep before pasting.",
    "Final line: RETRO_COMPLETE",
    "What went well: TDD-first surfaced the bug early.",
    # Lowercase variants — regex is case-insensitive.
    "what went well? everything.",
    "retrospective notes from the v0.9.5 retro arc.",
    # Loose whitespace handling.
    "Quality:  3 (mid-retro)",
    "lessons   learned: don't merge on a Friday.",
]

# Negative cases — these should NOT match.
NEGATIVE_CASES = [
    "Implementation note: refactored helper.",
    "feat: add retro audit script",
    "Just a regular PR comment about quality assurance.",  # "quality" without ":<digit>"
    "Lessons from the trenches:",  # missing "learned"
    "",  # empty
    "Stage 11 review: APPROVE.",
]


def test_positive_cases_match():
    """Every documented retro-marker shape must match the regex."""
    for text in POSITIVE_CASES:
        assert COMPILED.search(text), f"Expected match but got none for: {text!r}"


def test_negative_cases_do_not_match():
    """Non-retro text must NOT match the regex (no false positives)."""
    for text in NEGATIVE_CASES:
        assert not COMPILED.search(text), f"Expected no match but found one in: {text!r}"


def test_regex_is_a_string_not_pattern():
    """The constant must be a raw regex string so callers can choose flags."""
    assert isinstance(RETRO_MARKER_REGEX, str), (
        "RETRO_MARKER_REGEX must be a string (callers compile with their own flags). "
        "If a pre-compiled Pattern is needed too, expose it under a different name."
    )


def test_audit_script_uses_constant():
    """The audit script must import the constant rather than re-embedding the regex.

    Catches future regressions where the regex is copy-pasted back into the
    audit script (the failure mode #1249 was filed to prevent).
    """
    audit_script = __import__("pathlib").Path(__file__).parent.parent / "audit-pipeline-bypass.py"
    text = audit_script.read_text(encoding="utf-8")
    # The script must import RETRO_MARKER_REGEX from this module.
    assert "RETRO_MARKER_REGEX" in text, (
        "audit-pipeline-bypass.py must import RETRO_MARKER_REGEX from scripts.lib.retro_markers"
    )
    # The literal regex pattern must NOT appear inline a second time.
    inline_literal = (
        r"retrospective|quality:\s*\d|lessons\s+learned|retro_complete|what\s+went\s+well"
    )
    occurrences = text.count(inline_literal)
    # Allow 0 occurrences (constant is imported); reject 1+ inline literal hits
    # which would mean the script ALSO embeds it directly.
    assert occurrences == 0, (
        f"audit-pipeline-bypass.py should not embed the regex literal directly; "
        f"found {occurrences} occurrence(s). Import RETRO_MARKER_REGEX instead."
    )
