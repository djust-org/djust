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
import dataclasses
import datetime
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.lib.template_library_parity import classify, harness, variants

# The ratchet must run on every PR. Its cost is a module fixture (three
# subprocesses, charged to whichever test asks first), and `.test_durations`
# sums setup + call + teardown per test: once it records > 1 s for that test,
# tests/slow_tier.py would mark it `slow` and `-m "not slow"` would deselect the
# one test that asserts the ratchet. `pr_tier` is the tier's explicit opt-out.
pytestmark = pytest.mark.pr_tier

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
    probe: dict[str, str]
    planted: str = ""
    control: str = ""


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
    probe: dict[str, str] = {}
    if json_path is not None and json_path.exists():
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        rows = {
            tag: classify.TagRow.from_fields([r[name] for name in classify.HEADER[1:]])
            for tag, r in payload["rows"].items()
        }
        unmeasured, probe = payload["unmeasured"], payload["probe"]
    return Run(proc.returncode, stdout, stderr, rows, unmeasured, probe)


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Run]:
    """The three subprocess runs, started together (each is ~5-8 s, mostly startup).

    The planted tag and its control are the first two ``IDENTICAL`` rows of the
    baseline that are not child tags (a parent may render its children itself, so a
    node planted in one is never called), so this never names a tag that a later
    fix could make non-identical.
    """
    out = tmp_path_factory.mktemp("parity3663")
    identical = [
        t.split(":", 1)[1]
        for t, r in sorted(classify.read_baseline(BASELINE).items())
        if r.tag_class == classify.IDENTICAL and t.split(":", 1)[1] not in variants.PARENT
    ]
    planted, control = identical[0], identical[1]
    paths = {name: out / (name + ".json") for name in ("check", "self", "plant")}
    procs = {
        "check": _start(["--json", str(paths["check"])]),
        "self": _start(["--self-compare", "--json", str(paths["self"])]),
        "plant": _start(
            [
                "--only",
                "%s,%s" % (planted, control),
                "--plant",
                planted,
                "--json",
                str(paths["plant"]),
            ]
        ),
    }
    try:
        done = {name: _finish(proc, paths[name]) for name, proc in procs.items()}
    finally:
        for proc in procs.values():  # a timeout on one must not orphan the others
            if proc.poll() is None:
                proc.kill()
                proc.communicate()
    for run in done.values():
        run.planted, run.control = planted, control
    return done


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

    def test_no_tag_claims_parity_on_little_evidence(self, runs):
        """IDENTICAL / escape-only / WHITESPACE-ONLY / NOT-RENDERABLE are statements that nothing
        else differs; they are only that if the tag's own output appeared and the harness
        passed at least MIN_EXERCISED of the parameters the tag can read."""
        for tag, row in runs["check"].rows.items():
            if row.tag_class in (classify.DIFFERENT, "UNEXERCISED"):
                continue
            passed, readable = row.passed_readable()
            assert row.own_output > 0, "%s: %s, but its own output never appeared" % (tag, row)
            assert passed >= classify.MIN_EXERCISED * readable, "%s: %s" % (tag, row)

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
        assert max(len(",".join(r)) for r in rows) < 400

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

    def test_the_subprocess_saw_the_frozen_clock_the_counting_uuid_and_this_checkout(self, runs):
        probe = runs["check"].probe
        assert probe["today"] == "%04d-%02d-%02d" % harness.FROZEN_DATE
        assert re.fullmatch(r"0{8}-0{4}-0{4}-0{4}-0{9}[0-9a-f]{3}", probe["uuid"]), probe["uuid"]
        assert Path(probe["djust"]).resolve().is_relative_to(ROOT), probe["djust"]
        assert runs["self"].probe == probe

    def test_the_harness_refuses_an_already_configured_process(self):
        before = os.environ.get("DJANGO_SETTINGS_MODULE")
        with pytest.raises(RuntimeError, match="fresh process"):
            harness.configure_django()
        assert os.environ.get("DJANGO_SETTINGS_MODULE") == before


# --------------------------------------------------------------------------- #


def row(tag_class=classify.IDENTICAL, **kw):
    """A TagRow with the fields a test does not care about filled in."""
    kw.setdefault("compared", 10)
    kw.setdefault("exercised", "5/5")
    kw.setdefault("own_output", 10)
    return classify.TagRow(tag_class, **kw)


def _measure(variant, py, rs, invalid=False, present=True):
    py_status, py_out = py
    rs_status, rs_out = rs
    return classify.measure_variant(variant, invalid, py_status, py_out, rs_status, rs_out, present)


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

    def test_the_harnesss_counting_uuids_in_ids_are_not_a_difference(self):
        a = '<b id="x-00000000-0000-0000-0000-000000000001" for="t-00000000">x</b>'
        b = '<b id="x-00000000-0000-0000-0000-000000000002" for="t-00000000">x</b>'
        assert _measure("typical", (OK, a), (OK, b)).aspects == ()
        hexed = '<b id="x-%s">x</b>'
        assert (
            _measure(
                "typical", (OK, hexed % ("0" * 31 + "1")), (OK, hexed % ("0" * 31 + "2"))
            ).aspects
            == ()
        )

    def test_other_hex_looking_ids_are_a_difference(self):
        """Only the harness's own uuid shape is normalised: ``item-deadbeef`` vs
        ``item-cafebabe`` are two different ids, and so are two long numbers."""
        for a, b in (("item-deadbeef", "item-cafebabe"), ("1234567890", "1234567891")):
            v = _measure("typical", (OK, '<b id="%s">x</b>' % a), (OK, '<b id="%s">x</b>' % b))
            assert v.cls == classify.DIFFERENT and v.aspects == ("ids",), (a, v)

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

    def test_a_per_parameter_variant_is_an_escape_variant(self):
        assert classify.is_escape_variant("safe") and classify.is_escape_variant("safe:title")
        assert classify.is_escape_variant("literal")
        assert not classify.is_escape_variant("evil:title") and not classify.is_escape_variant(
            "typical"
        )

    # --- a child tag's own output ---------------------------------------------

    def test_own_fragment_is_what_the_child_added_to_the_parent(self):
        bare = '<div class="menu"><button>Menu</button></div>'
        with_child = '<div class="menu"><button>Menu</button><a href="/x">Item</a></div>'
        assert classify.own_fragment(bare, with_child) == '<a href="/x">Item</a>'

    def test_a_child_the_parent_hides_contributes_nothing(self):
        bare = '<div class="menu"><button>Menu</button></div>'
        assert classify.own_fragment(bare, bare) == ""

    def test_a_difference_in_the_parent_alone_is_not_charged_to_the_child(self):
        py_bare = '<div class="a"><i>m</i></div>'
        rs_bare = '<div class="b"><i>m</i></div>'
        py = '<div class="a"><i>m</i><b>child</b></div>'
        rs = '<div class="b"><i>m</i><b>child</b></div>'
        assert (
            classify.own_fragment(py_bare, py)
            == classify.own_fragment(rs_bare, rs)
            == "<b>child</b>"
        )

    # --- the tag aggregate ----------------------------------------------------

    def _tag(self, *specs, passed=1, readable=1):
        results = [_measure(*spec) for spec in specs]
        return classify.classify_tag(results, passed, readable)

    def test_a_tag_with_every_variant_identical_is_identical(self):
        r = self._tag(("typical", (OK, "a"), (OK, "a")), ("evil", (OK, "b"), (OK, "b")))
        assert r == classify.TagRow(classify.IDENTICAL, 0, "", "", 2, "1/1", 2)

    def test_a_difference_outside_the_escape_probes_is_markup(self):
        r = self._tag(
            ("typical", (OK, '<i class="a">x</i>'), (OK, '<i class="b">x</i>')),
            ("safe", (OK, "<b>safe</b>"), (OK, "&lt;b&gt;")),
        )
        assert r.tag_class == classify.DIFFERENT
        assert r.different == 2
        assert "classes:1" in r.aspects.split("+")

    def test_the_aspects_column_counts_the_variants_each_aspect_differs_in(self):
        r = self._tag(
            ("typical", (OK, '<i class="a">x</i>'), (OK, '<i class="b">x</i>')),
            ("evil", (OK, '<i class="a">y</i>'), (OK, '<i class="b">y</i>')),
            ("none", (OK, "<i>z</i>"), (OK, "<b>z</b>")),
        )
        assert r.aspects == "classes:2+tags:1"
        assert r.different == 3

    def test_a_difference_only_in_literal_and_safe_is_escape_policy(self):
        # Django passes a mark_safe value into an attribute raw; Rust escapes it.
        # Unescaped, the two attribute values are the same string: only the
        # escaping differs.
        raw = '<p title="<b>safe</b>">x</p>'
        escaped = '<p title="&lt;b&gt;safe&lt;/b&gt;">x</p>'
        r = self._tag(
            ("typical", (OK, "<i>x</i>"), (OK, "<i>x</i>")),
            ("safe:title", (OK, raw), (OK, escaped)),
        )
        assert r.tag_class == "DIFFERENT-escape-policy-only"
        assert r.aspects == "escaping:1"

    def test_the_same_difference_outside_literal_and_safe_is_markup(self):
        raw = '<p title="<b>safe</b>">x</p>'
        escaped = '<p title="&lt;b&gt;safe&lt;/b&gt;">x</p>'
        r = self._tag(("typical", (OK, raw), (OK, escaped)))
        assert r.tag_class == classify.DIFFERENT

    def test_a_one_engine_error_is_recorded_even_when_the_tag_is_also_different(self):
        r = self._tag(
            ("typical", (OK, "<i>x</i>"), (OK, "<b>x</b>")),
            ("none", (ERR, "TypeError"), (OK, "<b>x</b>")),
        )
        assert r.tag_class == classify.DIFFERENT
        assert r.errors == "none:python"

    def test_the_errors_column_says_which_engine_rejected_which_variant(self):
        r = self._tag(
            ("typical", (OK, "a"), (OK, "a")),
            ("none", (OK, "a"), (ERR, "boom")),
            ("missing", (ERR, "boom"), (OK, "a")),
        )
        assert r.tag_class == "NOT-RENDERABLE-IN-ONE-ENGINE"
        assert r.errors == "missing:python+none:rust"

    def test_an_invalid_call_does_not_decide_the_class_but_is_still_recorded(self):
        r = self._tag(
            ("typical", (OK, "a"), (OK, "a")),
            ("noargs", (ERR, "missing required"), (OK, "a"), True),
        )
        assert r.tag_class == classify.IDENTICAL
        assert r.errors == "noargs:python"

    def test_a_tag_where_nothing_rendered_is_unexercised_not_identical(self):
        r = self._tag(
            ("typical", (ERR, "x"), (ERR, "y")), ("n", (ERR, "x"), (ERR, "y"), False, False)
        )
        assert r.tag_class == "UNEXERCISED"

    def test_a_tag_whose_own_output_never_appeared_is_unexercised_not_identical(self):
        """Both engines agree on the parent's empty shell: that says nothing about the child."""
        r = self._tag(
            ("typical", (OK, ""), (OK, ""), False, False),
            ("evil", (OK, ""), (OK, ""), False, False),
        )
        assert r.tag_class == "UNEXERCISED"
        assert r.own_output == 0

    def test_a_tag_exercised_on_under_half_of_what_it_reads_is_unexercised_not_identical(self):
        agree = [("typical", (OK, "a"), (OK, "a"))]
        assert self._tag(*agree, passed=1, readable=11).tag_class == "UNEXERCISED"
        assert self._tag(*agree, passed=6, readable=11).tag_class == classify.IDENTICAL
        assert self._tag(*agree, passed=6, readable=11).exercised == "6/11"

    def test_one_proven_difference_outranks_low_exercise(self):
        r = self._tag(("typical", (OK, "a"), (OK, "b")), passed=1, readable=11)
        assert r.tag_class == classify.DIFFERENT

    def test_compared_counts_only_variants_both_engines_rendered(self):
        r = self._tag(
            ("typical", (OK, "a"), (OK, "a")),
            ("none", (ERR, "x"), (OK, "a")),
            ("missing", (ERR, "x"), (ERR, "x")),
        )
        assert r.compared == 1


class TestCompare:
    BASE = {
        "lib:a": row(classify.IDENTICAL),
        "lib:b": row(classify.DIFFERENT, different=6, aspects="classes:6+text:3", compared=8),
        "lib:c": row(
            "DIFFERENT-escape-policy-only", different=2, aspects="escaping:2", errors="none:python"
        ),
    }

    def _with(self, tag, **changes):
        return dict(self.BASE, **{tag: dataclasses.replace(self.BASE[tag], **changes)})

    def _one(self, now):
        [finding] = classify.compare(now, self.BASE)
        return finding

    def test_equal_tables_have_no_findings(self):
        assert classify.compare(dict(self.BASE), self.BASE) == []

    def test_a_regressed_class_is_worse_and_names_the_tag(self):
        finding = self._one(self._with("lib:a", tag_class=classify.DIFFERENT, different=1))
        assert (finding.tag, finding.kind) == ("lib:a", classify.WORSE)
        assert "IDENTICAL -> DIFFERENT" in finding.message
        assert "make template-library-parity-baseline" in finding.message

    def test_an_improved_class_fails_and_says_to_update_the_baseline(self):
        finding = self._one(
            self._with("lib:b", tag_class=classify.IDENTICAL, different=0, aspects="")
        )
        assert (finding.tag, finding.kind) == ("lib:b", classify.BETTER)
        assert "make template-library-parity-baseline" in finding.message

    def test_a_new_divergence_in_an_already_different_tag_is_worse(self):
        """The baseline moves inside DIFFERENT: one more differing variant is a regression."""
        finding = self._one(self._with("lib:b", different=7))
        assert finding.kind == classify.WORSE
        assert "differing variants 6 -> 7" in finding.message

    def test_a_fixed_variant_in_an_already_different_tag_is_better(self):
        finding = self._one(self._with("lib:b", different=5, aspects="classes:5+text:3"))
        assert finding.kind == classify.BETTER

    def test_one_more_variant_differing_in_an_aspect_is_worse(self):
        finding = self._one(self._with("lib:b", aspects="classes:6+text:4"))
        assert finding.kind == classify.WORSE
        assert "text" in finding.message

    def test_a_new_aspect_on_a_different_tag_is_worse(self):
        finding = self._one(self._with("lib:b", aspects="a11y:1+classes:6+text:3"))
        assert finding.kind == classify.WORSE
        assert "a11y" in finding.message

    def test_a_dropped_aspect_is_better(self):
        assert self._one(self._with("lib:b", aspects="classes:6")).kind == classify.BETTER

    def test_a_new_one_engine_error_is_worse(self):
        finding = self._one(self._with("lib:a", errors="typical:rust"))
        assert finding.kind == classify.WORSE
        assert "typical:rust" in finding.message

    def test_lost_coverage_is_worse(self):
        finding = self._one(self._with("lib:a", compared=7))
        assert finding.kind == classify.WORSE
        assert "variants compared 10 -> 7" in finding.message

    def test_fewer_parameters_exercised_is_worse(self):
        finding = self._one(self._with("lib:a", exercised="4/5"))
        assert finding.kind == classify.WORSE
        assert "parameters exercised 5 -> 4" in finding.message

    def test_a_tag_that_learns_a_parameter_the_harness_does_not_pass_is_worse(self):
        finding = self._one(self._with("lib:a", exercised="5/6"))
        assert finding.kind == classify.WORSE
        assert "parameters the tag can read 5 -> 6" in finding.message

    def test_a_tag_whose_own_output_disappears_is_worse(self):
        finding = self._one(self._with("lib:a", own_output=0, tag_class="UNEXERCISED"))
        assert finding.kind == classify.WORSE

    def test_a_new_unexercised_tag_fails(self):
        assert self._one(self._with("lib:a", tag_class="UNEXERCISED")).kind == classify.WORSE

    def test_better_in_one_way_and_worse_in_another_is_worse(self):
        finding = self._one(self._with("lib:b", different=5, aspects="a11y:1+classes:5+text:3"))
        assert finding.kind == classify.WORSE

    def test_a_tag_with_no_baseline_row_fails_until_classified(self):
        now = dict(self.BASE, **{"lib:new": row()})
        [finding] = classify.compare(now, self.BASE)
        assert (finding.tag, finding.kind) == ("lib:new", classify.NEW)

    def test_a_baseline_row_for_a_vanished_tag_fails(self):
        now = {k: v for k, v in self.BASE.items() if k != "lib:c"}
        [finding] = classify.compare(now, self.BASE)
        assert (finding.tag, finding.kind) == ("lib:c", classify.STALE)

    def test_a_restricted_run_skips_the_new_and_stale_checks_only(self):
        now = {"lib:a": dataclasses.replace(self.BASE["lib:a"], tag_class=classify.DIFFERENT)}
        [finding] = classify.compare(now, self.BASE, partial=True)
        assert finding.kind == classify.WORSE
        assert classify.compare({"lib:zz": self.BASE["lib:a"]}, self.BASE, partial=True) == []

    def test_a_baseline_that_does_not_parse_is_refused(self):
        head = ",".join(classify.HEADER)
        with pytest.raises(ValueError, match="header"):
            classify.parse_baseline("tag,class\nlib:a,IDENTICAL\n")
        with pytest.raises(ValueError, match="unknown class"):
            classify.parse_baseline(head + "\nlib:a,SORT-OF-SAME,0,,,3,1/1,1\n")
        with pytest.raises(ValueError, match="twice"):
            line = "lib:a,IDENTICAL,0,,,3,1/1,1\n"
            classify.parse_baseline(head + "\n" + line + line)

    def test_a_row_round_trips_through_the_file(self):
        text = classify.render_baseline(self.BASE)
        assert classify.parse_baseline(text) == self.BASE


class TestGateOffs:
    """Revert each guarantee in turn and show something goes red (#1468)."""

    def test_django_against_itself_is_identical_for_every_tag(self, runs):
        run = runs["self"]
        assert run.returncode == 0, run.stdout + run.stderr[-2000:]
        assert run.rows, "the self-comparison measured nothing"
        measured = {r.tag_class for r in run.rows.values()}
        # IDENTICAL, or UNEXERCISED for a tag whose own output never appears at all
        assert measured <= {classify.IDENTICAL, "UNEXERCISED"}
        assert {r.errors for r in run.rows.values()} == {""}
        # ... over the same tags the real run measures, not a subset
        assert set(run.rows) == set(runs["check"].rows)

    def test_a_difference_planted_in_the_python_tag_is_caught(self, runs, baseline):
        run = runs["plant"]
        planted, control = "djust_components:" + run.planted, "djust_components:" + run.control
        assert set(run.rows) == {planted, control}
        # the mutation applied: the planted tag is no longer what the baseline says
        assert baseline[planted].tag_class == classify.IDENTICAL
        assert run.rows[planted].tag_class == classify.DIFFERENT
        # the ratchet said so, naming the tag, with a failing exit code
        assert run.returncode == 1, run.stdout + run.stderr[-2000:]
        assert "%s: WORSE" % planted in run.stdout
        # and only that tag: the control is untouched
        assert run.rows[control] == baseline[control]
        assert control not in run.stdout

    def test_a_flipped_baseline_row_is_caught_in_both_directions(self, runs, baseline):
        measured = runs["check"].rows
        identical = next(
            t for t, r in sorted(measured.items()) if r.tag_class == classify.IDENTICAL
        )
        different = next(
            t for t, r in sorted(measured.items()) if r.tag_class == classify.DIFFERENT
        )

        claims_worse = dict(baseline)
        claims_worse[identical] = dataclasses.replace(
            baseline[identical], tag_class=classify.DIFFERENT, different=1
        )
        [finding] = classify.compare(measured, claims_worse)
        assert (finding.tag, finding.kind) == (identical, classify.BETTER)

        claims_better = dict(baseline)
        claims_better[different] = dataclasses.replace(
            baseline[different], tag_class=classify.IDENTICAL, different=0, aspects=""
        )
        [finding] = classify.compare(measured, claims_better)
        assert (finding.tag, finding.kind) == (different, classify.WORSE)

        assert claims_worse != baseline and claims_better != baseline  # the mutations applied
        assert classify.compare(measured, baseline) == []  # and the unmutated control is clean

    def test_one_more_differing_variant_in_a_different_tag_is_caught(self, runs, baseline):
        """The hole the review found: inside DIFFERENT the old baseline could not move."""
        measured = runs["check"].rows
        tag = next(t for t, r in sorted(measured.items()) if r.tag_class == classify.DIFFERENT)
        claims_fewer = dict(baseline)
        claims_fewer[tag] = dataclasses.replace(
            baseline[tag], different=baseline[tag].different - 1
        )
        [finding] = classify.compare(measured, claims_fewer)
        assert (finding.tag, finding.kind) == (tag, classify.WORSE)

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

    def test_a_gateoff_killed_mid_mutation_leaves_the_real_tree_untouched(self):
        """The first gate-off script edited the real files in place: a reader of the tree
        measured a mutant, and SIGTERM mid-run left one behind. It now mutates a throwaway
        copy, so the real tree is byte-identical before, DURING and after, and the copy goes."""
        script = ROOT / "scripts" / "template-library-parity-gateoff.py"
        spec = importlib.util.spec_from_file_location("parity_gateoff_kill", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        label, target, old, _new = next(m for m in module.MUTATIONS if "kbd escapes" in m[0])
        touched = sorted({m[1] for m in module.MUTATIONS})
        before = {p: p.read_bytes() for p in touched}
        proc = subprocess.Popen(
            [sys.executable, str(script), "--only", "kbd escapes", "--no-control"],
            cwd=ROOT,
            env=_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        guard = threading.Timer(240, proc.kill)  # never wedge the suite on a hung child
        guard.start()
        try:
            work = None
            for line in proc.stdout:
                if line.startswith("workdir: "):
                    work = Path(line.split(": ", 1)[1].strip())
                if line.startswith("mutating: "):
                    break
            else:
                pytest.fail("the script exited before it mutated anything")
            # mid-mutation: the COPY carries the mutant, the real file does not
            copy = work / target.relative_to(ROOT)
            assert old not in copy.read_text(encoding="utf-8"), (
                "the mutation did not apply to the copy"
            )
            assert old in target.read_text(encoding="utf-8")
            assert {p: p.read_bytes() for p in touched} == before
            proc.send_signal(signal.SIGTERM)
            returncode = proc.wait(timeout=120)
        finally:
            guard.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        assert returncode in (128 + signal.SIGTERM, -signal.SIGTERM), returncode
        assert not work.exists(), "the throwaway copy was left behind"
        assert {p: p.read_bytes() for p in touched} == before

    def test_every_source_mutation_of_the_gateoff_script_still_applies(self):
        """The list in scripts/template-library-parity-gateoff.py is how the ratchet is
        shown to be load-bearing; a mutation whose text has moved proves nothing."""
        spec = importlib.util.spec_from_file_location(
            "parity_gateoff", ROOT / "scripts" / "template-library-parity-gateoff.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.MUTATIONS
        for label, path, old, new in module.MUTATIONS:
            text = path.read_text(encoding="utf-8")
            assert old in text, "mutation text not found for: %s" % label
            assert text.replace(old, new, 1) != text, "no-op mutation: %s" % label


# --------------------------------------------------------------------------- #


class TestWhatTheHarnessSends:
    """The review's finding: a tag whose handler delegates to a ``render_*`` helper was
    exercised on one key of eleven, so IDENTICAL meant nothing. These pin the cure."""

    def _handlers(self):
        from djust.components import rust_handlers as rh

        return rh

    def test_discovery_follows_a_native_handler_into_its_render_helper(self):
        rh = self._handlers()
        keys, params = harness.discover(rh.ModalHandler)
        assert {"title", "size", "open", "close_event", "description"} <= keys
        # the entry point itself reads exactly one key; everything else comes from render_modal
        assert harness._keys_in(harness._node_of(rh.ModalHandler)[1]) == {"open"}

    def test_discovery_follows_each_of_the_delegating_handlers(self):
        rh = self._handlers()
        expect = {
            rh.ConfirmDialogHandler: {"message", "title", "confirm_label", "cancel_label"},
            rh.DropdownMenuHandler: {"label", "align", "open", "toggle_event"},
            rh.MenuItemHandler: {"label", "icon", "event", "href", "disabled", "danger"},
        }
        for handler, wanted in expect.items():
            keys, _ = harness.discover(handler)
            assert wanted <= keys, (handler.__name__, wanted - keys)

    def test_discovery_stops_at_the_libraries_under_test(self):
        """A key read by Django or the engine is not a tag argument."""
        rh = self._handlers()
        keys, params = harness.discover(rh.ModalHandler)
        assert "request" not in keys and "autoescape" not in keys

    def test_discovered_keys_are_identifiers(self):
        keys = harness._keys_of(
            'def f(kw):\n    return kw["ok_1"], kw.get("="), kw["a b"], "x" in kw'
        )
        assert keys == {"ok_1", "x"}

    def test_a_render_helper_parameters_count_as_arguments_but_carriers_do_not(
        self, tmp_path, monkeypatch
    ):
        module = TestParameterDiscovery()._module(
            tmp_path,
            monkeypatch,
            "djust.components.fake3663",
            """
            def render_widget(self, kw, title, label="x", content=""):
                return title

            def handler(args):
                return render_widget(None, {}, "t")
            """,
        )
        keys, params = harness.discover(module.handler)
        assert params == {"title", "label"}

    def test_each_string_parameter_gets_its_own_safe_and_evil_call(self):
        info = variants.TagInfo(
            lib="lib",
            name="widget",
            py_kind="raw",
            py_keys=["open", "title", "label", "href"],
            is_block=True,
        )
        generated = {v.name: v.src for v in variants.make_variants(info, lambda c: None, {})}
        for param in ("title", "label"):
            assert "%s=safe_html" % param in generated["safe:" + param]
            assert "%s=evil" % param in generated["evil:" + param]
        # the others are NOT hostile in that call, and the boolean switch is on so it renders
        assert "label=safe_html" not in generated["safe:title"]
        assert "open=True" in generated["safe:title"]
        # an url parameter has no mark_safe form (a path), but does have a hostile one
        assert "safe:href" not in generated and "evil:href" in generated

    def test_a_tag_with_varargs_is_called_with_safe_literal_and_hostile_positionals(self):
        info = variants.TagInfo(
            lib="lib",
            name="kbd",
            py_kind="simple_tag",
            sig=[("keys", "opt", "VAR_POSITIONAL")],
        )
        generated = {v.name: v.src for v in variants.make_variants(info, lambda c: None, {})}
        assert generated["varargs_safe"] == "{% load lib %}{% kbd safe_html safe_html %}"
        assert {"varargs_typical", "varargs_literal", "varargs_evil"} <= set(generated)

    def test_a_child_tag_is_rendered_inside_an_open_parent_and_has_a_bare_parent_to_subtract(self):
        parent = variants.TagInfo(
            lib="lib", name="dropdown_menu", py_kind="raw", py_keys=["open"], is_block=True
        )
        child = variants.TagInfo(lib="lib", name="menu_item", py_kind="raw", py_keys=["label"])
        generated = variants.make_variants(child, lambda c: parent, {})
        typical = next(v for v in generated if v.name == "typical")
        assert typical.src.startswith("{% load lib %}{% dropdown_menu open=True %}{% menu_item ")
        assert typical.bare == "{% load lib %}{% dropdown_menu open=True %}{% enddropdown_menu %}"

    def test_a_simple_tag_is_called_with_the_keys_only_rust_reads(self):
        info = variants.TagInfo(
            lib="lib",
            name="icon",
            py_kind="simple_tag",
            sig=[("name", "req", "POSITIONAL_OR_KEYWORD")],
            rust_keys=["name", "set"],
        )
        generated = {v.name: v.src for v in variants.make_variants(info, lambda c: None, {})}
        assert "set=" in generated["rust_only_keys"]
        assert variants.passed_count(info) == 2

    def test_the_real_tags_the_review_named_are_no_longer_identical(self, baseline):
        """kbd, modal, confirm_dialog, dropdown_menu and menu_item differ under input the first
        harness never sent; if one becomes IDENTICAL it must be because it was fixed."""
        for name in (
            "kbd",
            "modal",
            "confirm_dialog",
            "confirm_dialog_block",
            "dropdown_menu",
            "menu_item",
        ):
            row_ = baseline["djust_components:" + name]
            assert row_.tag_class in (classify.DIFFERENT, "DIFFERENT-escape-policy-only"), name
            assert row_.own_output > 0, name


class TestParameterDiscovery:
    def _module(self, tmp_path, monkeypatch, name, source):
        path = tmp_path / (name + ".py")
        path.write_text(textwrap.dedent(source), encoding="utf-8")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        harness._top_level_nodes.cache_clear()
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
            v.name: (v.src, v.invalid_usage)
            for v in variants.make_variants(info, lambda c: None, {})
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
            v.src for v in variants.make_variants(info, lambda c: None, {}) if v.name == "typical"
        ]
        assert typical == '{% load lib %}{% item id="id1" %}' + variants.BODY + "{% enditem %}"
        child = self._info(py_kind="raw", py_keys=["id"], is_block=True, name="accordion_item")
        [wrapped] = [
            v.src for v in variants.make_variants(child, lambda c: None, {}) if v.name == "typical"
        ]
        assert wrapped.startswith("{% load lib %}{% accordion %}{% accordion_item ")
        assert wrapped.endswith("{% endaccordion_item %}{% endaccordion %}")

    def test_generation_is_a_pure_function_of_the_info(self):
        info = self._info(sig=[("items", "opt", "POSITIONAL_OR_KEYWORD")])
        first = variants.make_variants(info, lambda c: None, {})
        assert first == variants.make_variants(info, lambda c: None, {})
        assert [v.name for v in first].count("typical_str") == 1  # a list parameter

    def test_parameter_kinds_come_from_the_name(self):
        assert variants.kind_of("toggle_event") == "event"
        assert variants.kind_of("href") == "url"
        assert variants.kind_of("show_icon") == "bool"
        assert variants.kind_of("rows") == "list"
        assert variants.kind_of("width") == "num"
        assert variants.kind_of("anything_else") == "str"
