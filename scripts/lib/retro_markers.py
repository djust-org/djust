"""Canonical retro-marker regex for pipeline-run Stage 14 audit gates.

Filed as #1249 (extract retro-marker regex to shared constants module —
v0.9.2-2 retro Action Tracker #207). Previously the regex was defined
in two places: ``scripts/audit-pipeline-bypass.py`` and the Stage 14
``subagent_prompt`` text in ``.pipeline-templates/{feature,bugfix}-state.json``.
Two-source-of-truth bugs (the prompts drift from the audit script's
detection rule) are silent and only surface when an audit run flags a
PR whose retro comment matched the prompt's regex but not the
script's. This module is the canonical one truth.

Callers compile with their own ``re`` flags (typically ``re.IGNORECASE``)
so the constant stays portable across re-vs-regex backends.

Example::

    import re
    from scripts.lib.retro_markers import RETRO_MARKER_REGEX

    pattern = re.compile(RETRO_MARKER_REGEX, re.IGNORECASE)
    has_retro = bool(pattern.search(comment_body))
"""

from __future__ import annotations

#: Regex matching the textual markers a Stage 14 retro comment is required
#: to contain. Match is case-insensitive (callers should compile with
#: ``re.IGNORECASE``). Five marker shapes are accepted:
#:
#: * ``Retrospective`` (heading or prose)
#: * ``Quality: <digit>`` (the 1-5 self-rating line)
#: * ``Lessons learned`` (the lessons-learned section)
#: * ``RETRO_COMPLETE`` (the mandatory final-line sentinel)
#: * ``What went well`` (one of the standard retro headings)
#:
#: At least one match in the comment body is sufficient to count the PR
#: as having a Stage 14 retro.
RETRO_MARKER_REGEX = (
    r"retrospective|quality:\s*\d|lessons\s+learned|retro_complete|what\s+went\s+well"
)
