"""The `htmlparser.rs` header's version claim and figures must stay true (#2289).

The header used to read "a transcription of CPython 3.12's `html/parser.py`".
Imprecise in a way that misleads: the HTML5-spec rewrite landed in **3.12.10**,
so 3.12.9 *is* a CPython 3.12 and djust differs from it on a quarter of the
corpus. A reader on 3.12.9 taking that at its word would expect a match.

The body of the file always got this right — there are 16 separate `3.12.10`
citations in it. Only the header generalised.

A corrected header is worth little if nothing keeps it correct, so these tests
recompute the header's own figures and fail when it goes stale. This is the
prose-invariant rule (CLAUDE.md v1.0.8-1): a doc claim of the form "X differs
on N values" must be run, not merely proofread — verifying the citation is
necessary and not sufficient, because a citation can be exact while the claim
it supports is false.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.utils.html import escape  # noqa: E402

from djust import _rust  # noqa: E402

HEADER = Path(_rust.__file__).parent.parent.parent / "crates/djust_templates/src/htmlparser.rs"
FIXTURE = Path(__file__).parent / "fixtures/striptags_reference_2273.json"


def _header_text() -> str:
    if not HEADER.exists():
        pytest.skip("Rust source not available next to the built extension")
    return HEADER.read_text(encoding="utf-8").split("\npub ", 1)[0]


def test_the_header_names_the_patch_release_not_the_minor() -> None:
    """The claim the issue is about."""
    text = _header_text()
    assert "CPython 3.12's" not in text, (
        "the header generalised to 'CPython 3.12' again — 3.12.9 and 3.12.10 "
        "are different parsers and djust matches only the latter"
    )
    assert "3.12.10+" in text


def test_the_header_says_the_behaviour_is_pinned_not_host_dependent() -> None:
    """#2286's decision, which is the reason the divergence is acceptable."""
    text = _header_text()
    assert "pinned behaviour on every host" in text
    assert "sys.version_info" in text, (
        "the header should say djust deliberately does not branch on the "
        "running interpreter — that is what makes the divergence a fixed, "
        "documented property rather than a deployment hazard"
    )


def test_the_headers_figures_still_match_a_live_measurement() -> None:
    """Recompute the table. This is the half that cannot rot silently.

    Renders ``{{ p|striptags }}`` over the fixture corpus and compares against
    each recorded interpreter's answer through Django's ``escape``. Cells where
    CPython raises (the unrelated DoS guard) are excluded — which is why the
    3.12.13/3.13.7 row is 0 and not 2.
    """
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    versions = data["versions"]
    differs = dict.fromkeys(versions, 0)
    corpus = 0
    for bucket in (data["stable"], data["unstable"]):
        for value, answer in bucket.items():
            got = _rust.render_template("{{ p|striptags }}", {"p": value})
            corpus += 1
            per = answer if isinstance(answer, dict) else dict.fromkeys(versions, answer)
            for version in versions:
                recorded = per.get(version)
                if not (isinstance(recorded, str) and recorded.startswith("OK:")):
                    continue
                if got != escape(recorded[3:]):
                    differs[version] += 1

    assert corpus == 4000, f"the header says 4000 values; the fixture has {corpus}"

    text = _header_text()
    rows = dict(re.findall(r"^//! \| ([\d., ]+?) \| (\d+) \|", text, re.M))
    assert rows, "the header's figure table is missing or its shape changed"

    for spelling, claimed in rows.items():
        for version in (v.strip() for v in spelling.split(",")):
            assert version in differs, f"header names {version!r}, not in the fixture"
            assert differs[version] == int(claimed), (
                f"header claims djust differs from {version} on {claimed} values; "
                f"measured {differs[version]}. Update the table — or find out why "
                f"striptags moved."
            )

    # Every recorded interpreter must appear in the table, or a row silently
    # dropped is indistinguishable from one that was never measured.
    named = {v.strip() for s in rows for v in s.split(",")}
    assert named == set(versions), f"table covers {named}, fixture records {set(versions)}"
