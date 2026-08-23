"""The full suite must be exercised in BOTH orderings, somewhere (#2187).

`pytest-randomly` is not installed, so a serial run executes tests in
deterministic definition order, while `-n auto` executes them in whatever
sharding xdist picks. Those are different orderings, and an order-dependent
bug — a leaked module-global, a singleton mutated by an earlier test — can
hide under one while surfacing under the other. #2187 is an open instance:
a test that failed on one `-n auto` sharding, passed in isolation, and passed
on a plain re-run.

The pre-push hook used to supply the definition-order run, but only by
inheritance — its documented reason for being serial (enforcing benchmark
latency thresholds) was deliberately removed in #2156, and it cost 330s
against 84s parallel on a suite of 10,260 tests. It was switched to `-n auto`.

That is only safe because `main-health` still runs the suite serially every
day, so definition order keeps being exercised — just off the push path, which
is the right place for it: an ordering flake is a property of `main` rather
than of the branch being pushed.

The comment in each file says so, but a comment is not a guard — this repo
treats a pin that cannot go red as decorative (#1859). This test is the
mechanical form: it fails if BOTH runners end up parallel, which would close
the definition-order gap everywhere at once and do it invisibly, because the
suite would simply keep passing.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRE_PUSH = ROOT / "scripts/pre-push-pytest.sh"
MAIN_HEALTH = ROOT / ".github/workflows/main-health.yml"

# The line that actually runs the whole suite, in each runner. Matching the
# invocation rather than the file keeps an `-n auto` in some unrelated step
# (or in a comment) from being read as the suite's ordering.
#
# Two spellings, because the runners genuinely differ: main-health lists the
# three roots literally, while pre-push holds them in a `PATHS` array and
# expands `"${PATHS[@]}"`. The first version of this guard only matched the
# literal form — and rather than passing vacuously on the other, it hard-failed
# with "no full-suite invocation found", which is how this was caught.
SUITE_INVOCATION = re.compile(
    r"^[^#\n]*pytest\s+"
    r"(?:tests/\s+python/tests/\s+python/djust/tests/|\"\$\{PATHS\[@\]\}\")"
    r"[^\n]*",
    re.M,
)


def _suite_command(path: Path) -> str:
    """The full-suite pytest invocation in `path`, comments excluded."""
    m = SUITE_INVOCATION.search(path.read_text())
    assert m, (
        f"no full-suite pytest invocation found in {path.name} — the runner was "
        f"restructured, so this guard is no longer looking at the right line "
        f"and must be updated rather than left silently passing."
    )
    return m.group(0)


def _is_parallel(cmd: str) -> bool:
    """Does this invocation run sharded?

    Accepts a literal `-n auto` and pre-push's indirection, where the flag is
    set conditionally into a `PARALLEL` array (xdist has to be PROBED, not
    assumed — a pytest without it aborts on `-n` rather than degrading).
    """
    return bool(re.search(r"-n\s+(auto|\d+)", cmd)) or "PARALLEL[@]" in cmd


def test_pre_push_runs_the_suite_in_parallel() -> None:
    """330s -> 84s. Serial here bought nothing after #2156 removed its reason."""
    assert _is_parallel(_suite_command(PRE_PUSH)), (
        "the pre-push suite run lost its `-n auto`. If that was deliberate, the "
        "wall-clock goes back to ~330s on every push; see the comment above the "
        "invocation for what serial did and did not buy."
    )


def test_pre_push_probes_for_xdist_rather_than_assuming_it() -> None:
    """`-n auto` without xdist ABORTS the run; it does not degrade.

    pytest exits with `unrecognized arguments: -n`, so the suite never runs and
    the pusher gets an argparse usage dump from the one script whose job is to
    make a blocked push legible. Guarding the flag behind an import probe is
    what makes a partial install slow instead of broken.
    """
    src = PRE_PUSH.read_text()
    assert re.search(r"import xdist", src), (
        "pre-push no longer probes for xdist before passing `-n auto`. On an "
        "interpreter without it, pytest aborts on the unknown flag and the "
        "suite does not run at all."
    )
    # The probe is only worth having if the flag is actually gated on it.
    probe_at = src.index("import xdist")
    invocation_at = src.index(_suite_command(PRE_PUSH))
    assert probe_at < invocation_at, (
        "the xdist probe runs AFTER the suite invocation, so it cannot gate it"
    )


def test_main_health_runs_the_suite_in_definition_order() -> None:
    """The daily serial run is the ONLY remaining definition-order coverage."""
    assert not _is_parallel(_suite_command(MAIN_HEALTH)), (
        "main-health gained `-n auto`, so NO runner executes the suite in "
        "definition order any more. pytest-randomly is not installed, so that "
        "ordering is exercised nowhere else, and order-dependent bugs (#2187) "
        "become undetectable. Either revert this, or move the definition-order "
        "run somewhere else off the push path and update this test to point at it."
    )


def test_both_orderings_are_covered_between_the_two_runners() -> None:
    """The property that actually matters, stated once.

    The two tests above pin each runner individually; this one states the
    invariant they exist to protect, so a future change that swaps their roles
    (parallel pre-push -> serial, serial main-health -> parallel) still leaves
    a test asserting the thing we care about rather than the arrangement we
    happen to have.
    """
    orderings = {
        _is_parallel(_suite_command(PRE_PUSH)),
        _is_parallel(_suite_command(MAIN_HEALTH)),
    }
    assert orderings == {True, False}, (
        "both full-suite runners now use the SAME ordering "
        f"({'parallel' if True in orderings else 'serial'}), so one of the two "
        "orderings is no longer exercised anywhere. Order-dependent bugs only "
        "surface under some orderings — see #2187."
    )
