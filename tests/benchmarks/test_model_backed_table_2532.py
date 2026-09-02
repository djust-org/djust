"""Unit tests for the five-bucket profile support module (#2532). No DB.

Covers the caller classifier bucket 2 rests on, the table builder the
terminal summary prints, and two structural pins on the benchmark module:
it never asserts a duration, and every variant × phase it declares appears
in a table built from its rows.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List

import pytest

from tests.benchmarks.model_backed_profile_2532 import (
    BUCKET_COLUMNS,
    Crossings,
    PhaseRow,
    fast_path_label,
    format_table,
    install_crossing_counters,
    summarize,
    write_json_if_requested,
)

HERE = Path(__file__).resolve().parent
BENCHMARK_MODULE = HERE / "test_model_backed_render_2532.py"

VARIANTS = (
    "list_control",
    "list_property",
    "list_reverse",
    "list_fk_nosel",
    "presenter_control",
    "presenter_reverse",
    "snapshot",
)
PHASES = ("mount", "text_change", "attr_change", "row_text_change")

#: The no-threshold pin. A comparison of any ``*_ms`` attribute — whatever the
#: receiver expression (``row.total_ms``, ``phases["x"].total_ms``) — against
#: anything but literal zero is a duration threshold. ``> 0`` / ``> 0.0`` is a
#: PRESENCE check (the persist column on the snapshot variant) and is exempt.
#: The zero exemption lives INSIDE the lookahead (``(?!\s*0...)``) and the
#: operator is pinned with ``(?!=)``: with ``\s*`` outside it, or ``=?`` free
#: to give up its ``=``, the engine backtracks past the exemption and flags
#: ``> 0.0`` anyway (the shape the first version of this regex had).
NO_THRESHOLD_RE = re.compile(r"assert\s+[^\n]*\.\w+_ms\s*[<>]=?(?!=)(?!\s*0(?:\.0+)?(?![.\w]))")


def _row(variant: str, phase: str, **overrides: object) -> PhaseRow:
    base = dict(
        variant=variant,
        phase=phase,
        frame_type="mount" if phase == "mount" else "patch",
        total_ms=10.0,
        render_ms=3.0,
        xings=0,
        xing_ms=0.0,
        py_xings=4,
        queries=1,
        sql_ms=0.1,
        parse_ms=1.0,
        diff_ms=0.5,
        ser_ms=0.2,
        fast_path=None if phase == "mount" else 1.0,
    )
    base.update(overrides)
    return PhaseRow(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class TestCrossingClassifier:
    def test_call_during_rust_render_from_outside_the_serializer_is_a_crossing(self):
        assert (
            Crossings.classify(in_rust_render=True, caller_file="/x/djust/renderers/html.py")
            == "rust"
        )

    def test_serializer_internal_call_during_rust_render_is_a_proxy_rewrap(self):
        assert (
            Crossings.classify(in_rust_render=True, caller_file="/x/djust/serialization.py")
            == "proxy"
        )

    def test_call_outside_rust_render_is_python_side_whatever_the_caller(self):
        for caller in (
            "/x/djust/mixins/rust_bridge.py",
            "/x/djust/mixins/context.py",
            "/x/djust/renderers/html.py",
        ):
            assert Crossings.classify(in_rust_render=False, caller_file=caller) == "python"

    def test_record_splits_counts_and_time_by_origin(self):
        c = Crossings()
        c.record("Post", 0.001, "/x/djust/mixins/rust_bridge.py")
        c.in_rust_render = True
        c.record("Page", 0.002, "/x/djust/renderers/html.py")
        c.record("normalize:Post", 0.003, "/x/djust/serialization.py")
        c.in_rust_render = False
        assert (c.rust_calls, c.proxy_calls, c.python_calls) == (1, 1, 1)
        assert c.kinds == {"Page": 1}
        assert abs(c.rust_secs - 0.002) < 1e-9
        assert abs(c.proxy_secs - 0.003) < 1e-9
        assert abs(c.python_secs - 0.001) < 1e-9
        c.reset()
        assert (c.rust_calls, c.proxy_calls, c.python_calls, c.kinds) == (0, 0, 0, {})

    def test_install_patches_the_names_rust_resolves_and_restores_them(self):
        import djust.serialization as ser

        orig_protect = ser._protect_sidecar_value
        orig_normalize = ser.normalize_django_value
        c = Crossings()
        restore = install_crossing_counters(c)
        try:
            assert ser._protect_sidecar_value is not orig_protect
            assert ser.normalize_django_value is not orig_normalize
            # A direct Python call is Python-side; the same call with the
            # render flag set is a crossing.
            assert ser._protect_sidecar_value(42) == 42
            assert c.python_calls == 1 and c.rust_calls == 0
            c.in_rust_render = True
            assert ser.normalize_django_value("s") == "s"
            c.in_rust_render = False
            assert c.rust_calls == 1
        finally:
            restore()
        assert ser._protect_sidecar_value is orig_protect
        assert ser.normalize_django_value is orig_normalize


# ---------------------------------------------------------------------------
# Rows + table
# ---------------------------------------------------------------------------


class TestPhaseRowBuckets:
    def test_rust_ms_is_render_minus_crossing_python_time_floored_at_zero(self):
        assert _row("v", "mount", render_ms=5.0, xing_ms=1.5).rust_ms == 3.5
        assert _row("v", "mount", render_ms=1.0, xing_ms=2.0).rust_ms == 0.0

    def test_state_ms_is_sync_minus_jit(self):
        assert _row("v", "text_change", sync_ms=4.0, jit_ms=1.0).state_ms == 3.0

    def test_fast_flag_reads_the_differ_value(self):
        assert _row("v", "mount").fast is None
        assert _row("v", "text_change", fast_path=0.0).fast is False
        assert _row("v", "text_change", fast_path=1.0).fast is True
        assert _row("v", "row_text_change", fast_path=2.0).fast is True

    def test_fast_path_labels(self):
        assert [fast_path_label(v) for v in (None, 0.0, 1.0, 2.0)] == [
            "-",
            "full",
            "frag",
            "region",
        ]


class TestSummaryTable:
    def _rows(self) -> List[PhaseRow]:
        rows = []
        for variant in VARIANTS:
            for phase in PHASES:
                for total in (12.0, 10.0, 11.0):  # three rounds, median 11
                    rows.append(
                        _row(
                            variant,
                            phase,
                            total_ms=total,
                            xings=357 if variant == "presenter_reverse" else 0,
                            fast_path=None
                            if phase == "mount"
                            else (0.0 if phase == "attr_change" else 1.0),
                        )
                    )
        return rows

    def test_table_lists_every_variant_and_phase_once_with_the_median(self):
        summary = summarize(self._rows())
        keys = [(e["variant"], e["phase"]) for e in summary]
        assert keys == [(v, p) for v in VARIANTS for p in PHASES]
        assert all(e["rounds"] == 3 for e in summary)
        assert all(e["total_ms"] == 11.0 for e in summary)
        reverse_mount = next(e for e in summary if e["variant"] == "presenter_reverse")
        assert reverse_mount["xings"] == 357
        attr = next(e for e in summary if e["phase"] == "attr_change")
        assert attr["fast"] == "full"
        assert next(e for e in summary if e["phase"] == "text_change")["fast"] == "frag"

    def test_dropping_a_variant_is_visible(self):
        rows = [r for r in self._rows() if r.variant != "list_fk_nosel"]
        assert {e["variant"] for e in summarize(rows)} == set(VARIANTS) - {"list_fk_nosel"}

    def test_format_table_has_a_header_and_one_line_per_group(self):
        text = format_table(self._rows())
        lines = text.splitlines()
        assert lines[0].split()[:2] == ["variant", "phase"]
        assert len(lines) == 2 + len(VARIANTS) * len(PHASES)
        assert all(header in lines[0] for header, _ in BUCKET_COLUMNS)
        assert "presenter_reverse" in text and "row_text_change" in text
        assert format_table([]) == ""

    def test_json_dump_only_when_requested(self, tmp_path):
        rows = self._rows()
        assert write_json_if_requested(rows, env={}) is None
        out = tmp_path / "t.json"
        assert write_json_if_requested(rows, env={"DJUST_BENCH_TABLE_JSON": str(out)}) == str(out)
        payload = json.loads(out.read_text())
        assert len(payload["rows"]) == len(rows)
        assert len(payload["medians"]) == len(VARIANTS) * len(PHASES)
        assert all("persist_calls" in m for m in payload["medians"])

    def test_json_dump_names_the_missing_directory(self, tmp_path):
        missing = tmp_path / "no_such_dir" / "t.json"
        with pytest.raises(AssertionError, match=r"does not exist"):
            write_json_if_requested(self._rows(), env={"DJUST_BENCH_TABLE_JSON": str(missing)})
        assert not missing.exists()


# ---------------------------------------------------------------------------
# Structural pins on the benchmark module
# ---------------------------------------------------------------------------


class TestBenchmarkModuleShape:
    def test_no_duration_threshold_is_asserted(self):
        """Plan §4: no thresholds. If one is ever added it must go through
        ``_assert_benchmark_under`` (median, skipped under ``-n auto``)."""
        src = BENCHMARK_MODULE.read_text()
        assert "benchmark.stats" not in src
        hit = NO_THRESHOLD_RE.search(src)
        assert hit is None, f"duration threshold in the benchmark module: {hit.group(0)!r}"

    def test_no_threshold_pin_catches_every_receiver_shape(self):
        """The pin is only as good as its regex: it must catch a threshold on a
        subscripted receiver (the shape the first version missed), on ``<=``,
        and on every ``*_ms`` attribute — and must NOT flag a presence check
        against literal zero."""
        caught = (
            'assert phases["x"].total_ms < 5',
            "assert row.render_ms <= budget",
            "assert rows[0].persist_ms > 1.5",
            "assert _by_phase(rows)['mount'].xing_ms<10",
            "assert r.sql_ms >= 0.5",
        )
        for src in caught:
            assert NO_THRESHOLD_RE.search(src), f"pin missed a threshold: {src!r}"
        exempt = (
            "assert row.persist_ms > 0.0",
            "assert row.persist_ms > 0",
            "assert phases[event].persist_ms >= 0",
            "assert row.persist_calls == 1",
            "assert row.xings == 0",
        )
        for src in exempt:
            assert not NO_THRESHOLD_RE.search(src), f"pin flagged a non-threshold: {src!r}"

    def test_declares_the_seven_variants_and_three_events(self):
        src = BENCHMARK_MODULE.read_text()
        for variant in VARIANTS:
            assert f'"{variant}"' in src
        for event in PHASES[1:]:
            assert f'"{event}"' in src

    def test_only_the_snapshot_variant_opts_into_state_persistence(self):
        """The persist column is claimed non-zero for exactly one variant; the
        opt-in must be spelled as a comparison with that variant's name, so a
        second opted-in variant (or none) is visible in the source."""
        src = BENCHMARK_MODULE.read_text()
        assert 'SNAPSHOT_VARIANT = "snapshot"' in src
        assert "enable_state_snapshot = variant == SNAPSHOT_VARIANT" in src
