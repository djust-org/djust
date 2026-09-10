"""Structural pins for the deleted context-narrowing filter (#2739).

``_sync_state_to_rust`` used to narrow the context handed to Rust by the
names the template referenced, via ``_get_template_deps``, which read the
template source from ``self._template_content`` — an attribute nothing ever
assigned (the working spelling everywhere else is the METHOD
``_get_template_content()``). The filter therefore never fired once.

It was deleted rather than wired up (#2737: read-set narrowing is unsound
while ``build_py_context`` hands bridged tag handlers the whole context).
These pins keep the dead name from coming back under the same spelling, so
the drift cannot silently recur.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import djust

PACKAGE_ROOT = Path(djust.__file__).resolve().parent

# The two method names that legitimately contain the dead attribute name as
# a substring. A word-boundary regex would not separate them, so they are
# stripped before the scan.
_LEGIT_METHOD_NAMES = ("_get_template_content", "_extract_liveview_template_content")


def _package_sources() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if "tests" not in p.parts)


def _scan(pattern: str) -> list[str]:
    rx = re.compile(pattern)
    hits: list[str] = []
    for path in _package_sources():
        text = path.read_text(encoding="utf-8")
        for name in _LEGIT_METHOD_NAMES:
            text = text.replace(name, "")
        for lineno, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{path.relative_to(PACKAGE_ROOT)}:{lineno}: {line.strip()}")
    return hits


def test_scan_is_non_vacuous():
    """The scanner sees the package: a name that IS used must be found."""
    assert _scan(r"\b_get_template_content\b") == [], "the legit names must be stripped"
    assert _scan(r"\b_sync_state_to_rust\b"), "scanner found nothing at all — broken glob?"


@pytest.mark.parametrize(
    "dead_name",
    ["_template_content", "_get_template_deps", "_template_deps"],
)
def test_dead_name_is_gone_from_package_source(dead_name):
    hits = _scan(rf"\b{dead_name}\b")
    assert hits == [], (
        f"{dead_name!r} is back in python/djust — it was the never-assigned "
        f"attribute / never-firing filter deleted in #2739 (see #2737). "
        f"Hits: {hits}"
    )


def test_liveview_has_no_get_template_deps():
    from djust import LiveView

    assert not hasattr(LiveView, "_get_template_deps")
