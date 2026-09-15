"""#2849 — resolve the checkable references in changelog.d/ fragments.

A fragment citing a test class name or file path that does not exist is the
same defect class as a doc citing a method that does not exist (#2652): the
claim contradicts the tree, and five shipped PRs carried such claims with
nothing catching them. ``scripts/check-changelog-fragment-references.py``
checks the mechanical subset: backtick-quoted paths and Test-class names.

Count claims (``N regression cases in `<file>` ``) are NOT re-checked here —
``scripts/check-changelog-test-counts.py`` already scans fragments (see
tests/test_changelog_test_counts.py).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-changelog-fragment-references.py"


def _load_check():
    spec = importlib.util.spec_from_file_location("check_changelog_fragment_references", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec: dataclass processing looks the module up in
    # sys.modules by name.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


check = _load_check()


def _run_script() -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)


class TestCurrentFragmentsResolve:
    """The false-positive measurement for #2849, pinned: every fragment on
    the tree must pass. A fragment committed with an unresolvable claim is
    the defect, so this going red is correct, not noise."""

    def test_all_current_fragments_pass_in_process(self):
        fragments = sorted(p for p in check.FRAGMENT_DIR.glob("*.md") if p.name != "README.md")
        assert fragments, "no fragments found — the corpus disappeared?"
        assert check.check_fragments(REPO_ROOT, fragments) == 0

    def test_all_current_fragments_pass_as_subprocess(self):
        assert _run_script().returncode == 0


def _fragment(repo_root: Path, body: str) -> Path:
    f = repo_root / "changelog.d" / "9999.probe.md"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(body, encoding="utf-8")
    return f


class TestPathResolution:
    def test_missing_path_is_flagged(self, tmp_path):
        frag = _fragment(tmp_path, "- Fixes it. Cases in `python/djust/tests/test_nope_9999.py`.\n")
        assert check.check_fragments(tmp_path, [frag]) == 1

    def test_root_relative_path_resolves(self, tmp_path):
        (tmp_path / "tests" / "js").mkdir(parents=True)
        (tmp_path / "tests" / "js" / "x_9999.test.js").write_text("//\n")
        frag = _fragment(tmp_path, "- Fixes it. Cases in `tests/js/x_9999.test.js`.\n")
        assert check.check_fragments(tmp_path, [frag]) == 0

    def test_docs_website_relative_shorthand_resolves(self, tmp_path):
        # Fragments cite doc pages both root-relative and website-relative
        # (`core-concepts/events.md` lives under docs/website/).
        (tmp_path / "docs" / "website" / "core-concepts").mkdir(parents=True)
        (tmp_path / "docs" / "website" / "core-concepts" / "events.md").write_text("#\n")
        frag = _fragment(tmp_path, "- Documented in `core-concepts/events.md`.\n")
        assert check.check_fragments(tmp_path, [frag]) == 0

    def test_module_relative_path_under_python_djust_resolves(self, tmp_path):
        (tmp_path / "python" / "djust" / "theming").mkdir(parents=True)
        (tmp_path / "python" / "djust" / "theming" / "_types.py").write_text("#\n")
        frag = _fragment(tmp_path, "- Defined in `theming/_types.py`.\n")
        assert check.check_fragments(tmp_path, [frag]) == 0

    def test_non_path_backtick_tokens_are_ignored(self, tmp_path):
        body = (
            "- `ViewRuntime.dispatch_mount` and `#1612` and bare `manage.py`, "
            "plus `<div>x</div>` markup.\n"
        )
        frag = _fragment(tmp_path, body)
        assert check.check_fragments(tmp_path, [frag]) == 0


class TestClassResolution:
    def test_missing_test_class_is_flagged(self, tmp_path):
        # The #2554 shape: "two new cases in `TestCustomFilters`" where the
        # class does not exist anywhere in the tree.
        frag = _fragment(tmp_path, "- Two new cases in `TestCustomFilters`.\n")
        result = check.check_fragments(tmp_path, [frag])
        assert result == 1

    def test_existing_test_class_resolves(self, tmp_path):
        pkg = tmp_path / "python" / "tests"
        pkg.mkdir(parents=True)
        (pkg / "test_probe_9999.py").write_text(
            "class TestProbe9999:\n    def test_x(self):\n        pass\n",
            encoding="utf-8",
        )
        frag = _fragment(tmp_path, "- New cases in `TestProbe9999`.\n")
        assert check.check_fragments(tmp_path, [frag]) == 0

    def test_flagged_report_names_the_class(self, tmp_path, capsys):
        frag = _fragment(tmp_path, "- Two new cases in `TestCustomFilters`.\n")
        check.check_fragments(tmp_path, [frag])
        err = capsys.readouterr().err
        assert "`TestCustomFilters`" in err
        assert "no `class <name>` definition" in err

    @pytest.mark.parametrize(
        "token",
        ["TestDispatchSingleEventParity2847", "TestLiveViewTestClient"],
    )
    def test_real_corpus_classes_resolve(self, token):
        # Sanity: the resolver finds the real classes the current corpus cites.
        assert token in check._defined_test_classes(REPO_ROOT)


@pytest.mark.parametrize(
    "target",
    ["path", "class"],
)
def test_missing_reference_names_fragment_line_and_reference(tmp_path, target, capsys):
    if target == "path":
        body = "- Cases in `python/djust/tests/test_nope_9999.py`.\n"
    else:
        body = "- Cases in `TestNope9999`.\n"
    frag = _fragment(tmp_path, body)
    assert check.check_fragments(tmp_path, [frag]) == 1
    err = capsys.readouterr().err
    assert "9999.probe.md:1:" in err
    assert "`TestNope9999`" in err or "test_nope_9999.py" in err
