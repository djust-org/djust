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
fast tier, like an unlabelled PR; the push to main after the merge still runs the full
matrix and the slow tier. `external-pr-gate.yml` also listens to `merge_group` and
reports success there, because the PR passed it to be queued.

**Hang dumps.** The Python shard step passes `-o faulthandler_timeout=1200`. If one test
(setup, call and teardown together) runs longer than 20 minutes, pytest writes every
thread's stack to the job log, naming the test, and carries on; nothing fails. The value
is above three times the slowest test recorded in `.test_durations`, and
`tests/test_ci_hang_dump_3800.py` fails when a recorded test outgrows it. The
`python-tests` job also has `timeout-minutes: 45` (about 3x the slowest shard in the
last five green main runs, 15.4 minutes), so a hung shard ends in bounded time instead
of GitHub's 360-minute default; it must stay above the dump threshold so the dump can
fire first. Neither is a diagnosis on its own: the dump needs a hang longer than 20
minutes, the timeout only bounds it.

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
