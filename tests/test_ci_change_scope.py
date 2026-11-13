"""Merge-queue support and path scoping in the Tests workflow.

Three things are pinned here, because each can be wrong while the suite stays green:

1. ``scripts/ci-change-scope.py`` -- the rule table. A wrong skip is worse than a slow run, so
   the table is tested from both sides: what may skip, and (more importantly) every kind of
   file that must NOT.
2. The workflow wiring -- ``merge_group`` triggers on the workflows whose checks are required,
   ``changes`` ahead of every expensive job, and a ``test-summary`` gate that is still
   ``needs``-complete.
3. The gate itself. Both bash blocks of ``test-summary`` are executed over a scenario table, so
   "a failure still fails the merge" and "a legitimate skip does not" are observed, not argued
   (#1713: being in ``needs`` and being echoed is not the same as gating).
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.git_env import isolated_git_env

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TEST_YML = WORKFLOWS / "test.yml"
EXTERNAL_GATE = WORKFLOWS / "external-pr-gate.yml"
SCOPE_SCRIPT = ROOT / "scripts" / "ci-change-scope.py"
STRATEGY_DOC = ROOT / "docs" / "testing-strategy.md"


def _load_scope():
    spec = importlib.util.spec_from_file_location("ci_change_scope", SCOPE_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["ci_change_scope"] = module
    spec.loader.exec_module(module)
    return module


scope = _load_scope()


def _workflow(path: Path = TEST_YML) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _triggers(doc: dict) -> dict:
    # `on:` is the YAML 1.1 boolean True.
    return doc.get("on", doc.get(True))


# --------------------------------------------------------------------------- #
# 1. The rule table
# --------------------------------------------------------------------------- #

DOCS_FILES = [
    "CHANGELOG.md",
    "RETRO.md",
    "README.md",
    "CLAUDE.md",
    "changelog.d/3800.fixed.md",
    "changelog.d/README.md",
    "docs/testing-strategy.md",
    "docs/website/guides/forms.md",
    "docs/adr/ADR-001.md",
]
TEST_PY_FILES = [
    "tests/test_something.py",
    "tests/unit/test_nested_deep.py",
    "tests/conftest.py",
    "python/tests/test_x_3800.py",
    "python/djust/tests/test_y.py",
    "python/djust/tests/helpers/util.py",
]
# Each of these must keep the FULL workflow running, on its own and next to docs/tests.
MUST_NOT_SKIP = [
    ".github/workflows/test.yml",
    ".github/workflows/external-pr-gate.yml",
    ".github/actions/djust-playwright-server/action.yml",
    "scripts/ci-change-scope.py",
    "scripts/check-doc-snippets.py",
    "pyproject.toml",
    "uv.lock",
    "package.json",
    "package-lock.json",
    "Cargo.toml",
    "Cargo.lock",
    "crates/djust_vdom/src/lib.rs",
    "crates/djust_templates/tests/test_x.rs",
    "python/djust/live_view.py",
    "python/djust/static/djust/src/12-vdom-patch.js",
    ".test_durations",
    ".test_collected_floor",
    "Makefile",
    # a nested .md is not a root-level doc: python/djust/ ships some of them
    "python/djust/skills/README.md",
    "crates/djust_vdom/README.md",
    # a fixture sits next to the tests but is not a .py module -- a Rust test include_str!s one
    "python/tests/fixtures/root_selection_3031.json",
    "tests/js/fixtures/x.json",
    "tests/js/some.test.js",
    # near-misses of the two prefixes
    "docsx/readme.txt",
    "tests_extra/test_a.py",
    "python/djust/tests_helpers.py",
    "mytests/test_a.py",
    # a non-.py file under docs-looking names
    "changelog.d.py",
]


def test_the_documented_classes_agree_with_the_script():
    for f in DOCS_FILES:
        assert scope.is_docs(f), f
        assert not scope.is_test_py(f), f
    for f in TEST_PY_FILES:
        assert scope.is_test_py(f), f
        assert not scope.is_docs(f), f
    for f in MUST_NOT_SKIP:
        assert not scope.is_docs(f) and not scope.is_test_py(f), f


def test_docs_only_change_skips_everything():
    assert scope.classify(DOCS_FILES) == scope.Scope(docs_only=True, rust_skip=True)


def test_test_only_change_skips_only_the_rust_job():
    s = scope.classify(TEST_PY_FILES)
    assert s == scope.Scope(docs_only=False, rust_skip=True)
    # ...and docs mixed with tests is still "tests may not skip the Python matrix"
    assert scope.classify(DOCS_FILES + TEST_PY_FILES) == scope.Scope(False, True)


@pytest.mark.parametrize("path", MUST_NOT_SKIP)
@pytest.mark.parametrize(
    "company",
    [[], ["docs/a.md"], ["tests/test_a.py"], ["docs/a.md", "tests/test_a.py"]],
    ids=["alone", "with-docs", "with-tests", "with-both"],
)
def test_nothing_that_can_change_behaviour_ever_skips(path, company):
    assert scope.classify([*company, path]) == scope.FULL_RUN, (path, company)


def test_docs_only_implies_rust_skip():
    # The workflow skips rust-tests on `rust_skip` alone and every other job on `docs_only`;
    # the docs-only gate step then demands rust-tests be skipped too. They must not disagree.
    for files in (DOCS_FILES, TEST_PY_FILES, MUST_NOT_SKIP, DOCS_FILES + TEST_PY_FILES):
        s = scope.classify(files)
        assert not s.docs_only or s.rust_skip, files


@pytest.mark.parametrize(
    "files",
    [
        [],
        [""],
        ["/etc/passwd"],
        ["docs/../scripts/x.py"],
        ["docs/./a.md"],
        ["docs//a.md"],
        ["docs\\a.md"],
    ],
)
def test_doubt_runs_everything(files):
    assert scope.classify(files) == scope.FULL_RUN


# --- end to end against a real repository, both event shapes ---------------- #


def _git(repo: Path, *args: str) -> str:
    env = isolated_git_env(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    out = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return out.stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    for name in ("README.md", "crates/a.rs", "tests/test_a.py", "docs/a.md"):
        f = tmp_path / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    # the checkout in CI has the target branch as origin/main
    _git(tmp_path, "update-ref", "refs/remotes/origin/main", "HEAD")
    return tmp_path


def _run_script(repo: Path, tmp_path: Path, *argv: str) -> dict[str, str]:
    return _run_script_logged(repo, tmp_path, *argv)[0]


def _run_script_logged(repo: Path, tmp_path: Path, *argv: str) -> tuple[dict[str, str], str]:
    sink = tmp_path / "github_output"
    sink.unlink(missing_ok=True)
    env = isolated_git_env(GITHUB_OUTPUT=str(sink))
    done = subprocess.run(
        [sys.executable, str(SCOPE_SCRIPT), *argv],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stdout + done.stderr  # never fails on classification
    outputs = dict(line.split("=", 1) for line in sink.read_text().splitlines())
    return outputs, done.stdout


def _mg(base: str, head: str, base_ref: str = "refs/heads/main") -> list[str]:
    return ["--event", "merge_group", "--base", base, "--head", head, "--base-ref", base_ref]


def _commit(repo: Path, files: dict[str, str | None], message: str = "c") -> str:
    for name, body in files.items():
        f = repo / name
        if body is None:
            f.unlink()
            continue
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("event", ["pull_request", "merge_group"])
@pytest.mark.parametrize(
    ("change", "docs_only", "rust_skip"),
    [
        ({"docs/a.md": "y\n", "CHANGELOG.md": "z\n"}, "true", "true"),
        ({"tests/test_a.py": "y\n"}, "false", "true"),
        ({"docs/a.md": "y\n", "tests/test_a.py": "y\n"}, "false", "true"),
        ({"crates/a.rs": "y\n"}, "false", "false"),
        ({"docs/a.md": "y\n", "crates/a.rs": "y\n"}, "false", "false"),
        ({".test_durations": "{}\n"}, "false", "false"),
        ({"scripts/x.py": "1\n"}, "false", "false"),
    ],
)
def test_script_classifies_a_real_diff(repo, tmp_path, event, change, docs_only, rust_skip):
    base = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, change)
    if event == "merge_group":
        argv = _mg(base, head)
    else:
        argv = ["--event", event, "--base", base, "--head", head]
    out = _run_script(repo, tmp_path, *argv)
    assert out == {"docs_only": docs_only, "rust_skip": rust_skip}


def test_a_rename_out_of_the_source_tree_into_docs_is_not_docs_only(repo, tmp_path):
    # `git diff --name-only` with rename detection lists only the NEW path (docs/...), which
    # would read as docs-only. --no-renames lists the deleted crates/ path too.
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "docs").mkdir(exist_ok=True)
    _git(repo, "mv", "crates/a.rs", "docs/a_moved.md")
    _git(repo, "commit", "-q", "-m", "rename")
    head = _git(repo, "rev-parse", "HEAD")
    out = _run_script(repo, tmp_path, *_mg(base, head))
    assert out == {"docs_only": "false", "rust_skip": "false"}


def test_pull_request_diff_is_taken_from_the_merge_base(repo, tmp_path):
    # main moved on with a code change after the branch point; the PR itself is docs-only.
    fork = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "feature")
    head = _commit(repo, {"docs/a.md": "y\n"})
    _git(repo, "checkout", "-q", "main")
    main_tip = _commit(repo, {"crates/a.rs": "moved on\n"})
    assert fork != main_tip
    out = _run_script(repo, tmp_path, "--event", "pull_request", "--base", main_tip, "--head", head)
    assert out == {"docs_only": "true", "rust_skip": "true"}


@pytest.mark.parametrize(
    "argv",
    [
        ["--event", "merge_group"],  # no shas
        ["--event", "merge_group", "--base", "0" * 40, "--head", "1" * 40, "--base-ref", "main"],
        ["--event", "merge_group", "--base", "0" * 40, "--head", "1" * 40],  # no base_ref
        ["--event", "push", "--base", "a", "--head", "b"],  # unscoped event
    ],
)
def test_script_fails_open_and_still_exits_zero(repo, tmp_path, argv):
    assert _run_script(repo, tmp_path, *argv) == {"docs_only": "false", "rust_skip": "false"}


def _grouped_queue(repo: Path) -> tuple[str, str, str]:
    """A [code PR, docs PR] group on main: returns (main sha, group parent, head)."""
    main_sha = _git(repo, "rev-parse", "HEAD")
    code_pr = _commit(repo, {"crates/a.rs": "code change\n"})
    head = _commit(repo, {"docs/a.md": "docs change\n"})
    return main_sha, code_pr, head


def test_a_grouped_queue_entry_is_not_docs_only_whatever_base_sha_means(repo, tmp_path):
    main_sha, group_parent, head = _grouped_queue(repo)
    # if base_sha is the group's OWN parent, base_sha..head_sha is the docs PR alone ...
    out, log = _run_script_logged(repo, tmp_path, *_mg(group_parent, head))
    # ... and the merge-base view still sees the code PR, so the union runs everything
    assert out == {"docs_only": "false", "rust_skip": "false"}
    assert "crates/a.rs" in log and "docs/a.md" in log
    # if base_sha is the target branch tip, the two views agree
    out, _ = _run_script_logged(repo, tmp_path, *_mg(main_sha, head))
    assert out == {"docs_only": "false", "rust_skip": "false"}


def test_the_log_shows_both_diffs_so_a_first_queue_run_can_confirm_base_sha(repo, tmp_path):
    _, group_parent, head = _grouped_queue(repo)
    _, log = _run_script_logged(repo, tmp_path, *_mg(group_parent, head))
    assert f"by base_sha {group_parent[:12]}..head_sha {head[:12]}" in log, log
    assert "by merge-base with origin/main..head_sha" in log, log
    assert "the two merge_group diffs differ" in log, log
    by_base_sha = log.split("by base_sha", 1)[1].split("file(s) by merge-base", 1)[0]
    assert "docs/a.md" in by_base_sha and "crates/a.rs" not in by_base_sha, log


def test_the_classified_set_is_the_union_not_either_diff_alone(repo, tmp_path):
    # base_sha is older than the merge-base (main moved on with a code change): the base_sha
    # diff then holds a file the merge-base diff does not. The union must keep it.
    old_base = _git(repo, "rev-parse", "HEAD")
    _commit(repo, {"crates/a.rs": "main moved on\n"})
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    head = _commit(repo, {"docs/a.md": "y\n"})
    out, log = _run_script_logged(repo, tmp_path, *_mg(old_base, head))
    assert out == {"docs_only": "false", "rust_skip": "false"}
    assert "the two merge_group diffs differ" in log


def test_a_docs_only_entry_directly_on_main_still_skips(repo, tmp_path):
    main_sha = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, {"docs/a.md": "y\n"})
    out, log = _run_script_logged(repo, tmp_path, *_mg(main_sha, head))
    assert out == {"docs_only": "true", "rust_skip": "true"}
    assert "differ" not in log


@pytest.mark.parametrize("base_ref", ["", "refs/heads/no-such-branch"])
def test_a_missing_or_unfetched_base_ref_runs_everything(repo, tmp_path, base_ref):
    main_sha = _git(repo, "rev-parse", "HEAD")
    head = _commit(repo, {"docs/a.md": "y\n"})
    out = _run_script(repo, tmp_path, *_mg(main_sha, head, base_ref))
    assert out == {"docs_only": "false", "rust_skip": "false"}


# --------------------------------------------------------------------------- #
# 2. Workflow wiring
# --------------------------------------------------------------------------- #

GATED = [
    "rust-tests",
    "python-tests",
    "python-tests-django6-only",
    "windows-ai-inventory",
    "javascript-tests",
    "javascript-coverage",
    "playwright-tests",
    "browser-smoke",
    "nav-hooks-guard",
    "security-scan",
    "security-tests",
    "demo-checks",
    "benchmarks",
    "django-template-suite",
]


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def test_merge_group_triggers_exist_on_every_required_workflow():
    for path in (TEST_YML, EXTERNAL_GATE):
        assert "merge_group" in _triggers(_workflow(path)), (
            f"{path.name} reports a check that is (or will be) required; without a merge_group "
            "trigger the merge queue waits for it forever"
        )


def test_test_yml_does_not_filter_the_merge_group_trigger_by_path():
    # `paths-ignore` is not evaluated for merge_group; the in-workflow `changes` job does the
    # scoping. A `branches` filter would silently keep the trigger from firing.
    assert _triggers(_workflow())["merge_group"] == {"types": ["checks_requested"]}


def test_every_expensive_job_is_downstream_of_changes_and_names_its_skip_output():
    jobs = _workflow()["jobs"]
    assert "changes" not in jobs["changes"].get("needs", [])
    for name, job in jobs.items():
        if name in ("changes", "test-summary"):
            continue
        reaches_changes = "changes" in _needs(job) or any(
            "changes" in _needs(jobs[dep]) for dep in _needs(job) if dep in jobs
        )
        assert reaches_changes, f"{name} runs on a docs-only change"
        if "changes" in _needs(job):
            cond = str(job.get("if", ""))
            wanted = "rust_skip" if name == "rust-tests" else "docs_only"
            assert f"needs.changes.outputs.{wanted} != 'true'" in cond, (name, cond)


def test_only_rust_tests_reads_rust_skip():
    # rust_skip says "docs or Python test modules only". Any other job reading it would skip
    # work a test change can break.
    for name, job in _workflow()["jobs"].items():
        uses = "rust_skip" in str(job.get("if", ""))
        assert uses == (name == "rust-tests"), name


def test_the_gate_needs_every_job_it_judges_and_changes():
    gate = _workflow()["jobs"]["test-summary"]
    assert gate["if"] == "always()"
    assert set(GATED) | {"changes"} <= set(_needs(gate))


def test_changes_job_is_fail_safe_on_unscoped_events():
    steps = _workflow()["jobs"]["changes"]["steps"]
    run = next(s["run"] for s in steps if s.get("id") == "scope")
    assert "docs_only=false" in run and "rust_skip=false" in run
    checkout = next(s for s in steps if str(s.get("uses", "")).startswith("actions/checkout"))
    assert checkout["with"]["fetch-depth"] == 0
    assert "merge_group" in checkout["if"] and "pull_request" in checkout["if"]


def test_the_queue_is_treated_like_a_pull_request_for_matrix_and_tier():
    jobs = _workflow()["jobs"]
    for name in ("python-build", "python-tests"):
        expr = str(jobs[name]["strategy"]["matrix"]["python-version"])
        # #4074: only the nightly (schedule, via main-health) and a manual run widen the matrix,
        # so the queue -- like a PR and the push to main -- runs 3.12 alone.
        assert "github.event_name == 'schedule'" in expr, (name, expr)
        assert "github.event_name == 'workflow_dispatch'" in expr, (name, expr)
        assert "merge_group" not in expr and "pull_request" not in expr, (name, expr)
    step = next(s for s in jobs["python-tests"]["steps"] if "-m pytest" in (s.get("run") or ""))
    assert "github.event_name == 'merge_group'" in step["env"]["PYTEST_MARK"]


def test_the_changes_step_passes_the_merge_group_payload_to_the_script():
    """W14: nothing else pins this arm, and a typo here silently fails open (full runs)."""
    step = next(s for s in _workflow()["jobs"]["changes"]["steps"] if s.get("id") == "scope")
    env, run = step["env"], step["run"]
    assert env["MG_BASE_SHA"] == "${{ github.event.merge_group.base_sha }}"
    assert env["MG_HEAD_SHA"] == "${{ github.event.merge_group.head_sha }}"
    assert env["MG_BASE_REF"] == "${{ github.event.merge_group.base_ref }}"
    assert env["PR_BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert env["PR_HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    arm = re.search(r"merge_group\)\s*\n\s*(python3 scripts/ci-change-scope\.py[^\n]*?);;", run)
    assert arm, run
    assert arm.group(1).split() == [
        "python3",
        "scripts/ci-change-scope.py",
        "--event",
        "merge_group",
        "--base",
        '"$MG_BASE_SHA"',
        "--head",
        '"$MG_HEAD_SHA"',
        "--base-ref",
        '"$MG_BASE_REF"',
    ]
    pr_arm = re.search(r"pull_request\)\s*\n\s*(python3 scripts/ci-change-scope\.py[^\n]*?);;", run)
    assert pr_arm
    assert '"$PR_BASE_SHA"' in pr_arm.group(1) and '"$PR_HEAD_SHA"' in pr_arm.group(1)


def test_external_pr_gate_survives_a_merge_group_event():
    doc = _workflow(EXTERNAL_GATE)
    group = doc["concurrency"]["group"]
    # no PR number and no `github.event.number` under merge_group: without a last-resort
    # unique key every queue entry shares one group and cancels the others.
    assert group.rstrip("} ").endswith("|| github.sha"), group
    steps = doc["jobs"]["maintainer-approval"]["steps"]
    real = next(s for s in steps if s.get("uses", "").startswith("actions/github-script"))
    assert real["if"] == "github.event_name != 'merge_group'"  # it dereferences pull_request
    queue = next(s for s in steps if s.get("if") == "github.event_name == 'merge_group'")
    assert "run" in queue


def test_no_job_in_test_yml_needs_a_pull_request_payload_under_the_queue():
    # `github.event.pull_request.*` is null under merge_group. The only readers allowed are
    # the label lookup (null -> "no label", i.e. the PR-like fast path) and the changes job
    # (which reads the merge_group payload for that event).
    text = TEST_YML.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    uses = sorted(set(re.findall(r"github\.event\.pull_request\.[\w.*]+", code)))
    assert uses == [
        "github.event.pull_request.base.sha",
        "github.event.pull_request.head.sha",
        "github.event.pull_request.labels.*.name",
    ], uses


# --------------------------------------------------------------------------- #
# 3. The gate, executed
# --------------------------------------------------------------------------- #

_EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def _gate_steps() -> dict[str, dict]:
    steps = _workflow()["jobs"]["test-summary"]["steps"]
    return {s["name"]: s for s in steps}


def _render(text: str, ctx: dict[str, str]) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        assert key in ctx, f"gate reads an expression the scenario does not define: {key!r}"
        return ctx[key]

    return _EXPR.sub(sub, text)


def _if_true(condition: str | None, ctx: dict[str, str]) -> bool:
    if condition is None:
        return True
    expr = condition
    for key in sorted(ctx, key=len, reverse=True):
        expr = expr.replace(key, repr(ctx[key]))
    expr = expr.replace("&&", " and ").replace("||", " or ")
    return bool(eval(expr, {"__builtins__": {}}))  # noqa: S307 - our own workflow text


def gate(
    *,
    results: dict[str, str],
    changes: str = "success",
    docs_only: str = "false",
    rust_skip: str = "false",
    event: str = "pull_request",
) -> tuple[bool, list[str]]:
    """Run the real test-summary steps. Returns (job passes, names of the steps that ran)."""
    ctx = {
        "needs.changes.result": changes,
        "needs.changes.outputs.docs_only": docs_only,
        "needs.changes.outputs.rust_skip": rust_skip,
        "github.event_name": event,
        **{f"needs.{job}.result": res for job, res in results.items()},
    }
    ran, ok = [], True
    for name, step in _gate_steps().items():
        if not _if_true(step.get("if"), ctx):
            continue
        ran.append(name)
        env = isolated_git_env(
            **{key: _render(str(value), ctx) for key, value in (step.get("env") or {}).items()}
        )
        done = subprocess.run(
            ["bash", "-c", _render(step["run"], ctx)], env=env, capture_output=True, text=True
        )
        ok = ok and done.returncode == 0
    return ok, ran


def _all(result: str, **overrides: str) -> dict[str, str]:
    return {**{job: result for job in GATED}, **overrides}


# Jobs that carry a verdict in each path (playwright and the security scan only warn).
INFORMATIONAL = {"playwright-tests", "security-scan"}
CHAIN_JOBS = [j for j in GATED if j not in INFORMATIONAL]

CODE_PR = {  # a code change on a pull request: everything runs, the slow coverage tier does not
    "results": _all("success", **{"javascript-coverage": "skipped", "security-scan": "skipped"}),
    "event": "pull_request",
}
TEST_ONLY_PR = {
    "results": _all(
        "success",
        **{"javascript-coverage": "skipped", "security-scan": "skipped", "rust-tests": "skipped"},
    ),
    "rust_skip": "true",
    "event": "pull_request",
}
DOCS_QUEUE = {
    "results": _all("skipped"),
    "docs_only": "true",
    "rust_skip": "true",
    "event": "merge_group",
}


def test_each_legitimate_outcome_passes_through_exactly_one_gate_step():
    chain, docs = "Check test results", "Docs/changelog-only change -- the gated jobs were skipped"
    assert gate(**CODE_PR) == (True, [chain])
    assert gate(**TEST_ONLY_PR) == (True, [chain])
    assert gate(**{**TEST_ONLY_PR, "event": "merge_group"}) == (True, [chain])
    assert gate(**DOCS_QUEUE) == (True, [docs])
    assert gate(**{**CODE_PR, "event": "merge_group"}) == (True, [chain])
    assert gate(**{**CODE_PR, "event": "push", "results": _all("success")}) == (True, [chain])


@pytest.mark.parametrize("event", ["pull_request", "merge_group", "push", "workflow_dispatch"])
@pytest.mark.parametrize("docs_only", ["true", "false", ""])
@pytest.mark.parametrize("changes", ["success", "failure", "cancelled", "skipped"])
def test_exactly_one_gate_step_runs_in_every_state(changes, docs_only, event):
    # A gate where neither step runs would pass silently; both running would run the
    # docs-only verdict on top of the chain.
    _, ran = gate(results=_all("success"), changes=changes, docs_only=docs_only, event=event)
    assert len(ran) == 1, (changes, docs_only, event, ran)


@pytest.mark.parametrize("bad", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("job", CHAIN_JOBS)
@pytest.mark.parametrize(
    "scenario", [CODE_PR, {**CODE_PR, "event": "merge_group"}], ids=["pr", "queue"]
)
def test_a_job_that_did_not_succeed_fails_the_merge(scenario, job, bad):
    # javascript-coverage is the one job allowed to be skipped on these events.
    if job == "javascript-coverage" and bad == "skipped":
        pytest.skip("allowed: the slow coverage tier is off on PRs and in the queue")
    results = {**scenario["results"], job: bad}
    assert gate(**{**scenario, "results": results})[0] is False, (job, bad)


@pytest.mark.parametrize("bad", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("job", [j for j in CHAIN_JOBS if j != "rust-tests"])
def test_a_test_only_change_still_gates_everything_but_cargo(job, bad):
    if job == "javascript-coverage" and bad == "skipped":
        pytest.skip("allowed: the slow coverage tier is off on PRs")
    results = {**TEST_ONLY_PR["results"], job: bad}
    assert gate(**{**TEST_ONLY_PR, "results": results})[0] is False, (job, bad)


@pytest.mark.parametrize("bad", ["success", "failure", "cancelled"])
@pytest.mark.parametrize("job", GATED)
def test_a_docs_only_entry_demands_every_job_report_skipped(job, bad):
    # success here means the scoping did not take effect as predicted; failure is a real one.
    results = {**DOCS_QUEUE["results"], job: bad}
    assert gate(**{**DOCS_QUEUE, "results": results})[0] is False, (job, bad)


@pytest.mark.parametrize("changes", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("scenario", [DOCS_QUEUE, TEST_ONLY_PR], ids=["docs-only", "test-only"])
def test_a_skip_is_never_accepted_when_the_scope_job_did_not_succeed(scenario, changes):
    # `changes` broke, so its dependants were skipped; the outputs it may have set are ignored.
    assert gate(**{**scenario, "changes": changes})[0] is False


@pytest.mark.parametrize("rust_skip", ["false", ""])
def test_rust_tests_may_not_be_skipped_without_the_scope_vouching(rust_skip):
    assert gate(**{**TEST_ONLY_PR, "rust_skip": rust_skip})[0] is False


def test_rust_tests_failure_fails_even_when_the_scope_allowed_a_skip():
    results = {**TEST_ONLY_PR["results"], "rust-tests": "failure"}
    assert gate(**{**TEST_ONLY_PR, "results": results})[0] is False


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule"])
def test_main_and_dispatch_runs_accept_no_skips(event):
    everything = _all("success")
    assert gate(results=everything, event=event)[0] is True
    # `changes` emits false/false for these events; even if a future edit let it say "true", the
    # gate checks the event itself before accepting a skip.
    skipped_rust = {**everything, "rust-tests": "skipped"}
    assert gate(results=skipped_rust, event=event, rust_skip="false")[0] is False
    assert gate(results=skipped_rust, event=event, rust_skip="true")[0] is False
    assert (
        gate(results=_all("skipped"), event=event, docs_only="true", rust_skip="true")[0] is False
    )
    assert gate(results={**everything, "javascript-coverage": "skipped"}, event=event)[0] is False


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("job", sorted(INFORMATIONAL))
def test_informational_jobs_do_not_block_a_code_change(job, result):
    results = {**CODE_PR["results"], job: result}
    assert gate(**{**CODE_PR, "results": results})[0] is True


# --------------------------------------------------------------------------- #
# The rule table is documented where a reader will look, and says what the script does
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", [TEST_YML, STRATEGY_DOC, SCOPE_SCRIPT], ids=lambda p: p.name)
def test_the_rule_table_is_quoted_in_the_workflow_the_script_and_the_testing_strategy(path):
    text = path.read_text(encoding="utf-8")
    for needle in (
        "changelog.d",
        "RETRO.md",
        "python/djust/tests",
        "python/tests",
        "docs_only",
        "rust_skip",
    ):
        assert needle in text, (path.name, needle)
