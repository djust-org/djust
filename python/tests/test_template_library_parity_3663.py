"""The parity ratchet for djust's own template libraries (#3663, #3669).

``scripts/template-library-parity.py`` renders every tag of ``djust_components``
and ``theme_tags`` that has a native handler through Django's engine (the
Python tag) and through ``DjustTemplateBackend`` (the Rust engine), classifies
each pair, and compares the table with ``scripts/template-library-parity-baseline.csv``.
This file is its test.

Everything that changes process-global state — the harness replaces ``uuid``
and ``datetime`` and configures Django's settings — runs in a SUBPROCESS, the
way ``test_django_template_suite_2517`` runs its adapter. That is the whole
isolation story: nothing here can reach another test in the worker, and nothing
another test did (cleared handlers, overridden settings) can reach the
measurement, because the subprocess starts Django cold through the real
``AppConfig.ready()`` path. ``TestNothingLeaks`` pins the first half.

What each class proves:

* ``TestTheRatchet`` — the measured table equals the baseline, tag for tag, and
  the baseline file is canonical, derived (no typed counts) and HTML-free.
* ``TestClassifier`` — the classes, from synthetic engine outputs. Not a
  tautology: each case would be classified differently if the rule changed.
* ``TestCompare`` — worse fails, better fails, a new tag fails, a removed tag
  fails; each names the tag and says what to do.
* ``TestGateOffs`` — the ratchet is load-bearing: Django against itself is
  all-``IDENTICAL`` (the harness adds no difference), a difference planted in
  the Python tag IS caught, and a mutated baseline IS caught.
* ``TestParameterDiscovery`` — the interpreter-independent source lookup.

Refs #3663, #3669, #3655, #1468, #1459, #1646, #1125.
"""

from __future__ import annotations

import csv
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.lib.template_library_parity import classify, harness, variants

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "template-library-parity.py"
BASELINE = ROOT / "scripts" / "template-library-parity-baseline.csv"
TIMEOUT = 300


@dataclass
class Run:
    returncode: int
    stdout: str
    stderr: str
    rows: dict[str, classify.TagRow]
    unmeasured: list[str]


def _env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "python"), env.get("PYTHONPATH")]))
    return env


def _start(args: list[str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _finish(proc: subprocess.Popen[str], json_path: Path | None) -> Run:
    stdout, stderr = proc.communicate(timeout=TIMEOUT)
    rows: dict[str, classify.TagRow] = {}
    unmeasured: list[str] = []
    if json_path is not None and json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        rows = {
            tag: classify.TagRow(r["class"], r["aspects"], r["errors"], int(r["compared"]))
            for tag, r in payload["rows"].items()
        }
        unmeasured = payload["unmeasured"]
    return Run(proc.returncode, stdout, stderr, rows, unmeasured)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Run]:
    """The three subprocess runs, started together (each is ~5 s, mostly startup)."""
    out = tmp_path_factory.mktemp("parity3663")
    paths = {name: out / (name + ".json") for name in ("check", "self", "plant")}
    procs = {
        "check": _start(["--json", str(paths["check"])]),
        "self": _start(["--self-compare", "--json", str(paths["self"])]),
        "plant": _start(["--only", "kbd,icon", "--plant", "kbd", "--json", str(paths["plant"])]),
    }
    return {name: _finish(proc, paths[name]) for name, proc in procs.items()}


@pytest.fixture(scope="module")
def baseline() -> dict[str, classify.TagRow]:
    return classify.read_baseline(BASELINE)


# --------------------------------------------------------------------------- #


class TestTheRatchet:
    def test_every_tag_matches_the_baseline(self, runs):
        run = runs["check"]
        assert run.returncode == 0, (
            "the parity table differs from scripts/template-library-parity-baseline.csv\n"
            "%s\n%s" % (run.stdout, run.stderr[-2000:])
        )

    def test_the_measured_tags_are_exactly_the_baseline_tags(self, runs, baseline):
        assert set(runs["check"].rows) == set(baseline)

    def test_no_native_tag_is_silently_skipped(self, runs):
        """Measured plus unmeasured is every tag of both libraries — derived, not typed."""
        from django.template.library import import_library

        expected = {
            "%s:%s" % (label, name)
            for label, dotted in harness.LIBRARIES.items()
            for name in import_library(dotted).tags
        }
        run = runs["check"]
        assert set(run.rows) | set(run.unmeasured) == expected
        assert not set(run.rows) & set(run.unmeasured)

    def test_every_measured_tag_was_actually_compared(self, runs):
        """A tag whose variants all errored would look 'unchanged'; it must say so."""
        unexercised = [
            tag for tag, row in runs["check"].rows.items() if row.tag_class == "UNEXERCISED"
        ]
        assert unexercised == []
        assert all(row.compared > 0 for row in runs["check"].rows.values())

    def test_the_libraries_covered_are_the_two_djust_ships(self):
        """An intent pin, and the module names a change to either library selects this file by."""
        assert harness.LIBRARIES == {
            "djust_components": "djust.components.templatetags.djust_components",
            "theme_tags": "djust.theming.templatetags.theme_tags",
        }

    def test_the_baseline_file_is_canonical(self):
        text = BASELINE.read_text(encoding="utf-8")
        assert classify.render_baseline(classify.parse_baseline(text)) == text, (
            "the baseline was edited by hand; regenerate it with "
            "`make template-library-parity-baseline`"
        )

    def test_the_baseline_records_no_markup(self):
        assert "<" not in BASELINE.read_text(encoding="utf-8")

    def test_the_baseline_has_one_short_row_per_tag(self):
        with BASELINE.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        assert rows[0] == list(classify.HEADER)
        assert all(len(r) == len(classify.HEADER) for r in rows)
        assert max(len(",".join(r)) for r in rows) < 200

    def test_the_exclusion_table_is_the_recorded_one(self, baseline):
        """Nothing is excluded. If a tag ever has to be, it needs a reason here
        (a tag that renders differently per run), and it is not in the baseline."""
        for tag, reason in harness.EXCLUDED.items():
            assert reason.strip(), "%s is excluded without a reason" % tag
            assert tag not in baseline
        assert harness.EXCLUDED == {}


class TestNothingLeaks:
    def test_this_process_still_has_the_real_uuid_and_clock(self, runs):
        """The harness patches ``uuid`` and ``datetime`` — in its own process only."""
        assert uuid.uuid4.__module__ == "uuid"
        assert datetime.date.today.__qualname__ == "date.today"
        assert datetime.datetime.now.__qualname__ == "datetime.now"

    def test_the_harness_refuses_an_already_configured_process(self):
        before = os.environ.get("DJANGO_SETTINGS_MODULE")
        with pytest.raises(RuntimeError, match="fresh process"):
            harness.configure_django()
        assert os.environ.get("DJANGO_SETTINGS_MODULE") == before


# --------------------------------------------------------------------------- #

T = classify.TagRow


def _measure(variant, py, rs, invalid=False):
    py_status, py_out = py
    rs_status, rs_out = rs
    return classify.measure_variant(variant, invalid, py_status, py_out, rs_status, rs_out)


OK = "ok"
ERR = "err"


class TestClassifier:
    def test_identical(self):
        assert classify.classify_variant(OK, "<b>x</b>", OK, "<b>x</b>") == classify.IDENTICAL

    def test_whitespace_only(self):
        a = "<div>\n  <b>x</b>\n</div>"
        b = "<div><b>x</b></div>"
        assert classify.classify_variant(OK, a, OK, b) == classify.WHITESPACE_ONLY

    def test_a_changed_word_is_different_not_whitespace(self):
        assert classify.classify_variant(OK, "<b>x</b>", OK, "<b>y</b>") == classify.DIFFERENT

    def test_one_engine_error_names_which_engine(self):
        assert classify.classify_variant(ERR, "boom", OK, "<b>x</b>") == classify.ERR_ONE_ENGINE
        assert classify.classify_variant(OK, "<b>x</b>", ERR, "boom") == classify.ERR_ONE_ENGINE
        both = classify.classify_variant(ERR, "a", ERR, "b")
        assert both == classify.BOTH_ERROR

    def test_aspects_name_what_differs(self):
        v = _measure("typical", (OK, '<div class="a">x</div>'), (OK, '<div class="b">x</div>'))
        assert v.aspects == ("classes",)
        v = _measure("typical", (OK, "<i>x</i>"), (OK, "<b>x</b>"))
        assert v.aspects == ("tags",)
        v = _measure("typical", (OK, '<b role="tab">x</b>'), (OK, '<b role="button">x</b>'))
        assert v.aspects == ("a11y",)
        v = _measure("typical", (OK, '<b dj-click="a">x</b>'), (OK, '<b dj-click="b">x</b>'))
        assert v.aspects == ("data/dj-attrs",)
        v = _measure("typical", (OK, '<b id="a">x</b>'), (OK, '<b id="b">x</b>'))
        assert v.aspects == ("ids",)
        v = _measure("typical", (OK, '<b title="a">x</b>'), (OK, '<b title="b">x</b>'))
        assert v.aspects == ("other-attrs",)

    def test_uuids_in_ids_are_not_a_difference(self):
        a = '<b id="x-0a1b2c3d-0000-0000-0000-000000000001">x</b>'
        b = '<b id="x-0a1b2c3d-0000-0000-0000-000000000002">x</b>'
        assert _measure("typical", (OK, a), (OK, b)).aspects == ()

    def test_the_scan_survives_a_gt_in_an_attribute_a_comment_and_a_script(self):
        markup = '<b title="a > b" class="c d"><!-- <i> -->x<script>if (a<b) {}</script></b>'
        found = classify.scan(markup)
        assert found.tags == ["b", "script"]
        assert found.classes == ["c", "d"]
        assert found.other == [("title", "a > b")]
        assert found.text.strip() == "x"

    def test_unescaped_markup_is_an_escaping_difference(self):
        raw = (OK, "<p><b>safe</b></p>")
        escaped = (OK, "<p>&lt;b&gt;safe&lt;/b&gt;</p>")
        v = _measure("safe", raw, escaped)
        assert v.cls == classify.DIFFERENT
        assert "escaping" in v.aspects

    def test_a_javascript_url_only_one_side_is_an_escaping_difference(self):
        a = (OK, '<img src="javascript:alert(1)">')
        b = (OK, '<img src="#">')
        assert "escaping" in _measure("typical", a, b).aspects

    def test_addresses_are_normalised(self):
        assert classify.norm_addr("<built-in at 0x7f3a9c001234>") == "<built-in at 0xADDR>"

    # --- the tag aggregate ----------------------------------------------------

    def _tag(self, *specs):
        results = [_measure(*spec) for spec in specs]
        return classify.classify_tag(results)

    def test_a_tag_with_every_variant_identical_is_identical(self):
        row = self._tag(("typical", (OK, "a"), (OK, "a")), ("evil", (OK, "b"), (OK, "b")))
        assert row == T(classify.IDENTICAL, "", "", 2)

    def test_a_difference_outside_the_escape_probes_is_markup(self):
        row = self._tag(
            ("typical", (OK, '<i class="a">x</i>'), (OK, '<i class="b">x</i>')),
            ("safe", (OK, "<b>safe</b>"), (OK, "&lt;b&gt;")),
        )
        assert row.tag_class == classify.DIFFERENT
        assert "classes" in row.aspects.split("+")

    def test_a_difference_only_in_literal_and_safe_is_escape_policy(self):
        # Django passes a mark_safe value into an attribute raw; Rust escapes it.
        # Unescaped, the two attribute values are the same string: only the
        # escaping differs.
        raw = '<p title="<b>safe</b>">x</p>'
        escaped = '<p title="&lt;b&gt;safe&lt;/b&gt;">x</p>'
        row = self._tag(
            ("typical", (OK, "<i>x</i>"), (OK, "<i>x</i>")),
            ("safe", (OK, raw), (OK, escaped)),
        )
        assert row.tag_class == "DIFFERENT-escape-policy-only"
        assert row.aspects == "escaping"

    def test_the_same_difference_outside_literal_and_safe_is_markup(self):
        raw = '<p title="<b>safe</b>">x</p>'
        escaped = '<p title="&lt;b&gt;safe&lt;/b&gt;">x</p>'
        row = self._tag(("typical", (OK, raw), (OK, escaped)))
        assert row.tag_class == classify.DIFFERENT

    def test_a_one_engine_error_is_recorded_even_when_the_tag_is_also_different(self):
        row = self._tag(
            ("typical", (OK, "<i>x</i>"), (OK, "<b>x</b>")),
            ("none", (ERR, "TypeError"), (OK, "<b>x</b>")),
        )
        assert row.tag_class == classify.DIFFERENT
        assert row.errors == "none:python"

    def test_the_errors_column_says_which_engine_rejected_which_variant(self):
        row = self._tag(
            ("typical", (OK, "a"), (OK, "a")),
            ("none", (OK, "a"), (ERR, "boom")),
            ("missing", (ERR, "boom"), (OK, "a")),
        )
        assert row.tag_class == "NOT-RENDERABLE-IN-ONE-ENGINE"
        assert row.errors == "missing:python+none:rust"

    def test_an_invalid_call_does_not_decide_the_class_but_is_still_recorded(self):
        row = self._tag(
            ("typical", (OK, "a"), (OK, "a")),
            ("noargs", (ERR, "missing required"), (OK, "a"), True),
        )
        assert row.tag_class == classify.IDENTICAL
        assert row.errors == "noargs:python"

    def test_a_tag_where_nothing_rendered_is_unexercised_not_identical(self):
        row = self._tag(("typical", (ERR, "x"), (ERR, "y")))
        assert row == T("UNEXERCISED", "", "", 0)

    def test_compared_counts_only_variants_both_engines_rendered(self):
        row = self._tag(
            ("typical", (OK, "a"), (OK, "a")),
            ("none", (ERR, "x"), (OK, "a")),
            ("missing", (ERR, "x"), (ERR, "x")),
        )
        assert row.compared == 1


class TestCompare:
    BASE = {
        "lib:a": T(classify.IDENTICAL, "", "", 10),
        "lib:b": T(classify.DIFFERENT, "classes+text", "", 8),
        "lib:c": T("DIFFERENT-escape-policy-only", "escaping", "none:python", 9),
    }

    def test_equal_tables_have_no_findings(self):
        assert classify.compare(dict(self.BASE), self.BASE) == []

    def test_a_regressed_class_is_worse_and_names_the_tag(self):
        now = dict(self.BASE, **{"lib:a": T(classify.DIFFERENT, "classes", "", 10)})
        [finding] = classify.compare(now, self.BASE)
        assert (finding.tag, finding.kind) == ("lib:a", classify.WORSE)
        assert "IDENTICAL -> DIFFERENT" in finding.message
        assert "make template-library-parity-baseline" in finding.message

    def test_an_improved_class_fails_and_says_to_update_the_baseline(self):
        now = dict(self.BASE, **{"lib:b": T(classify.IDENTICAL, "", "", 8)})
        [finding] = classify.compare(now, self.BASE)
        assert (finding.tag, finding.kind) == ("lib:b", classify.BETTER)
        assert "make template-library-parity-baseline" in finding.message

    def test_a_new_aspect_on_a_different_tag_is_worse(self):
        now = dict(self.BASE, **{"lib:b": T(classify.DIFFERENT, "a11y+classes+text", "", 8)})
        [finding] = classify.compare(now, self.BASE)
        assert finding.kind == classify.WORSE
        assert "a11y" in finding.message

    def test_a_dropped_aspect_is_better(self):
        now = dict(self.BASE, **{"lib:b": T(classify.DIFFERENT, "classes", "", 8)})
        [finding] = classify.compare(now, self.BASE)
        assert finding.kind == classify.BETTER

    def test_a_new_one_engine_error_is_worse(self):
        now = dict(self.BASE, **{"lib:a": T(classify.IDENTICAL, "", "typical:rust", 10)})
        [finding] = classify.compare(now, self.BASE)
        assert finding.kind == classify.WORSE
        assert "typical:rust" in finding.message

    def test_lost_coverage_is_worse(self):
        now = dict(self.BASE, **{"lib:a": T(classify.IDENTICAL, "", "", 7)})
        [finding] = classify.compare(now, self.BASE)
        assert finding.kind == classify.WORSE
        assert "coverage lost" in finding.message

    def test_better_in_one_way_and_worse_in_another_is_worse(self):
        now = dict(self.BASE, **{"lib:b": T(classify.DIFFERENT, "a11y", "", 8)})
        [finding] = classify.compare(now, self.BASE)
        assert finding.kind == classify.WORSE

    def test_a_tag_with_no_baseline_row_fails_until_classified(self):
        now = dict(self.BASE, **{"lib:new": T(classify.IDENTICAL, "", "", 3)})
        [finding] = classify.compare(now, self.BASE)
        assert (finding.tag, finding.kind) == ("lib:new", classify.NEW)

    def test_a_baseline_row_for_a_vanished_tag_fails(self):
        now = {k: v for k, v in self.BASE.items() if k != "lib:c"}
        [finding] = classify.compare(now, self.BASE)
        assert (finding.tag, finding.kind) == ("lib:c", classify.STALE)

    def test_a_restricted_run_skips_the_new_and_stale_checks_only(self):
        now = {"lib:a": T(classify.DIFFERENT, "classes", "", 10)}
        [finding] = classify.compare(now, self.BASE, partial=True)
        assert finding.kind == classify.WORSE
        assert classify.compare({"lib:zz": self.BASE["lib:a"]}, self.BASE, partial=True) == []

    def test_a_baseline_that_does_not_parse_is_refused(self):
        with pytest.raises(ValueError, match="header"):
            classify.parse_baseline("tag,class\nlib:a,IDENTICAL\n")
        with pytest.raises(ValueError, match="unknown class"):
            classify.parse_baseline(",".join(classify.HEADER) + "\nlib:a,SORT-OF-SAME,,,3\n")
        with pytest.raises(ValueError, match="twice"):
            classify.parse_baseline(
                ",".join(classify.HEADER) + "\nlib:a,IDENTICAL,,,3\nlib:a,IDENTICAL,,,3\n"
            )


class TestGateOffs:
    """Revert each guarantee in turn and show something goes red (#1468)."""

    def test_django_against_itself_is_identical_for_every_tag(self, runs):
        run = runs["self"]
        assert run.returncode == 0, run.stdout + run.stderr[-2000:]
        assert run.rows, "the self-comparison measured nothing"
        assert {r.tag_class for r in run.rows.values()} == {classify.IDENTICAL}
        assert {r.errors for r in run.rows.values()} == {""}
        # ... over the same tags the real run measures, not a subset
        assert set(run.rows) == set(runs["check"].rows)

    def test_a_difference_planted_in_the_python_tag_is_caught(self, runs, baseline):
        run = runs["plant"]
        assert set(run.rows) == {"djust_components:kbd", "djust_components:icon"}
        # the mutation applied: kbd is no longer what the baseline says
        assert baseline["djust_components:kbd"].tag_class == classify.IDENTICAL
        assert run.rows["djust_components:kbd"].tag_class == classify.DIFFERENT
        # the ratchet said so, naming the tag, with a failing exit code
        assert run.returncode == 1, run.stdout + run.stderr[-2000:]
        assert "djust_components:kbd: WORSE" in run.stdout
        # and only that tag: the control is untouched
        assert run.rows["djust_components:icon"] == baseline["djust_components:icon"]
        assert "djust_components:icon" not in run.stdout

    def test_a_flipped_baseline_row_is_caught_in_both_directions(self, runs, baseline):
        measured = runs["check"].rows
        identical = next(
            t for t, r in sorted(measured.items()) if r.tag_class == classify.IDENTICAL
        )
        different = next(
            t for t, r in sorted(measured.items()) if r.tag_class == classify.DIFFERENT
        )

        claims_worse = dict(baseline)
        claims_worse[identical] = T(classify.DIFFERENT, "classes", "", baseline[identical].compared)
        [finding] = classify.compare(measured, claims_worse)
        assert (finding.tag, finding.kind) == (identical, classify.BETTER)

        claims_better = dict(baseline)
        claims_better[different] = T(classify.IDENTICAL, "", "", baseline[different].compared)
        [finding] = classify.compare(measured, claims_better)
        assert (finding.tag, finding.kind) == (different, classify.WORSE)

        assert claims_worse != baseline and claims_better != baseline  # the mutations applied
        assert classify.compare(measured, baseline) == []  # and the unmutated control is clean

    def test_a_deleted_and_an_added_baseline_row_are_caught(self, runs, baseline):
        measured = runs["check"].rows
        victim = sorted(baseline)[0]
        without = {k: v for k, v in baseline.items() if k != victim}
        assert [(f.tag, f.kind) for f in classify.compare(measured, without)] == [
            (victim, classify.NEW)
        ]
        with_extra = dict(baseline, **{"lib:ghost": baseline[victim]})
        assert [(f.tag, f.kind) for f in classify.compare(measured, with_extra)] == [
            ("lib:ghost", classify.STALE)
        ]


# --------------------------------------------------------------------------- #


class TestParameterDiscovery:
    def _module(self, tmp_path, monkeypatch, name, source):
        path = tmp_path / (name + ".py")
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        harness._top_level_sources.cache_clear()
        return module

    def test_a_class_bound_twice_resolves_to_the_last_binding(self, tmp_path, monkeypatch):
        module = self._module(
            tmp_path,
            monkeypatch,
            "dup3663",
            """
            class Handler:
                def keys(self, kw):
                    return kw["first"]

            class Handler:
                def keys(self, kw):
                    return kw["second"]
            """,
        )
        source = harness._source_of(module.Handler)
        assert '"second"' in source and '"first"' not in source
        assert harness._keys_of(source) == {"second"}

    def test_a_decorated_wrapper_resolves_to_the_wrapped_function(self, tmp_path, monkeypatch):
        module = self._module(
            tmp_path,
            monkeypatch,
            "wrap3663",
            """
            import functools

            def inner(kw):
                return kw.get("wanted")

            @functools.wraps(inner)
            def outer(kw):
                return inner(kw)
            """,
        )
        assert harness._keys_of(harness._source_of(module.outer)) == {"wanted"}

    def test_a_name_that_is_not_top_level_has_no_source_rather_than_a_guess(
        self, tmp_path, monkeypatch
    ):
        module = self._module(
            tmp_path,
            monkeypatch,
            "nested3663",
            """
            def make():
                def hidden(kw):
                    return kw.get("x")
                return hidden
            """,
        )
        assert harness._source_of(module.make()) == ""


class TestVariantGeneration:
    def _info(self, **kw):
        base = dict(lib="lib", name="widget", py_kind="simple_tag")
        base.update(kw)
        return variants.TagInfo(**base)

    def test_a_simple_tag_is_called_by_keyword_positionally_and_with_nothing(self):
        info = self._info(
            sig=[
                ("label", "req", "POSITIONAL_OR_KEYWORD"),
                ("url", "opt", "POSITIONAL_OR_KEYWORD"),
            ]
        )
        generated = {
            v: (src, invalid)
            for v, src, invalid in variants.make_variants(info, lambda c: None, {})
        }
        assert {"typical", "required_only", "positional_1", "positional_2", "noargs"} <= set(
            generated
        )
        assert generated["typical"][0] == (
            '{% load lib %}{% widget label="Sample text" url="/path/" %}'
        )
        assert generated["positional_1"][0] == '{% load lib %}{% widget "Sample text" %}'
        # a required parameter makes the no-argument call an INVALID usage
        assert generated["noargs"] == ("{% load lib %}{% widget %}", True)
        assert generated["typical"][1] is False

    def test_a_block_tag_gets_a_body_and_its_parent(self):
        info = self._info(py_kind="raw", py_keys=["id"], is_block=True, name="item")
        [typical] = [
            s for v, s, _ in variants.make_variants(info, lambda c: None, {}) if v == "typical"
        ]
        assert typical == '{% load lib %}{% item id="id1" %}' + variants.BODY + "{% enditem %}"
        child = self._info(py_kind="raw", py_keys=["id"], is_block=True, name="accordion_item")
        [wrapped] = [
            s for v, s, _ in variants.make_variants(child, lambda c: None, {}) if v == "typical"
        ]
        assert wrapped.startswith("{% load lib %}{% accordion %}{% accordion_item ")
        assert wrapped.endswith("{% endaccordion_item %}{% endaccordion %}")

    def test_generation_is_a_pure_function_of_the_info(self):
        info = self._info(sig=[("items", "opt", "POSITIONAL_OR_KEYWORD")])
        first = variants.make_variants(info, lambda c: None, {})
        assert first == variants.make_variants(info, lambda c: None, {})
        assert [v for v, _, _ in first].count("typical_str") == 1  # a list parameter

    def test_parameter_kinds_come_from_the_name(self):
        assert variants.kind_of("toggle_event") == "event"
        assert variants.kind_of("href") == "url"
        assert variants.kind_of("show_icon") == "bool"
        assert variants.kind_of("rows") == "list"
        assert variants.kind_of("width") == "num"
        assert variants.kind_of("anything_else") == "str"
