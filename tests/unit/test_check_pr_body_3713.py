"""``scripts/check_pr_body.py``: lint a PR body for placeholders and dangling references (#3713).

The logic is a pure function with the resolver injected, so nothing here touches the
network. The empirical canary (CLAUDE.md, #1459) is ``TestCanary``: a synthetic body
carrying each placeholder class and a dangling ``#999999999`` must be reported, and a
clean body must pass. ``scripts/check-pr-body-gateoff.json`` runs ``gate_off.py``
against this file (each rule dropped in turn must turn a test red).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_pr_body.py"
GATEOFF = ROOT / "scripts" / "check-pr-body-gateoff.json"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-body-lint.yml"
TEST_YML = ROOT / ".github" / "workflows" / "test.yml"


def _load():
    spec = importlib.util.spec_from_file_location("check_pr_body_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_pr_body_under_test"] = module
    spec.loader.exec_module(module)
    return module


cpb = _load()

REPO = "acme/widgets"
EXISTING = {("number", "1"), ("number", "3714"), ("number", "42"), ("comment", "555")}


class Resolver:
    """Resolves what is in EXISTING; records every lookup; ``flaky`` ids answer None."""

    def __init__(self, existing=EXISTING, flaky=()):
        self.existing, self.flaky, self.calls = existing, set(flaky), []

    def __call__(self, kind, ident):
        self.calls.append((kind, ident))
        if ident in self.flaky:
            return None
        return (kind, ident) in self.existing


def check(body, resolver=None, repo=REPO):
    return cpb.check_body(body, resolver or Resolver(), repo=repo)


def tokens(report):
    return [(f.kind, f.token) for f in report.findings]


# --------------------------------------------------------------------------- placeholders


@pytest.mark.parametrize(
    "text, token",
    [
        ("Fixes the thing. TBD", "TBD"),
        ("see #TBD", "TBD"),
        ("coverage: XXX", "XXX"),
        ("range <N>", "<N>"),
        ("added N tests for it", "N tests"),
        ("Closes #NNN", "#NNN"),
        ("Closes #N", "#N"),
    ],
)
def test_each_placeholder_class_is_reported(text, token):
    report = check(text)
    assert [f.token for f in report.findings if f.kind == "placeholder"] == [token]
    assert all(f.kind == "placeholder" for f in report.findings)


@pytest.mark.parametrize(
    "text",
    [
        "TBDs are fine in a word like OUTBD",
        "the Nth test, N-gram, 4 tests, Nested, #Normal",
        "wrote XX and X and XY",
        "<Name> <issue>.<section>.md",
    ],
)
def test_ordinary_prose_is_not_a_placeholder(text):
    assert check(text).ok


def test_the_report_carries_the_line_number():
    report = check("clean line\n\nsecond paragraph\nCloses #NNN\n")
    assert [(f.line, f.token) for f in report.findings] == [(4, "#NNN")]


# --------------------------------------------------------------------------- references


def test_a_dangling_number_reference_is_reported():
    report = check("Closes #999999999")
    assert tokens(report) == [("dangling-number", "#999999999")]


def test_a_resolving_number_reference_passes():
    assert check("Closes #3714\nCloses #42").ok


def test_closes_lines_are_checked_like_any_reference():
    report = check("Closes #3714\nCloses #3715\n")
    assert tokens(report) == [("dangling-number", "#3715")]
    assert report.findings[0].line == 2


def test_a_bare_issuecomment_that_does_not_resolve_is_reported():
    assert tokens(check("see issuecomment-999")) == [("dangling-comment", "issuecomment-999")]


def test_a_bare_issuecomment_that_resolves_passes():
    assert check("see issuecomment-555").ok


def test_an_issue_url_of_this_repo_is_checked():
    body = f"see https://github.com/{REPO}/issues/3715 and https://github.com/{REPO}/pull/42"
    report = check(body)
    assert [f.token for f in report.findings] == ["#3715"]


def test_a_comment_link_of_this_repo_is_checked_for_both_the_issue_and_the_comment():
    ok = f"https://github.com/{REPO}/issues/3714#issuecomment-555"
    assert check(ok).ok
    bad_comment = f"https://github.com/{REPO}/issues/3714#issuecomment-556"
    assert tokens(check(bad_comment)) == [("dangling-comment", "issuecomment-556")]
    bad_issue = f"https://github.com/{REPO}/issues/3716#issuecomment-555"
    assert tokens(check(bad_issue)) == [("dangling-number", "#3716")]


def test_urls_of_other_repos_are_not_checked_at_all():
    resolver = Resolver()
    body = "https://github.com/django/django/issues/99999#issuecomment-123456 and https://x.org/p#7"
    assert check(body, resolver).ok
    assert resolver.calls == []


def test_the_url_match_is_case_insensitive_about_the_repo():
    assert tokens(check("https://github.com/ACME/Widgets/issues/3716")) == [
        ("dangling-number", "#3716")
    ]


# --------------------------------------------------------------------------- false positives


def test_owner_repo_hash_number_is_another_repos_number():
    resolver = Resolver()
    assert check("upstream django/django#99999 and acme/other#7", resolver).ok
    assert resolver.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "C#12 and F#9",
        "html entity &#35; and &#9999;",
        "anchor page#12 and a/b#34",
        "a path docs/#12 and src/#7",
        "colour #123abc",
        "see [the docs](https://example.org/page#2) and <https://example.org/x#5>",
        "a url https://example.org/a#99999 inline",
    ],
)
def test_hash_digits_that_are_not_references_are_ignored(text):
    resolver = Resolver()
    assert check(text, resolver).ok
    assert resolver.calls == []


def test_the_text_of_a_link_is_a_label_not_a_reference():
    """Found dogfooding the last 40 merged PRs: a Dependabot body (#3681) links other repos'
    issues as ``<a href=".../Rich-Harris/magic-string/issues/363">#363</a>``."""
    resolver = Resolver()
    body = (
        '<li>respect the offset (<a href="https://redirect.github.com/Rich-Harris/magic-string'
        '/issues/363">#363</a>)</li> and [#364](https://example.org/x) and [see #365](https://e.org)'
    )
    assert check(body, resolver).ok
    assert resolver.calls == []


def test_a_link_to_this_repo_is_checked_by_its_target_not_its_label():
    bad = f"[#42](https://github.com/{REPO}/issues/999999)"
    assert tokens(check(bad)) == [("dangling-number", "#999999")]
    assert check(f"[anything](https://github.com/{REPO}/issues/42)").ok


def test_a_placeholder_in_link_text_is_still_a_placeholder():
    assert tokens(check("[#NNN](https://example.org) and <a href='x'>TBD</a>")) == [
        ("placeholder", "#NNN"),
        ("placeholder", "TBD"),
    ]


def test_a_bare_reference_outside_link_text_is_still_checked():
    assert tokens(check("[label](https://e.org) then #999999")) == [("dangling-number", "#999999")]


def test_fenced_code_is_ignored_including_tildes_and_unterminated():
    body = "text\n```\nTBD #999999999 N tests\n```\nafter\n~~~python\nXXX #888\n~~~\n"
    assert check(body).ok
    assert check("open fence\n```\nTBD #999999999 never closed").ok


def test_a_longer_fence_is_not_closed_by_a_shorter_one():
    body = "````\n```\nTBD\n```\nstill code TBD\n````\nreal TBD\n"
    assert [(f.line, f.token) for f in check(body).findings] == [(7, "TBD")]


def test_a_fence_marker_inside_prose_does_not_open_a_block():
    assert tokens(check("say ``` inline TBD")) == [("placeholder", "TBD")]


def test_inline_code_is_ignored():
    resolver = Resolver()
    body = "the token `TBD`, the ref `#999999999`, double ``a ` TBD`` and `N tests`"
    assert check(body, resolver).ok
    assert resolver.calls == []


def test_prose_next_to_inline_code_is_still_checked():
    assert tokens(check("`ok` then TBD")) == [("placeholder", "TBD")]


def test_html_comments_are_ignored_including_unterminated_ones():
    template = "<!-- Describe the change: TBD, closes #999999999, N tests -->\nreal body\n"
    assert check(template).ok
    assert check("body\n<!-- never closed TBD #999999999").ok


def test_the_ignore_marker_skips_only_its_own_line():
    body = "upstream says #999999 <!-- pr-body-lint: ignore -->\nbut #999998\n"
    report = check(body)
    assert [(f.line, f.token) for f in report.findings] == [(2, "#999998")]


def test_placeholder_on_an_ignored_line_is_skipped_too():
    assert check("TBD pr-body-lint: ignore").ok


# --------------------------------------------------------------------------- resolver contract


def test_a_lookup_that_could_not_be_made_is_unchecked_not_a_failure():
    report = check("Closes #7777", Resolver(flaky={"7777"}))
    assert report.ok
    assert [f.token for f in report.unchecked] == ["#7777"]


def test_each_reference_is_looked_up_once():
    resolver = Resolver()
    check("#42 #42\n#42 and https://github.com/acme/widgets/issues/42", resolver)
    assert resolver.calls == [("number", "42")]


def test_without_a_repo_urls_are_not_treated_as_this_repos():
    resolver = Resolver()
    assert check("https://github.com/acme/widgets/issues/99999", resolver, repo="").ok
    assert resolver.calls == []


def test_findings_are_sorted_by_line():
    report = check("#999999 \nTBD\n#999998")
    assert [f.line for f in report.findings] == [1, 2, 3]


# --------------------------------------------------------------------------- canary (#1459)

SYNTHETIC_BAD = """\
## Summary

Adds the gate. TBD about the count: XXX, range <N>, N tests added.
Closes #NNN
Closes #999999999
See https://github.com/acme/widgets/issues/3714#issuecomment-123456789 for the discussion.

```
TBD in a code block is fine
```
"""

CLEAN = """\
## Summary

Adds the gate. 12 tests in `tests/unit/test_x.py`; `TBD` is a token the lint looks for.
Closes #3714
Upstream django/django#12345 is the origin. See https://github.com/acme/widgets/issues/3714#issuecomment-555.
"""


class TestCanary:
    def test_the_synthetic_bad_body_reports_every_class(self):
        report = check(SYNTHETIC_BAD)
        assert sorted(tokens(report)) == sorted(
            [
                ("placeholder", "TBD"),
                ("placeholder", "XXX"),
                ("placeholder", "<N>"),
                ("placeholder", "N tests"),
                ("placeholder", "#NNN"),
                ("dangling-number", "#999999999"),
                ("dangling-comment", "issuecomment-123456789"),
            ]
        )

    def test_the_clean_body_passes(self):
        report = check(CLEAN)
        assert report.ok and not report.unchecked

    def test_cli_exit_codes(self, tmp_path, capsys):
        bad, good = tmp_path / "bad.md", tmp_path / "good.md"
        bad.write_text(SYNTHETIC_BAD)
        good.write_text(CLEAN)
        resolver = Resolver()
        assert cpb.main(["--repo", REPO, "--body-file", str(bad)], resolver=resolver) == 1
        assert "7 finding(s)" in capsys.readouterr().out
        assert cpb.main(["--repo", REPO, "--body-file", str(good)], resolver=resolver) == 0
        assert "0 finding(s)" in capsys.readouterr().out

    def test_cli_reads_the_body_from_an_environment_variable(self, monkeypatch):
        monkeypatch.setenv("PR_BODY_UNDER_TEST", "Closes #NNN")
        assert (
            cpb.main(["--repo", REPO, "--body-env", "PR_BODY_UNDER_TEST"], resolver=Resolver()) == 1
        )
        monkeypatch.delenv("PR_BODY_UNDER_TEST")
        assert (
            cpb.main(["--repo", REPO, "--body-env", "PR_BODY_UNDER_TEST"], resolver=Resolver()) == 2
        )


# --------------------------------------------------------------------------- the gh resolver


class _Proc:
    def __init__(self, returncode, stderr=""):
        self.returncode, self.stderr = returncode, stderr


@pytest.mark.parametrize(
    ("proc", "expected"),
    [
        (_Proc(0), True),
        (_Proc(1, "gh: Not Found (HTTP 404)"), False),
        (_Proc(1, "gh: API rate limit exceeded (HTTP 403)"), None),
    ],
)
def test_gh_resolver_maps_the_api_answer(monkeypatch, proc, expected):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return proc

    monkeypatch.setattr(cpb.subprocess, "run", fake_run)
    resolve = cpb.gh_resolver(REPO)
    assert resolve("number", "12") is expected
    assert seen[0][:3] == ["gh", "api", f"repos/{REPO}/issues/12"]
    assert resolve("comment", "34") is expected
    assert seen[1][2] == f"repos/{REPO}/issues/comments/34"


def test_gh_resolver_reports_a_missing_gh_as_unchecked(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(cpb.subprocess, "run", boom)
    assert cpb.gh_resolver(REPO)("number", "1") is None


# --------------------------------------------------------------------------- the workflow


def _job():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["pr-body-lint"]


def test_the_workflow_is_non_gating():
    """rc4 finding #3: a new CI job is ``continue-on-error`` and out of the aggregate gate."""
    assert _job()["continue-on-error"] is True
    summary = yaml.safe_load(TEST_YML.read_text())["jobs"]["test-summary"]
    assert "pr-body-lint" not in summary["needs"]
    assert "pr-body-lint" not in "\n".join(s.get("run", "") for s in summary["steps"])


def test_the_workflow_passes_the_body_through_the_environment_not_the_shell():
    """A PR body is attacker-controlled text: never ``${{ }}``-interpolate it into ``run:``."""
    job = _job()
    run = "\n".join(step.get("run", "") for step in job["steps"])
    assert "github.event.pull_request" not in run
    env = {k: v for step in job["steps"] for k, v in step.get("env", {}).items()}
    assert env["PR_BODY"] == "${{ github.event.pull_request.body }}"
    assert "--body-env PR_BODY" in run


# --------------------------------------------------------------------------- the gate-off list


def test_every_gateoff_mutation_text_still_applies():
    spec = json.loads(GATEOFF.read_text())
    assert spec["mutations"]
    for entry in spec["mutations"]:
        source = (ROOT / entry["file"]).read_text()
        found = source.count(entry["old"])
        assert found == entry.get("expect", 1), f"{entry['label']!r}: found {found} time(s)"
        assert entry["old"] != entry["new"]
