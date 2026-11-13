"""Constructing views of many different templates stays linear (#3998).

The Rust renderer keeps one copy of each template source text in a process-wide
table and gives every view of that source a reference to it. The table drops
the sources no view holds any more by scanning itself, under the one lock every
view construction takes. Scanning on each new source made N views of N
different templates cost N scans of N entries; the scan now runs when the table
has doubled since the last one.

The exact count (entries scanned) is asserted in Rust
(``crates/djust_live/src/retained.rs``,
``distinct_live_sources_cost_a_linear_number_of_scanned_entries``): the table
is private to the extension, so Python has nothing to count. This is the same
property seen from Python, as a growth ratio with no time bound: four times
the live sources must cost about four times as much, where a scan per source
costs sixteen.
"""

from __future__ import annotations

import time
from typing import List

from djust._rust import RustLiveView
from djust.tests._growth import FACTOR, assert_scales_linearly

BASE = 4_000

#: Distinct for every call, so no run finds a source an earlier run left.
_RUN = [0]


def _construct(scale: int) -> None:
    _RUN[0] += 1
    run = _RUN[0]
    held: List[object] = [
        RustLiveView(f"<div dj-root>source {run}/{i} {{{{ n }}}}</div>", [])
        for i in range(BASE * scale)
    ]
    assert len(held) == BASE * scale


def test_views_of_distinct_templates_cost_linear_time_to_construct():
    # Warm up at the LARGE size: the first run that needs that much memory
    # pays the operating system for it, which is not the table's cost.
    _construct(FACTOR)
    assert_scales_linearly(
        _construct,
        factor=FACTOR,
        runs=5,
        clock=time.perf_counter,
        what="constructing views of distinct live template sources",
    )


def test_two_views_of_one_template_hold_one_text_and_report_no_address():
    source = "<div dj-root>one text, two views {{ n }}</div>"
    first, second = RustLiveView(source, []), RustLiveView(source, [])
    report = first.retained_bytes()
    assert report["template_source"] == 0
    assert report["shared_template_source"] == len(source)
    assert report["template_source_holders"] == 2
    del second
    assert first.retained_bytes()["template_source_holders"] == 1
    # A heap address is not something to put in a metrics pipeline.
    assert not any("address" in key or "ptr" in key for key in report)
