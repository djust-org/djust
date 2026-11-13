"""The promotion ledger and path rules: tests that fail late run early (``tests/early_tier.py``).

Everything here runs on synthetic inputs (junit XML, ledgers, duration maps, change lists and a
throwaway pytest project), so it does not depend on which real tests are slow, skip or fail, on
the Python or Django version, or on whether an outer run is itself recording (the outer CI run
sets the change-status and early-only variables; every inner run clears them).

The two tests at the end read the real tree: every ledger entry names a test that exists (a
renamed test fails here, by name), and every path-rule target exists. That makes this module a
cross-file gate, so it is in ``MERGE_GATES`` (#3757).
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from scripts import promotion_ledger as pl
from tests import early_tier
from tests.git_env import GIT_EXECUTION_VARS

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/main-health.yml"

#: Environment an outer run may carry that would leak into an inner one.
OUTER_ENV = (
    early_tier.CHANGED_STATUS_FILE_ENV,
    early_tier.CHANGED_STATUS_ENV,
    early_tier.EARLY_ONLY_ENV,
    "DJUST_SLOW_TIER",
    "GITHUB_OUTPUT",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (*OUTER_ENV, *GIT_EXECUTION_VARS):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# Synthetic inputs
# --------------------------------------------------------------------------- #


def junit(cases: list[tuple[str, str, str]]) -> str:
    """``[(nodeid, status, tier)]`` -> a junit document as pytest writes it with the plugin on."""
    body = []
    for nodeid, status, tier in cases:
        inner = {
            "passed": "",
            "failed": '<failure message="boom">trace</failure>',
            "error": '<error message="teardown">trace</error>',
            "skipped": '<skipped message="no" type="pytest.skip">no</skipped>',
        }[status]
        body.append(
            '<testcase classname="x" name="y" time="0.1"><properties>'
            f'<property name="djust_nodeid" value="{nodeid}" />'
            f'<property name="djust_tier" value="{tier}" />'
            f"</properties>{inner}</testcase>"
        )
    # a collection error: no node id, nothing to promote
    body.append(
        '<testcase classname="" name="tests.broken" time="0"><error message="x"/></testcase>'
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        + "".join(body)
        + "</testsuite></testsuites>"
    )


def write_junit(tmp_path: Path, cases, name: str = "j.xml") -> Path:
    path = tmp_path / name
    path.write_text(junit(cases))
    return path


def entry(test: str, *, last_failed="2026-01-01", streak=0, pinned=False, promoted=None) -> dict:
    return {
        "test": test,
        "promoted": promoted or last_failed,
        "last_failed": last_failed,
        "reason": "synthetic",
        "green_streak": streak,
        "pinned": pinned,
    }


def results(cases) -> dict[str, pl.Result]:
    return {nodeid: pl.Result(status, tier) for nodeid, status, tier in cases}


# --------------------------------------------------------------------------- #
# Reading a late run
# --------------------------------------------------------------------------- #


def test_junit_gives_each_node_id_its_status_and_pre_merge_tier(tmp_path):
    path = write_junit(
        tmp_path,
        [
            ("a.py::test_pass", "passed", "early"),
            ("a.py::test_fail[1]", "failed", "late"),
            ("a.py::test_err", "error", "late"),
            ("a.py::test_skip", "skipped", "nightly"),
        ],
    )
    got = pl.parse_junit([path])
    assert {k: (v.status, v.tier) for k, v in got.items()} == {
        "a.py::test_pass": ("passed", "early"),
        "a.py::test_fail[1]": ("failed", "late"),
        "a.py::test_err": ("failed", "late"),
        "a.py::test_skip": ("skipped", "nightly"),
    }


def test_a_call_pass_with_a_teardown_error_counts_as_failed(tmp_path):
    a = write_junit(tmp_path, [("a.py::test_x", "passed", "late")], "a.xml")
    b = write_junit(tmp_path, [("a.py::test_x", "error", "late")], "b.xml")
    assert pl.parse_junit([a, b])["a.py::test_x"].status == "failed"


def test_failed_ids_file_reads_pytest_failed_lines(tmp_path):
    path = tmp_path / "ids.txt"
    path.write_text("# from a push to main\na.py::test_x - assert 1 == 2\n\nb.py::T::test_y[p]\n")
    assert set(pl.parse_failed_ids(path)) == {"a.py::test_x", "b.py::T::test_y[p]"}


def test_the_ledger_key_is_the_function_with_parameters_stripped():
    assert early_tier.entry_key("a.py::T::test_x[1-a::b]") == "a.py::T::test_x"
    assert early_tier.entry_key("a.py::test_x") == "a.py::test_x"


# --------------------------------------------------------------------------- #
# Promotion
# --------------------------------------------------------------------------- #


def _promote(ledger, cases, *, durations=None, rule_targets=None, date="2026-11-12"):
    return pl.promote(
        ledger,
        results(cases),
        date=date,
        reason="nightly",
        durations=durations or {},
        rule_targets=rule_targets or {},
    )


def test_a_late_failure_is_promoted_once_per_function():
    ledger = {"entries": []}
    report = _promote(
        ledger,
        [
            ("a.py::test_late[1]", "failed", "late"),
            ("a.py::test_late[2]", "failed", "late"),
            ("a.py::test_ok", "passed", "late"),
        ],
    )
    assert report.promoted == ["a.py::test_late"]
    (only,) = ledger["entries"]
    assert only == {
        "test": "a.py::test_late",
        "promoted": "2026-11-12",
        "last_failed": "2026-11-12",
        "reason": "nightly",
        "green_streak": 0,
        "pinned": False,
    }
    assert report.membership_changed


def test_a_failure_already_in_the_pre_merge_tier_is_not_promoted():
    ledger = {"entries": []}
    report = _promote(ledger, [("a.py::test_fast", "failed", "early")])
    assert ledger["entries"] == [] and report.already_early == ["a.py::test_fast"]
    assert not report.membership_changed


def test_a_nightly_only_failure_is_not_promoted():
    ledger = {"entries": []}
    report = _promote(ledger, [("a.py::test_n", "failed", "nightly")])
    assert ledger["entries"] == [] and report.nightly_only == ["a.py::test_n"]


def test_a_failure_a_path_rule_already_covers_is_reported_not_promoted():
    ledger = {"entries": []}
    report = _promote(
        ledger,
        [("tests/test_gate.py::test_pin", "failed", "late")],
        rule_targets={"tests/test_gate.py": "test-module-added-or-removed"},
    )
    assert ledger["entries"] == []
    assert report.rule_covered == {"tests/test_gate.py::test_pin": "test-module-added-or-removed"}
    assert "extend the rule" in report.markdown()


def test_an_entry_that_fails_again_has_its_streak_reset():
    ledger = {"entries": [entry("a.py::test_x", last_failed="2026-10-01", streak=12)]}
    report = _promote(ledger, [("a.py::test_x[1]", "failed", "early")])
    assert report.refreshed == ["a.py::test_x"] and not report.promoted
    assert ledger["entries"][0]["green_streak"] == 0
    assert ledger["entries"][0]["last_failed"] == "2026-11-12"


def test_without_a_tier_failed_ids_use_the_recorded_durations():
    ledger = {"entries": []}
    report = _promote(
        ledger,
        [("a.py::test_slow", "failed", "unknown"), ("a.py::test_fast", "failed", "unknown")],
        durations={"a.py::test_slow": 4.0, "a.py::test_fast": 0.1},
    )
    assert report.promoted == ["a.py::test_slow"]
    assert report.already_early == ["a.py::test_fast"]


# --------------------------------------------------------------------------- #
# Demotion
# --------------------------------------------------------------------------- #


def test_a_green_nightly_credits_every_entry_that_passed():
    ledger = {
        "entries": [
            entry("a.py::test_pass", streak=3),
            entry("a.py::test_skip", streak=3),
            entry("a.py::test_absent", streak=3),
        ]
    }
    pl.demote(
        ledger,
        results(
            [("a.py::test_pass[1]", "passed", "early"), ("a.py::test_skip", "skipped", "early")]
        ),
        date="2026-11-12",
        durations={},
    )
    streaks = {e["test"]: e["green_streak"] for e in ledger["entries"]}
    assert streaks == {"a.py::test_pass": 4, "a.py::test_skip": 3, "a.py::test_absent": 3}


def test_an_unpinned_entry_leaves_after_the_threshold_and_a_pinned_one_stays():
    n = early_tier.DEMOTE_AFTER_GREEN
    ledger = {
        "entries": [
            entry("a.py::test_due", streak=n - 1),
            entry("a.py::test_not_yet", streak=n - 2),
            entry("a.py::test_pinned", streak=n + 50, pinned=True),
        ]
    }
    report = pl.demote(
        ledger,
        results(
            [(f"a.py::{t}", "passed", "early") for t in ("test_due", "test_not_yet", "test_pinned")]
        ),
        date="2026-11-12",
        durations={},
    )
    assert report.demoted == ["a.py::test_due"]
    assert [e["test"] for e in ledger["entries"]] == ["a.py::test_not_yet", "a.py::test_pinned"]


def test_a_failing_entry_on_a_demote_run_is_reset_not_credited():
    ledger = {"entries": [entry("a.py::test_x", streak=7)]}
    pl.demote(
        ledger, results([("a.py::test_x", "failed", "early")]), date="2026-11-12", durations={}
    )
    assert ledger["entries"][0]["green_streak"] == 0


# --------------------------------------------------------------------------- #
# Budget
# --------------------------------------------------------------------------- #


def test_over_budget_the_entry_whose_last_failure_is_oldest_leaves_first():
    budget = early_tier.BUDGET_SECONDS
    durations = {
        "a.py::test_old[1]": budget / 2,
        "a.py::test_old[2]": 1.0,
        "a.py::test_mid": budget / 2,
        "a.py::test_new": budget / 2,
        "a.py::test_pinned": 5.0,
    }
    ledger = {
        "entries": [
            entry("a.py::test_new", last_failed="2026-11-01"),
            entry("a.py::test_pinned", last_failed="2025-01-01", pinned=True),
            entry("a.py::test_old", last_failed="2026-01-01"),
            entry("a.py::test_mid", last_failed="2026-06-01"),
        ]
    }
    evicted = pl.enforce_budget(ledger, durations)
    assert evicted == ["a.py::test_old", "a.py::test_mid"]
    assert [e["test"] for e in ledger["entries"]] == ["a.py::test_new", "a.py::test_pinned"]


def test_within_budget_nothing_leaves():
    ledger = {"entries": [entry("a.py::test_x")]}
    assert pl.enforce_budget(ledger, {"a.py::test_x": 1.0}) == []
    assert len(ledger["entries"]) == 1


def test_pinned_entries_alone_over_budget_fail_loudly():
    ledger = {"entries": [entry("a.py::test_x", pinned=True)]}
    with pytest.raises(pl.BudgetError, match="pinned"):
        pl.enforce_budget(ledger, {"a.py::test_x": early_tier.BUDGET_SECONDS + 1})


def test_promotion_enforces_the_budget():
    ledger = {"entries": [entry("a.py::test_old", last_failed="2026-01-01")]}
    report = _promote(
        ledger,
        [("a.py::test_new", "failed", "late")],
        durations={"a.py::test_old": 100.0, "a.py::test_new": 100.0},
    )
    assert report.promoted == ["a.py::test_new"] and report.evicted == ["a.py::test_old"]


# --------------------------------------------------------------------------- #
# The CLI and the streak state the nightly carries between runs
# --------------------------------------------------------------------------- #


def _repo(tmp_path: Path, entries: list[dict], durations: dict | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    (root / early_tier.LEDGER_FILE).write_text(json.dumps({"entries": entries}))
    (root / early_tier.DURATIONS_FILE).write_text(json.dumps(durations or {}))
    return root


def _ledger(root: Path) -> dict:
    return json.loads((root / early_tier.LEDGER_FILE).read_text())


def test_the_state_carries_streaks_between_nights_and_keeps_a_proposed_demotion(tmp_path):
    n = early_tier.DEMOTE_AFTER_GREEN
    checked_in = [entry("a.py::test_x", streak=0, promoted="2026-01-01")]
    state = tmp_path / "state.json"
    junit_ok = write_junit(tmp_path, [("a.py::test_x", "passed", "early")])
    for night in range(n):
        root = _repo(tmp_path / str(night), copy.deepcopy(checked_in))
        assert (
            pl.main(
                ["--root", str(root), "demote", "--junit", str(junit_ok), "--state", str(state)]
            )
            == 0
        )
    # the checked-in ledger was never patched: the last night proposes the demotion ...
    assert _ledger(root)["entries"] == []
    # ... and the state still holds the entry's streak, so the next night proposes it again
    # instead of restarting the count from the checked-in 0.
    carried = json.loads(state.read_text())["entries"]
    assert [(e["test"], e["green_streak"]) for e in carried] == [("a.py::test_x", n)]
    root = _repo(tmp_path / "again", copy.deepcopy(checked_in))
    pl.main(["--root", str(root), "demote", "--junit", str(junit_ok), "--state", str(state)])
    assert _ledger(root)["entries"] == []


def test_the_cli_reports_a_membership_change_for_the_workflow(tmp_path, monkeypatch):
    root = _repo(tmp_path, [], {"a.py::test_late": 3.0})
    out = tmp_path / "gh-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    junit_red = write_junit(tmp_path, [("a.py::test_late", "failed", "late")])
    summary = tmp_path / "summary.md"
    rc = pl.main(
        ["--root", str(root), "promote", "--junit", str(junit_red), "--summary", str(summary)]
    )
    assert rc == 0
    assert out.read_text() == "membership_changed=true\n"
    assert "a.py::test_late" in summary.read_text()
    assert [e["test"] for e in _ledger(root)["entries"]] == ["a.py::test_late"]


def test_the_cli_exits_2_when_pinned_entries_exceed_the_budget(tmp_path):
    root = _repo(
        tmp_path,
        [entry("a.py::test_x", pinned=True)],
        {"a.py::test_x": early_tier.BUDGET_SECONDS * 2},
    )
    junit_ok = write_junit(tmp_path, [("a.py::test_x", "passed", "early")])
    assert pl.main(["--root", str(root), "demote", "--junit", str(junit_ok)]) == 2


# --------------------------------------------------------------------------- #
# Path rules
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("rule", early_tier.PATH_RULES, ids=lambda r: r.name)
def test_each_rule_fires_on_its_example_and_not_on_an_unrelated_change(rule):
    status = sorted(rule.statuses)[0]
    assert early_tier.fired_rules([(status, rule.example)]) == [rule]
    assert early_tier.fired_rules([("M", "README.md"), ("A", "python/djust/live_view.py")]) == []


@pytest.mark.parametrize("rule", early_tier.PATH_RULES, ids=lambda r: r.name)
def test_each_rule_says_why(rule):
    assert rule.why and rule.targets and rule.statuses <= {"A", "D", "M"}


def test_a_modified_test_module_does_not_fire_the_registry_rule_but_added_or_deleted_does():
    rule = early_tier.PATH_RULES[0]
    assert rule.name == "test-module-added-or-removed"
    path = "python/djust/tests/sub/test_thing_1.py"
    assert early_tier.fired_rules([("M", path)], [rule]) == []
    assert early_tier.fired_rules([("A", path)], [rule]) == [rule]
    assert early_tier.fired_rules([("D", path)], [rule]) == [rule]
    # not a test module: a helper under a test root, a test-looking file elsewhere
    assert (
        early_tier.fired_rules([("A", "tests/helpers.py"), ("A", "docs/test_x.py")], [rule]) == []
    )


def test_a_rename_is_a_delete_plus_an_add():
    got = early_tier.parse_status(["R087\ttests/test_old.py\ttests/test_new.py", "M\tMakefile"])
    assert got == {("D", "tests/test_old.py"), ("A", "tests/test_new.py"), ("M", "Makefile")}


def test_the_registry_rule_reads_merge_gates_from_the_makefile(tmp_path):
    (tmp_path / "Makefile").write_text(
        "X := 1\nMERGE_GATES := \\\n\ttests/a.py \\\n\ttests/b.py\n\nY := 2\n"
    )
    got = early_tier.selected_by_rules(tmp_path, {("A", "tests/test_new.py")})
    assert got == {
        "tests/a.py": "test-module-added-or-removed",
        "tests/b.py": "test-module-added-or-removed",
    }


def test_the_change_status_comes_from_the_environment_when_given(tmp_path):
    listing = tmp_path / "status.txt"
    listing.write_text("A\ttests/test_n.py\nM\tREADME.md\n")
    env = {early_tier.CHANGED_STATUS_FILE_ENV: str(listing)}
    assert early_tier.changed_status(tmp_path, env) == {
        ("A", "tests/test_n.py"),
        ("M", "README.md"),
    }
    env = {early_tier.CHANGED_STATUS_ENV: "D\ttests/test_gone.py"}
    assert early_tier.changed_status(tmp_path, env) == {("D", "tests/test_gone.py")}


# --------------------------------------------------------------------------- #
# The collection hook, in a throwaway project
# --------------------------------------------------------------------------- #

INI = """
[pytest]
asyncio_default_fixture_loop_scope = function
markers =
    pr_tier: pr tier
    nightly: nightly
    slow: slow
    sweep: sweep
"""

PROJECT = """
import pytest

def test_slow_promoted():
    pass

def test_slow_plain():
    pass

def test_fast():
    pass

@pytest.mark.pr_tier
def test_slow_pr_tier():
    pass
"""


@pytest.fixture
def project(pytester):
    pytester.makeini(INI)
    pytester.makepyfile(test_p=PROJECT)
    pytester.makepyfile(test_gate="def test_pin():\n    pass\n")
    slow = ["test_slow_promoted", "test_slow_plain", "test_slow_pr_tier"]
    durations = {f"test_p.py::{name}": 5.0 for name in slow}
    durations["test_gate.py::test_pin"] = 5.0
    (pytester.path / ".test_durations").write_text(json.dumps(durations))
    (pytester.path / "tests").mkdir()
    (pytester.path / early_tier.LEDGER_FILE).write_text(
        json.dumps({"entries": [entry("test_p.py::test_slow_promoted")]})
    )
    (pytester.path / "Makefile").write_text("MERGE_GATES := \\\n\ttest_gate.py\n")
    return pytester


def _inner(pytester, monkeypatch, *args, changes="", only=False):
    monkeypatch.setenv(early_tier.CHANGED_STATUS_ENV, changes)
    if only:
        monkeypatch.setenv(early_tier.EARLY_ONLY_ENV, "1")
    return pytester.runpytest(
        "-p", "tests.early_tier", "-p", "tests.slow_tier", "-p", "no:cacheprovider",
        "-p", "no:django", "-p", "no:randomly", *args,
    )  # fmt: skip


def test_a_ledger_entry_runs_in_the_fast_tier(project, monkeypatch):
    res = _inner(project, monkeypatch, "-m", "not slow", "-v")
    res.assert_outcomes(passed=3)
    res.stdout.fnmatch_lines(["*test_slow_promoted PASSED*", "*test_slow_pr_tier PASSED*"])
    assert "test_slow_plain" not in res.stdout.str()
    assert "test_pin" not in res.stdout.str()


def test_a_path_rule_runs_its_pins_in_the_fast_tier_only_for_its_change(project, monkeypatch):
    res = _inner(project, monkeypatch, "-m", "not slow", "-v", changes="A\ttests/test_brand_new.py")
    res.stdout.fnmatch_lines(["*test_gate.py::test_pin PASSED*"])
    res.stdout.fnmatch_lines(["*path rules fired: test-module-added-or-removed*"])
    res = _inner(project, monkeypatch, "-m", "not slow", "-v", changes="M\ttests/test_brand_new.py")
    assert "test_pin" not in res.stdout.str()


def test_early_only_runs_exactly_the_ledger_pr_tier_and_rule_pins(project, monkeypatch):
    res = _inner(project, monkeypatch, "-v", changes="A\ttests/test_x.py", only=True)
    res.assert_outcomes(passed=3)
    out = res.stdout.str()
    for name in ("test_slow_promoted", "test_slow_pr_tier", "test_pin"):
        assert f"{name} PASSED" in out, name
    assert "test_fast" not in out and "test_slow_plain" not in out


def test_the_junit_report_carries_node_ids_and_tiers(project, monkeypatch):
    report = project.path / "j.xml"
    _inner(project, monkeypatch, f"--junitxml={report}")
    got = {k: v.tier for k, v in pl.parse_junit([report]).items()}
    assert got == {
        "test_p.py::test_slow_promoted": "early",
        "test_p.py::test_slow_plain": "late",
        "test_p.py::test_fast": "early",
        "test_p.py::test_slow_pr_tier": "early",
        "test_gate.py::test_pin": "late",
    }


# --------------------------------------------------------------------------- #
# The nightly's report job posts the patch; it never pushes
# --------------------------------------------------------------------------- #


def _report_script() -> str:
    wf = yaml.safe_load(WORKFLOW.read_text())
    for step in wf["jobs"]["report"]["steps"]:
        if "github-script" in str(step.get("uses", "")):
            return step["with"]["script"]
    raise AssertionError("no github-script step in the report job")


def _run_report(*, suite_failed: str, ledger_changed: str, open_issues: list[dict]) -> list[dict]:
    if shutil.which("node") is None:
        pytest.skip("needs node")
    harness = textwrap.dedent("""
        const calls = [];
        const OPEN = %s;
        const github = {
          paginate: async () => OPEN,
          rest: {issues: {
            listForRepo: () => {},
            create: async (o) => { calls.push({op: 'create', ...o}); },
            createComment: async (o) => { calls.push({op: 'comment', ...o}); },
            update: async (o) => { calls.push({op: 'update', ...o}); },
          }},
        };
        const core = {summary: {addRaw: () => {}, write: async () => {}}, setFailed: () => {}};
        const context = {serverUrl: 'https://github.com', repo: {owner: 'o', repo: 'r'}, runId: 1};
        Object.assign(process.env, {
          SUITE_RESULT: 'success', FAILED: %s, SUMMARY: '1 failed', MATRIX_RESULT: 'success',
          ON_MAIN: 'true', LEDGER_CHANGED: %s, LEDGER_SUMMARY: '**Promoted** (1)',
          LEDGER_PATCH: '+    "test": "a.py::test_x",',
        });
        (async () => {
        %s
        })().then(() => console.log('@@' + JSON.stringify(calls)));
        """) % (
        json.dumps(open_issues),
        json.dumps(suite_failed),
        json.dumps(ledger_changed),
        textwrap.indent(_report_script(), "        "),
    )
    run = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    (line,) = [x for x in run.stdout.splitlines() if x.startswith("@@")]
    return json.loads(line[2:])


LEDGER_TITLE = "promotion ledger: changes to apply"


def test_a_red_night_attaches_the_promotion_patch_to_the_red_issue():
    calls = _run_report(suite_failed="true", ledger_changed="true", open_issues=[])
    (create,) = [c for c in calls if c["op"] == "create"]
    assert create["title"].startswith("main is RED")
    assert "```diff" in create["body"] and "a.py::test_x" in create["body"]


def test_a_red_night_without_a_ledger_change_posts_no_patch():
    calls = _run_report(suite_failed="true", ledger_changed="false", open_issues=[])
    (create,) = [c for c in calls if c["op"] == "create"]
    assert "```diff" not in create["body"]


def test_a_green_night_proposes_demotions_on_their_own_issue_and_updates_it():
    calls = _run_report(suite_failed="false", ledger_changed="true", open_issues=[])
    assert [(c["op"], c.get("title")) for c in calls] == [("create", LEDGER_TITLE)]
    calls = _run_report(
        suite_failed="false",
        ledger_changed="true",
        open_issues=[{"number": 7, "title": LEDGER_TITLE}],
    )
    assert [(c["op"], c["issue_number"]) for c in calls] == [("update", 7)]


def test_a_green_night_with_nothing_to_change_closes_the_ledger_issue():
    calls = _run_report(
        suite_failed="false",
        ledger_changed="false",
        open_issues=[{"number": 7, "title": LEDGER_TITLE}],
    )
    assert [(c["op"], c.get("state")) for c in calls] == [("comment", None), ("update", "closed")]


def test_the_nightly_cannot_push_and_never_tries_to():
    wf = yaml.safe_load(WORKFLOW.read_text())
    assert wf["permissions"] == {"contents": "read", "issues": "write"}
    assert not any("permissions" in job for job in wf["jobs"].values())
    text = WORKFLOW.read_text()
    assert "git push" not in text and "git commit" not in text
    suite = wf["jobs"]["suite"]["steps"]
    run = next(s for s in suite if s.get("id") == "run")
    assert "--junitxml=/tmp/suite-junit.xml" in run["run"]
    ledger = next(s for s in suite if s.get("id") == "ledger")
    assert "scripts/promotion_ledger.py" in ledger["run"]


# --------------------------------------------------------------------------- #
# The real tree
# --------------------------------------------------------------------------- #


def test_every_ledger_entry_names_a_test_that_exists_and_the_ledger_fits_its_budget():
    ledger = early_tier.load_ledger(ROOT / early_tier.LEDGER_FILE)
    problems = pl.missing_tests(ledger, ROOT)
    assert not problems, (
        "tests/promotion-ledger.json names tests that do not exist. Update the entry to the "
        "new name, or remove it:\n  " + "\n  ".join(problems)
    )
    durations = early_tier.load_durations(ROOT / early_tier.DURATIONS_FILE)
    assert pl.enforce_budget(copy.deepcopy(ledger), durations) == [], "the ledger is over budget"
    for e in ledger["entries"]:
        assert set(e) == {"test", "promoted", "last_failed", "reason", "green_streak", "pinned"}, e


def test_a_renamed_test_fails_the_existence_check_by_name(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_a.py").write_text(
        "class TestA:\n    def test_new_name(self):\n        pass\n"
    )
    ledger = {
        "entries": [
            entry("tests/test_a.py::TestA::test_new_name"),
            entry("tests/test_a.py::TestA::test_old_name"),
            entry("tests/test_gone.py::test_x"),
        ]
    }
    problems = pl.missing_tests(ledger, tmp_path)
    assert len(problems) == 2
    assert "defines no TestA::test_old_name" in problems[0]
    assert "tests/test_gone.py does not exist" in problems[1]


def test_every_path_rule_target_exists_and_the_registry_rule_covers_todays_late_failures():
    for rule in early_tier.PATH_RULES:
        for target in early_tier.expand_targets(rule, ROOT):
            assert (ROOT / target.split("::", 1)[0]).is_file(), (rule.name, target)
    # The pins that failed only in the nightly on 2026-11-12, after a new test file landed, are
    # selected by the registry rule for exactly that change: they need no ledger entry.
    covered = early_tier.selected_by_rules(ROOT, {("A", "tests/test_brand_new_4999.py")})
    assert "tests/test_merge_gates_3757.py" in covered
    assert "tests/test_git_env_guard_3179.py" in covered


def test_pr_ci_hands_the_change_status_to_the_path_rules():
    workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
    steps = [s for job in workflow["jobs"].values() for s in job.get("steps", [])]
    lister = next(
        s for s in steps if s.get("name") == "List the changed paths for the sweep sample"
    )
    assert "--name-status --no-renames" in lister["run"]
    assert "$RUNNER_TEMP/changed-status.txt" in lister["run"]
    shards = [s for s in steps if "--splits 4" in str(s.get("run", ""))]
    assert shards, "no pytest-split shard step found"
    for step in shards:
        assert step["env"][early_tier.CHANGED_STATUS_FILE_ENV].endswith("/changed-status.txt")
