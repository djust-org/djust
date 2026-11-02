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
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_pr_body.py"
GATEOFF = ROOT / "scripts" / "check-pr-body-gateoff.json"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-body-lint.yml"


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


# --------------------------------------------------------------------------- review follow-up


def test_a_fence_opened_on_a_list_item_line_does_not_hide_the_rest_of_the_body():
    body = "- ```\n  code TBD\n  ```\n\nTBD and #99999 after"
    report = check(body)
    assert [(f.line, f.token) for f in report.findings] == [(5, "#99999"), (5, "TBD")]


def test_numbered_and_nested_list_fences_are_recognised_too():
    body = "1. step\n   - ```\n     TBD\n     ```\nreal TBD\n"
    assert [(f.line, f.token) for f in check(body).findings] == [(5, "TBD")]


def test_a_triple_backtick_inline_span_at_line_start_is_not_a_fence():
    assert [f.token for f in check("```code``` then TBD\nand TBD again").findings] == ["TBD", "TBD"]


@pytest.mark.parametrize(
    "url, token",
    [
        (f"https://github.com/{REPO}/pull/NNN", "NNN"),
        (f"https://github.com/{REPO}/actions/runs/XXXXXXXX", "XXXXXXXX"),
        (f"https://github.com/{REPO}/issues/42#issuecomment-TBD", "TBD"),
        ("https://example.org/issues/N", "N"),
        ("https://example.org/a?id=XXX", "XXX"),
    ],
)
def test_a_placeholder_inside_a_url_is_reported(url, token):
    """The #3708 shape: links to comments that did not exist yet."""
    assert [(f.kind, f.token) for f in check(f"see {url} for it").findings] == [
        ("placeholder", token)
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/NASA/issues/1",
        "https://example.org/Nx/Xx/TBDs",
        "https://example.org/path/Name",
        "https://github.com/acme/widgets/actions/runs/123456",
        "https://example.org/a#diff-ABCDEF0123",
    ],
)
def test_ordinary_urls_are_not_placeholders(url):
    assert check(f"see {url} for it", Resolver(existing={("number", "123456")})).ok


def test_a_placeholder_url_on_an_ignored_line_is_skipped():
    assert check(f"https://github.com/{REPO}/pull/NNN pr-body-lint: ignore").ok


def test_distinct_lookups_are_capped_and_the_overflow_is_one_visible_note():
    resolver = Resolver(existing={("number", str(n)) for n in range(1, 1000)})
    body = " ".join(f"#{n}" for n in range(1, 301))
    report = check_with_cap(body, resolver, cap=50)
    assert len(resolver.calls) == 50
    assert report.ok
    notes = [f for f in report.unchecked if f.kind == "too-many-references"]
    assert len(notes) == 1
    assert "250 more were not checked" in notes[0].message and "first 50" in notes[0].message
    assert len(report.unchecked) == 1, "the overflow must not be reported one reference at a time"


def check_with_cap(body, resolver, cap):
    return cpb.check_body(body, resolver, repo=REPO, max_lookups=cap)


def test_the_cap_counts_distinct_references_not_occurrences():
    resolver = Resolver()
    body = "#42 " * 500 + "#1 " * 500
    assert cpb.check_body(body, resolver, repo=REPO, max_lookups=2).unchecked == []
    assert resolver.calls == [("number", "42"), ("number", "1")]


def test_the_default_cap_is_fifty():
    assert cpb.MAX_LOOKUPS == 50
    resolver = Resolver()
    check(" ".join(f"#{n}" for n in range(2000, 2200)), resolver)
    assert len(resolver.calls) == 50


def test_a_finding_past_the_cap_is_not_silently_lost_when_it_is_a_placeholder():
    body = " ".join(f"#{n}" for n in range(1, 100)) + " TBD"
    assert [f.token for f in check(body).findings if f.kind == "placeholder"] == ["TBD"]


@pytest.mark.parametrize(
    "body",
    ["[" * 65536, "<a>" * 21845, "<a" * 32768, "<code>" * 10922, "`` ` " * 13000, "[x][" * 16384],
    ids=["brackets", "anchors", "anchor-openers", "code-tags", "backticks", "refs"],
)
def test_adversarial_bodies_are_linear_not_quadratic(body):
    """Run in a subprocess with a generous bound: a quadratic pattern took 27 s on ``[`` * 65536."""
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('c', {str(SCRIPT)!r})\n"
        "m = importlib.util.module_from_spec(spec); sys.modules['c'] = m; spec.loader.exec_module(m)\n"
        "sys.exit(0 if m.check_body(sys.stdin.read(), lambda k, i: True, repo='a/b').ok else 1)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], input=body, text=True, timeout=20)
    assert done.returncode == 0


def test_a_generic_type_parameter_is_not_the_n_placeholder():
    assert check("a List<N> and Map<K, N>").ok
    assert [f.token for f in check("range <N>").findings] == ["<N>"]


def test_code_and_pre_html_are_masked():
    body = "<code>#NNN TBD</code> <pre>\nTBD #99999\n</pre> but TBD"
    assert [(f.line, f.token) for f in check(body).findings] == [(3, "TBD")]
    assert check("<CODE>TBD</CODE>").ok


def test_an_unclosed_code_tag_does_not_hide_the_rest():
    assert [f.token for f in check("<code>never closed TBD").findings] == ["TBD"]


def test_reference_style_link_text_is_a_label():
    resolver = Resolver()
    assert check("[#12][1] and [see #99999][ref]\n\n[1]: https://x.org", resolver).ok
    assert resolver.calls == []


def test_this_repos_own_owner_repo_form_is_checked():
    report = check(f"see {REPO}#99999 and {REPO.upper()}#3714 and other/repo#99999")
    assert [f.token for f in report.findings] == [f"{REPO}#99999"]


def test_annotations_are_printed_and_cannot_forge_a_workflow_command(tmp_path, capsys):
    body = tmp_path / "b.md"
    # A URL of THIS repo is echoed into the message ("does not resolve"): attacker-controlled text.
    body.write_text(f"Closes #NNN\nhttps://github.com/{REPO}/issues/99999::error::pwned%0Aboom\n")
    assert (
        cpb.main(["--repo", REPO, "--body-file", str(body), "--annotate"], resolver=Resolver()) == 1
    )
    lines = capsys.readouterr().out.splitlines()
    annotations = [ln for ln in lines if ln.startswith("::")]
    assert annotations and all(
        ln.startswith("::warning title=pr-body-lint::") for ln in annotations
    )
    assert all("::" not in ln[len("::warning title=pr-body-lint::") :] for ln in annotations)
    assert any("pwned" in ln for ln in annotations), "the hostile URL never reached the message"
    assert not any("%0A" in ln for ln in annotations), "a raw %0A would split the command"
    assert not any(ln.lstrip().startswith("::error") for ln in lines)


def test_without_annotate_no_workflow_command_is_printed(tmp_path, capsys):
    body = tmp_path / "b.md"
    body.write_text("Closes #NNN\n")
    cpb.main(["--repo", REPO, "--body-file", str(body)], resolver=Resolver())
    assert not any(ln.startswith("::") for ln in capsys.readouterr().out.splitlines())


def test_a_missing_gh_for_pr_mode_is_a_clean_exit_two(monkeypatch, capsys):
    def boom(*a, **k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(cpb.subprocess, "run", boom)
    assert cpb.main(["--repo", REPO, "--pr", "1"], resolver=Resolver()) == 2
    assert "could not run gh" in capsys.readouterr().err


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
#
# What keeps this job from blocking a merge is ``continue-on-error`` here plus the branch
# protection's required-checks list, which no test can see; the job lives in its own file, so
# it can never appear in ``test.yml``'s ``test-summary.needs`` and asserting its absence there
# would be vacuous. These tests pin what IS in this file: the trigger, the token, and that no
# text an author controls reaches a shell.


def _workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _job():
    return _workflow()["jobs"]["pr-body-lint"]


def test_the_trigger_is_pull_request_and_only_pull_request():
    """``pull_request_target`` would run with a writable token and secrets on a fork PR."""
    wf = _workflow()
    triggers = wf.get("on", wf.get(True))  # YAML 1.1 reads a bare ``on`` as True
    assert set(triggers) == {"pull_request"}
    assert set(triggers["pull_request"]["types"]) == {"opened", "edited", "reopened", "synchronize"}


def test_the_token_is_read_only():
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read", "issues": "read", "pull-requests": "read"}
    assert "permissions" not in _job(), "a job-level map could widen the workflow's"
    assert "secrets." not in WORKFLOW.read_text().replace("secrets.GITHUB_TOKEN", "")


def test_no_expression_is_interpolated_into_any_shell_command():
    """A PR body, title or branch name is attacker-controlled text: never ``${{ }}`` into ``run:``."""
    for step in _job()["steps"]:
        assert "${{" not in step.get("run", ""), step
        for value in step.get("with", {}).values():
            assert "pull_request" not in str(value) and "head_ref" not in str(value), step


def test_the_body_reaches_the_script_only_through_the_environment():
    job = _job()
    run = "\n".join(step.get("run", "") for step in job["steps"])
    env = {k: v for step in job["steps"] for k, v in step.get("env", {}).items()}
    assert env == {
        "GH_TOKEN": "${{ github.token }}",
        "PR_BODY": "${{ github.event.pull_request.body }}",
    }
    assert "--body-env PR_BODY" in run and "--annotate" in run


def test_the_job_is_non_gating_and_bounded():
    job = _job()
    assert job["continue-on-error"] is True
    assert job["timeout-minutes"] <= 10


# --------------------------------------------------------------------------- the gate-off list


def test_every_gateoff_mutation_text_still_applies():
    spec = json.loads(GATEOFF.read_text())
    assert spec["mutations"]
    for entry in spec["mutations"]:
        source = (ROOT / entry["file"]).read_text()
        found = source.count(entry["old"])
        assert found == entry.get("expect", 1), f"{entry['label']!r}: found {found} time(s)"
        assert entry["old"] != entry["new"]
