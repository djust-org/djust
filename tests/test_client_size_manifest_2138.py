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
    static.mkdir(parents=True)
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


def test_the_build_refuses_to_write_a_zeroed_manifest(tmp_path):
    # The guard itself, exercised against the real script: with no
    # client.min.js.gz present the manifest must be left ALONE, not zeroed.
    import shutil

    static = tmp_path / "python/djust/static/djust"
    (static / "src").mkdir(parents=True)
    shutil.copy(ROOT / "python/djust/static/djust/client.js", static / "client.js")
    shutil.copy(ROOT / "python/djust/static/djust/client.min.js", static / "client.min.js")
    (static / "src" / "01-x.js").write_text("// x\n")
    good = {"shipped": {"kb": 58.7, "bytes": 60128}}
    (static / "client-sizes.json").write_text(json.dumps(good))
    # deliberately NO client.min.js.gz

    script = (ROOT / "scripts/build-client.sh").read_text()
    fn = script[
        script.index("write_size_manifest() {") : script.index(
            '\nif [ -f "$STATIC_DIR/client.min.js" ]; then'
        )
    ]
    runner = tmp_path / "run.sh"
    runner.write_text(f'STATIC_DIR="{static}"\nSRC_DIR="{static}/src"\n{fn}\nwrite_size_manifest\n')
    subprocess.run(["bash", str(runner)], capture_output=True, text=True)

    after = json.loads((static / "client-sizes.json").read_text())
    assert after == good, (
        "the manifest must be preserved when a measurement is unavailable, "
        f"not overwritten with zeros; got {after}"
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
