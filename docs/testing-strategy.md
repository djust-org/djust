# Test selection and checkpoints

Use focused checks during edits and full checks at integration/release checkpoints.
A selected pass is evidence for that selection, not evidence that the full suite
passes. This policy applies to all agents and supersedes older instructions to
run `make test` before every incremental push. A pipeline's full-suite integration
stage and the release checklist still apply.

## Commands by stage

Build the Rust extension for the checkout whenever Rust changes; shadowing Python
source from a worktree does not rebuild Rust. Use the existing worktree environment
or interpreter resolver. For direct pytest invocations in a linked worktree:

```bash
export PYTHONPATH="$(bash scripts/run-with-venv-python.sh --worktree-pythonpath):."
```

1. **Edit/reproducer:** run the changed test file/node and its nearby contract tests.
   This example exercises the selector itself:

   ```bash
   bash scripts/run-with-venv-python.sh -m pytest tests/unit/test_select_tests.py -q
   ```

   A small runtime smoke starting point (extend for the change):

   ```bash
   bash scripts/run-with-venv-python.sh -m pytest \
     python/djust/tests/test_http_ws_djid_parity_1642.py \
     python/djust/tests/test_security_mount_validation.py -q
   ```

2. **Related subsystem:** include both implementations/transports and their shared
   contracts. Use explicit files across all three roots, not an assumed complete
   marker taxonomy. The table below gives entry points, not exhaustive gates.

   For example, a theming change can start with catalogue, contrast and escaping
   checks, then add the changed component's contracts and JS/browser checks:

   ```bash
   bash scripts/run-with-venv-python.sh -m pytest \
     python/djust/tests/test_theming_catalogue.py \
     python/djust/tests/test_theming_contrast_all_presets_2060.py \
     python/djust/tests/test_ui_simple_components_escaping.py -q
   ```

3. **Pre-push:** let the installed hooks run. To exercise the same Python selector
   before pushing, use:

   ```bash
   make test-selected FROM=origin/main TO=HEAD
   ```

   `FROM`/`TO` are optional; defaults are the remote default branch and HEAD.
   The hook uses `PRE_COMMIT_FROM_REF`/`PRE_COMMIT_TO_REF` for the pushed range.
   The range is committed changes (`from...to`); uncommitted edits need explicit
   file/node checks. Rust selection includes reverse crate dependencies; existing
   JS, audit and other hooks remain in place. Unknown/shared changes run full
   Python coverage. The wrapper excludes nightly cases and disables benchmark
   measurement; benchmark test bodies still execute.

4. **Integration:** freeze the completed commit and run `make test-integration`
   (an alias for unchanged `make test`: all Python roots, JS and Rust). Required
   lint/security/browser checks and the full PR CI suite must pass on that commit
   before marking it ready. CI's normal Python shards exclude nightly; the local
   full target includes them. Do not skip hooks or weaken CI to make selected
   checks appear authoritative.
   In a linked worktree, prepare its own `.venv` and matching extension first
   (`make worktree-env`). The direct-pytest source-shadow command above does not
   change the full target's environment. Avoid overriding `PYTHON` on the make
   command line during integration: make propagates command-line overrides into
   subprocess scaffold fixtures; use the checkout's own resolved environment.

5. **Release:** use the existing release checklist and full CI on the exact frozen
   commit, including the actual supported Python runtimes, nightly/serial-order
   coverage, serial benchmark thresholds, security and applicable packaging/
   release checks. `make test-nightly` runs the exhaustive marked tests. Preserve
   the daily serial main-health job: parallel ordering cannot replace it. Python
   version labels alone are insufficient; runtime verification must match the
   matrix. The interpreter repair is tracked separately in PR #3357. Python 3.15
   is explicitly optional; supported-version failures remain blocking.

Do not rerun a complete suite after every command. Reuse evidence only for the
same tested commit/tree, dependency/configuration state, interpreter, extension
build and test selection. A changed condition invalidates the affected evidence;
final integration/release evidence must cover the final commit.

## Overlapping subsystem entry points

These groups describe contracts, not ownership boundaries or a new selector.
Test roots are historical locations, not clean unit/integration partitions.

| Group | Entry points and cross-contract checks |
| --- | --- |
| Templates, filters, Unicode and bridge | Relevant files in `python/tests/`, renderer/source pins in `tests/`, bridge consumers in `python/djust/tests/`; compare Django and Rust behavior. Engine changes require full coverage. |
| Runtime, events, state and transports | HTTP/WebSocket parity, mount/dispatch, hydration, persistence, async/cancellation and routing tests in all roots. Shared runtime changes require full coverage. |
| Security, auth and tenants | Mount/import/redirect validation, event reauthentication, exposure and tenant isolation; include both HTTP and WebSocket paths plus the dedicated security CI jobs. |
| Components, theming and accessibility | Component behavior/escaping plus theme, contrast and accessibility tests; include related JS/browser checks. `theming` exists but is not a universal component coverage marker. |
| Checks, scaffolding, CLI and packaging | Relevant check/command tests, generated-project behavior, documentation source pins and packaging checks; shared harness/dependency changes require full coverage. |

Performance, Python compatibility and state isolation overlap every group.
`integration`, `e2e` and `components` markers do not identify all relevant tests.
Name globs and `-k` are exploration aids; they cannot prove subsystem completeness.

## Conservative selector contract

`scripts/select-tests.py` remains the single Python selection mechanism used by
`scripts/pre-push-pytest.sh` and `make test-selected`:

- Each changed path must map to an existing test (changed test, module name/import,
  or a source-pin basename mention). Several changes may map to the same test;
  every path is still accounted for independently.
- An unclassified path, including a removed test without a remaining mapping,
  requires FULL, even if another path selected tests. An empty range/selection
  also requires FULL.
- Unreadable test source requires FULL: another matching filename cannot establish
  that unreadable tests have no relevant consumers.
- Shared harness/dependency/workflow files, package initializers, test support,
  state backends, LiveView/WebSocket runtime and the Rust engine require FULL.
  Existing routing/flip/convergence branch triggers remain.
- The CLI reads all production `python/djust` Python modules. If a production
  module statically imports a changed module, require FULL for transitive impact.
  Absolute/relative imports, imports inside functions, and type-checking imports
  are included conservatively. An unreadable/unparseable graph requires FULL.
- Manual-only `tests/playwright` and `tests/js` files never become explicit pytest
  targets; their dedicated runners remain necessary. A FULL Python fallback does
  not substitute for them.

Static imports and source mentions are heuristics, not an exhaustive dependency
proof (dynamic imports, templates and generated code can cross boundaries). If
review finds shared/transitive impact the selector cannot see, force full checks:

```bash
DJUST_PREPUSH_FULL=1 bash scripts/pre-push-pytest.sh
```

## CI change scope and the merge queue

`.github/workflows/test.yml` listens to `merge_group` (so GitHub's merge queue can
run it; the queue itself is a repository setting) and starts with a `changes` job
that runs `scripts/ci-change-scope.py` on `pull_request` and `merge_group` events.
Push, dispatch and the nightly run always run everything. The script classifies the
changed files and the expensive jobs read two outputs:

| class | paths |
|---|---|
| DOCS | root-level `*.md` (`CHANGELOG.md`, `RETRO.md`, ...), `docs/**`, `changelog.d/**` |
| TEST_PY | `*.py` under `tests/`, `python/tests/`, `python/djust/tests/` |

| change set | `docs_only` | `rust_skip` | skipped |
|---|---|---|---|
| only DOCS | true | true | every job |
| only DOCS and TEST_PY | false | true | `rust-tests` only |
| anything else | false | false | nothing |

"Anything else" includes workflow files, `scripts/`, `pyproject.toml`, `uv.lock`,
`Cargo.*`, `crates/`, `python/djust/` source, `.test_durations`, a nested `*.md`
(`python/djust/**/README.md`), and any non-`.py` file in a test tree: the one Rust
read into the Python test tree is `python/tests/fixtures/root_selection_3031.json`.
A test-only change still runs the whole Python matrix, JavaScript, the benchmarks and
the browser jobs. The script is fail-open: an empty change set, a failed diff, an
unexpected path or a missing sha all mean a full run.

**Merge-queue diff.** What `merge_group.base_sha` means for a grouped queue is not
verified (it might be the group's own parent, so `base_sha..head_sha` would cover only
the last PR and a [code PR, docs PR] group could read as docs-only). For `merge_group`
the script therefore classifies the union of two diffs, both ending at `head_sha`:
from `base_sha`, and from `merge-base(head_sha, origin/<base_ref>)` (`base_ref` is in
the payload). The union can only add files, so it can only make a run bigger; a
missing `base_ref` or an unfetched branch runs everything. The `changes` step logs both
file lists, and notes when they differ, so the first real queue run shows what
`base_sha` was.

**How a skip is accepted and a failure still blocks.** `test-summary` takes one of two
paths, chosen by `changes`. If `changes` succeeded and said `docs_only`, a dedicated
step requires every gated job to report exactly `skipped`. Otherwise the existing
AND-chain runs and requires `success` from each job; the only skip it tolerates is
`rust-tests`, and only with `changes` succeeded and `rust_skip == true`. If `changes`
itself fails, its dependants are skipped, and a skip that `changes` did not vouch for
fails the gate. `tests/test_ci_change_scope.py` executes both bash blocks over these
cases.

Two things differ from a pull request. `paths-ignore` is not evaluated for
`merge_group`, so a docs-only queue entry starts the workflow and the `changes` job does
the skipping (a docs-only *pull request* is still handled by `paths-ignore` and
`test-summary-docs-shim.yml`). The queue has no PR labels, so it runs 3.12 only and the
fast tier, like an unlabelled PR; the push to main after the merge runs the slow tier on
3.12 (the other interpreters run nightly; see "Tiers" below). `external-pr-gate.yml` also listens to `merge_group` and
reports success there, because the PR passed it to be queued.

**Hang dumps.** The Python shard step passes `-o faulthandler_timeout=1800`. If one test
(setup, call and teardown together) runs longer than 30 minutes, pytest writes every
thread's stack to the job log, naming the test, and carries on; nothing fails. The value
is above three times the slowest test recorded in `.test_durations`, and
`tests/test_ci_hang_dump_3800.py` fails when a recorded test outgrows it. The
`python-tests` job also has `timeout-minutes: 45` (about 3x the slowest shard in the
last five green main runs, 15.4 minutes), so a hung shard ends in bounded time instead
of GitHub's 360-minute default; it must stay above the dump threshold so the dump can
fire first. Neither is a diagnosis on its own: the dump needs a hang longer than 20
minutes, the timeout only bounds it.

### Tiers: what runs where (#4074)

| event | interpreters x Django x shards | tier (`-m`) | exhaustive sweeps |
|---|---|---|---|
| pull request | 3.12 x Django 5.2 x 4, + the Django-6-only job, + 3.14t smoke | `not nightly and not slow` | sampled |
| merge queue | same as a pull request | `not nightly and not slow` | sampled |
| push to `main` | same as a pull request | `not nightly` (slow tier included) | sampled |
| nightly `main-health` (07:00 UTC) | serial suite on 3.12, plus `test.yml` called with the full matrix (3.12-3.15 x 5.2/6.0/6.1 x 4) | serial: everything; matrix: `not nightly` | serial: **every unit**; matrix: sampled |
| manual `test.yml` dispatch | full matrix | `not nightly` | every unit (untick `full_sweeps` to sample) |
| PR labelled `full-tests` | 3.12 x Django 5.2 x 4 + the Django-6-only job | `not nightly` | every unit |

**One Django series before merge.** The shards run on Django 5.2 (the LTS, and what the dev
environment uses). About two hundred tests exist only on Django 6 (template partials, the 6.1 CSP
nonce tag, the 6.1 tag-registration checks) and skip on 5.2, so `python-tests (Django 6.1, skipped
on 5.2)` runs them: each 5.2 shard writes the node id of every test it skipped
(`tests/skip_ledger.py`: a skip marker, a module that skips itself at import, a fixture or a test
body that skips, all alike; not an xfail, not a sweep case the sample left out), and that job runs
exactly those on Django 6.1, after the shards. A new Django-6-only file needs no registration. It
refuses to run with fewer than four shard lists or an empty one, and `test-summary` requires it.
A test skipped there too was skipped on every cell; `-rs` lists it with its reason.
`tests/test_skip_ledger_4074.py` pins the ledger and the wiring. The trade: a break that shows only
on Django 6.0, and a 6.x-only break in a test that also runs (and passes) on 5.2, are found by the
next nightly instead of before merge.

**The slow tier** is derived: `tests/slow_tier.py` marks every test recorded over 1 s in
`.test_durations` as `slow`. `@pytest.mark.pr_tier` keeps a slow test in the PR tier (a
structural pin: a ratchet, an inventory, a doc table, a census); `@pytest.mark.nightly` keeps it
out of every `test.yml` run (only the serial `main-health` suite runs it).

**The exhaustive sweeps** are `@pytest.mark.sweep("<population>")`: the marked-safe attribute
sweep (`python/djust/tests/test_marked_safe_attribute_sweep_3898.py`: component classes, data
tags, item parameters), the safe-markup payload sweep (`python/tests/test_safe_markup_audit_3774.py`:
every tag, function, Rust handler and named driver) and the guard's differential fuzz
(`python/tests/test_safe_markup_guard_3820.py`: extra seeds and analysis mutants). In full they were
about 30% of all recorded test time. `tests/sweep_sample.py` runs them two ways:

- **Full**, every unit: a bare `pytest` (no `-m`, as `main-health` runs it), `DJUST_SWEEP_SAMPLE=0`,
  or `make test-sweeps-full`.
- **Sampled**: whenever the run deselects `slow`, or with `DJUST_SWEEP_SAMPLE=1` (CI sets it on the
  push to `main`). A sampled sweep always drives (1) the units whose source files the change touches
  (CI lists them in `$RUNNER_TEMP/changed-paths.txt` from the PR's diff; locally it is the diff
  against `origin/main` plus the working tree), (2) every unit, if a file the whole sweep runs
  through changed (the sweep itself, its driver, `components/utils.py`, the taint analysis: each
  file's `SHARED_PATHS`), (3) the regression-pinned units (the payload sweep's named drivers), and
  (4) a seeded draw of a fixed number of the rest (`*_BUDGET` in each file, about a minute each).

The census tests in those files (every class, pair and tag reached; the exact known-leak sets; the
site audit; the doc table; the baseline ratchet) are `pr_tier` and run in full on every pull
request. The two tests that ask whether EVERY audited sink ran need the whole payload sweep and are
`nightly`. `python/tests/test_sweep_tiers_4074.py` pins all of it: every slow test in a sweep file
has exactly one tier, every population has a size floor (a refactor cannot quietly sweep less), and
a sampled selection is never empty.

**Reproducing a sampled failure.** The seed is `DJUST_SWEEP_SEED`; CI sets
`<commit sha>-py<version>-dj<django>`, so each cell draws a different sample and successive commits
rotate through the population. The report header prints it, the summary lists every population's
`chosen of total (forced)`, and a failing sweep test prints the exact command, for example:

    DJUST_SWEEP_SAMPLE=1 DJUST_SWEEP_SEED=<seed> DJUST_SWEEP_CHANGED=<paths> \
      .venv/bin/python -m pytest '<node id>'

`make test-sweeps SEED=<seed> CHANGED=<paths>` does the same for all three files.

**The cost of sampling.** A defect at one unit that no change touches is caught before merge only
if the draw lands on it. Per pull-request run (three Django cells, three draws): about 54% for a
payload-sweep unit, 51% for a data tag, 36% for an item parameter, 55% for an analysis mutant, 62%
for a URL component class and 8% for one of the 165 component classes that are neither URL nor template classes. The nightly
`main-health` suite catches it with certainty, so the worst case is a security regression found the
next morning instead of before merge. A change to the unit's own file is always swept.

**Starting the full tier on any branch** (a train checkpoint, a CI change, a branch with no PR):

    gh workflow run test.yml --repo djust-org/djust --ref <branch>
    gh workflow run main-health.yml --repo djust-org/djust --ref <branch>

The first is the full `test.yml` run: every interpreter (3.12-3.15) x Django 5.2/6.0/6.1 x 4 shards,
the slow tier, and every unit of the sweeps (`-f full_sweeps=false` samples them instead). It costs
about 800 runner-minutes; on a free-plan organisation the price is the 20-job concurrency it holds
for the better part of an hour, not money. The second is what the nightly runs: the serial full
suite on that branch (every unit of the sweeps, definition order) plus the called matrix; on a
branch it reports in the run instead of filing an issue. Follow either with `gh run watch <id>`.
A dispatch never cancels a pull-request run of the same branch (their refs, and so their
concurrency groups, differ); a second dispatch on the same branch cancels the first.

**Nightly failures** are reported by `main-health.yml`: its `report` job opens (or comments on) the
`main is RED` issue when the serial suite or the called cross-version matrix fails, and closes it
when both are green. A manual run of `main-health` on a branch tests that branch and fails the run
instead of filing an issue.

### The early tier: tests that failed late run early

A test that runs only late (the push to `main`, the nightly) fails after the change that broke it
has merged. The early tier keeps such tests before merge. It is honoured where `pr_tier` is
(`tests/slow_tier.py` does not mark an early test `slow`), so PR and merge-queue CI run it inside
their fast tier, and `make test-early` runs exactly it locally. All numbers live in
`tests/early_tier.py`.

- **Promotion.** The nightly `main-health` suite writes a junit report in which every test carries
  its node id and its pre-merge tier. When it is red, `scripts/promotion_ledger.py promote` adds
  each failing test that is NOT already in the pre-merge tier to `tests/promotion-ledger.json`.
  Not promoted: a test that is already early (a fast test failing on `main` is a merge
  interaction, not a tier problem), a `nightly`-marked test (nightly-only by declaration), and a
  test a path rule already selects (the report says so: if the breaking change did not match the
  rule, extend the rule). A failure from a push to `main` can be promoted by hand:
  `python scripts/promotion_ledger.py promote --failed-ids ids.txt` (one node id per line; without
  a junit report the tier comes from `.test_durations`).
- **The workflow never pushes.** `main-health` has `contents: read` and `issues: write` and gains no
  permission: the ledger change is a patch, posted on the `main is RED` issue when a test is
  promoted, and on a `promotion ledger: changes to apply` issue when a green night demotes one.
  Apply it with `git apply` in a pull request (the one fixing the red, typically).
- **Demotion.** On a green night `promotion_ledger.py demote` credits every entry whose test passed
  with one green night; an entry that fails again restarts at zero. After `DEMOTE_AFTER_GREEN` (30)
  consecutive green nights an unpinned entry leaves. The streaks between nights are kept in the
  actions cache (no token permission); a lost cache restarts them, so an entry stays early longer,
  never shorter.
- **Pinned.** `"pinned": true` (set by hand) keeps an entry for good: never demoted, never evicted.
- **Budget.** The ledger may cost at most `BUDGET_SECONDS` (180 s) of recorded duration. Over it,
  unpinned entries leave in order of oldest last failure; pinned entries alone over it make the
  script exit 2.
- **Granularity.** An entry is a test function (`path::Class::test_name`, parameters stripped):
  parametrized ids are derived from values and churn, and a whole file is too coarse for the
  budget. `tests/test_promotion_ledger.py` fails, naming the entry, when an entry's test is renamed
  or deleted.

**Path rules** (`PATH_RULES` in `tests/early_tier.py`) are early by change shape rather than by
failure. In a run that deselects `slow`, a rule whose change shape matches the run's changed paths
(CI writes `$RUNNER_TEMP/changed-status.txt` with `git diff --name-status --no-renames`; locally it
is the diff against `origin/main` plus the working tree and untracked files) keeps its pins in the
fast tier:

| rule | fires on | runs |
|---|---|---|
| `test-module-added-or-removed` | a test module added, deleted or renamed | every module in `MERGE_GATES` (Makefile) |
| `system-check-changed` | `python/djust/checks/`, `docs/system-checks.md` | the check-ID uniqueness and C0xx docs-table pins |
| `client-js-source-changed` | `python/djust/static/djust/src/` | `tests/test_client_size_manifest_2138.py` |

Each rule names the incident that justifies it; add one the same way, with an `example` path the
tests check fires it.

**Running the early tier locally** before handing work over:

    make test-early

It collects the ledger entries' files, every file using `pr_tier` and the files the fired rules
name, and keeps only the early tests (`pytest --early-only`) on two workers. A rule selects
whole files, but the exhaustive sweep units in them are left to their own sampling (their
`pr_tier` census tests stay).

### The merge queue is on (ruleset `merge-queue`)

The `merge-queue` ruleset on the default branch requires the `test-summary` check and
turns on the GitHub merge queue (squash, grouping `ALLGREEN`, up to 3 entries built and
merged together, 90-minute check timeout). Repository admins are bypass actors, so the
direct admin merge path still works as a fallback. To roll the queue back, set the
ruleset's enforcement to `disabled` (Settings, Rules, Rulesets) or delete it; nothing
else depends on it. The first real `merge_group` run (PR #3822, a docs-only change) confirmed the scope rules'
assumptions for a single-entry queue: `MG_BASE_SHA` was the previous `main` tip, `MG_HEAD_SHA` the
queue commit, `MG_BASE_REF` was `refs/heads/main`, `origin/main` was present after checkout, and the
`base_sha` diff and the merge-base diff listed the same files, so `changes (scope)` classified the run
docs-only and `test-summary` passed in 23 s with every heavy job skipped. A queue entry holding
several PRs has not been observed yet; the two-diff union in `scripts/ci-change-scope.py` exists for
that case.

Required approving reviews on `main` are 0 (GitHub will not let the author approve their own PR, and
no other account has write access), so the queue's gate is the required `test-summary` check. Pushes
and merges to `main` are restricted to the repository owner through branch protection.

### Reading CI logs: `gh run view --log` truncates silently

`gh run view --job <id> --log` stops at the first log line over 64 KiB, with no error,
so a job can look like it went silent mid-run when it finished normally (this is what
made #3800 look like a hang). Fetch the full log through the API instead:

```bash
gh api --allow-escape-sequences repos/<owner>/<repo>/actions/jobs/<job-id>/logs > job.log
```

`tests/short_param_ids.py` keeps collected test ids short so the pytest lines stay far
below that limit.

## Local load guard and flow metrics

**The rule: CI is the full-suite evidence; a developer machine runs what it changed.** CI runs the
full 3.12-3.15 matrix on every PR head and every `main` push, so a whole-suite run locally adds
nothing CI will not report and costs the CPU of everything else on the machine (load averages of
23-25 with three agents each running `-n 8`, 2026-11-05). `tests/local_load_guard.py` (registered
in the root `conftest.py`) enforces it. Outside CI, unless `DJUST_FULL_LOCAL=1`:

- xdist is held to 2 workers however many `-n N` / `-n auto` asked for (`DJUST_LOCAL_MAX_WORKERS`);
- a run that collects more than 5,000 tests (`DJUST_LOCAL_MAX_ITEMS`; the full suite is about
  35,000) stops before running any, with the way out in the message.

The `make test*` targets and the pre-push full-suite fallback set `DJUST_FULL_LOCAL=1` themselves:
they are the deliberate gates. Everything else should be a file, a class, `-k`, or
`make test-selected`. Results for a pushed commit: `gh pr checks N`, or
`gh run list --workflow Tests --branch main --commit <FULL 40-char sha>` (a short sha returns nothing).

**Time per PR and per issue is measured, not remembered:** `make flow-metrics` (`DAYS=3`, `CHECK=1` to
exit non-zero on a breach) reads GitHub and compares against budgets set tighter than the
2026-11-04/05 baseline (27 merged PRs: open to merged median 0.6h, p90 2.0h; 42 closed issues:
open to closed median 6.3h, p75 17.4h, p90 28.4h). The finding from that baseline: time inside a PR
is short (CI plus the merge queue); the time is in the wait before a PR exists and in serial review
rounds. Run it at every retro; it is not a merge gate (a metric on history would block unrelated work).

## Gate-off over many sites: `scripts/gate_off_batch.py`

A gate-off reverts the change under test and re-runs the tests that guard it; every one of
them must go red. For one or a few mutations use `scripts/gate_off.py` (#3714), which runs the
test command once per mutation. A sweep over many call sites does not scale that way: #3903
had about 190 sites and a full run took about four minutes, so a run per site is over twelve
hours. Narrowing each run to the tests of the site's component is fast but is no longer the CI
configuration, and a narrowed run can hide a gap in what the full run covers.

`scripts/gate_off_batch.py` (#3905) applies all the mutations of a *batch* at once, runs the
**whole** test command once, and attributes each failing test to a mutation by the text of its
failure. On a synthetic fixture, three mutations that each break a distinct test cost one full
run instead of three. It reuses `gate_off.py` for the bounded run (deadline and RSS ceiling:
`HUNG` and `RUNAWAY-MEMORY` are reported as `CAUGHT` with the measurement), the per-directory
lock and out-of-tree journal, the SHA-256-verified restore (also on SIGINT, SIGTERM and SIGHUP)
and the classification of pytest's summary (errors counted with failures; a module that did not
import is `INVALID`, never a number).

Describe the mutations as data, in the `gate_off.py` spec plus the fields that attribute a
failure to a mutation:

```json
{
  "root": ".",
  "test_files": ["tests/test_calc.py"],
  "deadline": 600,
  "mutations": [
    {
      "label": "alpha() returns the wrong value",
      "file": "pkg/calc.py",
      "old": "def alpha():\n    return 1",
      "new": "def alpha():\n    return 99",
      "component": "alpha",
      "names": ["calc.alpha("]
    },
    {
      "label": "gamma() returns the wrong value",
      "file": "pkg/calc.py",
      "old": "def gamma():\n    return 3",
      "new": "def gamma():\n    return 99",
      "component": "gamma",
      "test_ids": ["::test_gamma"]
    }
  ]
}
```

```bash
make gate-off-batch SPEC=mutations.json                 # one full run per batch
make gate-off-batch SPEC=mutations.json ARGS="--jobs 2" # two workers (the second on a copy of the tree)
make gate-off-batch SPEC=mutations.json ARGS="--survivors-only"  # confirm last run's survivors, one at a time
```

How a result is reached (it is never guessed):

* A failing test belongs to the mutation whose `names` appear in its failure text, or whose
  `test_ids` regexes match its `classname::name`. A mutation with a test only it claims is
  `CAUGHT`, and that test is the evidence in the report.
* A test claimed by two mutations, or by none, is ambiguous. A mutation with no test of its own
  that is touched by either is re-run **alone**, and the single run decides. A batch that does
  not collect, hangs or runs out of memory cannot be attributed to a member, so every member is
  re-run alone and the culprit is then reported as `INVALID` or as a bounded catch.
* A mutation no failure names, when every failure of the batch was claimed by someone else, is
  `SURVIVED`. `--verify-survivors` re-runs those alone too; `--survivors-only` re-runs the
  survivors of the previous report alone and merges the rows.
* At most one mutation per `component` shares a batch, and mutations whose texts overlap in one
  file, or that differ in `command`, `deadline`, `rss_mb` or `smoke`, never do. A mutation with
  no `component`, `names` or `test_ids` cannot be attributed and always runs alone.
* Every text must match exactly once and change the source, or the row is `INVALID`. Files are
  restored from the bytes saved before the first write, in a `finally`, and the run refuses to
  start when a mutated file has uncommitted changes (`--allow-dirty` to opt out); `git status`
  is checked again afterwards.

The report is written to `<spec>.gateoff-report.json` (`--json` to choose) next to a human table
that ends with the number of full runs made against the number a run per mutation would take.

Limits. Attribution is by **name**: the failure text has to name the site, so use specific
names (`calc.alpha(`, not `alpha`). A site inside a module-level helper shared by several
components is attributed to the module. A failure that names mutation A but was caused by B
alone is attributed to A. Several mutations applied at once can mask an interaction. A survivor
is a question, not a pass: an equivalent mutation, two mechanisms shadowing each other, or
coverage that is genuinely missing look identical from here (#3903 had four survivors, all
equivalent). The runner is pytest; `-x` and `--maxfail` are refused because they stop at the
first failure. `--jobs` is limited to 2 because the machine is shared.

The tool runs the test command under a small watchdog that kills the command's whole process group
the moment the tool is gone, so even a SIGKILLed tool (or an outer harness that SIGKILLs its pytest
worker) cannot leave a spinning orphan behind; every busy loop in the tool's own fixtures is also
bounded to 45 s. The tool is itself gate-off'd with `gate_off.py`: `scripts/gate-off-batch-selfcheck.json` lists
the attribution, planning, restore and validation rules whose removal must turn
`tests/unit/test_gate_off_batch_3905.py` red (run it from a copy of the files it names, as its
`_comment` says).

## Measurements and next improvements

Main `0aa117d6` [CI run 37083226366](https://github.com/djust-org/djust/actions/runs/37083226366)
collected 41,372 items. Its four Python 3.12 shards passed 39,753 and skipped 1,547,
with pytest wall times of 257, 352, 418 and 385 seconds; extension/dependency setup
added roughly 55–79 seconds per shard. Scheduling can add queue time.

The then-committed `.test_durations` has 40,042 node IDs. Its accumulated worker
durations are not wall-clock predictions: 4,308 contrast-preset cases record about
19 seconds, while 25 ADR037 binding-check cases record about 342 seconds. Profile
setup/call/teardown and collection/startup separately before changing fixtures.
The binding fixture creates/imports temporary modules and templates and collects
garbage during cleanup; that is a profiling lead, not a demonstrated bottleneck.

Identical differential-corpus inputs already share a content/build/argv-keyed,
locked session cache across workers, and corpus readers are grouped for sharding.
Keep mutation isolation and per-test global resets. No case removal or fixture
scope widening is justified by counts alone.

Record selected files, collection size, wall time and FULL reasons against the
full CI outcomes; investigate any failure absent from a selected run. Refresh
shard timings only from verified complete matching artifacts using
`make test-durations-from-ci RUN=<id>`; never infer durations or claim speedups
without measurements. Selection safeguards can intentionally increase FULL
fallbacks. The expected benefit is avoiding redundant complete runs during edits,
not reducing authoritative integration coverage.
