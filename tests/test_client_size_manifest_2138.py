"""One measured source for every client-size claim in prose (#2138).

Three places quoted a client size, describing **two different artifacts**, and
only the README pair was checked. So the unchecked pair (CLAUDE.md) drifted
more than 2× — ``~87 KB`` against an actual ``~188 KB`` — while the checked
pair drifted across three PRs that each added a few hundred bytes, none of
which crossed the ±3 KB band alone.

The result was three failing tests on ``main``, which made the pre-push hook
reject **every** branch until #2133 landed.

Two things had to change:

1. ``scripts/build-client.sh`` writes ``client-sizes.json``, the single
   measured source, and it is **committed** — the ``.gz`` artifacts are
   gitignored, so without it the check measures a locally-built file and
   warns-and-skips on a fresh clone, meaning CI and a contributor could
   disagree about whether a claim is in band.
2. The checker resolves the artifact **per line** rather than per file, so a
   doc that legitimately cites both is checked against the right one for each
   claim — and a figure quoted precisely to say it was *wrong* can be marked
   historical rather than forced to be current.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "python/djust/static/djust/client-sizes.json"
CHECKER = ROOT / "scripts/check-doc-snippets.py"


def _manifest() -> dict:
    if not MANIFEST.is_file():
        pytest.skip("client-sizes.json absent — run `make build-js`")
    return json.loads(MANIFEST.read_text())


# --- the manifest is the source ------------------------------------------


def test_the_manifest_is_committed():
    # The whole point. The .gz artifacts are gitignored, so a manifest that is
    # only generated locally leaves a fresh clone with nothing to check.
    out = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(MANIFEST.relative_to(ROOT))],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, (
        "client-sizes.json must be tracked — the .gz files it describes are "
        "gitignored, so CI and a contributor would otherwise measure different "
        "things (or nothing)"
    )


def test_the_manifest_names_both_artifacts():
    m = _manifest()
    assert m["shipped"]["artifact"] == "client.min.js.gz"
    assert m["unminified"]["artifact"] == "client.js"
    # Conflating them is how three figures for "the client size" drifted apart.
    assert m["shipped"]["kb"] < m["unminified"]["gz_kb"], (
        "the shipped bundle must be smaller than the unminified input; if this "
        "fails the two sections are describing the same thing"
    )


def test_the_manifest_matches_the_files_on_disk():
    # A stale manifest is worse than none: it would authorise a wrong claim.
    m = _manifest()
    static = ROOT / "python/djust/static/djust"
    assert (static / "client.js").stat().st_size == m["unminified"]["bytes"]
    assert (static / "client.min.js").stat().st_size == m["minified_raw"]["bytes"]
    gz = static / "client.min.js.gz"
    if gz.is_file():
        # Tolerance, not equality. build-client.sh:20-25 gitignores the .gz
        # precisely because gzip is an unpinned system tool whose output can
        # differ byte-for-byte across contributor toolchains — so asserting
        # exact bytes would either fail on a different machine or reintroduce
        # the per-PR diff noise #2054 removed. The KB figure is what prose
        # quotes, so that is what must agree.
        assert abs(gz.stat().st_size / 1024 - m["shipped"]["kb"]) < 1.0


def test_the_module_count_matches_the_source_tree():
    m = _manifest()
    src = ROOT / "python/djust/static/djust/src"
    actual = len([p for p in src.glob("[0-9]*.js")])
    assert actual == m["unminified"]["modules"], (
        f"manifest says {m['unminified']['modules']} modules, tree has {actual}"
    )


# --- the checker uses it, per line ----------------------------------------


def _run_checker(tmp_path: Path, readme: str, claude: str, manifest: dict) -> str:
    static = tmp_path / "python/djust/static/djust"
    # exist_ok: a table-driven test calls this repeatedly with one tmp_path.
    static.mkdir(parents=True, exist_ok=True)
    (static / "client-sizes.json").write_text(json.dumps(manifest))
    (static / "client.min.js.gz").write_bytes(b"x" * int(manifest["shipped"]["kb"] * 1024))
    (tmp_path / "README.md").write_text(readme)
    (tmp_path / "CLAUDE.md").write_text(claude)
    (tmp_path / "QUICKSTART.md").write_text("# q\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname="x"\nversion="1.0.0"\n')
    r = subprocess.run(
        [
            sys.executable,
            str(CHECKER),
            "--readme",
            str(tmp_path / "README.md"),
            "--quickstart",
            str(tmp_path / "QUICKSTART.md"),
            "--pyproject",
            str(tmp_path / "pyproject.toml"),
            "--bundle",
            str(static / "client.min.js.gz"),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return r.stdout + r.stderr


_M = {
    "shipped": {"artifact": "client.min.js.gz", "kb": 58.0},
    "unminified": {"artifact": "client.js", "gz_kb": 188.0, "modules": 55},
}


def test_a_claim_is_checked_against_the_artifact_its_line_names(tmp_path):
    # The heart of it. 58 is right for the shipped bundle and wildly wrong for
    # the unminified input; which one applies depends on the line's MARKER.
    out = _run_checker(
        tmp_path,
        readme="- ~58 KB gzipped client JavaScript\n",
        claude="- client.js is ~58 KB gz <!-- size-claim: unminified -->\n",
        manifest=_M,
    )
    assert "CLAUDE.md" in out, "the unminified claim of ~58 KB must be caught"
    assert "README.md:1" not in out, "the shipped claim of ~58 KB must pass"


def test_a_correct_unminified_claim_passes(tmp_path):
    out = _run_checker(
        tmp_path,
        readme="- ~58 KB gzipped client JavaScript\n",
        claude="- client.js is ~188 KB gz <!-- size-claim: unminified -->\n",
        manifest=_M,
    )
    assert "outside the tolerance band" not in out, out


def test_an_unminified_claim_without_its_marker_is_checked_as_shipped(tmp_path):
    # The contract stated as a consequence: no marker means the SHIPPED
    # bundle, whatever words happen to be on the line. The previous resolver
    # guessed from keywords, and the real docs passed by accident of word
    # order — this makes "you must say so" explicit rather than implied.
    out = _run_checker(
        tmp_path,
        readme="- ~58 KB gzipped client JavaScript\n",
        claude="- the unminified client.js is ~188 KB gz\n",
        manifest=_M,
    )
    assert "outside the tolerance band" in out, (
        "without a marker the 188 KB claim must be read as a SHIPPED claim and rejected"
    )


def test_a_historical_figure_can_be_marked_rather_than_forced_current(tmp_path):
    # Prose sometimes quotes a number precisely to say it was WRONG. Without
    # an escape hatch the only way to keep that sentence is to delete the
    # number, which loses the point of the sentence.
    out = _run_checker(
        tmp_path,
        readme="- ~58 KB gzipped client JavaScript\n",
        claude="- these read ~87 KB gz until #2138 <!-- size-claim: historical -->\n",
        manifest=_M,
    )
    assert "outside the tolerance band" not in out, out


def test_an_unmarked_stale_figure_is_still_caught(tmp_path):
    # The escape hatch must be opt-in per line, or it defeats the check.
    out = _run_checker(
        tmp_path,
        readme="- ~58 KB gzipped client JavaScript\n",
        claude="- these read ~87 KB gz until #2138\n",
        manifest=_M,
    )
    assert "outside the tolerance band" in out


def test_the_real_docs_pass_against_the_real_manifest():
    # The regression that started this: main was red because a claim had
    # drifted out of band and nothing caught the other two.
    r = subprocess.run([sys.executable, str(CHECKER)], capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stdout + r.stderr


# --- the manifest must never lie ------------------------------------------


def test_no_measurement_in_the_manifest_is_zero():
    # A zeroed manifest is worse than none, because the checker TRUSTS it: a
    # 0 KB "shipped" figure produces a band of [-3, +3] and rejects every
    # correct claim in README and CLAUDE.md. That is a strictly worse version
    # of the #2133 wall this manifest exists to remove — and the first version
    # of build-client.sh produced exactly that when run without terser (a
    # path the script explicitly supports) or without gzip.
    m = _manifest()
    zeros = []
    for section, body in m.items():
        if not isinstance(body, dict):
            continue
        for field, value in body.items():
            if isinstance(value, (int, float)) and value == 0:
                zeros.append(f"{section}.{field}")
    assert not zeros, f"zeroed measurements in client-sizes.json: {zeros}"


_GOOD_MANIFEST = {"shipped": {"kb": 58.7, "bytes": 60128}}


def _run_write_size_manifest(tmp_path: Path, *, gz: bool, modules: int):
    """Run the REAL ``write_size_manifest`` against a synthetic static dir.

    Returns ``(CompletedProcess, manifest_text_after)``. The manifest is read as
    TEXT, not JSON: with every guard gone the heredoc can emit invalid JSON, and
    a decode error in the harness would be reported as a harness bug rather than
    the finding it is.
    """
    import shutil

    static = tmp_path / "python/djust/static/djust"
    (static / "src").mkdir(parents=True)
    shutil.copy(ROOT / "python/djust/static/djust/client.js", static / "client.js")
    shutil.copy(ROOT / "python/djust/static/djust/client.min.js", static / "client.min.js")
    if gz:
        (static / "client.min.js.gz").write_bytes(b"x" * 60128)
    for i in range(modules):
        (static / "src" / f"{i:02d}-x.js").write_text("// x\n")
    (static / "client-sizes.json").write_text(json.dumps(_GOOD_MANIFEST))

    script = (ROOT / "scripts/build-client.sh").read_text()
    fn = script[
        script.index("write_size_manifest() {") : script.index(
            '\nif [ -f "$STATIC_DIR/client.min.js" ]; then'
        )
    ]
    runner = tmp_path / "run.sh"
    runner.write_text(f'STATIC_DIR="{static}"\nSRC_DIR="{static}/src"\n{fn}\nwrite_size_manifest\n')
    proc = subprocess.run(["bash", str(runner)], capture_output=True, text=True)
    return proc, (static / "client-sizes.json").read_text()


class TestEachBuildGuardIsIndependentlyLoadBearing:
    """Two guards stop a zeroed manifest; each needs its own failing test.

    ``write_size_manifest`` has a missing-``.gz`` SKIP and a zero-measurement
    REFUSAL, and both leave the manifest untouched — so a single test that only
    asserts "the manifest was preserved" stays green when *either one* is
    deleted, and goes red only when both are. Defense in depth is right; the
    coverage gap is not. Empirically, on the real script (#2148):

    ======================  =====================  ========================
    variant                 no ``.gz``             ``.gz`` + 0 modules
    ======================  =====================  ========================
    both guards             rc 0, SKIPPED          rc 1, refused
    skip removed            rc 1, refused          rc 1, refused
    refusal removed         rc 0, SKIPPED          rc 0, **manifest zeroed**
    ======================  =====================  ========================

    Each test below picks the column that moves for exactly one guard, and
    asserts the *distinguishing signal* — exit code and message — rather than
    the outcome both guards happen to share.
    """

    def test_a_missing_gz_skips_rather_than_falling_through_to_the_refusal(self, tmp_path):
        # Guard 1. Reachable by design: build-client.sh explicitly supports
        # running without terser, which leaves no .gz to measure. That is not
        # an error, so it must exit 0 and say so. Delete this guard and the
        # zero-refusal catches the same case at rc 1 with an ERROR — the
        # manifest survives either way, which is why this asserts the signal.
        proc, after = _run_write_size_manifest(tmp_path, gz=False, modules=1)
        assert proc.returncode == 0, (
            "a missing .gz is a supported build, not a failure; got "
            f"rc={proc.returncode}\n{proc.stderr}"
        )
        assert "SKIPPED (no client.min.js.gz" in proc.stderr, proc.stderr
        assert json.loads(after) == _GOOD_MANIFEST, "the existing manifest must be preserved"

    def test_a_zero_measurement_is_refused_even_when_the_gz_is_present(self, tmp_path):
        # Guard 2, on the only path the skip cannot reach: the .gz IS there, so
        # guard 1 passes, but an empty src tree makes `modules` 0. A zeroed
        # manifest is worse than none — the checker TRUSTS it and would reject
        # every correct claim in the repo, the #2133 wall this exists to remove.
        proc, after = _run_write_size_manifest(tmp_path, gz=True, modules=0)
        assert proc.returncode == 1, (
            f"a zero measurement must fail the build; got rc={proc.returncode}\n{proc.stdout}"
        )
        assert "refusing to write a size manifest with a zero measurement" in proc.stderr
        assert json.loads(after) == _GOOD_MANIFEST, (
            f"the manifest must be preserved, not overwritten with zeros; got {after}"
        )


# --- the checker resolves artifacts EXPLICITLY, never by guessing ---------


def test_the_artifact_is_resolved_by_marker_not_by_keywords():
    # The first resolver inferred the artifact from words on the line with
    # "last hint wins". "minified" is a substring of "unminified" and always
    # won on position, so the unminified hint could never fire at all; and
    # "we minify client.js down to ~58 KB" resolved to the UNMINIFIED artifact
    # because `client.js` came last. The real docs passed by accident of word
    # order. Prose is not a reliable place to infer intent from.
    import importlib.util

    spec = importlib.util.spec_from_file_location("c", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cases = [
        ("- unminified `client.js` is ~188 KB gz <!-- size-claim: unminified -->", "unminified"),
        ("- we minify client.js down to ~58 KB gz", "shipped"),
        ("- the 55 modules in `static/djust/src/client.js` compile to ~58 KB gz", "shipped"),
        ("- ~58 KB gzipped client JavaScript", "shipped"),
    ]
    for line, expected in cases:
        assert mod._artifact_for_line(line, "CLAUDE.md")[0] == expected, line


# Files that carry a checker-VISIBLE `~N KB` figure on a client-ish line and
# are deliberately NOT in _SIZE_CLAIM_FILES. Each needs a reason, because the
# whole failure mode this suite exists for is a claim nobody is looking at.
#
# Three legitimate kinds live here, and none of them is "we forgot":
#   1. append-only records — correcting them would falsify history (#2028);
#   2. figures that are not djust's shipped total — a competitor's bundle, a
#      per-feature delta, a brotli artifact the manifest does not name. The
#      checker compares every claim on a matching line against ONE measured
#      number, so guarding these would reject correct prose;
#   3. the checker and its own tests, whose fixture strings are stale on purpose.
_KNOWN_UNGUARDED_SIZE_CLAIMS = {
    "CHANGELOG.md": "append-only record; entries describe what shipped at the time",
    "RETRO.md": "append-only record; several figures are quoted precisely as wrong",
    "docs/adr/010-resumable-uploads.md": "decision record — the budget as it stood at decision time",
    "docs/adr/025-js-extension-sockets.md": "third-party (Stimulus) and per-adapter delta figures",
    "docs/archive/PR_SUMMARY.md": "archived PR write-up; a delta, not a total",
    "docs/example-site/EXAMPLE_SITE_PHASE5_PLAN.md": "competitor column (Phoenix/Livewire)",
    "docs/internal/IMPROVEMENT_BRAINSTORM-2026-07.md": (
        "brotli figure + a quoted stale target, neither named by the manifest"
    ),
    "docs/state-management/STATE_MANAGEMENT_API.md": (
        "2025-01 design doc — a stale 'current' baseline beside per-feature "
        "delta estimates; needs the same dated treatment as "
        "IMPLEMENTATION_PHASE2.md, tracked as #2148 follow-up"
    ),
    "docs/state-management/STATE_MANAGEMENT_COMPARISON.md": (
        "competitor columns (Phoenix/Livewire); djust's own cell has no `~`"
    ),
    "docs/website/guides/large-lists.md": "per-module delta, not the bundle total",
    "scripts/check-doc-snippets.py": "the checker's own explanatory comments",
    "tests/test_check_doc_snippets.py": "fixture strings, deliberately out of band",
    "tests/test_client_size_manifest_2138.py": "this file's own fixture strings",
}


def test_every_file_with_a_client_size_claim_is_guarded_or_listed():
    """No client-size claim may sit in a file nobody looks at (#2148).

    This is the shape of the bug rather than an instance of it. `~5KB` was
    known stale since PR #796 and was still being copied forward four releases
    later, because "correct the number" was a diligence act with nothing
    checking that the file it lived in would be looked at again. #2147
    corrected ten claims and guarded nine files; nineteen more survived in
    files nobody had enumerated.

    So the property is mechanical: a tracked file either has its claims checked
    against the measured manifest, or appears above with a reason. A new file
    can still carry a stale figure — but not silently.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("c", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    tracked = subprocess.run(
        ["git", "ls-files", "*.md", "*.txt", "*.html", "*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert tracked.returncode == 0, tracked.stderr
    guarded = set(mod._SIZE_CLAIM_FILES)

    unguarded: dict[str, list[str]] = {}
    for rel in tracked.stdout.split():
        if rel in guarded or rel in _KNOWN_UNGUARDED_SIZE_CLAIMS:
            continue
        try:
            lines = (ROOT / rel).read_text().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(lines, 1):
            if not mod._SIZE_CLIENT_CONTEXT_RE.search(line):
                continue
            if mod._size_marker(line) == "historical":
                continue
            for m in mod._SIZE_CLAIM_RE.finditer(line):
                unguarded.setdefault(rel, []).append(f"L{lineno}: ~{m.group(1)} KB")

    assert not unguarded, (
        "these files state a client-size figure that nothing checks:\n"
        + "\n".join(f"  {rel}: {', '.join(v)}" for rel, v in sorted(unguarded.items()))
        + "\n\nEither add the file to _SIZE_CLAIM_FILES in "
        "scripts/check-doc-snippets.py (run `make sizes` for the current "
        "figure), mark the individual line `<!-- size-claim: historical -->` "
        "if it is quoted to say it WAS wrong, or add the file to "
        "_KNOWN_UNGUARDED_SIZE_CLAIMS above WITH A REASON."
    )


def test_the_known_unguarded_list_does_not_outlive_its_entries():
    """An allowlist that names files with nothing to allow is stale.

    Without this, a cleaned-up file leaves its exemption behind and the next
    stale claim added to it is exempt for free.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("c", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    dead = []
    for rel in _KNOWN_UNGUARDED_SIZE_CLAIMS:
        p = ROOT / rel
        if not p.is_file():
            dead.append(f"{rel} (file is gone)")
            continue
        has_claim = any(
            mod._SIZE_CLIENT_CONTEXT_RE.search(line)
            and mod._size_marker(line) != "historical"
            and mod._SIZE_CLAIM_RE.search(line)
            for line in p.read_text().splitlines()
        )
        if not has_claim:
            dead.append(f"{rel} (no client-size claim left)")
    assert not dead, "stale entries in _KNOWN_UNGUARDED_SIZE_CLAIMS: " + "; ".join(dead)


class TestOneMarkerSpellingRule:
    """All three markers match by ONE rule (#2148).

    The artifact markers used to be dict keys compared byte-for-byte, so five
    of six plausible spellings fell back to the default, while the historical
    marker was a bare substring and *was* tolerant — two marker families with
    two matching rules, for no reason anything about their meaning supports.

    Nothing here was a correctness hole: an unrecognised artifact marker fell
    back to ``shipped``, so a wrong-artifact claim failed LOUDLY rather than
    passing. This pins the ergonomics fix.
    """

    # Every spelling a writer plausibly produces.
    SPELLINGS = [
        "<!-- size-claim: {v} -->",  # canonical
        "<!--size-claim: {v}-->",  # no outer spaces
        "<!--size-claim:{v}-->",  # no spaces at all
        "<!--  size-claim:  {v}  -->",  # extra spaces
        "<!-- SIZE-CLAIM: {V} -->",  # shouting
        "<!-- Size-Claim: {Vv} -->",  # title case
    ]

    @staticmethod
    def _checker():
        import importlib.util

        spec = importlib.util.spec_from_file_location("c", CHECKER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _render(self, value: str) -> list[str]:
        return [s.format(v=value, V=value.upper(), Vv=value.capitalize()) for s in self.SPELLINGS]

    def test_every_spelling_of_the_unminified_marker_resolves_the_same(self):
        mod = self._checker()
        for marker in self._render("unminified"):
            line = f"- client.js is ~188 KB gz {marker}"
            assert mod._artifact_for_line(line, "CLAUDE.md") == ("unminified", "gz_kb"), marker

    def test_every_spelling_of_the_shipped_marker_resolves_the_same(self):
        mod = self._checker()
        for marker in self._render("shipped"):
            line = f"- the client is ~58 KB gz {marker}"
            assert mod._artifact_for_line(line, "CLAUDE.md") == ("shipped", "kb"), marker

    def test_every_spelling_of_the_historical_marker_suppresses(self, tmp_path):
        # The historical marker is checked on a different code path from the
        # artifact markers, so resolving it is not enough — it has to actually
        # suppress a stale figure end to end, through the real checker.
        for marker in self._render("historical"):
            out = _run_checker(
                tmp_path,
                readme="- ~58 KB gzipped client JavaScript\n",
                claude=f"- these read ~87 KB gz until #2138 {marker}\n",
                manifest=_M,
            )
            assert "outside the tolerance band" not in out, f"{marker}\n{out}"

    def test_a_misspelled_marker_value_is_reported_not_silently_ignored(self, tmp_path):
        # The tolerant rule is a SPELLING tolerance, not a wildcard.
        #
        # Asserting only "it does not suppress" would be tautological: both
        # lookups are membership tests, so an unrecognised value behaves the
        # same whether the regex bounds its alternation or not — the gate-off
        # proved a first version of this test could not go red. The bound is
        # made load-bearing by REPORTING the value, which is the assertion
        # below; the non-suppression is asserted alongside it.
        out = _run_checker(
            tmp_path,
            readme="- ~58 KB gzipped client JavaScript\n",
            claude="- these read ~87 KB gz until #2138 <!-- size-claim: histrical -->\n",
            manifest=_M,
        )
        assert "unknown size-claim marker `histrical`" in out, out
        assert "outside the tolerance band" in out, out

    def test_a_marker_the_check_understands_is_not_reported_as_unknown(self, tmp_path):
        # The other direction: the report must not fire on the real markers,
        # or every correctly-marked line in the repo becomes an error.
        out = _run_checker(
            tmp_path,
            readme="- ~58 KB gzipped client JavaScript\n",
            claude=(
                "- client.js is ~188 KB gz <!-- size-claim: unminified -->\n"
                "- the client is ~58 KB gz <!-- size-claim: shipped -->\n"
                "- these read ~87 KB gz until #2138 <!-- size-claim: historical -->\n"
            ),
            manifest=_M,
        )
        assert "unknown size-claim marker" not in out, out
        assert "outside the tolerance band" not in out, out

    def test_a_marker_missing_its_comment_delimiters_does_not_suppress(self, tmp_path):
        # The historical marker used to be a BARE substring, so this line
        # suppressed. It is prose, not a marker; requiring `<!-- ... -->` is
        # what makes one rule cover all three markers.
        out = _run_checker(
            tmp_path,
            readme="- ~58 KB gzipped client JavaScript\n",
            claude="- we treat size-claim: historical figures loosely: ~87 KB gz\n",
            manifest=_M,
        )
        assert "outside the tolerance band" in out, out


def test_a_client_claim_without_the_word_gz_is_still_checked(tmp_path):
    # The gate used to be `"gz" in line`, which missed `~5KB client runtime`
    # in README and five more copies across docs — every one wrong by ~11x,
    # i.e. the most-wrong claims in the repo were the ones the check could not
    # see.
    out = _run_checker(
        tmp_path,
        readme="| djust auto-injects the ~5KB client runtime into every response |\n",
        claude="# c\n",
        manifest=_M,
    )
    assert "outside the tolerance band" in out, out


def test_a_non_client_size_claim_is_not_checked(tmp_path):
    # The widened gate must not false-positive on unrelated sizes — memory,
    # page weight, test fixtures.
    out = _run_checker(
        tmp_path,
        readme="- the panel uses ~10 KB memory per 50 events\n- a ~16 KB Tailwind fixture\n",
        claude="# c\n",
        manifest=_M,
    )
    assert "outside the tolerance band" not in out, out
