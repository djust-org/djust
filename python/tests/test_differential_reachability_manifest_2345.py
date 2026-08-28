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

:class:`TestItWouldHaveCaughtTheHistoricalBlindSpots` is the empirical canary
(#1459) for that claim: each case reconstructs the pre-fix corpus by editing a
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
        substitution is forced rather than convenient: #2296's fix REMOVED
        ``dictsort`` from ``ITEM_SAFETY_PRESERVING_FILTERS`` (the constant's own
        doc-comment keeps it as the worked example), so the historical name is
        no longer in the engine-derived requirement set at all and cannot be
        made missing from it. ``linenumbers`` has the identical shape — granted
        output safety by ``SAFE_OUTPUT_FILTERS``, and itself the subject of a
        shipped XSS (#2291, ``{{ p|linenumbers|safe }}``).
        """
        script = mutated_script(tmp_path, ('    "linenumbers",\n', ""))
        missing = rows(run_manifest(script))["chain"]["missing"]
        assert missing == ["linenumbers"], missing

    def test_2325_no_tag_cell_existed_at_all(self, tmp_path: pathlib.Path) -> None:
        """The corpus was entirely ``{{ p|… }}``; a filter on a TAG operand is a
        different resolution path and djust had open-coded it four times."""
        script = mutated_script(
            tmp_path,
            ('TAG_SHAPES = {\n    "for":', 'TAG_SHAPES = {}\n_PRE_2325_TAG_SHAPES = {\n    "for":'),
            ("PATH_SHAPES = {\n", "PATH_SHAPES: dict[str, str] = {}\n_PRE_2334_PATH_SHAPES = {\n"),
        )
        missing = rows(run_manifest(script))["tag"]["missing"]
        assert sorted(missing) == ["for", "if", "with"], missing

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
                "ARG_SPELLINGS = {\n",
                "ARG_SPELLINGS: dict[str, str] = {}\n_PRE_2345_SPELLINGS = {\n",
            ),
        )
        missing = rows(run_manifest(script))["argument"]["missing"]
        assert len(missing) == 4, missing
        assert any("does not resolve" in m for m in missing)
        assert any("is a ValueError" in m for m in missing)
        assert any("is a TypeError" in m for m in missing)
        assert any("past djust's" in m for m in missing)

    def test_the_issues_nineteen_spellings_leave_the_pad_cap_unreachable(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The manifest earning its keep on its own first run.

        #2345 proposed nineteen spellings. Measured against the engine's four
        argument errors, they reach three: no spelling parses to a width past
        ``MAX_PAD_WIDTH``, so the cap's error — the guard standing between a
        template-supplied width and an allocator ABORT (#2328) — is
        unreachable. ``q-huge`` is the twentieth, added because of this.
        """
        script = mutated_script(tmp_path, ('    "q-huge": \'"99999999999999999999"\',\n', ""))
        missing = rows(run_manifest(script))["argument"]["missing"]
        assert len(missing) == 1 and "past djust's" in missing[0], missing


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


class TestTheArgumentAxisCorpus:
    """The corpus #2345 asks for, and the properties it must have."""

    def test_the_spellings_cover_every_class_the_issue_names(self) -> None:
        data = run_manifest()
        # The requirement side is the engine's four argument errors; the swept
        # side is measured by rendering. Both are recomputed, so this asserts
        # the corpus reaches them rather than that a list has 20 entries.
        assert rows(data)["argument"]["missing"] == []
        assert len(rows(data)["argument"]["required"]) == 4

    def test_the_argument_mode_builds_cells_and_they_are_argument_cells(
        self, tmp_path: pathlib.Path
    ) -> None:
        out = tmp_path / "arg.json"
        proc = subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), "--axis", "argument", str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-4000:]
        payload = json.loads(out.read_text())
        cells = [k for k in payload if not k.startswith("@@")]
        assert cells, "the argument mode built no cells"
        assert all(k.startswith("@arg ") for k in cells)
        assert payload["@@mode"] == "argument"
        assert payload["@@cells_by_axis"] == {"argument": len(cells)}
        # Enough of the corpus to be a corpus: every argument-taking built-in,
        # every spelling. The product is recomputed from the emitted cells.
        filters = {k.split("@arg ", 1)[1].split(":", 1)[0] for k in cells}
        assert len(filters) == 29, sorted(filters)

    def test_a_cell_where_both_engines_raise_agrees(self, tmp_path: pathlib.Path) -> None:
        """The raise-BIT is what this axis compares, never the message.

        Django raises `ValueError` / `TypeError` / `VariableDoesNotExist` from
        Python; djust raises a `RuntimeError` wrapping a Rust error whose text
        names the filter and the argument. Those strings can never match, so
        recording them marks every raising cell as permanently disagreeing —
        and the axis then cannot tell "djust now raises where Django does not",
        which is a real regression and the whole point of #2328, from "both
        raise, as they always did".

        Measured: with the messages kept, this corpus reports 2,014 of 4,060
        cells disagreeing and NO filter clean; with them collapsed, 1,117 and
        ten filters clean — including every filter #2328 fixed.
        """
        out = tmp_path / "arg.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), "--axis", "argument", str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        payload = json.loads(out.read_text())
        # `center:"notanumber"` raises on both sides for the SAME reason — the
        # argument is not an integer — and the two messages have nothing in
        # common. It is the cell the collapse exists for.
        cell = payload["@arg center:q-word\thappy\tvar"]
        assert cell == ["<<RAISED>>", "<<RAISED>>"], cell
        raising = [
            k
            for k, v in payload.items()
            if not k.startswith("@@") and v[0] == "<<RAISED>>" and v[1] == "<<RAISED>>"
        ]
        assert len(raising) > 200, (
            f"only {len(raising)} cells raise on both sides — the corpus is not "
            "reaching the raise bit this axis exists to measure"
        )

    def test_an_unparseable_argument_actually_disagrees_somewhere(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Non-vacuity for the whole axis (#1468 in corpus form).

        A corpus that built argument cells which all AGREED would be
        coverage-shaped and blind — worse than absent, because it would make
        the axis look measured. These are the divergences #2344 and #2346 name.
        """
        out = tmp_path / "arg.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), "--axis", "argument", str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        payload = json.loads(out.read_text())
        disagree = [k for k, v in payload.items() if not k.startswith("@@") and v[0] != v[1]]
        assert disagree, "no argument cell disagrees — the axis measures nothing"


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
            "an interruptible sweep still has to be interruptible"
        )

    def test_a_panicking_cell_is_recorded_rather_than_fatal(self, tmp_path: pathlib.Path) -> None:
        """Empirical, against the real panic: `{{ 42|stringformat:"" }}`."""
        out = tmp_path / "panic.py"
        out.write_text(
            "from djust import _rust\n"
            "try:\n"
            "    r = _rust.render_template('{{ p|stringformat:\"\" }}', {'p': 42})\n"
            "    print('NO PANIC', repr(r))\n"
            "except BaseException as exc:\n"
            "    print('CAUGHT', type(exc).__mro__[1].__name__)\n",
            encoding="utf-8",
        )
        proc = subprocess.run(  # noqa: S603 — a file this test wrote, no shell
            [sys.executable, str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr[-2000:]
        if proc.stdout.startswith("NO PANIC"):
            pytest.skip("#2343 is fixed; this cell no longer panics")
        assert proc.stdout.startswith("CAUGHT BaseException"), proc.stdout
