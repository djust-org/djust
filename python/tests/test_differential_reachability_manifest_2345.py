"""The corpus reachability manifest, and the five blind spots it is measured against (#2345).

Why a manifest and not a sixth coupling test
--------------------------------------------
``scripts/filter-parity-differential.py`` has now reported CLEAN over five
surfaces it could not construct, and each time the remedy was to hand-add one
more corpus axis plus one more bespoke coupling test:

===== ============================================== ==========================
 #     the axis the corpus was blind on               what it hid
===== ============================================== ==========================
2296   a filter in a safety set, not the hot sets     a live XSS, reported as 0
2325   tag operands — no tag cell existed at all      four resolution sites
2334   dict-view paths; every dict had tame keys      dict iteration + a key gap
2290   the custom-filter path                         ``SafeData`` across PyO3
2345   invalid filter ARGUMENTS                       508 regressed cells
===== ============================================== ==========================

A corpus gap is silent BY CONSTRUCTION: "no axis reported a problem" and "no
axis exists for the problem" print the same thing. So the durable fix is not a
sixth bespoke test — it is for the corpus to DECLARE its axes and for each axis
to name the set the ENGINE says it must cover, recomputed from the engine at
check time rather than transcribed.

What #2354 landed, and what this adds
-------------------------------------
#2354 closed the *corpus* half of #2345 while this was in flight: it widened
``FILTER_ARGS``'s single always-valid argument into ``ARG_SPELLINGS``, and made
``render_both`` record a Rust panic as a ``<<PANIC …>>`` cell rather than dying
on one. All of that is untouched here.

What it did not do is the part the issue asks for in its own words — *make
corpus-reachability structural rather than a habit, so the tool can tell you
what it CANNOT reach*. That is this file's subject, and it earned its keep on
its first run against the merged corpus: the manifest reported ``pad_width``'s
cap UNREACHABLE from #2354's nineteen spellings, which is why there is a
twentieth. It also fixes the misdiagnosis #2345 names by number — the
same-build guard, which #2354 left as it was.

:class:`TestItWouldHaveCaughtTheHistoricalBlindSpots` is the empirical canary
(#1459) for the claim: each case reconstructs the pre-fix corpus by editing a
COPY of the script, runs the manifest against it, and asserts what the manifest
says. **#2296, #2325, #2290 and #2345 go red** — plus #2305, a sixth from the
same family. **#2334 does not**, in either of its halves, and
:class:`TestTheLimitTheManifestDoesNotClose` pins the reason rather than leaving
it as a hopeful silence — because a coverage tool that overstates its reach is
the exact failure this whole issue is about, one level up.

How the manifest is read here
-----------------------------
As a SUBPROCESS emitting JSON, never by importing the script: it calls
``settings.configure`` and appends a ``Library`` to Django's default engine at
import time, so importing it into a pytest process would mutate the global
filter registry for every other test in the session. Running it is also the
stronger check — it proves the tool works end to end, which an import does not.
"""

from __future__ import annotations

import ast
import json
import os
import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "filter-parity-differential.py"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "python"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return env


def run_manifest(script: pathlib.Path = SCRIPT, *args: str) -> dict:
    """The manifest the tool emits, as data."""
    proc = subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
        [sys.executable, str(script), "--manifest", "--json", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(REPO),
        check=False,
    )
    assert proc.returncode == 0, f"the manifest run failed:\n{proc.stderr[-4000:]}"
    return json.loads(proc.stdout)


def rows(data: dict) -> dict[str, dict]:
    return {row["axis"]: row for row in data["axes"]}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return run_manifest()


def mutated_script(tmp_path: pathlib.Path, *edits: tuple[str, str]) -> pathlib.Path:
    """A COPY of the differential with the given replacements applied.

    Every edit must match exactly once. An edit that silently fails to apply
    would make the canary report "the manifest is clean" for the wrong reason —
    the gate-off failure mode the milestone canon names first.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    for old, new in edits:
        assert source.count(old) == 1, f"edit matched {source.count(old)}x, expected 1: {old!r}"
        source = source.replace(old, new, 1)
    assert source != SCRIPT.read_text(encoding="utf-8"), "the mutation changed nothing"
    # The script locates the crates it parses as `parents[1] / "crates"`, so the
    # copy has to sit one directory down from a root that has one. A symlink
    # rather than a copy: the requirement sets must come from the REAL Rust
    # source, or the canary would be measuring a snapshot of it.
    (tmp_path / "scripts").mkdir(exist_ok=True)
    crates = tmp_path / "crates"
    if not crates.exists():
        crates.symlink_to(REPO / "crates", target_is_directory=True)
    copy = tmp_path / "scripts" / "filter-parity-differential.py"
    copy.write_text(source, encoding="utf-8")
    return copy


class TestTheManifestIsCleanOnMain:
    """Every declared axis reaches every member its engine-derived source names."""

    def test_no_axis_has_a_missing_member(self, manifest: dict) -> None:
        broken = {row["axis"]: row["missing"] for row in manifest["axes"] if row.get("missing")}
        assert not broken, (
            f"{broken} — the corpus cannot construct a cell reaching these, so every "
            "cell the sweep builds is blind to any behaviour that turns on them. Add "
            "the corpus member in the SAME commit as the engine change."
        )

    def test_no_exemption_is_stale(self, manifest: dict) -> None:
        """An exemption whose member IS now swept must go, not linger.

        Same rule as ``RAISE_BIT_NOT_CLOSED`` in the #2328 contract test: a
        stale exemption is a hole in the sweep that looks like a decision.
        """
        stale = {
            row["axis"]: row["stale_exemptions"]
            for row in manifest["axes"]
            if row.get("stale_exemptions")
        }
        assert not stale, f"{stale} are exempt AND swept — delete their rows"

    def test_every_exemption_carries_a_reason(self, manifest: dict) -> None:
        for row in manifest["axes"]:
            for member, reason in row["exempt"].items():
                assert len(reason) >= 30, (
                    f"{row['axis']}/{member} is exempt with no real reason. Writing the "
                    "reason is the point: it forces the honest question for each."
                )

    def test_the_axes_are_the_ones_this_file_documents(self, manifest: dict) -> None:
        """A SET, not a floor (#1125): deleting an axis fails here too."""
        assert set(rows(manifest)) == {
            "filter",
            "chain",
            "whitespace",
            "argument",
            # The failures the VALUE-side chokepoints raise (#2435/#2451).
            # Split out of `argument` rather than added beside it: that axis
            # keeps every literal naming a filter, on the reasoning that
            # "nothing else in these modules does" — which stopped being true
            # when `int_value_error` and `value_op_error` arrived. The first
            # hid the break because `get_digit` and `divisibleby` take an
            # argument, so `arg_cells()` reaches it by coincidence; the second
            # is raised only by filters that take NONE, so the argument corpus
            # could never build a cell for it and the manifest reported it
            # MISSING from an axis it does not belong to.
            "value-op",
            "argument-filter",
            "tag",
            # The positions Django's `Variable.__init__` underscore rule runs
            # at (#2418). A rule about the NAME rather than the value, so it
            # fires whether or not the name resolves — and this corpus could
            # not see it, because every head it wrote was `p` and every
            # argument spelling was a name Django ACCEPTS. Its `required` set
            # is read out of `parser.rs`'s `validate_variable_name` call sites,
            # so a fourth position is reported MISSING until a cell exists.
            "variable-name",
            # The TAG-OPERAND parse sites a COMPILE-time refusal must survive
            # (#2411). A cross of `tag` and `argument`, and neither could build
            # its cells: `arity` writes `{{ }}` only and `tag` gives each
            # filter one VALID argument, so a cell needing a tag operand AND an
            # unresolvable argument AND a refusal after it did not exist — and
            # 1,227 `{% if %}` templates Django refuses to compile rendered
            # here while every axis reported `0 MISSING`.
            "masked-refusal",
            "entrypoint",
            "grant-shape",
            # The ANSWERS Django's own `ForNode.render` can give an operand
            # (#2382). Distinct from `loop-variable`, which is about what the
            # loop BINDS: djust collapsed the refusal into the empty branch, so
            # every `forloop` member was correct on a cell that should not have
            # rendered at all.
            "for-operand-outcome",
            "loop-variable",
            "arity",
            # The characters Django's LEXER splits an expression on, which a
            # QUOTED argument may carry (#2409). Distinct from `argument`,
            # which is about what an argument RESOLVES to: this one is about
            # whether the expression was cut in the right place before any
            # argument existed.
            "separator-in-constant",
            # A COMPOSITION row (#2372): a pair of axes each individually swept
            # is not thereby swept together. Not the full N-squared — a pair
            # earns a row when both axes touch the same resolution step.
            "builtin x mapping",
            # A falsy AND a truthy inhabitant of every `Value` variant, in both
            # the value and the argument channel (#2469). The one slice of
            # `input-shape` the ENGINE does name — `Value::is_truthy` is a
            # `match` over the enum — carved out of it and made mechanical,
            # because the corpus held a falsy inhabitant of four variants out
            # of eleven and no `timedelta` at all, so #2458 (whose whole
            # subject is `bool(o)`) moved zero cells on every axis.
            "value-truthiness",
            "input-shape",
        }

    def test_exactly_one_axis_is_declared_unverified(self, manifest: dict) -> None:
        """The honest limit is one row, and it is named.

        If a second axis ever goes UNVERIFIED, the manifest's claim weakens and
        this test is where that gets noticed rather than absorbed.
        """
        unverified = [row["axis"] for row in manifest["axes"] if row["unverified"]]
        assert unverified == ["input-shape"]


class TestItWouldHaveCaughtTheHistoricalBlindSpots:
    """The empirical canary (#1459): reconstruct each pre-fix corpus, run the
    manifest against it, and assert what it reports.

    Every case edits a COPY of the script — never the real one — and every edit
    is asserted to have matched exactly once, so a canary cannot pass because
    its mutation silently did nothing.
    """

    def test_2296_a_safety_set_member_missing_from_the_hot_sets(
        self, tmp_path: pathlib.Path
    ) -> None:
        """``dictsort`` was granted item safety and never composed, and the
        two-build compare printed ``REGRESSIONS: 0 / INTRODUCED: 0`` over a
        live XSS.

        Reproduced with ``linenumbers`` rather than ``dictsort``, and the
        substitution is forced rather than convenient. ``dictsort`` was in
        ``ITEM_SAFETY_PRESERVING_FILTERS`` for one review round of #2296 and
        the review took it back out, so it never reached a merged version of
        the constant — ``git log -S`` on the constant returns exactly one
        commit, #2296's own, already carrying ``[&str; 1]``. The historical
        name is therefore not in the engine-derived requirement set at all and
        cannot be made missing from it; the constant's doc-comment keeps it as
        the worked example instead.

        ``linenumbers`` has the identical shape — granted output safety by
        ``SAFE_OUTPUT_FILTERS``, and itself the subject of a shipped XSS
        (#2291, ``{{ p|linenumbers|safe }}``).
        """
        script = mutated_script(tmp_path, ('    "linenumbers",\n', ""))
        missing = rows(run_manifest(script))["chain"]["missing"]
        assert missing == ["linenumbers"], missing

    def test_2325_no_tag_cell_existed_at_all(self, tmp_path: pathlib.Path) -> None:
        """The corpus was entirely ``{{ p|… }}``; a filter on a TAG operand is a
        different resolution path and djust had open-coded it four times.

        The expected list grew from three to nine in #2355, and the growth is
        the point rather than churn: ``cycle`` / ``firstof`` / ``ifchanged`` /
        ``regroup`` / ``widthratio`` / ``filter`` take a filter-expression
        operand too and carried an exemption row reading "TAKES A
        FILTER-EXPRESSION OPERAND and is not swept" — an admission rather than
        a property. With shapes for all nine, emptying the shape dicts reports
        all nine, which is what this canary is for.
        """
        script = mutated_script(
            tmp_path,
            ('TAG_SHAPES = {\n    "for":', 'TAG_SHAPES = {}\n_PRE_2325_TAG_SHAPES = {\n    "for":'),
            ("PATH_SHAPES = {\n", "PATH_SHAPES: dict[str, str] = {}\n_PRE_2334_PATH_SHAPES = {\n"),
        )
        missing = rows(run_manifest(script))["tag"]["missing"]
        assert sorted(missing) == [
            "cycle",
            "filter",
            "firstof",
            "for",
            "if",
            "ifchanged",
            "regroup",
            "widthratio",
            "with",
        ], missing

    def test_2355_six_tags_took_a_filter_operand_and_were_exempt(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The #2355 pre-fix state: the shapes absent AND the exemption rows
        present, which is how the manifest reported CLEAN over four
        divergences — three of them silent.

        The distinction this canary draws is the one the issue turns on: an
        exempt member is not reported as missing, so an exemption row whose
        reason is "nobody got to it" makes a gap invisible in exactly the way
        no-cell-at-all does. Removing the shapes ALONE (the test above) reports
        them; removing the shapes AND writing the exemption rows reports
        nothing.
        """
        exemptions = "".join(
            f'    "{tag}": "TAKES A FILTER-EXPRESSION OPERAND and is not swept",\n'
            for tag in ("cycle", "firstof", "ifchanged", "regroup", "widthratio", "filter")
        )
        script = mutated_script(
            tmp_path,
            ('TAG_SHAPES = {\n    "for":', 'TAG_SHAPES = {}\n_PRE_2355_TAG_SHAPES = {\n    "for":'),
            ("PATH_SHAPES = {\n", "PATH_SHAPES: dict[str, str] = {}\n_PRE_2334_PATH_SHAPES = {\n"),
            ("TAGS_NOT_SWEPT = {\n", "TAGS_NOT_SWEPT = {\n" + exemptions),
        )
        row = rows(run_manifest(script))["tag"]
        # The six are silent — exempt, so not missing — while the three #2325
        # added are still reported. That asymmetry IS the #2355 finding.
        assert sorted(row["missing"]) == ["for", "if", "with"], row["missing"]
        for tag in ("cycle", "firstof", "ifchanged", "regroup", "widthratio", "filter"):
            assert tag in row["exempt"], tag

    def test_2290_the_custom_filter_entry_point_was_never_called(
        self, tmp_path: pathlib.Path
    ) -> None:
        """``register_custom_filter`` had been on the module all along and no
        cell dispatched through it, so the whole of what a project's own filters
        see was unmeasured — and ``SafeData`` was invisible across PyO3."""
        script = mutated_script(
            tmp_path,
            ("    _rust.register_custom_filter(", "    _register_custom_filter_DISABLED = ("),
        )
        missing = rows(run_manifest(script))["entrypoint"]["missing"]
        assert missing == ["register_custom_filter"], missing

    def test_2305_the_corpus_carried_a_marked_list_and_no_marked_tuple(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A sixth, and the one that proves ``input-shape`` is not wholly blind.

        ``Context::items_are_safe`` accepts ``Value::List`` AND ``Value::Tuple``;
        the corpus carried only a marked LIST, so ``mark_input_safety``'s
        missing ``PyTuple`` arm was invisible. Adding ``t-marked`` moved 80
        cells. This is the one slice of the value-shape axis with a mechanical
        source, and it is why ``input-shape`` says UNVERIFIED *outside*
        ``grant-shape`` rather than UNVERIFIED flat.
        """
        script = mutated_script(
            tmp_path,
            (
                '    "t-marked": (mark_safe("<b>x</b>"), mark_safe("<i>y</i>")),',
                '    "t-marked-PRE_2305": [mark_safe("<b>x</b>"), mark_safe("<i>y</i>")],',
            ),
            # And in the chain axis's key list, or the corpus references a key
            # `INPUTS` no longer has and the manifest raises instead of
            # reporting. A canary that crashes is not a canary (#2135).
            ('    "t-marked",\n', '    "t-marked-PRE_2305",\n'),
        )
        missing = rows(run_manifest(script))["grant-shape"]["missing"]
        assert missing == ["Tuple"], missing

    def test_2345_the_argument_axis_had_one_valid_spelling_per_filter(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The issue this manifest ships with. ``FILTER_ARGS`` gives every
        filter ONE argument and it is always VALID, so a change entirely about
        arguments that do not parse or do not resolve moves zero cells."""
        script = mutated_script(
            tmp_path,
            (
                "ARG_SPELLINGS = [\n",
                "ARG_SPELLINGS: list[str] = []\n_PRE_2345_SPELLINGS = [\n",
            ),
        )
        row = rows(run_manifest(script))["argument"]
        # EVERY required error becomes unreachable, which is the claim — and it
        # is a set comparison rather than a count, so it survives the engine
        # growing a new argument error (as #2346 did, 4 -> 6).
        assert sorted(row["missing"]) == sorted(row["required"]), row["missing"]
        joined = "\n".join(row["missing"])
        for kind in ("does not resolve", "is a ValueError", "is a TypeError", "past djust's"):
            assert kind in joined, kind

    def test_2400_no_cell_could_have_the_wrong_argument_COUNT(self, tmp_path: pathlib.Path) -> None:
        """The seventh, and the largest: 48 of Django's 57 built-ins.

        ``cells()`` gives every argument-taking filter exactly ONE argument out
        of ``FILTER_ARGS`` and gives the rest none; ``arg_cells()`` sweeps
        spellings of a single argument over the filters that take one. Neither
        can write ``{{ p|upper:"x" }}`` or ``{{ p|default }}`` — so the manifest
        reported ``0 MISSING`` on ten axes over ~345,000 cells while the single
        biggest class of divergence in the corpus went unseen.

        A set comparison rather than a count, so it survives Django adding or
        dropping a built-in — and the counted assertions below are per-COUNT
        rather than a total, so #2409 widening `ARITY_COUNTS` to include the
        two-argument shape does not have to restate this issue's number.
        """
        script = mutated_script(
            tmp_path,
            (
                "    for name in sorted(register.filters):\n"
                "        for provided in ARITY_COUNTS:\n",
                "    for name in []:\n        for provided in ARITY_COUNTS:\n",
            ),
        )
        row = rows(run_manifest(script))["arity"]
        assert sorted(row["missing"]) == sorted(row["required"]), row["missing"]
        # #2400's 48 is the count over the counts #2400 was about — 0 and 1.
        # #2409 added 2, which Django's LEXER refuses for every filter whatever
        # its signature says, so the row for it is one per built-in and is a
        # different axis of the same table.
        by_count: dict[str, int] = {}
        for member in row["missing"]:
            by_count[member.rsplit(":", 1)[1]] = by_count.get(member.rsplit(":", 1)[1], 0) + 1
        assert by_count["0"] + by_count["1"] == 48, by_count
        # The two shapes the axis is about, both named in the report.
        joined = " ".join(row["missing"])
        assert "upper:1" in joined, "the EXTRA-argument half is not reported"
        assert "default:0" in joined, "the MISSING-argument half is not reported"
        assert "upper:2" in joined, "the LEXER half (#2409) is not reported"

    def test_removing_the_pad_cap_spelling_makes_the_cap_unreachable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The manifest earning its keep against the corpus it ships beside.

        #2354's ``ARG_SPELLINGS`` had nineteen entries and they reached three
        of the engine's four argument errors. The fourth — ``pad_width``'s cap,
        the guard standing between a template-supplied width and an allocator
        ABORT (#2328) — needs a width that PARSES and saturates past ``isize``,
        and every one of the nineteen either parses to a small number, fails to
        parse, or fails to resolve. So #2345 added a twentieth, and this
        removes it again to prove the report was real rather than decorative.

        This is the manifest reporting a gap in live, already-merged code, not
        in a synthetic reconstruction — which is the strongest form the claim
        has.

        The list has grown past twenty since (#2366's four typed bindings), so
        the mutation removes the ONE spelling by name rather than truncating
        the list at it — which is what the first version did, and what made
        this test fail with "edit matched 0x" the moment anything was appended.

        Since #2469 the cap has TWO inhabitants and the mutation removes both.
        ``known_big`` binds ``12345678901234567890`` — added for
        ``arg:BigInt:truthy`` on the ``value-truthiness`` axis, since a
        magnitude past ``i64`` has no falsy inhabitant — and a resolved
        argument of that size reaches the cap by the same route the literal
        does. That is a real second route rather than an accident, and this
        test going red on the first run of #2469 is how it was found: removing
        only the literal reported the cap REACHABLE, which is the honest
        answer once a second spelling reaches it.
        """
        script = mutated_script(
            tmp_path,
            ("\n    '\"99999999999999999999\"',", ""),
            ('\n    "known_big",', ""),
        )
        row = rows(run_manifest(script))["argument"]
        assert len(row["missing"]) == 1 and "past djust's" in row["missing"][0], row["missing"]
        # And every OTHER required error is still reachable from the nineteen,
        # so the report names the one gap rather than blaming the whole axis.
        # Stated as a set difference: the count moves when the engine grows an
        # argument error, and the claim does not.
        assert len(row["required"]) - len(row["missing"]) >= 3
        assert set(row["missing"]) < set(row["required"])

    #: The three contiguous runs #2469 added, so the mutation removes the
    #: corpus rows rather than the axis that reports them — an axis deleted
    #: alongside its inhabitants would report clean for the wrong reason.
    PRE_2469_INPUTS = """    "i-zero": 0,
    "f-zero": 0.0,
    "dec-zero": Decimal("0"),
    "t-empty": (),
    "d-empty": {},
    "td-zero": datetime.timedelta(0),
    "td-plain": datetime.timedelta(seconds=90),
"""
    PRE_2469_ARG_CONTEXT = """    "known_empty": "",
    "known_zero": 0,
    "known_float": 1.5,
    "known_float_zero": 0.0,
    "known_true": True,
    "known_false": False,
    "known_none": None,
    "known_big": 12345678901234567890,
    "known_decimal": Decimal("2.5"),
    "known_decimal_zero": Decimal("0"),
    "known_empty_list": [],
    "known_empty_tuple": (),
    "known_empty_dict": {},
    "known_td": datetime.timedelta(seconds=90),
    "known_td_zero": datetime.timedelta(0),
"""
    PRE_2469_ARG_SPELLINGS = """    "known_empty",
    "known_zero",
    "known_float",
    "known_float_zero",
    "known_true",
    "known_false",
    "known_none",
    "known_big",
    "known_decimal",
    "known_decimal_zero",
    "known_empty_list",
    "known_empty_tuple",
    "known_empty_dict",
    "known_td",
    "known_td_zero",
"""

    def test_2469_no_cell_could_have_a_FALSY_argument_and_no_timedelta_existed(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The eighth blind spot, and the first the design could have caught.

        ``ARG_CONTEXT`` bound six objects and every one was Python-TRUTHY, so
        the corpus could construct no cell where a *resolved* argument's
        falsiness is the question — which is the whole of what
        ``ArgType::is_falsy``'s first arm answers. And no entry of ``INPUTS``
        was a ``timedelta``, the ONE member of the ``Value::Encoded`` family
        with a falsy inhabitant. So #2458 — whose entire subject is
        ``bool(timedelta(0))`` — moved **zero cells on every axis** while
        changing four measured behaviours.

        The reconstruction removes the corpus rows and leaves the axis in
        place, and the axis names all 21 gaps: six ``value:`` variants, plus
        ``value:Encoded`` in BOTH answers (nothing in the corpus reached that
        variant at all), plus fourteen in the argument channel.
        """
        script = mutated_script(
            tmp_path,
            (self.PRE_2469_INPUTS, ""),
            (self.PRE_2469_ARG_CONTEXT, ""),
            (self.PRE_2469_ARG_SPELLINGS, ""),
        )
        row = rows(run_manifest(script))["value-truthiness"]
        missing = set(row["missing"])
        # The two the issue names first, and the reason it was filed at all.
        assert "arg:Encoded:falsy" in missing, missing
        assert {"value:Encoded:falsy", "value:Encoded:truthy"} <= missing, missing
        # Every variant that HAS a falsy inhabitant was unreachable in one
        # channel or the other. Stated as a superset so a future variant does
        # not have to be added here as well.
        assert {
            "value:Decimal:falsy",
            "value:Float:falsy",
            "value:Integer:falsy",
            "value:Object:falsy",
            "value:Tuple:falsy",
            "arg:Bool:falsy",
            "arg:Decimal:falsy",
            "arg:Integer:falsy",
            "arg:List:falsy",
            "arg:None:falsy",
            "arg:Object:falsy",
            "arg:String:falsy",
            "arg:Tuple:falsy",
        } <= missing, sorted(missing)
        # ...and the axis is not simply reporting everything: the truthy
        # inhabitants the pre-#2469 corpus DID have are still swept, so the
        # report names the gap rather than blaming the whole axis.
        assert "value:String:falsy" not in missing, "`s-empty` was already there"
        assert "value:List:falsy" not in missing, "`l-empty` was already there"
        assert "value:Bool:falsy" not in missing, "`b-false` was already there"
        assert "arg:String:truthy" not in missing, "`known` was already there"
        assert "arg:Encoded:truthy" not in missing, "`known_dt` was already there"
        assert set(row["missing"]) < set(row["required"])

    def test_2469_the_mutation_is_a_corpus_edit_and_not_an_axis_deletion(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Non-vacuity for the canary above (#1468/#2135).

        A reconstruction that deleted the axis would also report ``missing``
        as empty rather than as 21 rows, and the test above would pass by
        arithmetic. Assert the axis is still declared in the mutated copy, and
        that every OTHER axis is still clean — so the 21 rows are this axis's
        report and not fallout from a broken script.
        """
        script = mutated_script(
            tmp_path,
            (self.PRE_2469_INPUTS, ""),
            (self.PRE_2469_ARG_CONTEXT, ""),
            (self.PRE_2469_ARG_SPELLINGS, ""),
        )
        data = rows(run_manifest(script))
        assert "value-truthiness" in data, "the mutation deleted the axis, not the corpus"
        broken = {a: r["missing"] for a, r in data.items() if r.get("missing")}
        assert set(broken) == {"value-truthiness"}, broken

    #: The corpus rows #2477 added, and the ONE line that lets the axis name
    #: them. Held apart so the canary below can run the SAME corpus through
    #: both the extended axis and the one that shipped with #2469.
    ROWS_2477_INPUTS = """    "set-empty": set(),
    "set-plain": {"<img src=x onerror=alert(1)>"},
"""
    ROWS_2477_ARG_CONTEXT = """    "known_set_empty": set(),
    "known_set": {"<img src=x onerror=alert(1)>"},
"""
    ROWS_2477_ARG_SPELLINGS = """    "known_set_empty",
    "known_set",
"""
    AXIS_2477 = "    return [*_rust_value_variants(), *_no_variant_outcomes()]"
    AXIS_PRE_2477 = "    return list(_rust_value_variants())"

    #: What the extended axis reports over a corpus with those rows removed.
    GAP_2477 = {
        "value:falsy_opaque:falsy",
        "value:str-fallback:truthy",
        "arg:falsy_opaque:falsy",
        "arg:str-fallback:truthy",
    }

    #: The two `INPUTS_LAZY` dict-view rows (#2482). They land on the SAME two
    #: value-channel arms the `set` pair does — `dv-keys-empty` on
    #: `falsy_opaque` and `dv-keys-plain` on the terminal `str()` — so the
    #: #2477 canary below has to remove them TOO, or the gap it is built to
    #: reproduce is filled by a row from a later issue and the canary silently
    #: stops reproducing anything. Found by running it: leaving them in turned
    #: `GAP_2477` from four members into two.
    ROWS_2482_LAZY_EMPTY = '    "dv-keys-empty": lambda: {}.keys(),\n'
    ROWS_2482_LAZY_PLAIN = (
        '    "dv-keys-plain": lambda: {"<img src=x onerror=alert(1)>": 1}.keys(),\n'
    )

    def _without_the_2477_rows(self, tmp_path: pathlib.Path, *extra: tuple[str, str]):
        return mutated_script(
            tmp_path,
            (self.ROWS_2477_INPUTS, ""),
            (self.ROWS_2477_ARG_CONTEXT, ""),
            (self.ROWS_2477_ARG_SPELLINGS, ""),
            (self.ROWS_2482_LAZY_EMPTY, ""),
            (self.ROWS_2482_LAZY_PLAIN, ""),
            *extra,
        )

    def test_2477_the_variant_only_axis_reports_the_no_variant_class_COVERED(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The ninth blind spot, and the first the axis itself was the cause of.

        #2469 built ``value-truthiness`` to stop a falsiness gap going
        unmeasured a seventh time, and it enumerated the ``Value`` VARIANTS.
        That is the wrong enumeration for the question, and #2466 is the
        proof: every value that issue is about — ``set()``, ``frozenset()``,
        ``complex(0)``, an empty ``dict_keys``, a zero-``__len__`` class — has
        no variant, and the ABSENCE is the defect. ``td-zero`` supplied
        ``Encoded:falsy``, so the axis said ``0 MISSING`` about a class it
        could not construct a single cell for.

        Run rather than argued: this is the pre-#2477 axis over the same
        corpus the canary below uses, and it reports nothing missing.
        """
        script = self._without_the_2477_rows(tmp_path, (self.AXIS_2477, self.AXIS_PRE_2477))
        row = rows(run_manifest(script))["value-truthiness"]
        assert row["missing"] == [], row["missing"]
        # ...and it is the VARIANT enumeration that is doing it: none of the
        # four members the extended axis names is even in its required set.
        assert not (self.GAP_2477 & set(row["required"])), sorted(
            self.GAP_2477 & set(row["required"])
        )

    def test_2477_the_outcome_axis_names_the_gap_the_variant_axis_could_not(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The same corpus, through the extended axis: four members MISSING.

        The pair is the empirical canary the tooling-PR rule asks for (#1459):
        one run of the tool reporting a gap that the version it replaces
        reports as covered, over an identical corpus.
        """
        script = self._without_the_2477_rows(tmp_path)
        data = rows(run_manifest(script))
        row = data["value-truthiness"]
        assert set(row["missing"]) == self.GAP_2477, row["missing"]
        # The requirement is read out of the Rust source, so the report names
        # WHERE each member came from — the fallback block, not the enum.
        for member in row["missing"]:
            assert "fallback block" in row["required"][member], row["required"][member]

    def test_2477_the_mutation_is_a_corpus_edit_and_not_a_broken_script(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Non-vacuity for both halves above (#1468/#2135).

        A mutation that broke the script, or that deleted the axis, would also
        produce a report the assertions above could be read as satisfying. So:
        the axis is still declared, every OTHER axis is still clean, and the
        four missing members are a strict subset of a required set that did
        not shrink.
        """
        data = rows(run_manifest(self._without_the_2477_rows(tmp_path)))
        assert "value-truthiness" in data, "the mutation deleted the axis, not the corpus"
        broken = {a: r["missing"] for a, r in data.items() if r.get("missing")}
        assert set(broken) == {"value-truthiness"}, broken
        row = data["value-truthiness"]
        assert set(row["missing"]) < set(row["required"])
        # The truthy partners the corpus already had are still swept, so the
        # report names the gap rather than blaming the whole axis.
        for still_covered in ("value:Encoded:falsy", "arg:Encoded:truthy", "value:Object:falsy"):
            assert still_covered not in row["missing"], still_covered

    #: The rows #2482 added, held apart for the same reason the #2477 pair is:
    #: the canary below runs the SAME axis over a corpus with and without them.
    #: `known_falsy_iter` is the argument-channel half; `o-falsy-iter` is the
    #: value-channel one, and the two dict-view rows are separate constants
    #: above because the #2477 canary needs those two and not these.
    ROWS_2482_ARG_CONTEXT = '    "known_falsy_iter": INPUTS_LAZY["o-falsy-iter"](),\n'
    ROWS_2482_ARG_SPELLINGS = '    "known_falsy_iter",\n'
    #: The one bit that decides which arm the row lands on. Flipping it makes
    #: the object TRUTHY, so `falsy_opaque`'s gate is irrelevant and the
    #: terminal `str()` arm is reached in the OTHER answer — a mutation that
    #: leaves the row, the corpus and the script intact and moves exactly the
    #: member under test. Both channels move together because `ARG_CONTEXT`
    #: builds its inhabitant from this same factory.
    ROW_2482_FALSY_BIT = '"__bool__": lambda self: False,'
    ROW_2482_TRUTHY_BIT = '"__bool__": lambda self: True,'

    #: What the axis reports over a corpus whose falsy-iterable row is no
    #: longer falsy — the member the exemption #2482 deleted used to cover.
    GAP_2482 = {"value:str-fallback:falsy", "arg:str-fallback:falsy"}

    def test_2482_the_falsy_str_fallback_member_needs_the_factory_rows(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The empirical canary for #2482 (#1459), and the gate-off for the
        deleted exemption (#1468).

        `("str-fallback", "falsy")` was an EXEMPTION until #2482 — the axis
        declared the member uninhabitable and moved on. It was not: a falsy
        object with `__iter__` and no `__len__` reaches the terminal
        `Value::String(ob.str()?)`, and #2466's own doc-comment names that
        shape as one it DECLINED. Removing the two rows that inhabit it must
        put the member back in `missing`, in both channels — which is the same
        run that proves the rows are what covers it, rather than something
        else in the corpus happening to.

        The exemption's stated reason was also false, and that is the finding
        rather than a footnote: it said a class instance "cannot be a row here
        at all" because `test_sequence_op_chokepoint_2451.corpus()` evaluates
        values in a three-name namespace. `eval` injects `__builtins__` into a
        globals mapping that has none, so `type("C", (), {...})()` evaluates
        there perfectly well.
        """
        script = mutated_script(tmp_path, (self.ROW_2482_FALSY_BIT, self.ROW_2482_TRUTHY_BIT))
        row = rows(run_manifest(script))["value-truthiness"]
        assert set(row["missing"]) == self.GAP_2482, row["missing"]
        # Each names the fallback BLOCK as its source, not the enum: no `Value`
        # variant models the object, which is the whole of why #2477 had to
        # stop enumerating variants.
        for member in row["missing"]:
            assert "fallback block" in row["required"][member], row["required"][member]

    def test_2482_the_mutation_is_a_corpus_edit_and_not_a_broken_script(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Non-vacuity for the canary above (#2129/#2135).

        A mutation that broke the script — or that made some OTHER axis go red
        — would produce a report the assertion above could be read as
        satisfying. So: the axis is still declared, `value-truthiness` is the
        ONLY axis with a gap, and the two missing members are a strict subset
        of a required set that did not shrink.

        The corpus is also asserted INTACT: the mutation flips one bit of one
        row rather than removing anything, so all three factory rows must
        still be swept on `input-shape`. A mutation that had deleted the
        mapping would report the same `missing` set for a different reason.
        """
        script = mutated_script(tmp_path, (self.ROW_2482_FALSY_BIT, self.ROW_2482_TRUTHY_BIT))
        data = rows(run_manifest(script))
        assert "value-truthiness" in data, "the mutation deleted the axis, not the corpus"
        broken = {a: r["missing"] for a, r in data.items() if r.get("missing")}
        assert set(broken) == {"value-truthiness"}, broken
        row = data["value-truthiness"]
        assert set(row["missing"]) < set(row["required"])
        assert not row["stale_exemptions"], row["stale_exemptions"]
        shapes = set(data["input-shape"]["swept"])
        assert {"dv-keys-empty", "dv-keys-plain", "o-falsy-iter"} <= shapes, sorted(shapes)

    def test_2482_the_argument_channel_is_covered_by_its_OWN_row(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Two rows, two mechanisms, two independently-red tests (#2129).

        The canary above moves BOTH channels with one edit, because both
        inhabitants come from one factory — which is the right coupling for
        the object's SHAPE and the wrong evidence for "does the argument
        channel have its own row". So this removes only the `ARG_CONTEXT`
        binding and its spelling: exactly `arg:str-fallback:falsy` must go
        missing, and the value channel must stay covered.

        Without this, deleting `known_falsy_iter` entirely would leave the
        suite green — the value row would cover for it, which is the
        two-mechanisms-shadowing-each-other shape.
        """
        script = mutated_script(
            tmp_path,
            (self.ROWS_2482_ARG_CONTEXT, ""),
            (self.ROWS_2482_ARG_SPELLINGS, ""),
        )
        row = rows(run_manifest(script))["value-truthiness"]
        assert set(row["missing"]) == {"arg:str-fallback:falsy"}, row["missing"]

    def test_2482_the_unpicklable_row_is_swept_and_not_merely_present(self) -> None:
        """The dict-view half of #2466's class, which had no row at all.

        `measure`'s `@cmp` axis deep-copies its second operand, and all three
        dict views raise `TypeError: cannot pickle` under `deepcopy` — so an
        empty `dict_keys` could not be a corpus row, and was swept nowhere:
        not on the axis it breaks, and not on the twenty-odd it would have
        been fine on. Asserted on the LIVE manifest rather than the mutated
        one, because the claim is about `main`.
        """
        row = rows(run_manifest())["input-shape"]
        assert {"dv-keys-empty", "dv-keys-plain", "o-falsy-iter"} <= set(row["swept"])

    def test_2477_the_arm_reader_refuses_a_pattern_that_stopped_matching(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The drift alarm, exercised rather than trusted.

        The no-variant outcomes are named by ARM, and the arm list is read out
        of `impl FromPyObject for Value`'s fallback block. What makes that
        honest is the count check: every arm but the last ends in a
        `return Ok(…)`, so a pattern that stopped matching — or an arm added
        without one — is a mismatch rather than a silent reclassification.

        Mutating one alternative of the pattern must make the manifest FAIL
        loudly. A reader that shrugged would leave the next conversion arm
        folded into whichever existing outcome its objects happened to hit,
        which is the whole failure mode this axis exists to end.
        """
        script = mutated_script(
            tmp_path,
            (r'|ob\.getattr\("(?P<dunder>__\w+__)"\)', r'|ob\.getattr\("(?P<dunder>NOPE)"\)'),
        )
        proc = subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(script), "--manifest", "--json"],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode != 0, "the reader accepted a pattern that matches nothing"
        assert "_FALLBACK_ARM_PATTERN matched" in proc.stderr, proc.stderr[-2000:]


class TestTheLimitTheManifestDoesNotClose:
    """#2334's two halves, and why neither is caught — pinned, not hoped for.

    A coverage tool that overstates its reach is the failure this issue is
    about, one level up. So the honest account is asserted the same way the
    catches are: by running it.
    """

    def test_a_dict_with_tame_keys_is_not_reported(self, tmp_path: pathlib.Path) -> None:
        """The hostile-KEY half. Nothing in either engine's source says a dict's
        keys must be hostile — that is a VALUE choice inside an axis that
        already existed, and it was found by a person noticing.
        """
        script = mutated_script(
            tmp_path,
            ('    "d-hostile-key": {\n', '    "d-tame-key-PRE_2334": {\n'),
        )
        data = run_manifest(script)
        assert not any(row.get("missing") for row in data["axes"]), (
            "the manifest reported a missing member for a VALUE-shape change. If "
            "that is now derivable, `input-shape` should stop being UNVERIFIED "
            "and this test should be replaced by the catch."
        )
        assert rows(data)["input-shape"]["unverified"]

    def test_the_dict_view_path_shapes_are_not_reported(self, tmp_path: pathlib.Path) -> None:
        """The dotted-path half. ``{% for k, v in p.items %}`` is a third
        resolution shape, and the `tag` axis cannot see its absence: the tags
        those shapes use (`for`, `if`, `with`) are the same ones `TAG_SHAPES`
        already sweeps, so removing every PATH shape leaves the tag axis clean.

        Since #2402 the same mutation ALSO removes every ``forloop`` cell, and
        the `loop-variable` axis DOES report those; since #2382 it removes the
        bare ``{% for x in p %}`` shape too, and `for-operand-outcome` reports
        all three of its members. So the assertion is now "the axes that notice
        are `loop-variable` and `for-operand-outcome`" — each widening is a
        sharper statement of the same limit than "nothing notices" was.

        The dotted-path half remains uncaught, and it is the reason this test
        exists: no axis names ``p.items`` as a requirement. Both axes that DO
        fire here fire for their own reason (a `forloop` member, an operand
        outcome), neither of which mentions a dotted path — so removing every
        ``p.items`` shape while KEEPING the bare-loop and ``forloop`` ones
        would still be reported clean. That is what keeps `input-shape`
        UNVERIFIED.
        """
        script = mutated_script(
            tmp_path,
            ("PATH_SHAPES = {\n", "PATH_SHAPES: dict[str, str] = {}\n_PRE_2334_PATH_SHAPES = {\n"),
        )
        data = run_manifest(script)
        noticed = {row["axis"] for row in data["axes"] if row.get("missing")}
        assert noticed == {"loop-variable", "for-operand-outcome"}, (
            f"axes reporting a missing member: {sorted(noticed)}. If an axis "
            "other than these now notices the dict-view PATH shapes going away, "
            "that is the catch — update this test to it."
        )
        assert sorted(rows(data)["for-operand-outcome"]["missing"]) == [
            "empty-branch",
            "iterated",
            "refused",
        ], rows(data)["for-operand-outcome"]
        assert rows(data)["loop-variable"]["missing"] == [
            "counter",
            "counter0",
            "first",
            "last",
            "parentloop",
            "revcounter",
            "revcounter0",
        ], rows(data)["loop-variable"]


def run_compare(
    tmp_path: pathlib.Path,
    base: dict,
    after: dict,
    *args: str,
) -> subprocess.CompletedProcess:
    """`--compare` over two hand-built results files.

    Synthetic rather than two real builds: `compare`'s decisions are about the
    two FILES — their cell sets, their recorded digests, which cells moved —
    and building the engine twice to exercise them would test the compiler.
    The two-build run is what the fix PRs do; this is what the tool does with
    the result.
    """
    base_path, after_path = tmp_path / "base.json", tmp_path / "after.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")
    return subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
        [sys.executable, str(SCRIPT), "--compare", str(base_path), str(after_path), *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(REPO),
        check=False,
    )


#: Two `@arg` cells and one `{{ }}` cell, enough for `compare` to bucket by
#: axis. `agree|s-plain` agrees on both sides; the `@arg` pair is where the
#: movement is put, or not.
def _results(build: str, moved: bool) -> dict:
    return {
        "upper\ts-plain": ["ABC", "ABC"],
        "@arg center:q-word\thappy\tvar": ["<<RAISED>>", "<<RAISED>>" if moved else "ab"],
        "@arg center:q-int\thappy\tvar": ["  ab ", "  ab "],
        "@@build": build,
        "@@mode": "argument",
    }


class TestEveryCellFamilyHasAnAxis:
    """`axis_of` must classify every cell the corpus builds.

    An unlisted `@`-prefix does not fail loudly — it falls through to the
    `{{ }}` split and is reported as `filter` or `chain`. That is worse than
    imprecise: the per-axis movement report is what tells a reader an axis
    moved nothing, and a misfiled family makes a blind axis look busy.

    #2347's `@builtin` family arrived while this was in flight and would have
    been misfiled exactly that way; this is the check that would have said so.
    """

    def test_every_at_prefix_in_measure_is_classified(self, tmp_path: pathlib.Path) -> None:
        """MEASURED from a real run, not read off the source: every distinct
        `@`-family the corpus emits is claimed by a named axis, and none of
        them lands in the `{{ }}` fallback."""
        out = tmp_path / "cells.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        payload = json.loads(out.read_text())
        families = {
            k.split(" ", 1)[0].split("\t", 1)[0]
            for k in payload
            if k.startswith("@") and not k.startswith("@@")
        }
        assert families, "the corpus emits no @-prefixed cells at all"

        source = SCRIPT.read_text(encoding="utf-8")
        body = source.split("def axis_of(", 1)[1].split("\ndef ", 1)[0]
        unlisted = [f for f in families if f'"{f}' not in body]
        assert not unlisted, (
            f"{sorted(unlisted)} are cell families `axis_of` does not classify, so their "
            "cells are reported under `filter`/`chain` and the per-axis movement report "
            "lies about them. Add a branch in the SAME commit as the family."
        )

    def test_the_builtin_family_is_reported_under_its_own_name(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Non-vacuity for the branch #2347's family needed: the count under
        `builtin` must be exactly the number of `@builtin` cells, so a
        fallthrough would show up as zero here and a surplus elsewhere."""
        out = tmp_path / "cells.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        payload = json.loads(out.read_text())
        built = [k for k in payload if k.startswith("@builtin ")]
        assert built, "#2347's builtin-value axis built no cells"
        assert payload["@@cells_by_axis"].get("builtin") == len(built)


class TestTheSameBuildGuardIsAnswered:
    """The misdiagnosis #2345 names, and the mechanism that replaces it."""

    def test_two_files_from_the_same_build_still_fail(self, tmp_path: pathlib.Path) -> None:
        """The half the old guard got right, and it must survive: a baseline
        measured against the SAME build proves nothing."""
        proc = run_compare(tmp_path, _results("aaa", False), _results("aaa", True))
        assert proc.returncode == 1, proc.stdout
        assert "the SAME `_rust` build" in proc.stdout

    def test_two_real_builds_with_no_movement_are_not_called_a_stale_baseline(
        self, tmp_path: pathlib.Path
    ) -> None:
        """#2328's reading, and the misdiagnosis this issue is about.

        Identical agreement counts, two genuinely different builds, zero moved
        cells. The old guard returned FAIL and told the reader to rebuild a
        baseline that was already correct. The change was real; the corpus
        could not see it.
        """
        proc = run_compare(tmp_path, _results("aaa", False), _results("bbb", False))
        assert proc.returncode == 0, proc.stdout
        assert "genuinely two builds" in proc.stdout
        assert "an axis this corpus does not sweep" in proc.stdout
        assert "the baseline is not real" not in proc.stdout

    def test_require_moved_fails_when_the_named_axis_did_not_move(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The refusal, made actionable: a change declares its axis, and a run
        that could not move it is a failure rather than a note."""
        proc = run_compare(
            tmp_path,
            _results("aaa", False),
            _results("bbb", False),
            "--require-moved",
            "argument",
        )
        assert proc.returncode == 1, proc.stdout
        assert "--require-moved named ['argument']" in proc.stdout

    def test_require_moved_passes_when_it_did_move(self, tmp_path: pathlib.Path) -> None:
        """Non-vacuity for the gate: one that failed either way would be a
        blanket refusal rather than a measurement."""
        proc = run_compare(
            tmp_path,
            _results("aaa", False),
            _results("bbb", True),
            "--require-moved",
            "argument",
        )
        assert proc.returncode == 0, proc.stdout
        assert "argument" in proc.stdout

    def test_the_per_axis_movement_is_reported(self, tmp_path: pathlib.Path) -> None:
        """ "0 moved" is never printed without saying WHERE."""
        proc = run_compare(tmp_path, _results("aaa", False), _results("bbb", True))
        assert re.search(r"argument\s+1 moved\s+of\s+2", proc.stdout), proc.stdout
        assert re.search(r"filter\s+0 moved\s+of\s+1", proc.stdout), proc.stdout

    def test_the_results_file_records_the_rust_build_digest(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        assert "def build_digest(" in source
        assert 'payload[META_PREFIX + "build"] = build_digest()' in source

    def test_the_guard_compares_digests_rather_than_agreement_counts(self) -> None:
        """The pre-#2345 guard read identical agreement counts as "the baseline
        is not real". That is one of TWO causes, and #2328 hit the other: two
        genuinely different builds, zero moved cells, because the change was on
        an axis the corpus could not construct.
        """
        source = SCRIPT.read_text(encoding="utf-8")
        assert 'meta_b["build"] == meta_a["build"]' in source, (
            "the same-build question must be ANSWERED from the recorded digest, "
            "not inferred from the agreement counts"
        )
        # And the inference must no longer be able to fail a two-build run.
        guard = source.split("def compare(", 1)[1]
        inferred = re.findall(
            r"if len\(agree_b\) == len\(agree_a\):\n\s+print\(\s*\n?\s*\"FAIL", guard
        )
        assert not inferred, (
            "a bare agreement-count comparison still returns FAIL — that is the "
            "misdiagnosis this issue is about"
        )

    def test_the_metadata_rows_are_stripped_from_the_cell_set(self) -> None:
        """A results file written before #2345 carries no metadata and must
        still compare — the `@@` prefix cannot become a cell.
        """
        source = SCRIPT.read_text(encoding="utf-8")
        assert 'META_PREFIX = "@@"' in source
        assert "if not k.startswith(META_PREFIX)" in source


class TestTheManifestAbsorbedRatherThanReplacedWhatLandedFirst:
    """#2354 shipped the corpus half of #2345 while this was in flight.

    A rebase resolution that dropped it would look CLEAN — this file's tests
    would still pass, because they are about the manifest — and the loss would
    be invisible. So the absorption is asserted here, by PARSING the corpus
    literals out of the AST rather than grepping the file: a grep matches a
    comment, and the prose about `ARG_SPELLINGS` would survive its deletion.

    Are the two mechanisms redundant? **No, and the evidence is that the
    manifest CHANGED the corpus twice.** They are the two halves of one thing:
    `ARG_SPELLINGS` / `arg_cells` BUILD the cells, and the manifest is the
    check that the built set covers what the engine can do. A redundant second
    mechanism could not have moved the first — this one did, twice:

    * it reported `pad_width`'s cap unreachable from the nineteen spellings, so
      there is a twentieth;
    * it reported four of Django's 29 argument-taking built-ins absent from the
      sweep entirely (`json_script`, `timesince`, `timeuntil`, `urlencode`),
      because `arg_cells` iterated `FILTER_ARGS` — the ESCAPING axis's table of
      one benign argument per filter, which is a different question with a
      25/29 overlap. It iterates `django_argument_filters()` now.

    Both were live, already-merged code. That is the manifest doing the job the
    hand-added axis cannot do for itself.
    """

    #: Every spelling that came from UPSTREAM: #2354's nineteen, plus the
    #: `False` #2347 added (the third context builtin, and the one whose answer
    #: differs from both others — `int(False)` is 0 where `True` is 1 and
    #: `None` raises).
    #:
    #: A transcription, deliberately, and the only one in this file. Everything
    #: else here is recomputed from the engine, but the job of THIS check is to
    #: detect a DELETION during a merge resolution — and a set derived from the
    #: file under test could not, because it would move with the deletion. The
    #: companion test below keeps it honest by asserting the complement: what
    #: this branch adds is exactly one entry.
    LANDED_UPSTREAM = [
        '"5"', "5", '"2.7"', "2.7", '" 5 "', '"+5"', '"1_0"', '"-3"', "-3", '"0"',
        '"notanumber"', '""',
        "missingvar", "no.such.path", "known",
        "True", "False", "None", "7.", "0x10",
    ]  # fmt: skip

    @staticmethod
    def literal(name: str):
        """A module-level literal, read from the AST.

        Never imported: the script configures Django settings and mutates the
        global filter registry at import time.
        """
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        node = next(
            n.value
            for n in tree.body
            if isinstance(n, ast.Assign)
            for t in n.targets
            if isinstance(t, ast.Name) and t.id == name
        )

        # `literal_eval` per ELEMENT rather than over the whole node: since
        # #2366 `ARG_CONTEXT` binds a `datetime.datetime(...)`, which is a
        # `Call` and not a literal, and `literal_eval` refuses the whole dict
        # for it. The elements this file asks about are all literals; a
        # non-literal is kept as its unparsed source text, which is enough for
        # "is this key present" and for "what did this branch add".
        def one(sub_node):
            try:
                return ast.literal_eval(sub_node)
            except (ValueError, TypeError):
                return ast.unparse(sub_node)

        if isinstance(node, ast.List):
            return [one(e) for e in node.elts]
        if isinstance(node, ast.Dict):
            return {one(k): one(v) for k, v in zip(node.keys, node.values)}
        return ast.literal_eval(node)

    def test_every_spelling_upstream_landed_is_still_swept(self) -> None:
        spellings = self.literal("ARG_SPELLINGS")
        dropped = [s for s in self.LANDED_UPSTREAM if s not in spellings]
        assert not dropped, (
            f"{dropped} came from upstream (#2354, #2347) and are gone. A merge "
            "resolution reverted a merged fix; the manifest is meant to ABSORB that "
            "corpus, not replace it."
        )

    #: What each later branch added, and why. Scope discipline, not a floor:
    #: an unexplained addition fails, and so does a deletion.
    ADDED_SINCE = {
        '"99999999999999999999"': (
            "#2345 — the manifest reported `pad_width`'s cap unreachable from "
            "the nineteen spellings"
        ),
        "known_list": "#2366 — a resolved argument whose `int()` is a TypeError",
        "known_tuple": "#2366 — the same, at the tuple shape",
        "known_dict": "#2366 — the same, at the mapping shape",
        "known_dt": (
            "#2366 — the COUNTER-example: a `datetime` is already a string by "
            "the time any filter sees it, so it measures the extraction "
            "boundary rather than the dispatch table and must keep diverging"
        ),
        # Spelled as f-strings over Django's own two separator constants, so
        # the SOURCE text is what this AST reader sees rather than the value.
        # That is deliberate: reading the value would hide which grammar
        # constant each carries, and the point of the pair is that they are
        # Django's own.
        "f'\"a{_FILTER_SEPARATOR}b\"'": (
            "#2409 — a quoted argument carrying the FILTER separator, so the "
            "expression split is under test rather than the argument's value"
        ),
        "f'\"a{_FILTER_ARGUMENT_SEPARATOR}b\"'": (
            '#2409 — the same at the ARGUMENT separator: `{{ p|date:"H:i" }}` '
            "is one filter with one argument, and a `find(':')` split made it "
            "two"
        ),
        "_x": (
            "#2418 — a name Django's `Variable.__init__` REFUSES. Every "
            "spelling above is one it accepts, so the corpus could not build "
            "an argument cell carrying a refused name, which is the position "
            "the bulk of that defect's divergent cells lived at"
        ),
        "p._priv": (
            "#2418 — the OTHER half of the same rule: `._` anywhere in the "
            "name, not just a leading `_`. `p` rather than `known` because a "
            "dotted path cannot be a context key and the binding pin below "
            "requires every `known*` spelling to be bound"
        ),
        '_("_x")': (
            "#2418 — the COUNTER-example: it BEGINS with `_` and Django "
            "compiles it, because the translate arm strips `_( … )` before the "
            "underscore check. A fix without that arm is stricter than Django "
            "and only this row can tell"
        ),
        # #2469: the resolved-context channel had six bindings and all six
        # were TRUTHY, so no cell could ask about an argument's falsiness —
        # which is the whole of what `ArgType::is_falsy`'s first arm answers.
        # One per `Value` variant, in both answers where the variant admits
        # both, which is what `value-truthiness` requires of this channel.
        **{
            spelling: f"#2469 — a resolved argument at {shape}"
            for spelling, shape in {
                "known_empty": "the falsy `String`",
                "known_zero": "the falsy `Integer`",
                "known_float": "the truthy `Float`",
                "known_float_zero": "the falsy `Float`",
                "known_true": "the truthy `Bool`",
                "known_false": "the falsy `Bool`",
                "known_none": "`Value::None`, whose only inhabitant is falsy",
                "known_big": "`Value::BigInt`, which is never zero",
                "known_decimal": "the truthy `Decimal`",
                "known_decimal_zero": "the falsy `Decimal`",
                "known_empty_list": "the falsy `List`",
                "known_empty_tuple": "the falsy `Tuple`",
                "known_empty_dict": "the falsy `Object`",
                "known_td": "the truthy `Encoded` (a `timedelta`)",
                "known_td_zero": "the falsy `Encoded` — the ONE member of the "
                "datetime family with a falsy inhabitant, and the reason "
                "#2458 measured 0 moved on every axis",
            }.items()
        },
        "known_str_zero": (
            "#2469 — the COUNTER-example: a resolved `str` spelling `0` is "
            "TRUTHY in Python and has no `.year`, so Django's `timesince` "
            "raises. A text-shaped falsiness rule read the number it spells "
            "and measured from now; only this row separates the two"
        ),
        "known_set_empty": (
            "#2477 — a resolved argument NO `Value` variant models, on the "
            "falsy side. It reaches `falsy_opaque` and crosses as a "
            "`Value::Encoded`; before this row the argument channel had no "
            "inhabitant of that conversion arm at all, and the whole of #2466 "
            "was invisible to every axis"
        ),
        "known_falsy_iter": (
            "#2482 — the FALSY inhabitant of the terminal "
            "`Value::String(ob.str()?)` arm, in the argument channel. "
            "`known_set` is truthy and `known_set_empty` reaches "
            "`falsy_opaque`, so between them the channel had a truthy "
            "`str-fallback` and no falsy one — the member the axis was "
            "EXEMPTING, on the grounds that a class instance could not be a "
            "corpus row. It could; the exemption was wrong rather than stale"
        ),
        "known_set": (
            "#2477 — the TRUTHY partner, which lands on a DIFFERENT arm: "
            "`falsy_opaque`'s own gate declines it, so it falls to the "
            "terminal `Value::String(ob.str()?)` and its type is lost at the "
            "conversion. That residue is what `STRINGIFIED_AT_EXTRACTION` in "
            "`test_int_argument_type_2366.py` names, and no cell reached it"
        ),
    }

    def test_every_addition_is_accounted_for(self) -> None:
        """Non-vacuity for the test above, and scope discipline: every spelling
        past the upstream set is one a branch added ON PURPOSE, with the reason
        written down."""
        added = [s for s in self.literal("ARG_SPELLINGS") if s not in self.LANDED_UPSTREAM]
        assert sorted(added) == sorted(self.ADDED_SINCE), (
            f"unaccounted spellings: {sorted(set(added) - set(self.ADDED_SINCE))}; "
            f"vanished: {sorted(set(self.ADDED_SINCE) - set(added))}"
        )

    def test_the_resolvable_lookup_binding_survives(self) -> None:
        bound = self.literal("ARG_CONTEXT")
        assert "known" in bound
        # ...and every `known*` spelling has something to resolve TO. A
        # spelling with no binding is a lookup MISS, which raises on both
        # engines for a reason that has nothing to do with the type it was
        # added to measure — a cell that agrees for the wrong reason.
        for spelling in self.literal("ARG_SPELLINGS"):
            if spelling.startswith("known"):
                assert spelling in bound, f"{spelling} is swept but never bound"

    def test_render_both_still_records_a_panic_as_a_cell(self) -> None:
        """#2354's other half. Asserted on the `render_both` BODY rather than
        the whole file, so a mention in the module docstring cannot satisfy it.
        """
        body = SCRIPT.read_text(encoding="utf-8").split("def render_both(", 1)[1]
        body = body.split("\ndef ", 1)[0]
        assert "<<PANIC " in body, "the PANIC marker was dropped from render_both"
        assert "except BaseException as exc:" in body

    def test_compare_still_gates_on_newly_panicking_cells(self) -> None:
        comp = SCRIPT.read_text(encoding="utf-8").split("def compare(", 1)[1]
        assert 'du.startswith("<<PANIC ")' in comp, "compare lost its panic accounting"
        assert "(panic_a - panic_b)" in comp, "compare no longer gates on new panics"

    def test_the_argument_sweep_covers_every_filter_django_takes_one_for(self) -> None:
        """The gap the manifest found in merged code, asserted from the other
        side: 29, not the 25 `FILTER_ARGS` happens to list."""
        row = rows(run_manifest())["argument-filter"]
        assert len(row["required"]) == 29
        assert row["missing"] == [], row["missing"]


class TestTheArgumentAxisCorpus:
    """#2354's corpus, and the one thing the manifest still says about it."""

    def test_the_spellings_reach_every_error_the_chokepoint_can_raise(self) -> None:
        """The requirement side is the engine's argument errors, parsed from
        `filters.rs`; the swept side is MEASURED by rendering. Both are
        recomputed, so this asserts the corpus reaches them rather than that a
        list has N entries."""
        row = rows(run_manifest())["argument"]
        assert row["missing"] == []
        # The requirement set is recomputed from the Rust source, so its SIZE
        # is not a fact about this test — it grew 4 -> 6 when #2346 added
        # `divisibleby`'s ZeroDivisionError and `floatformat`'s IndexError.
        # What is asserted is that each distinct failure the chokepoint can
        # produce is present, by kind.
        joined = "\n".join(row["required"])
        for kind in ("does not resolve", "is a ValueError", "is a TypeError", "past djust's"):
            assert kind in joined, kind

    def test_the_argument_cells_exist_and_disagree_somewhere(self, tmp_path: pathlib.Path) -> None:
        """Non-vacuity for the whole axis (#1468 in corpus form).

        A corpus that built argument cells which all AGREED would be
        coverage-shaped and blind — worse than absent, because it would make
        the axis look measured. These are the divergences #2344 and #2346 name.
        """
        out = tmp_path / "cells.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        payload = json.loads(out.read_text())
        arg_cells = {
            k: v for k, v in payload.items() if not k.startswith("@@") and k.startswith("@arg ")
        }
        assert arg_cells, "the corpus built no argument cells"
        assert [k for k, v in arg_cells.items() if v[0] != v[1]], (
            "no argument cell disagrees — the axis measures nothing"
        )
        assert payload["@@cells_by_axis"]["argument"] == len(arg_cells)

    def test_a_clock_dependent_argument_cell_records_its_AGREEMENT(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The blindness the manifest could not report, closed rather than filed.

        `timesince`/`timeuntil` are in `NONDET`, so every cell of theirs was
        rewritten to `<NONDET len=N>` on both sides and `load()` collapsed that
        to a bare `<NONDET>` — the two sides then always compared EQUAL. Right
        for `random`, whose draw is not comparable at all; BLIND for the two
        filters whose whole subject on this axis is their argument.

        Measured, which is how it was found: #2344 makes them read the argument
        as the comparison instant, 120 argument cells move, and the
        length-collapsed corpus reported **zero**. The tool built to catch a
        corpus that cannot see a change could not see that one.

        The comparable property is the AGREEMENT BIT. It is stable — both
        engines read the same clock microseconds apart — while a cell made
        deterministic by its argument reports honestly.

        Note this is NOT something the manifest itself can report: no axis asks
        "is this cell's answer comparable at all", and the requirement sets are
        about what the corpus BUILDS rather than what it can distinguish. It was
        found by using the tool on #2344, which is the same way every entry in
        this file's table was found.
        """
        out = tmp_path / "cells.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        payload = json.loads(out.read_text())
        clock = {
            k: v
            for k, v in payload.items()
            if k.startswith("@arg timesince:") or k.startswith("@arg timeuntil:")
        }
        assert clock, "no clock-dependent argument cell in the corpus"
        assert all(v[0] == "<NONDET>" for v in clock.values())
        assert {v[1] for v in clock.values()} <= {"<NONDET>", "<NONDET differs>"}

        # Non-vacuity, and the half a marker-shaped-but-never-produced encoding
        # would fail: the writer must ACTUALLY emit `<NONDET differs>`. An
        # assertion that merely allowed the two spellings passes when every
        # cell is `<NONDET>`, which is the collapse this replaced.
        differs = [k for k, v in clock.items() if v[1] == "<NONDET differs>"]
        assert differs, (
            "no clock-dependent cell records a disagreement, so the marker is "
            "shaped like a measurement and is not one. If `timesince` and "
            "`timeuntil` now agree with Django on every spelling, replace this "
            "with a synthetic disagreement — do not delete it."
        )

    def test_the_agreement_marker_survives_load(self, tmp_path: pathlib.Path) -> None:
        """`load()` rewrites `<NONDET len=N>` to a bare `<NONDET>`, and a
        rewrite that also swallowed `<NONDET differs>` would put the collapse
        back with extra steps. Asserted through `--compare`, which is the only
        consumer that matters."""

        def cell(differs: bool) -> dict:
            return {
                '@arg timesince:"5"\ts-img\targ': [
                    "<NONDET>",
                    "<NONDET differs>" if differs else "<NONDET>",
                ],
                '@arg center:"5"\ts-img\targ': ["  ab ", "  ab "],
                "@@build": "aaa" if differs else "bbb",
            }

        proc = run_compare(tmp_path, cell(True), cell(False))
        assert proc.returncode == 0, proc.stdout
        assert "newly AGREEING: 1" in proc.stdout, proc.stdout

    def test_the_random_filter_is_still_collapsed_rather_than_compared(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The other side of the same rule, and the reason it is not applied to
        the `{{ }}` corpus: `random` picks a different element each run, so its
        agreement bit is NOT stable and recording it would produce a cell that
        flaps. It stays `<NONDET len=N>`, which `load()` erases.

        `random` takes no argument, so it never reaches `nondet_agreement` —
        this asserts that rather than trusting it.
        """
        out = tmp_path / "cells.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        payload = json.loads(out.read_text())
        randoms = {
            k: v
            for k, v in payload.items()
            if "random" in k.split("\t")[0] and not k.startswith("@arity ")
        }
        assert randoms, "no `random` cell in the corpus"
        assert all(v[0].startswith("<NONDET len=") for v in randoms.values()), (
            "a `random` cell now records an agreement bit, which flaps between runs"
        )
        # The ARITY axis is the one place a `random` cell is DETERMINISTIC and
        # must stay comparable (#2400): `{{ p|random:"x" }}` is refused before
        # the filter runs, so there is no draw to flap. Collapsing it would
        # erase the movement this axis exists to measure.
        arity_randoms = {
            k: v
            for k, v in payload.items()
            if k.startswith("@arity ") and "random" in k.split("\t")[0]
        }
        assert arity_randoms, "the arity axis built no `random` cell"
        assert all(not v[0].startswith("<NONDET len=") for v in arity_randoms.values()), (
            "an arity `random` cell was collapsed; a refusal is deterministic"
        )


class TestRenderBothSurvivesAPanic:
    """A `pyo3` panic must become a CELL, not end the run (#2345).

    `PanicException`'s MRO is `BaseException` directly, so the previous
    `except Exception` did not catch it and the sweep died mid-run on the
    #2343 `stringformat` panic — found by the traceback rather than by a
    comparable cell.
    """

    def test_the_handler_catches_baseexception(self) -> None:
        body = SCRIPT.read_text(encoding="utf-8").split("def render_both(", 1)[1]
        body = body.split("\ndef ", 1)[0]
        assert "except BaseException" in body
        assert "except (KeyboardInterrupt, SystemExit):" in body, (
            "an interruptible sweep still has to be interruptible: the "
            "`except BaseException` that makes a panic a cell also catches "
            "Ctrl-C, so the two operator signals are re-raised ahead of it"
        )

    def test_the_interrupt_reraise_precedes_the_baseexception_arm(self) -> None:
        """ORDER, not just presence.

        `except BaseException` catches Ctrl-C as well as a panic, so the
        re-raise has to come FIRST or it never runs — and an except-clause
        order bug is invisible to every test that does not interrupt the
        process.

        The empirical half of this class used to render
        ``{{ 42|stringformat:"" }}`` and assert the panic became a cell.
        #2354 FIXED that panic, so the probe began skipping on every run — a
        test that can no longer go red is worse than absent. The panic
        BOUNDARY has its own coverage in
        ``python/tests/test_panic_boundary_2343.py``; what is left here is the
        property that file does not assert.
        """
        body = SCRIPT.read_text(encoding="utf-8").split("def render_both(", 1)[1]
        body = body.split("\ndef ", 1)[0]
        # Two `try` blocks, one per engine, and BOTH must re-raise ahead of
        # their catch-all. A SET, not a floor (#1125).
        assert body.count("except (KeyboardInterrupt, SystemExit):") == 2, body
        for arm in ("except Exception as exc:", "except BaseException as exc:"):
            assert body.index("except (KeyboardInterrupt, SystemExit):") < body.index(arm), arm
