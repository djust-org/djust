"""The differential's agreement number, and the whole class of fix it could not see (#2454).

The defect
----------
``scripts/filter-parity-differential.py`` computed agreement by RAW string
equality::

    agree_b = {k for k, (a, b) in base.items() if a == b}

Two engines that both REFUSE a template agree — that is what ``_outcome``
exists to say, and its docstring says it in those words — but their exception
texts can never match. Django raises ``TemplateSyntaxError: Invalid filter:
'nosuchfilter'``; djust raises ``RuntimeError: Template error: Unknown filter:
nosuchfilter``. So a parity fix whose entire effect is *"djust now refuses a
template Django refuses"* moved **zero** cells into agreement however
completely it succeeded.

That is not a corner: it is the whole of the ``masked-refusal``,
``variable-name``, arity and lexer-bound work. Measured on #2419's two real
builds (``ce4c867b4766fb1d`` → ``aa900bbe582fc0d8``, 353,909 cells)::

    agree BEFORE : 257295
    agree AFTER  : 257295
      masked-refusal     44 moved  of    384
    newly AGREEING: 0

— and ``--compare`` then printed its same-build NOTE, *"Either the change moves
nothing, or it moves an axis this corpus does not sweep"*, which was false in a
way only a reader who already knew the answer could spot.

Two things the issue got wrong, both corrected by running it
------------------------------------------------------------
1. The helper it names ``collapse()`` is called :func:`_outcome`. Same
   substance; the name in the issue does not exist.
2. It says 44 cells move into agreement. **40 do.** The other four already
   agreed by refusal on the baseline — djust refused them for an unrelated
   reason (``{% include %}`` with no template loader configured), so they moved
   without moving into agreement. The four-cell gap is the reason this file
   pins the AGREEMENT BIT rather than the moved count: they are different
   questions and the corpus contains cells that separate them.

What landed
-----------
:func:`agreement` returns both sets — ``exact`` unchanged, so every baseline
ever measured stays comparable, and ``collapsed`` added beside it. Adding a
number rather than redefining one is the whole design constraint: a results
file records no marker for which definition a reader used, so a silent change
of meaning would be undetectable between two copies of the script.
:func:`refusal_split` reports the class such a fix actually moves as its own
headline line.

And the same blindness was in the GATE, which the issue explicitly says it is
not — see :class:`TestTheGateSawNoRefusalClassRegressionEither`. Running the
#2419 comparison BACKWARDS (the fixed build as baseline, the unfixed build as
"after") is a real, un-synthetic permissiveness regression of 40 cells, and the
pre-fix tool reported ``REGRESSIONS: 0`` and exited **0** over it.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "filter-parity-differential.py"


def _env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "python"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return env


def run_compare(
    tmp_path: pathlib.Path,
    base: dict,
    after: dict,
    *args: str,
    script: pathlib.Path | None = None,
) -> subprocess.CompletedProcess:
    """``--compare`` over two hand-built results files.

    Mirrors ``test_differential_reachability_manifest_2345.run_compare``: what
    is under test is what ``compare`` does with two FILES, and building the
    engine twice to exercise it would test the compiler. The two-build run is
    the PR's canary; this is the unit.
    """
    base_path, after_path = tmp_path / "base.json", tmp_path / "after.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    after_path.write_text(json.dumps(after), encoding="utf-8")
    return subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
        [
            sys.executable,
            str(script or SCRIPT),
            "--compare",
            str(base_path),
            str(after_path),
            *args,
        ],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(REPO),
        check=False,
    )


#: Django's side of a cell it refuses, verbatim from the #2419 corpus run.
DJ_REFUSES = "<<EXC TemplateSyntaxError: Invalid filter: 'nosuchfilter'>>"
#: djust's, after #2419 made the name refuse at parse time.
DU_REFUSES = "<<EXC RuntimeError: Template error: Unknown filter: nosuchfilter>>"


def _cells(**overrides: list[str]) -> dict:
    """A minimum results file: one always-agreeing cell plus whatever is named.

    The always-agreeing `upper` cell keeps the two files' cell SETS equal and
    keeps `axis_of` able to bucket something, so the comparison reaches the
    numbers under test rather than the not-comparable refusal.
    """
    return {"upper\ts-plain": ["ABC", "ABC"], **overrides}


def _with_build(cells: dict, build: str) -> dict:
    return {**cells, "@@build": build, "@@cells_by_axis": {}}


def counts(out: str) -> dict[str, int]:
    """Every number this file asserts on, read off one `--compare` run."""
    got: dict[str, int] = {}
    for label, pattern in (
        ("agree_b", r"agree BEFORE : (\d+)"),
        ("coll_b", r"agree BEFORE : \d+\s+\(refusal-collapsed: (\d+)\)"),
        ("agree_a", r"agree AFTER  : (\d+)"),
        ("coll_a", r"agree AFTER  : \d+\s+\(refusal-collapsed: (\d+)\)"),
        ("perm_b", r"django REFUSES & djust RENDERS: (\d+) ->"),
        ("perm_a", r"django REFUSES & djust RENDERS: \d+ -> (\d+)"),
        ("strict_b", r"djust REFUSES & Django RENDERS: (\d+) ->"),
        ("strict_a", r"djust REFUSES & Django RENDERS: \d+ -> (\d+)"),
        ("newly", r"newly AGREEING: (\d+)"),
        ("newly_coll", r"newly AGREEING: \d+\s+\(refusal-collapsed: (\d+)\)"),
        ("no_longer", r"no longer agreeing: (\d+)"),
        ("coincidental", r"coincidental \(the filter itself diverges on both builds\): (\d+)"),
        ("regressions", r"REGRESSIONS : (\d+)"),
    ):
        m = re.search(pattern, out)
        assert m, f"{label}: no match for {pattern!r} in\n{out}"
        got[label] = int(m.group(1))
    return got


class TestTheHeadlineSeesARefusalClassFix:
    """The number a reader anchors on now moves when a refusal-class fix lands."""

    @pytest.fixture
    def fixed(self, tmp_path: pathlib.Path) -> subprocess.CompletedProcess:
        """#2419 in miniature: djust rendered where Django refused, now it refuses."""
        return run_compare(
            tmp_path,
            _with_build(_cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, "N"]}), "aaa"),
            _with_build(
                _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, DU_REFUSES]}), "bbb"
            ),
        )

    def test_the_exact_count_still_cannot_see_it(self, fixed) -> None:
        """The defect itself, pinned. The exact number is unchanged and stays
        unchanged — it is a published definition every existing baseline was
        measured against, and redefining it is the one option #2454 rejects."""
        got = counts(fixed.stdout)
        assert got["agree_b"] == got["agree_a"] == 1
        assert got["newly"] == 0

    def test_the_collapsed_count_does(self, fixed) -> None:
        got = counts(fixed.stdout)
        assert (got["coll_b"], got["coll_a"]) == (1, 2)
        assert got["newly_coll"] == 1

    def test_the_refusal_direction_is_its_own_headline(self, fixed) -> None:
        """The class a reader of this work actually wants, printed as a line
        rather than left to be hand-computed from the moved set — which is what
        #2418's and #2419's CHANGELOG entries both had to do."""
        got = counts(fixed.stdout)
        assert (got["perm_b"], got["perm_a"]) == (1, 0)
        assert (got["strict_b"], got["strict_a"]) == (0, 0)
        assert "+1; djust more permissive than Django" in fixed.stdout

    def test_the_same_build_note_no_longer_fires_over_it(self, fixed) -> None:
        """The false statement the old tool printed over #2419, in the reader's
        own words: *"Either the change moves nothing."*"""
        assert fixed.returncode == 0, fixed.stdout
        assert "genuinely two builds" in fixed.stdout
        assert "the change moves nothing" not in fixed.stdout

    def test_the_note_still_fires_when_nothing_moved_under_either_definition(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Non-vacuity for the branch above (#2328's reading must survive).

        A NOTE that never fires would be the opposite failure: #2328 was two
        real builds, zero moved cells, and the reader correctly told the corpus
        could not see the change.
        """
        cells = _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, "N"]})
        proc = run_compare(tmp_path, _with_build(cells, "aaa"), _with_build(cells, "bbb"))
        assert proc.returncode == 0, proc.stdout
        assert "the change moves nothing" in proc.stdout
        assert "under BOTH definitions" in proc.stdout

    def test_a_reworded_refusal_is_not_reported_as_movement_into_agreement(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The four cells the issue over-counted, in miniature.

        Both engines already refused; djust's wording changed, for a better
        reason. The cell MOVED and did not move into agreement, and the
        collapsed count must say so — otherwise it would be as blind in the
        other direction as raw equality is in this one.
        """
        proc = run_compare(
            tmp_path,
            _with_build(
                _cells(
                    **{
                        "@mask nosuchfilter\ti-int\tmask": [
                            DJ_REFUSES,
                            "<<EXC RuntimeError: Template error: Template loader not configured>>",
                        ]
                    }
                ),
                "aaa",
            ),
            _with_build(
                _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, DU_REFUSES]}), "bbb"
            ),
        )
        got = counts(proc.stdout)
        assert got["newly_coll"] == 0, "it agreed by refusal before and after"
        assert (got["coll_b"], got["coll_a"]) == (2, 2)
        assert (got["perm_b"], got["perm_a"]) == (0, 0)
        assert re.search(r"masked-refusal\s+1 moved", proc.stdout), proc.stdout


class TestTheGateSawNoRefusalClassRegressionEither:
    """The half #2454 says is unaffected, and is not.

    The issue's *"What it does NOT affect"* section reasons that the gating
    checks are computed off the moved sets and off ``live()``. ``REGRESSIONS``
    is not: it is ``agree_b - agree_a``, raw equality, and a cell where BOTH
    engines raised was never in ``agree_b`` to be missed from ``agree_a``.

    So djust ceasing to refuse a template Django refuses — the exact undoing of
    #2418/#2419 — passed the gate. Empirically, on the two real #2419 builds run
    backwards: ``REGRESSIONS: 0``, exit ``0``, over 40 genuinely regressed cells.
    """

    def test_djust_ceasing_to_refuse_where_django_refuses_is_a_regression(
        self, tmp_path: pathlib.Path
    ) -> None:
        proc = run_compare(
            tmp_path,
            _with_build(
                _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, DU_REFUSES]}), "aaa"
            ),
            _with_build(_cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, "N"]}), "bbb"),
            script=SCRIPT,
        )
        got = counts(proc.stdout)
        assert got["regressions"] == 1, proc.stdout
        assert proc.returncode == 1, proc.stdout
        assert (got["perm_b"], got["perm_a"]) == (0, 1)

    def test_a_plain_exact_regression_is_still_reported(self, tmp_path: pathlib.Path) -> None:
        """Non-weakening. The union can only ever be a superset of what raw
        equality reported, and the raw-equality half must still fire on its own
        — a cell both engines rendered identically and now do not."""
        proc = run_compare(
            tmp_path,
            _with_build(_cells(**{"upper\ts-img": ["AB", "AB"]}), "aaa"),
            _with_build(_cells(**{"upper\ts-img": ["AB", "ab"]}), "bbb"),
        )
        assert counts(proc.stdout)["regressions"] == 1, proc.stdout
        assert proc.returncode == 1, proc.stdout

    def test_a_reworded_refusal_on_both_sides_is_not_a_regression(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The false positive the union must not introduce. Both engines refuse
        before and after; only djust's wording changed. Nothing regressed, and
        a gate that fired here would make every message edit a failure."""
        proc = run_compare(
            tmp_path,
            _with_build(
                _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, "<<EXC A: one>>"]}), "aaa"
            ),
            _with_build(
                _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, "<<EXC B: two>>"]}), "bbb"
            ),
        )
        got = counts(proc.stdout)
        assert (got["no_longer"], got["regressions"]) == (0, 0), proc.stdout
        assert proc.returncode == 0, proc.stdout


class TestTheCoincidenceArmIsJudgedByTheSameDefinition:
    """`unmasked` decides whether a tag cell's new disagreement is its filter's.

    It asked that question by raw equality too. A twin where both engines merely
    RAISE reads as "the filter itself diverges" under raw equality and as
    "the filter AGREES" under the collapsed one — and only the second is true.
    Left alone, it would have excused exactly the regressions the widened gate
    exists to report.

    Both arms are exercised, because a classifier tested in one direction only
    is a classifier that could be a constant (#1859).
    """

    #: `@mask <expr>\t<key>\t<shape>` is a tag cell; `<expr>\t<key>` is its twin.
    TAG = "@mask nosuchfilter\ti-int\tmask"
    TWIN = "@mask nosuchfilter\ti-int"

    def test_a_tag_cell_is_still_excused_when_its_twin_genuinely_diverges(
        self, tmp_path: pathlib.Path
    ) -> None:
        """#2325's reading, preserved: the twin renders differently on both
        builds, so the tag cell's new disagreement is that divergence surfacing."""
        proc = run_compare(
            tmp_path,
            _with_build(_cells(**{self.TAG: ["", ""], self.TWIN: ["X", "Y"]}), "aaa"),
            _with_build(_cells(**{self.TAG: ["", "Z"], self.TWIN: ["X", "Y"]}), "bbb"),
        )
        got = counts(proc.stdout)
        assert (got["coincidental"], got["regressions"]) == (1, 0), proc.stdout
        assert proc.returncode == 0, proc.stdout

    def test_a_tag_cell_is_not_excused_when_its_twin_only_BOTH_RAISES(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The over-excuse. The twin's two engines both refuse — they agree —
        so it explains nothing, and the tag cell's new disagreement is the
        change's."""
        twin = ["<<EXC TemplateSyntaxError: a>>", "<<EXC RuntimeError: b>>"]
        proc = run_compare(
            tmp_path,
            _with_build(_cells(**{self.TAG: ["", ""], self.TWIN: twin}), "aaa"),
            _with_build(_cells(**{self.TAG: ["", "Z"], self.TWIN: twin}), "bbb"),
        )
        got = counts(proc.stdout)
        assert (got["coincidental"], got["regressions"]) == (0, 1), proc.stdout
        assert proc.returncode == 1, proc.stdout


class TestBothDefinitionsComeFromOneHelperAtEveryReader:
    """The caller SET, pinned in both directions (the sink-grep rule).

    Two readers compute agreement — `measure` prints it for one build, `compare`
    compares it across two — and #2454 is what happens when a definition is
    fixed at one of them and not the other. An exact-set assertion goes red for
    a reader that stops calling the helper AND for one that starts, which a
    floor (`>= 2`) cannot do.
    """

    @staticmethod
    def _callers(source: str, name: str) -> set[str]:
        tree = ast.parse(source)
        found = set()
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == name
                ):
                    found.add(fn.name)
        return found

    def test_the_agreement_readers_are_exactly_measure_and_compare(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        assert self._callers(source, "agreement") == {"measure", "compare"}
        assert self._callers(source, "refusal_split") == {"measure", "compare"}

    def test_the_pin_goes_red_when_a_reader_stops_calling_the_helper(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Canary, direction one: the #2454 shape itself — one reader keeps the
        raw comprehension while the other moves to the helper."""
        source = SCRIPT.read_text(encoding="utf-8")
        old = "    exact, collapsed = agreement(result)\n"
        assert source.count(old) == 1
        mutated = source.replace(
            old,
            "    exact = {k for k, (a, b) in result.items() if a == b}\n    collapsed = exact\n",
            1,
        )
        assert mutated != source
        assert self._callers(mutated, "agreement") == {"compare"}

    def test_the_pin_goes_red_when_a_THIRD_reader_appears(self) -> None:
        """Canary, direction two. A floor-based `>= 2` would pass this."""
        source = SCRIPT.read_text(encoding="utf-8")
        mutated = source + "\n\ndef report_agreement(d):\n    return agreement(d)\n"
        assert self._callers(mutated, "agreement") == {"measure", "compare", "report_agreement"}

    def test_no_raw_agreement_comprehension_survives_outside_the_helper(self) -> None:
        """The pre-fix expression, grepped for by its exact shape (#1391).

        `agreement` is the only place a cell's two sides may be compared to
        decide whether they agree; a second one is the drift this issue is.
        """
        source = SCRIPT.read_text(encoding="utf-8")
        raw = re.findall(r"\{k for k, \(a, b\) in \w+\.items\(\) if a == b\}", source)
        assert raw == [], f"a raw agreement comprehension survives: {raw}"


class TestEveryExistingBaselineStaysComparable:
    """The cost #2454 weighs option 1 against, and the reason it is not taken.

    A results file records no marker for which definition produced it, so the
    stored payload must not change: both numbers are DERIVED in `compare` from
    the cells themselves, which makes a file written by any older copy of this
    script comparable under both definitions at once.
    """

    def test_the_payload_gains_no_definition_marker(self, tmp_path: pathlib.Path) -> None:
        out = tmp_path / "cells.json"
        subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
            [sys.executable, str(SCRIPT), str(out)],
            capture_output=True,
            text=True,
            env=_env(),
            cwd=str(REPO),
            check=True,
        )
        meta = {k for k in json.loads(out.read_text()) if k.startswith("@@")}
        assert meta == {"@@build", "@@cells_by_axis", "@@manifest"}, (
            "a new metadata row would make a file written by this copy of the "
            "script structurally different from every baseline already measured"
        )

    def test_a_baseline_with_no_metadata_at_all_still_reports_both_numbers(
        self, tmp_path: pathlib.Path
    ) -> None:
        """A pre-#2345 file carries no `@@build`. It must still compare, and the
        collapsed reading must be available for it — the point of deriving both
        from the cells rather than recording either."""
        base = _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, "N"]})
        after = _cells(**{"@mask nosuchfilter\ti-int\tmask": [DJ_REFUSES, DU_REFUSES]})
        proc = run_compare(tmp_path, base, after)
        got = counts(proc.stdout)
        assert (got["coll_b"], got["coll_a"]) == (1, 2)
        assert "predates the build" not in proc.stdout, proc.stdout
