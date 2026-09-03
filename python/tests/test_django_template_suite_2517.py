"""Tests for the Django ``template_tests`` conformance runner (#2517).

The runner — ``scripts/run-django-template-suite.py`` plus the package under
``scripts/lib/django_template_suite/`` — routes Django's OWN template test
suite through djust's Rust engine and prints a pass percentage. That number
is the v1.2.0 conformance arc's scoreboard, so the tool that produces it has
to be trustworthy in the specific ways a scoreboard can lie:

* the arithmetic (``TestSummaryArithmetic``): ERROR and FAIL are different
  work and are counted separately; SKIP and XFAIL never enter a denominator;
* the recorder (``TestRecordingResult``): one JSON line per test, flushed,
  with ERROR outranking FAIL and a subtest failure marking its parent;
* the empirical canary (``TestEmpiricalCanary``, #1459): a template djust
  renders differently from Django MUST show up as FAIL, an unsupported tag
  as ERROR, and the gate-off (Django against itself) as 100 %;
* crash isolation (``TestCrashIsolation``): a segfaulting test is recorded
  as ERROR and the tests after it still run;
* the ratchet (``TestRatchetCompare``): ``compare`` says 1 on a drop, even
  though CI does not enforce it yet;
* the seam (``TestAdapterInSubprocess``): ``install()`` rebinds exactly the
  two names it claims to, leaves the ``TEMPLATES`` backend real, and
  produces ``DjustTemplate`` objects — not Django's;
* argv hardening (``TestArgvHardening``, the #2517 review): a traversal tag
  is refused before any filesystem access, a label that looks like an
  option is refused rather than smuggled past ``--gate-off``'s baseline
  refusal, and a baseline is never written from a run where nothing reached
  the engine.

Everything that mutates process globals (``install()``, ``settings.configure``)
runs in a subprocess, following ``test_differential_reachability_manifest_2345``.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import textwrap
import unittest
from types import ModuleType

import pytest

from scripts.lib.django_template_suite import adapter
from scripts.lib.django_template_suite.recorder import RecordingResult
from scripts.lib.django_template_suite.report import (
    compare,
    format_per_test_line,
    format_summary,
    percent,
    summarize,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "run-django-template-suite.py"
BASELINE = REPO / "scripts" / "django-template-suite-baseline.json"
DOC = REPO / "docs" / "TEMPLATE_BACKEND.md"
DOC_MARKER = "<!-- django-suite-claim -->"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "python"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return env


def _runner() -> ModuleType:
    """Import ``scripts/run-django-template-suite.py`` (a hyphenated file) for unit tests."""
    spec = importlib.util.spec_from_file_location("run_django_template_suite", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_cli(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    """Run the CLI as a subprocess from the repo root; the caller checks the rc."""
    return subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(REPO),
        check=False,
        timeout=timeout,
    )


def rec(
    test_id: str, status: str, *, touched: bool = True, message: str = "", crash: bool = False
) -> dict:
    row = {
        "event": "result",
        "id": test_id,
        "status": status,
        "message": message,
        "touched": touched,
        "ms": 1.0,
    }
    if crash:
        row["crash"] = True
    return row


def parsed_lines(text: str) -> dict[str, str]:
    """``{test_id: 'STATUS  id | message'}`` for every per-test line in the parsed output."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(OK|FAIL|ERROR|SKIP|XFAIL)\s+(\S+)", line)
        if m:
            out[m.group(2)] = line
    return out


# --------------------------------------------------------------------------- #
# (2) the summary parser
# --------------------------------------------------------------------------- #


class TestSummaryArithmetic:
    """``percent = ok / (ok + fail + error)``; SKIP and XFAIL are not tests that ran."""

    RECORDS = [
        rec("t.a", "OK"),
        rec("t.b", "OK"),
        rec("t.c", "OK"),
        rec("t.d", "FAIL", message="AssertionError: 'x' != 'y'"),
        rec("t.e", "ERROR", message="Exception: Unsupported template tag"),
        rec("t.f", "SKIP", message="not on this platform"),
        rec("t.g", "SKIP", message="not on this platform"),
        rec("t.h", "XFAIL"),
    ]

    def test_skipped_and_xfail_are_excluded_from_the_denominator(self) -> None:
        s = summarize(self.RECORDS)
        assert s.all.ran == 5
        assert s.all.percent == 60.00
        assert s.skipped == 2
        assert s.xfail == 1

    def test_error_and_fail_are_counted_separately(self) -> None:
        s = summarize(self.RECORDS)
        assert (s.all.ok, s.all.fail, s.all.error) == (3, 1, 1)
        assert (s.engine.ok, s.engine.fail, s.engine.error) == (3, 1, 1)

    def test_zero_over_zero_is_zero_not_a_crash(self) -> None:
        assert percent(0, 0) == 0.0
        s = summarize([])
        assert s.all.percent == 0.0
        assert s.engine.percent == 0.0
        assert s.all.ran == 0

    def test_rounding_is_two_decimals_half_up(self) -> None:
        assert percent(2, 3) == 66.67
        assert percent(1, 3) == 33.33
        assert percent(1, 8) == 12.5
        # 1/160 = 0.625 % — half-up gives 0.63; banker's rounding would give 0.62.
        assert percent(1, 160) == 0.63
        assert percent(5, 5) == 100.0

    def test_engine_bucket_is_the_touched_subset_and_all_is_everything(self) -> None:
        records = [
            rec("t.a", "OK", touched=True),
            rec("t.b", "FAIL", touched=True),
            rec("t.c", "OK", touched=False),
            rec("t.d", "OK", touched=False),
            rec("t.e", "OK", touched=False),
        ]
        s = summarize(records)
        assert (s.engine.ok, s.engine.fail, s.engine.error, s.engine.ran) == (1, 1, 0, 2)
        assert s.engine.percent == 50.0
        assert (s.all.ok, s.all.fail, s.all.ran) == (4, 1, 5)
        assert s.all.percent == 80.0
        assert (s.untouched.ok, s.untouched.ran) == (3, 3)

    def test_untouched_failures_are_a_harness_integrity_signal(self) -> None:
        records = [
            rec("t.a", "OK", touched=True),
            rec("t.b", "FAIL", touched=False),
            rec("t.c", "ERROR", touched=False),
            rec("t.d", "OK", touched=False),
        ]
        s = summarize(records)
        assert s.untouched_failures == 2
        lines = format_summary(s)
        assert any(line.startswith("WARNING: harness integrity: 2 untouched") for line in lines)

    def test_the_last_result_for_an_id_wins(self) -> None:
        # A relaunch must never double-count an id; the recorder keys on it.
        records = [rec("t.a", "FAIL"), rec("t.a", "OK")]
        s = summarize(records)
        assert (s.all.ok, s.all.fail, s.all.ran) == (1, 0, 1)

    def test_start_events_and_junk_lines_are_ignored(self) -> None:
        records = [
            {"event": "start", "id": "t.a"},
            rec("t.a", "OK"),
            {"event": "start", "id": "t.b"},
        ]
        s = summarize(records)
        assert s.all.ran == 1

    def test_crashes_are_listed_by_id(self) -> None:
        records = [
            rec("t.a", "OK"),
            rec("t.b", "ERROR", message="process crashed (signal 11)", crash=True),
        ]
        s = summarize(records)
        assert s.crashes == ["t.b"]
        assert s.engine.error == 1

    def test_format_summary_emits_the_spec_lines_exactly(self) -> None:
        records = [
            rec("t.a", "OK"),
            rec("t.b", "OK"),
            rec("t.c", "OK"),
            rec("t.d", "FAIL", message="AssertionError: 'x' != 'y'"),
            rec("t.e", "ERROR", message="process crashed (signal 11)", crash=True),
            rec("t.f", "OK", touched=False),
            rec("t.g", "OK", touched=False),
            rec("t.h", "SKIP", message="no"),
        ]
        lines = format_summary(summarize(records))
        assert lines == [
            "Django test suite passing: 60.00%",
            "1 ERROR / 1 FAIL / 3 OK   (5 tests exercised the djust engine; 1 skipped)",
            "whole label: 71.43%  (5 OK / 1 FAIL / 1 ERROR of 7; "
            "2 never reached djust and passed on Django)",
            "harness integrity: 0 untouched tests failed",
            "crashes isolated: 1 (listed above as ERROR: process crashed)",
        ]

    def test_headline_falls_back_to_the_whole_label_when_nothing_reached_djust(self) -> None:
        # The gate-off run (Django against itself) has an empty engine bucket;
        # the headline must say 100 % AND say why, never "0.00 %" of nothing.
        records = [rec("t.a", "OK", touched=False), rec("t.b", "OK", touched=False)]
        s = summarize(records)
        assert s.engine.ran == 0
        lines = format_summary(s)
        assert lines[0].startswith("Django test suite passing: 100.00%")
        assert "no test reached the djust engine" in lines[0]

    def test_per_test_lines(self) -> None:
        assert format_per_test_line(rec("t.a", "OK")) == "OK    t.a"
        assert format_per_test_line(rec("t.b", "FAIL", message="AssertionError: no")) == (
            "FAIL  t.b | AssertionError: no"
        )
        assert format_per_test_line(rec("t.c", "ERROR", message="Exception: x")) == (
            "ERROR t.c | Exception: x"
        )
        assert format_per_test_line(rec("t.d", "SKIP", message="why")) == "SKIP  t.d | why"
        assert format_per_test_line(rec("t.e", "XFAIL")) == "XFAIL t.e"

    def test_as_dict_is_the_baseline_schema(self) -> None:
        s = summarize(self.RECORDS)
        d = s.as_dict()
        assert d["ok"] == 3 and d["fail"] == 1 and d["error"] == 1 and d["ran"] == 5
        assert d["percent"] == 60.0
        assert d["all"] == {"ok": 3, "fail": 1, "error": 1, "ran": 5, "skipped": 2, "percent": 60.0}
        assert d["untouched_failures"] == 0
        assert d["crashes"] == []


# --------------------------------------------------------------------------- #
# (2) the recorder
# --------------------------------------------------------------------------- #


class _RecorderCases(unittest.TestCase):
    """The shapes unittest can produce, one method each (alphabetical = run order)."""

    __test__ = False  # a fixture for RecordingResult, not a test pytest should collect

    def test_a_ok(self) -> None:
        pass

    def test_b_fail(self) -> None:
        self.assertEqual("x", "y")

    def test_c_error(self) -> None:
        raise ValueError("boom <obj at 0xdeadbeef> in /tmp/abc/file.html\nsecond line")

    @unittest.skip("because")
    def test_d_skip(self) -> None:
        pass  # pragma: no cover

    def test_e_subtest_fail(self) -> None:
        with self.subTest(i=1):
            self.assertTrue(False)
        with self.subTest(i=2):
            pass

    def test_f_subtest_error_outranks_fail(self) -> None:
        with self.subTest(i=1):
            self.assertTrue(False)
        with self.subTest(i=2):
            raise RuntimeError("later")

    @unittest.expectedFailure
    def test_g_xfail(self) -> None:
        self.assertTrue(False)

    @unittest.expectedFailure
    def test_h_unexpected_success(self) -> None:
        pass

    def test_i_touches_the_adapter(self) -> None:
        adapter.TOUCH["count"] += 1


class TestRecordingResult:
    @pytest.fixture
    def records(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> list[dict]:
        out = tmp_path / "records.jsonl"
        monkeypatch.setenv("DJUST_SUITE_OUT", str(out))
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(_RecorderCases)
        runner = unittest.TextTestRunner(
            stream=io.StringIO(), verbosity=0, resultclass=RecordingResult
        )
        runner.run(suite)
        return [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]

    @staticmethod
    def _results(records: list[dict]) -> dict[str, dict]:
        return {r["id"].rsplit(".", 1)[1]: r for r in records if r["event"] == "result"}

    def test_one_start_and_one_result_per_test(self, records: list[dict]) -> None:
        starts = [r for r in records if r["event"] == "start"]
        results = [r for r in records if r["event"] == "result"]
        assert len(starts) == 9
        assert len(results) == 9
        assert [r["id"] for r in starts] == [r["id"] for r in results]

    def test_statuses(self, records: list[dict]) -> None:
        by = self._results(records)
        assert by["test_a_ok"]["status"] == "OK"
        assert by["test_b_fail"]["status"] == "FAIL"
        assert by["test_c_error"]["status"] == "ERROR"
        assert by["test_d_skip"]["status"] == "SKIP"
        assert by["test_d_skip"]["message"] == "because"
        assert by["test_e_subtest_fail"]["status"] == "FAIL"
        assert by["test_f_subtest_error_outranks_fail"]["status"] == "ERROR"
        assert by["test_g_xfail"]["status"] == "XFAIL"
        assert by["test_h_unexpected_success"]["status"] == "FAIL"

    def test_message_is_type_plus_first_line_normalised(self, records: list[dict]) -> None:
        by = self._results(records)
        assert by["test_b_fail"]["message"].startswith("AssertionError: ")
        msg = by["test_c_error"]["message"]
        assert msg.startswith("ValueError: boom <obj at 0x…>")
        assert "<tmp>" in msg
        assert "deadbeef" not in msg
        assert "/tmp/abc" not in msg
        assert "second line" not in msg
        assert by["test_f_subtest_error_outranks_fail"]["message"].startswith("RuntimeError: later")

    def test_touched_is_per_test(self, records: list[dict]) -> None:
        by = self._results(records)
        assert by["test_i_touches_the_adapter"]["touched"] is True
        assert all(
            r["touched"] is False for k, r in by.items() if k != "test_i_touches_the_adapter"
        )

    def test_every_result_has_a_duration(self, records: list[dict]) -> None:
        for r in records:
            if r["event"] == "result":
                assert isinstance(r["ms"], (int, float))

    def test_message_is_capped(self) -> None:
        from scripts.lib.django_template_suite.recorder import first_line

        long = "x" * 1000
        assert len(first_line((ValueError, ValueError(long), None))) == 300

    # -- S3/F1: the checkout root and the repo root must not leak -------------
    _RUST_SHAPE = (
        "Exception: Error rendering template (from %s/tests/template_tests/templates/x.html): "
        "Template error: Template not found: missing.html"
    )

    def test_checkout_root_is_normalised_to_django_src(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from scripts.lib.django_template_suite.recorder import normalize

        root = "/Users/someone/src/djust/.django-src/5.2.16"
        monkeypatch.setenv("DJUST_SUITE_SRC", root)
        assert normalize(self._RUST_SHAPE % root) == (
            "Exception: Error rendering template "
            "(from <django-src>/tests/template_tests/templates/x.html): "
            "Template error: Template not found: missing.html"
        )
        # The macOS ``/private`` spelling of the same root, and a trailing slash.
        assert "<django-src>/tests" in normalize(self._RUST_SHAPE % ("/private" + root))
        monkeypatch.setenv("DJUST_SUITE_SRC", root + "/")
        assert "<django-src>/tests" in normalize(self._RUST_SHAPE % root)

    def test_repo_root_is_normalised_to_repo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.lib.django_template_suite.recorder import normalize

        monkeypatch.delenv("DJUST_SUITE_SRC", raising=False)
        out = normalize("ValueError: bad file %s/python/djust/x.py" % REPO)
        assert out == "ValueError: bad file <repo>/python/djust/x.py"
        # A checkout inside the repo is <django-src>, never <repo>/.django-src.
        monkeypatch.setenv("DJUST_SUITE_SRC", str(REPO / ".django-src" / "5.2.16"))
        assert normalize(self._RUST_SHAPE % (REPO / ".django-src" / "5.2.16")).startswith(
            "Exception: Error rendering template (from <django-src>/tests/"
        )

    def test_no_env_means_no_checkout_substitution(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from scripts.lib.django_template_suite.recorder import normalize

        monkeypatch.delenv("DJUST_SUITE_SRC", raising=False)
        assert normalize("x /Users/a/b.html y") == "x /Users/a/b.html y"


# --------------------------------------------------------------------------- #
# (1) the empirical canary (#1459)
# --------------------------------------------------------------------------- #

CANARY_MODULE = textwrap.dedent(
    '''
    """Three templates: one both engines agree on, one djust gets wrong, one djust cannot parse."""

    from django.template import Context, Engine, TemplateSyntaxError
    from django.test import SimpleTestCase


    class Canary(SimpleTestCase):
        def test_1_variable_renders_the_same(self):
            out = Engine().from_string("{{ x }}").render(Context({"x": "hi"}))
            self.assertEqual(out, "hi")

        def test_2_incomplete_if_must_raise_at_parse(self):
            # Django raises TemplateSyntaxError while parsing; djust renders "yes".
            with self.assertRaises(TemplateSyntaxError):
                Engine().from_string("{% if foo == %}yes{% endif %}").render(Context({}))

        def test_3_autoescape_off(self):
            # Django renders "<b>"; djust raises on the unsupported tag.
            src = "{% autoescape off %}{{ x }}{% endautoescape %}"
            out = Engine().from_string(src).render(Context({"x": "<b>"}))
            self.assertEqual(out, "<b>")
    '''
)


def _write_module(tmp_path: pathlib.Path, name: str, source: str) -> None:
    (tmp_path / f"{name}.py").write_text(source, encoding="utf-8")


class TestEmpiricalCanary:
    """A template djust renders differently from Django shows up as FAIL; an unsupported tag as ERROR."""

    @pytest.fixture(scope="class")
    def djust_run(self, tmp_path_factory: pytest.TempPathFactory) -> tuple[str, dict]:
        root = tmp_path_factory.mktemp("canary")
        _write_module(root, "canary_tests", CANARY_MODULE)
        out_txt, out_json = root / "run.txt", root / "run.json"
        proc = run_cli(
            "run",
            "--discover-root",
            str(root),
            "--label",
            "canary_tests",
            "--parsed-output",
            str(out_txt),
            "--json",
            str(out_json),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return out_txt.read_text(encoding="utf-8"), json.loads(out_json.read_text("utf-8"))

    @pytest.fixture(scope="class")
    def gate_off_run(self, tmp_path_factory: pytest.TempPathFactory) -> tuple[str, dict]:
        root = tmp_path_factory.mktemp("canary-gate-off")
        _write_module(root, "canary_tests", CANARY_MODULE)
        out_txt, out_json = root / "run.txt", root / "run.json"
        proc = run_cli(
            "run",
            "--gate-off",
            "--discover-root",
            str(root),
            "--label",
            "canary_tests",
            "--parsed-output",
            str(out_txt),
            "--json",
            str(out_json),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return out_txt.read_text(encoding="utf-8"), json.loads(out_json.read_text("utf-8"))

    def test_divergence_is_fail_and_unsupported_is_error(self, djust_run: tuple[str, dict]) -> None:
        text, _ = djust_run
        lines = parsed_lines(text)
        assert lines["canary_tests.Canary.test_1_variable_renders_the_same"].startswith("OK    ")
        assert lines["canary_tests.Canary.test_2_incomplete_if_must_raise_at_parse"].startswith(
            "FAIL  canary_tests.Canary.test_2_incomplete_if_must_raise_at_parse | AssertionError: "
            "TemplateSyntaxError not raised"
        )
        # Since #2549 the unsupported tag is refused at `from_string`, as
        # `DjustTemplateSyntaxError` (a `TemplateSyntaxError`); the canary's
        # `assertEqual` never runs, so it is an ERROR, not a FAIL.
        assert lines["canary_tests.Canary.test_3_autoescape_off"].startswith(
            "ERROR canary_tests.Canary.test_3_autoescape_off | DjustTemplateSyntaxError: "
        )
        assert "Unsupported template tag" in lines["canary_tests.Canary.test_3_autoescape_off"]

    def test_engine_percent_and_touched(self, djust_run: tuple[str, dict]) -> None:
        text, data = djust_run
        assert "Django test suite passing: 33.33%" in text
        assert "1 ERROR / 1 FAIL / 1 OK   (3 tests exercised the djust engine; 0 skipped)" in text
        assert data["percent"] == 33.33
        assert (data["ok"], data["fail"], data["error"], data["ran"]) == (1, 1, 1, 3)
        assert data["untouched_failures"] == 0
        assert data["gate_off"] is False
        assert len(data["tests"]) == 3
        assert all(t["touched"] is True for t in data["tests"])

    def test_gate_off_is_django_against_itself(self, gate_off_run: tuple[str, dict]) -> None:
        text, data = gate_off_run
        lines = parsed_lines(text)
        assert len(lines) == 3
        assert all(line.startswith("OK    ") for line in lines.values())
        assert "Django test suite passing: 100.00%" in text
        assert data["gate_off"] is True
        assert data["ran"] == 0, "gate-off: nothing may reach the djust engine"
        assert data["all"]["percent"] == 100.0
        assert all(t["touched"] is False for t in data["tests"])


# --------------------------------------------------------------------------- #
# crash isolation
# --------------------------------------------------------------------------- #

CRASH_MODULE = textwrap.dedent(
    """
    import os
    import signal
    import time

    from django.test import SimpleTestCase


    class Crashy(SimpleTestCase):
        def test_a_before(self):
            pass

        def test_b_segfault(self):
            os.kill(os.getpid(), signal.SIGSEGV)

        def test_c_after(self):
            pass
    """
)

HANG_MODULE = textwrap.dedent(
    """
    import time

    from django.test import SimpleTestCase


    class Hangs(SimpleTestCase):
        def test_a_before(self):
            pass

        def test_b_hangs(self):
            time.sleep(60)

        def test_c_after(self):
            pass
    """
)


CRASH_IN_SETUPCLASS_MODULE = textwrap.dedent(
    """
    import os
    import signal

    from django.test import SimpleTestCase


    class AFinishes(SimpleTestCase):
        def test_a(self):
            pass

        def test_b(self):
            pass


    class BDiesInSetUpClass(SimpleTestCase):
        @classmethod
        def setUpClass(cls):
            super().setUpClass()
            os.kill(os.getpid(), signal.SIGSEGV)

        def test_c(self):
            pass
    """
)


class TestCrashIsolation:
    def test_crash_between_tests_names_the_finished_count_and_stops(
        self, tmp_path: pathlib.Path
    ) -> None:
        # F3: a segfault in a later class's setUpClass has no test in flight.
        # The recorder cannot attribute it, so the loop must say what it
        # knows — N finished, the crash is in class/module setup or teardown
        # — and exit 2, never "before any test started".
        _write_module(tmp_path, "setup_crash_tests", CRASH_IN_SETUPCLASS_MODULE)
        out_txt = tmp_path / "run.txt"
        proc = run_cli(
            "run",
            "--discover-root",
            str(tmp_path),
            "--label",
            "setup_crash_tests",
            "--parsed-output",
            str(out_txt),
        )
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "between tests after 2 finished" in proc.stderr
        assert "class/module setup or teardown" in proc.stderr
        assert "rerun with --label <module> to isolate" in proc.stderr
        assert "before any test started" not in proc.stderr
        lines = parsed_lines(out_txt.read_text(encoding="utf-8"))
        assert lines["setup_crash_tests.AFinishes.test_a"].startswith("OK    ")
        assert lines["setup_crash_tests.AFinishes.test_b"].startswith("OK    ")
        assert "setup_crash_tests.BDiesInSetUpClass.test_c" not in lines

    def test_segfault_is_recorded_and_the_rest_still_runs(self, tmp_path: pathlib.Path) -> None:
        _write_module(tmp_path, "crash_tests", CRASH_MODULE)
        out_txt, out_json = tmp_path / "run.txt", tmp_path / "run.json"
        proc = run_cli(
            "run",
            "--discover-root",
            str(tmp_path),
            "--label",
            "crash_tests",
            "--parsed-output",
            str(out_txt),
            "--json",
            str(out_json),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        lines = parsed_lines(out_txt.read_text(encoding="utf-8"))
        assert lines["crash_tests.Crashy.test_a_before"].startswith("OK    ")
        assert lines["crash_tests.Crashy.test_b_segfault"].startswith(
            "ERROR crash_tests.Crashy.test_b_segfault | process crashed (signal 11)"
        )
        assert lines["crash_tests.Crashy.test_c_after"].startswith("OK    ")
        data = json.loads(out_json.read_text("utf-8"))
        assert data["restarts"] == 1
        assert data["crashes"] == ["crash_tests.Crashy.test_b_segfault"]
        text = out_txt.read_text(encoding="utf-8")
        assert "crashes isolated: 1 (listed above as ERROR: process crashed)" in text

    def test_max_restarts_zero_is_could_not_run(self, tmp_path: pathlib.Path) -> None:
        _write_module(tmp_path, "crash_tests", CRASH_MODULE)
        proc = run_cli(
            "run",
            "--discover-root",
            str(tmp_path),
            "--label",
            "crash_tests",
            "--max-restarts",
            "0",
            "--parsed-output",
            str(tmp_path / "run.txt"),
        )
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "max restarts" in (proc.stdout + proc.stderr)

    def test_timeout_is_isolated_like_a_crash(self, tmp_path: pathlib.Path) -> None:
        _write_module(tmp_path, "hang_tests", HANG_MODULE)
        out_txt, out_json = tmp_path / "run.txt", tmp_path / "run.json"
        proc = run_cli(
            "run",
            "--discover-root",
            str(tmp_path),
            "--label",
            "hang_tests",
            "--timeout",
            "3",
            "--parsed-output",
            str(out_txt),
            "--json",
            str(out_json),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        lines = parsed_lines(out_txt.read_text(encoding="utf-8"))
        assert lines["hang_tests.Hangs.test_b_hangs"].startswith(
            "ERROR hang_tests.Hangs.test_b_hangs | process timed out"
        )
        assert lines["hang_tests.Hangs.test_c_after"].startswith("OK    ")
        assert json.loads(out_json.read_text("utf-8"))["restarts"] == 1


# --------------------------------------------------------------------------- #
# argv hardening (S1 / S2 of the #2517 review)
# --------------------------------------------------------------------------- #


class TestArgvHardening:
    def test_traversal_tag_is_refused_before_touching_the_cache(
        self, tmp_path: pathlib.Path
    ) -> None:
        # S1: ``<cache>/deep/../../x.partial`` is ``<tmp>/x.partial``; the old
        # code rmtree'd it before the clone even failed.
        cache = tmp_path / "cache" / "deep"
        cache.mkdir(parents=True)
        outside = tmp_path / "x.partial"
        outside.mkdir()
        (outside / "keep.txt").write_text("still here", encoding="utf-8")
        proc = run_cli("run", "--django-tag", "../../x", "--cache-dir", str(cache))
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "refusing to run" in proc.stderr and "'..'" in proc.stderr
        assert (outside / "keep.txt").read_text(encoding="utf-8") == "still here"
        assert "cloning" not in proc.stderr

    @pytest.mark.parametrize(
        "tag",
        ["../../x", "a/b", "a\\b", "-rf", "5.2 16", "", "5.2.16\n", ".."],
    )
    def test_validate_tag_refuses(self, tag: str) -> None:
        assert _runner().validate_tag(tag) is not None

    @pytest.mark.parametrize("tag", ["5.2.16", "5.2", "main", "stable-5.2.x", "4.2.0a1"])
    def test_validate_tag_accepts_ordinary_tags(self, tag: str) -> None:
        assert _runner().validate_tag(tag) is None

    def test_ensure_checkout_refuses_a_path_outside_the_cache(self, tmp_path: pathlib.Path) -> None:
        # The belt under validate_tag: containment is asserted on the resolved paths.
        runner = _runner()
        cache = tmp_path / "cache" / "deep"
        outside = tmp_path / "x.partial"
        outside.mkdir()
        assert runner.ensure_checkout("../../x", cache, quiet=True) is None
        assert outside.is_dir()
        runner.validate_tag = lambda tag: None  # bypass the first line of defence
        assert runner.ensure_checkout("../../x", cache, quiet=True) is None
        assert outside.is_dir()

    def test_option_looking_label_is_refused_and_writes_no_baseline(
        self, tmp_path: pathlib.Path
    ) -> None:
        # S2: ``--label=--gate-off`` used to reach the child as a bare
        # ``--gate-off``, run Django against itself, and — because the parent
        # never saw the flag — WRITE a ``ran: 0`` baseline.
        baseline = tmp_path / "b.json"
        proc = run_cli(
            "run",
            "--label=--gate-off",
            "--write-baseline",
            "--baseline",
            str(baseline),
            "--discover-root",
            str(tmp_path),
        )
        assert proc.returncode == 2, proc.stdout + proc.stderr
        assert "refusing label(s) that look like options: --gate-off" in proc.stderr
        assert not baseline.exists()

    def test_labels_are_passed_after_a_double_dash(self) -> None:
        import argparse

        runner = _runner()
        args = argparse.Namespace(discover_root=None, gate_off=True, labels=["a", "b"])
        argv = runner._child_argv(args, pathlib.Path("/x"))
        assert argv[-3:] == ["--", "a", "b"]
        assert argv[argv.index("--") - 1] == "--gate-off"

    def test_baseline_refusal_covers_gate_off_discover_and_ran_zero(self) -> None:
        refuse = _runner().baseline_refusal
        assert refuse(gate_off=True, discover=False, engine_ran=10) is not None
        assert refuse(gate_off=False, discover=True, engine_ran=10) is not None
        refusal = refuse(gate_off=False, discover=False, engine_ran=0)
        assert refusal is not None and "ran == 0" in refusal
        assert refuse(gate_off=False, discover=False, engine_ran=1) is None


# --------------------------------------------------------------------------- #
# (3) the ratchet
# --------------------------------------------------------------------------- #


def _result(
    percent_value: float, all_percent: float, *, ran: int = 1047, tag: str = "5.2.16"
) -> dict:
    return {
        "django": tag,
        "tag": tag,
        "ok": 456,
        "fail": 227,
        "error": 364,
        "ran": ran,
        "percent": percent_value,
        "all": {
            "ok": 863,
            "fail": 229,
            "error": 364,
            "ran": 1456,
            "skipped": 14,
            "percent": all_percent,
        },
        "untouched_failures": 0,
        "crashes": [],
    }


class TestRatchetCompare:
    def test_equal_is_zero(self) -> None:
        code, _ = compare(_result(43.55, 59.27), _result(43.55, 59.27))
        assert code == 0

    def test_a_drop_is_one_and_names_both_numbers(self) -> None:
        code, lines = compare(_result(43.55, 59.27), _result(43.54, 59.27))
        assert code == 1
        joined = "\n".join(lines)
        assert "43.55" in joined and "43.54" in joined

    def test_an_improvement_is_zero(self) -> None:
        code, _ = compare(_result(43.55, 59.27), _result(44.00, 59.27))
        assert code == 0

    def test_a_drop_in_the_whole_label_only_is_one(self) -> None:
        code, lines = compare(_result(43.55, 59.27), _result(43.55, 59.20))
        assert code == 1
        assert "whole label" in "\n".join(lines)

    def test_nothing_reached_djust_is_one_not_a_hundred_percent_pass(self) -> None:
        # install() silently not taking would score 100 % on the whole label;
        # the ratchet must not accept that as an improvement.
        current = _result(0.0, 100.0, ran=0)
        code, lines = compare(_result(43.55, 59.27), current)
        assert code == 1
        assert "no test reached the djust engine" in "\n".join(lines)

    def test_tag_mismatch_is_a_warning_not_a_verdict(self) -> None:
        code, lines = compare(
            _result(43.55, 59.27, tag="5.2.16"), _result(43.54, 59.27, tag="5.2.17")
        )
        assert code == 0
        assert any(line.startswith("WARNING") and "5.2.17" in line for line in lines)

    def test_cli_missing_baseline_is_two(self, tmp_path: pathlib.Path) -> None:
        current = tmp_path / "run.json"
        current.write_text(json.dumps(_result(43.55, 59.27)), encoding="utf-8")
        proc = run_cli("compare", "--baseline", str(tmp_path / "nope.json"), "--json", str(current))
        assert proc.returncode == 2, proc.stdout + proc.stderr

    def test_cli_drop_is_one(self, tmp_path: pathlib.Path) -> None:
        base, current = tmp_path / "base.json", tmp_path / "run.json"
        base.write_text(json.dumps(_result(43.55, 59.27)), encoding="utf-8")
        current.write_text(json.dumps(_result(40.00, 59.27)), encoding="utf-8")
        proc = run_cli("compare", "--baseline", str(base), "--json", str(current))
        assert proc.returncode == 1, proc.stdout + proc.stderr
        assert "43.55" in proc.stdout and "40.00" in proc.stdout


# --------------------------------------------------------------------------- #
# the adapter
# --------------------------------------------------------------------------- #

ADAPTER_PROBE = textwrap.dedent(
    """
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import django
    from django.conf import settings
    settings.configure(TEMPLATES=[], INSTALLED_APPS=[], SECRET_KEY="x")
    django.setup()

    import django.template.backends.django as backend_mod
    real_engine = backend_mod.Engine

    from scripts.lib.django_template_suite import adapter
    adapter.install()

    import django.template
    import django.template.engine
    from django.template import Context, Engine, TemplateDoesNotExist

    out = {
        "backend_engine_is_real": backend_mod.Engine is real_engine,
        "engine_module_rebound": django.template.engine.Engine is adapter.DjustEngine,
        "package_rebound": django.template.Engine is adapter.DjustEngine,
        "is_subclass_of_real": issubclass(adapter.DjustEngine, real_engine),
    }
    loaders = [
        (
            "django.template.loaders.cached.Loader",
            [("django.template.loaders.locmem.Loader", {"a.html": "{{ x }}!", "b.html": "B"})],
        )
    ]
    engine = Engine(loaders=loaders)
    before = adapter.TOUCH["count"]
    out["djust_render"] = engine.render_to_string("a.html", {"x": "hi"})
    out["django_render"] = real_engine(loaders=loaders).render_to_string("a.html", {"x": "hi"})
    try:
        engine.get_template("missing.html")
        out["missing"] = "no exception"
    except TemplateDoesNotExist as exc:
        out["missing"] = "TemplateDoesNotExist"
        out["tried"] = len(exc.tried)
    out["select"] = engine.select_template(["missing.html", "b.html"]).render(Context({}))
    out["touch_delta"] = adapter.TOUCH["count"] - before
    out["repr"] = repr(engine)
    # F2: the adapter must hand back djust's template object, not Django's.
    out["get_template_type"] = type(engine.get_template("a.html")).__name__
    out["from_string_type"] = type(engine.from_string("{{ x }}")).__name__
    out["django_template_type"] = type(real_engine(loaders=loaders).get_template("a.html")).__name__
    # F4: a bad loader must raise at get_template time, as on Django, never at construction.
    try:
        lazy = Engine(loaders=["no.such.Loader"])
        out["bad_loader_constructs"] = True
    except Exception as exc:  # noqa: BLE001 — the probe reports whatever happened
        out["bad_loader_constructs"] = type(exc).__name__
    else:
        try:
            lazy.get_template("a.html")
            out["bad_loader_get_template"] = "no exception"
        except ImportError as exc:
            out["bad_loader_get_template"] = type(exc).__name__
    print(json.dumps(out))
    """
)


class TestAdapterInSubprocess:
    @pytest.fixture(scope="class")
    def probe(self) -> dict:
        proc = subprocess.run(  # noqa: S603
            [sys.executable, "-c", ADAPTER_PROBE, str(REPO)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_install_rebinds_the_two_names_and_leaves_the_backend_real(self, probe: dict) -> None:
        assert probe["backend_engine_is_real"] is True
        assert probe["engine_module_rebound"] is True
        assert probe["package_rebound"] is True
        assert probe["is_subclass_of_real"] is True

    def test_render_to_string_matches_django(self, probe: dict) -> None:
        assert probe["djust_render"] == "hi!"
        assert probe["djust_render"] == probe["django_render"]

    def test_missing_template_raises_django_exception_with_tried(self, probe: dict) -> None:
        assert probe["missing"] == "TemplateDoesNotExist"
        assert probe["tried"] >= 1

    def test_select_template_falls_through(self, probe: dict) -> None:
        assert probe["select"] == "B"

    def test_touch_counter_and_repr(self, probe: dict) -> None:
        # render_to_string, get_template(missing), select_template (2 lookups) = 4.
        assert probe["touch_delta"] == 4
        assert probe["repr"].startswith("<Engine:")

    def test_adapter_produces_djust_templates_not_djangos(self, probe: dict) -> None:
        # F2: identical output ("hi!") from both engines cannot tell the seam
        # apart; the template TYPE can.
        assert probe["get_template_type"] == "DjustTemplate"
        assert probe["from_string_type"] == "DjustTemplate"
        assert probe["django_template_type"] == "Template"

    def test_bad_loader_raises_at_get_template_not_construction(self, probe: dict) -> None:
        # F4: ``template_dirs`` is lazy, so the suite's "bad loader raises on
        # first use" tests see the exception where Django raises it.
        assert probe["bad_loader_constructs"] is True
        assert probe["bad_loader_get_template"] in ("ModuleNotFoundError", "ImportError")


# --------------------------------------------------------------------------- #
# the doc claim
# --------------------------------------------------------------------------- #


class TestDocClaimMatchesBaseline:
    def test_doc_percentage_equals_the_baseline(self) -> None:
        assert BASELINE.exists(), "the baseline file is committed by the runner's --write-baseline"
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        claim_lines = [
            line for line in DOC.read_text(encoding="utf-8").splitlines() if DOC_MARKER in line
        ]
        if not claim_lines:
            pytest.skip(
                f"{DOC.name} has no {DOC_MARKER} line yet (Stage 9 adds it); "
                "the test pins the claim to the baseline once it exists"
            )
        m = re.search(r"(\d+\.\d{2})\s?%", claim_lines[0])
        assert m, f"no NN.NN% figure on the marker line: {claim_lines[0]!r}"
        assert float(m.group(1)) == baseline["percent"]

    def test_baseline_schema(self) -> None:
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        for key in ("django", "tag", "ok", "fail", "error", "ran", "percent", "all", "crashes"):
            assert key in baseline, key
        assert baseline["ran"] == baseline["ok"] + baseline["fail"] + baseline["error"]
        assert baseline["percent"] == percent(baseline["ok"], baseline["ran"])
        assert baseline["untouched_failures"] == 0

    def test_baseline_matches_the_local_run_when_there_is_one(self) -> None:
        """The doc line is pinned to the baseline — so the pair can go stale
        TOGETHER and stay green (#2563 review).

        `test_doc_percentage_equals_the_baseline` compares the doc to the
        baseline, and nothing compares the baseline to the ENGINE. A PR that
        moves 34 cells and forgets `--write-baseline` passes both; so would a
        PR that DROPS 34, because `compare` needs a Django checkout and a
        multi-minute run, so neither CI nor the pre-push hook calls it.

        The cheapest thing that closes the loop is the artefact the developer
        already produced: `make django-template-suite` writes
        `.django-src/last-run.json`. When it exists and was measured against
        the same Django tag, the committed baseline must EQUAL it. Equality,
        not `>=`, because both directions are bugs and they need different
        messages:

        * committed < measured — the baseline (and therefore the doc figure)
          is stale; re-run with `--write-baseline`. This is the case this
          test was written for: the run said 527, the file said 493.
        * committed > measured — a real drop, which is what the ratchet
          exists to catch and what `compare` would have said.

        Skipped when there is no local run (CI, a fresh clone), so it never
        demands a Django checkout — it only refuses to let one be ignored.
        `.django-src/` is gitignored, so this reads a local artefact only.
        """
        run_json = REPO / ".django-src" / "last-run.json"
        if not run_json.exists():
            pytest.skip("no local scoreboard run — `make django-template-suite` writes one")
        run = json.loads(run_json.read_text(encoding="utf-8"))
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        if run.get("tag") != baseline.get("tag"):
            pytest.skip(
                f"local run is Django {run.get('tag')}, baseline is {baseline.get('tag')} — "
                "not comparable"
            )
        if not run.get("ran"):
            pytest.skip("local run reached no engine cells (a gate-off or an aborted run)")
        if baseline["ok"] < run["ok"]:
            raise AssertionError(
                f"the committed baseline is STALE: it records {baseline['ok']} passing engine "
                f"cells but the local run measured {run['ok']}. The doc's "
                f"{DOC_MARKER} figure is pinned to the baseline, so both are wrong together. "
                "Re-run `scripts/run-django-template-suite.py run --write-baseline` and update "
                "the doc line to the new percentage."
            )
        if baseline["ok"] > run["ok"]:
            raise AssertionError(
                f"the local run DROPPED: {run['ok']} passing engine cells against the "
                f"baseline's {baseline['ok']}. This is the regression the ratchet exists to "
                "catch — run `scripts/run-django-template-suite.py compare --json "
                ".django-src/last-run.json` for the per-figure breakdown."
            )
        assert baseline["ran"] == run["ran"]


# --------------------------------------------------------------------------- #
# (4) the real checkout — skipped unless it is present
# --------------------------------------------------------------------------- #


def _real_checkout() -> pathlib.Path | None:
    import django

    override = os.environ.get("DJUST_DJANGO_SRC")
    candidates = [pathlib.Path(override)] if override else []
    candidates.append(REPO / ".django-src" / django.__version__)
    for path in candidates:
        if (path / "tests" / "runtests.py").exists():
            return path
    return None


@pytest.mark.skipif(_real_checkout() is None, reason="no Django checkout under .django-src/")
class TestAgainstRealDjangoCheckout:
    LABEL = "template_tests.syntax_tests.test_if"

    def _run(self, tmp_path: pathlib.Path, *extra: str) -> dict:
        out_json = tmp_path / "run.json"
        args = ["run", "--label", self.LABEL, "--json", str(out_json), "--quiet", *extra]
        src = _real_checkout()
        assert src is not None
        if src != REPO / ".django-src" / src.name:
            args += ["--django-src", str(src)]
        proc = run_cli(*args)
        assert proc.returncode == 0, proc.stdout + proc.stderr[-3000:]
        return json.loads(out_json.read_text("utf-8"))

    def test_if_module_through_djust(self, tmp_path: pathlib.Path) -> None:
        data = self._run(tmp_path)
        assert len(data["tests"]) == 115
        assert data["fail"] >= 1
        assert sum(1 for t in data["tests"] if t["touched"]) >= 100

    def test_if_module_gate_off(self, tmp_path: pathlib.Path) -> None:
        data = self._run(tmp_path, "--gate-off")
        assert len(data["tests"]) == 115
        assert all(t["status"] == "OK" for t in data["tests"])
        assert data["ran"] == 0

    def test_write_baseline_refuses_when_nothing_reached_the_engine(
        self, tmp_path: pathlib.Path
    ) -> None:
        # S2 belt: ``test_smartif`` measures Django's parser against itself and
        # never builds an Engine — the adapter installs but sees no tests. A
        # baseline from it would read ``ran: 0`` and neuter ``compare``.
        src = _real_checkout()
        assert src is not None
        baseline = tmp_path / "b.json"
        args = [
            "run",
            "--label",
            "template_tests.test_smartif",
            "--write-baseline",
            "--baseline",
            str(baseline),
            "--quiet",
        ]
        if src != REPO / ".django-src" / src.name:
            args += ["--django-src", str(src)]
        proc = run_cli(*args)
        assert proc.returncode == 2, proc.stdout + proc.stderr[-3000:]
        assert "no test reached the djust engine (ran == 0)" in proc.stderr
        assert not baseline.exists()
