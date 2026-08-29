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
            "argument-filter",
            "tag",
            "entrypoint",
            "grant-shape",
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

    def test_the_nineteen_spellings_leave_the_pad_cap_unreachable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The manifest earning its keep against the corpus it ships beside.

        #2354's ``ARG_SPELLINGS`` has nineteen entries and they reach three of
        the engine's four argument errors. The fourth — ``pad_width``'s cap,
        the guard standing between a template-supplied width and an allocator
        ABORT (#2328) — needs a width that PARSES and saturates past ``isize``,
        and every one of the nineteen either parses to a small number, fails to
        parse, or fails to resolve. So there is a twentieth, and this removes
        it again to prove the report was real rather than decorative.

        This is the manifest reporting a gap in live, already-merged code, not
        in a synthetic reconstruction — which is the strongest form the claim
        has.
        """
        script = mutated_script(tmp_path, ("\n    '\"99999999999999999999\"',\n]", "\n]"))
        row = rows(run_manifest(script))["argument"]
        assert len(row["missing"]) == 1 and "past djust's" in row["missing"][0], row["missing"]
        # And every OTHER required error is still reachable from the nineteen,
        # so the report names the one gap rather than blaming the whole axis.
        # Stated as a set difference: the count moves when the engine grows an
        # argument error, and the claim does not.
        assert len(row["required"]) - len(row["missing"]) >= 3
        assert set(row["missing"]) < set(row["required"])


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
        """
        script = mutated_script(
            tmp_path,
            ("PATH_SHAPES = {\n", "PATH_SHAPES: dict[str, str] = {}\n_PRE_2334_PATH_SHAPES = {\n"),
        )
        data = run_manifest(script)
        assert not any(row.get("missing") for row in data["axes"]), (
            "removing every dict-view PATH shape is now reported — good, but this "
            "test documents the opposite. Update it to the catch."
        )


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
        return ast.literal_eval(node)

    def test_every_spelling_upstream_landed_is_still_swept(self) -> None:
        spellings = self.literal("ARG_SPELLINGS")
        dropped = [s for s in self.LANDED_UPSTREAM if s not in spellings]
        assert not dropped, (
            f"{dropped} came from upstream (#2354, #2347) and are gone. A merge "
            "resolution reverted a merged fix; the manifest is meant to ABSORB that "
            "corpus, not replace it."
        )

    def test_the_only_addition_is_the_one_the_manifest_asked_for(self) -> None:
        """Non-vacuity for the test above, and scope discipline for this PR:
        the corpus grew by exactly the spelling the manifest reported missing,
        and by nothing else."""
        added = [s for s in self.literal("ARG_SPELLINGS") if s not in self.LANDED_UPSTREAM]
        assert added == ['"99999999999999999999"'], added

    def test_the_resolvable_lookup_binding_survives(self) -> None:
        assert "known" in self.literal("ARG_CONTEXT")

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
        randoms = {k: v for k, v in payload.items() if "random" in k.split("\t")[0]}
        assert randoms, "no `random` cell in the corpus"
        assert all(v[0].startswith("<NONDET len=") for v in randoms.values()), (
            "a `random` cell now records an agreement bit, which flaps between runs"
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
