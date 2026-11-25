"""The pre-push test selector, exercised on the pure function (#2526).

``scripts/select-tests.py`` decides which pytest files (and cargo packages) a
pushed range can affect. The selection is a pure function of the changed
paths, the existing test files and a text reader, so these tests need no git
fixture: they hand it lists and a dict-backed reader. The CLI (which touches
git) is exercised once, against this repo, only to prove it produces the same
shape the hook parses.

Every rule the script's docstring states has a test here that goes red when
that rule is removed — rule (c) in particular (basename mentions in test
text), because it is the rule that selects the Rust source-pin tests and it
caught two broken pins on 2026-09-02.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """This module runs git, directly or through a script that does. Under a
    git hook an inherited GIT_DIR / GIT_INDEX_FILE would aim those commands at
    the real repository (#2608, #3179)."""
    from tests.git_env import GIT_EXECUTION_VARS

    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "select-tests.py"


def _load():
    spec = importlib.util.spec_from_file_location("select_tests", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # Registered before exec: `from __future__ import annotations` + dataclass
    # resolves the module's globals through sys.modules.
    sys.modules["select_tests"] = mod
    spec.loader.exec_module(mod)
    return mod


st = _load()

TESTS = [
    "tests/unit/test_forms.py",
    "tests/unit/test_jit_codegen.py",
    "python/tests/test_renderer_pins.py",
    "python/tests/test_escape_2548.py",
    "python/djust/tests/test_websocket_mount.py",
    "tests/test_check_doc_snippets.py",
]
TEXTS = {
    "tests/unit/test_forms.py": "from djust.forms import FormMixin\n",
    "tests/unit/test_jit_codegen.py": "import djust.mixins.jit as jit\n",
    "python/tests/test_renderer_pins.py": 'src = (SRC / "renderer.rs").read_text()\n',
    "python/tests/test_escape_2548.py": "from djust import LiveView\n",
    "python/djust/tests/test_websocket_mount.py": "from djust.websocket import LiveViewConsumer\n",
    "tests/test_check_doc_snippets.py": 'run("scripts/check-doc-snippets.py")\n',
}


def _select(changed, branch="feat/anything", tests=TESTS, texts=TEXTS):
    return st.select_tests(changed, tests, lambda p: texts[p], branch)


# --------------------------------------------------------------------------
# (a) changed tests
# --------------------------------------------------------------------------


def test_a_changed_test_file_selects_itself():
    sel = _select(["tests/unit/test_forms.py"])
    assert not sel.full
    assert sel.tests == ["tests/unit/test_forms.py"]


def test_a_deleted_test_file_is_not_selected():
    # A path in the diff that no longer exists (a removed test) must not be
    # handed to pytest, which would abort the whole session on it.
    sel = _select(["tests/unit/test_gone.py", "tests/unit/test_forms.py"])
    assert sel.full
    assert "tests/unit/test_gone.py" in sel.reason
    assert "tests/unit/test_gone.py" not in sel.tests


@pytest.mark.parametrize(
    "path",
    [
        "tests/playwright/test_table_select.py",
        "tests/playwright/test_browser_smoke.py",
    ],
)
def test_a_manual_only_test_dirs_are_not_test_files(path):
    # #2781: pytest's own addopts (`--ignore=tests/playwright`,
    # `--ignore=tests/js`) only apply to pytest's directory-walk collection —
    # NOT to a node id passed explicitly. Before this fix, `is_test_file`
    # classified a file under `tests/playwright/` as a test file just like any
    # other `test_*.py`; `_all_test_files()` (which walks TEST_ROOTS through
    # this same predicate) then put it in the "existing tests" set, and rule
    # (a) selected it as a "changed test file" — handing it to pytest as an
    # explicit path, where it WAS collected and run despite the ignore.
    # `tests/playwright/test_table_select.py`'s top-level
    # `async def test_table_select()` (a standalone Playwright script, not a
    # pytest case) failed the pre-push run outright.
    assert not st.is_test_file(path), (
        f"{path} must not be classified as a test file — it lives under a "
        "manual-only dir pytest's own addopts already ignore"
    )


def test_a_manual_only_test_dirs_are_never_selected():
    # With `is_test_file` fixed, `_all_test_files()` never puts a
    # `tests/playwright/` file in the "existing tests" set — the realistic
    # shape (unlike a test that force-injects the path into `tests=`, which
    # would make rule (b) select it via its own basename trivially matching
    # its own stem). Changing ONLY such a file, with no pytest test naming it,
    # selects NO pytest tests (NONE) — never the manual file itself, and no
    # longer the whole suite: its own CI job is what runs it.
    path = "tests/playwright/test_table_select.py"
    sel = _select([path])
    assert path not in sel.tests
    assert not sel.full and sel.tests == [], sel.reason


@pytest.mark.parametrize(
    "path",
    [
        "tests/playwright/test_combobox_escape_blur.py",
        "tests/playwright/conftest_helpers.py",
        "tests/js/dj_flip.test.js",
        "changelog.d/3590.fixed.md",
    ],
)
def test_no_pytest_consumer_paths_do_not_force_full(path):
    # A Playwright/JS test or a changelog fragment changed alongside a module
    # must not escalate the push to all ~49k tests ("shared package/test
    # support" fired for tests/playwright/*.py because is_test_file excludes
    # that dir). The module's own tests are still selected.
    sel = _select(["python/djust/forms.py", path])
    assert not sel.full, sel.reason
    assert sel.tests == ["tests/unit/test_forms.py"]


def test_only_no_pytest_consumer_paths_select_none():
    sel = _select(["tests/playwright/test_transient_save_recovery.py", "changelog.d/3590.fixed.md"])
    assert not sel.full, sel.reason
    assert sel.tests == []
    assert "no pytest consumer" in sel.reason


def test_a_pytest_test_naming_a_no_consumer_path_is_still_selected():
    # Rule (c) still applies: a pytest pin that reads a Playwright file by name
    # must run when that file changes.
    pin = "tests/unit/test_playwright_inventory.py"
    sel = _select(
        ["tests/playwright/test_table_select.py"],
        tests=[*TESTS, pin],
        texts={**TEXTS, pin: 'PATHS = ["tests/playwright/test_table_select.py"]\n'},
    )
    assert not sel.full, sel.reason
    assert sel.tests == [pin]


def test_no_consumer_paths_do_not_mask_an_unmapped_change():
    # Only the no-consumer paths are exempt: an unmapped doc change next to one
    # still means "cannot tell" -> FULL.
    sel = _select(["changelog.d/3590.fixed.md", "docs/website/guides/loading-states.md"])
    assert sel.full


# --------------------------------------------------------------------------
# (b) modules: stem in the test name, or an import in its text
# --------------------------------------------------------------------------


def test_b_module_stem_in_test_name():
    sel = _select(["python/djust/forms.py"])
    assert "tests/unit/test_forms.py" in sel.tests


def test_b_module_imported_by_from_import():
    # Rename the test so only the import can match it.
    tests = ["tests/unit/test_validation.py"]
    texts = {"tests/unit/test_validation.py": "from djust.forms import FormMixin\n"}
    sel = _select(["python/djust/forms.py"], tests=tests, texts=texts)
    assert sel.tests == ["tests/unit/test_validation.py"]


def test_b_module_imported_by_dotted_path():
    tests = ["tests/unit/test_codegen.py"]
    texts = {"tests/unit/test_codegen.py": "import djust.mixins.jit as jit\n"}
    sel = _select(["python/djust/mixins/jit.py"], tests=tests, texts=texts)
    assert sel.tests == ["tests/unit/test_codegen.py"]


def test_b_package_init_maps_to_the_package():
    assert st.module_names("python/djust/mixins/__init__.py") == ("mixins", "djust.mixins")
    assert st.module_names("python/djust/mixins/jit.py") == ("jit", "djust.mixins.jit")
    assert st.module_names("scripts/check-x.py") == ("check-x", None)


def test_b_unrelated_module_selects_nothing_of_the_others():
    tests = ["tests/unit/test_forms.py", "python/djust/tests/test_websocket_mount.py"]
    sel = _select(["python/djust/presence.py"], tests=tests)
    # Nothing names or imports presence -> empty -> FULL (rule d), not a
    # spurious partial run.
    assert sel.full
    assert "empty" in sel.reason


# --------------------------------------------------------------------------
# (c) basename mentions — the Rust source-pin rule
# --------------------------------------------------------------------------


def test_engine_crates_force_the_full_suite() -> None:
    """A change under djust_templates or djust_vdom is exercised by every test
    that renders, not only by the tests naming the file (#2575 review)."""
    for path in (
        "crates/djust_templates/src/filters.rs",
        "crates/djust_vdom/src/diff.rs",
        "crates/djust_core/src/value.rs",
    ):
        sel = _select([path])
        assert sel.full, path


def test_c_rust_basename_selects_the_pin_test():
    """The rule that caught two broken pins on 2026-09-02. Gate-off target."""
    sel = _select(["crates/djust_components/src/renderer.rs"])
    assert not sel.full
    assert sel.tests == ["python/tests/test_renderer_pins.py"]


def test_c_basename_match_is_whole_token():
    # `renderer.rs` must not match `xrenderer.rs` or `renderer.rsx`.
    tests = ["python/tests/test_a.py", "python/tests/test_b.py"]
    texts = {
        "python/tests/test_a.py": 'read("xrenderer.rs")\n',
        "python/tests/test_b.py": 'read("renderer.rsx")\n',
    }
    sel = _select(["crates/djust_components/src/renderer.rs"], tests=tests, texts=texts)
    assert sel.full  # empty selection -> full, nothing falsely matched


def test_c_basename_match_inside_a_path_string():
    tests = ["python/tests/test_a.py"]
    texts = {"python/tests/test_a.py": 'Path("crates/djust_components/src/context.rs")\n'}
    sel = _select(["crates/djust_components/src/context.rs"], tests=tests, texts=texts)
    assert sel.tests == ["python/tests/test_a.py"]


def test_c_applies_to_non_rust_files_too():
    sel = _select(["scripts/check-doc-snippets.py"])
    assert "tests/test_check_doc_snippets.py" in sel.tests


# --------------------------------------------------------------------------
# (d) full-suite triggers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "tests/conftest.py",
        "python/djust/tests/conftest.py",
        "pyproject.toml",
        "pytest.ini",
        ".pre-commit-config.yaml",
        "scripts/pre-push-pytest.sh",
        "scripts/select-tests.py",
        "python/djust/__init__.py",
        "python/djust/apps.py",
        "crates/djust_core/src/value.rs",
        "crates/djust_core/src/lib.rs",
    ],
)
def test_d_full_suite_files(path):
    sel = _select([path, "tests/unit/test_forms.py"])
    assert sel.full, path
    assert path in sel.reason


def test_d_djust_core_tests_dir_does_not_trigger():
    # Only crates/djust_core/src/ is the whole-engine trigger; a djust_core
    # integration test is a plain Rust file.
    sel = _select(["crates/djust_core/tests/value_roundtrip.rs"])
    assert sel.full and "empty" in sel.reason  # no pytest mentions it


@pytest.mark.parametrize(
    "branch", ["feat/flip-mount", "fix/ws-routing", "adr022-convergence-3", "FLIP"]
)
def test_d_branch_name_triggers(branch):
    sel = _select(["tests/unit/test_forms.py"], branch=branch)
    assert sel.full
    assert branch in sel.reason


def test_d_empty_range_is_full():
    sel = _select([])
    assert sel.full


def test_d_empty_selection_is_full():
    sel = _select(["docs/website/guides/loading-states.md"])
    assert sel.full
    assert "empty" in sel.reason


def test_selection_is_sorted_and_deduplicated():
    sel = _select(["python/djust/forms.py", "tests/unit/test_forms.py", "tests/unit/test_forms.py"])
    assert sel.tests == sorted(set(sel.tests))


def test_reader_errors_require_full():
    def boom(_p):
        raise OSError("unreadable")

    sel = st.select_tests(["crates/djust_components/src/renderer.rs"], TESTS, boom, "x")
    assert sel.full and "unreadable" in sel.reason


# --------------------------------------------------------------------------
# cargo
# --------------------------------------------------------------------------

REV = {
    "djust_core": {"djust_vdom", "djust_templates", "djust_live"},
    "djust_vdom": {"djust_templates", "djust_live"},
}


def test_cargo_changed_crate_plus_dependents():
    ws, reason, pkgs = st.select_crates(["crates/djust_vdom/src/diff.rs"], REV)
    assert not ws
    assert pkgs == ["djust_live", "djust_templates", "djust_vdom"]


@pytest.mark.parametrize("path", ["Cargo.toml", "Cargo.lock", "crates/djust_core/src/value.rs"])
def test_cargo_workspace_triggers(path):
    ws, reason, pkgs = st.select_crates([path, "crates/djust_vdom/src/diff.rs"], REV)
    assert ws and pkgs == []


def test_cargo_no_crate_files():
    ws, reason, pkgs = st.select_crates(["python/djust/forms.py"], REV)
    assert not ws and pkgs == []


def test_cargo_reverse_deps_read_from_the_real_workspace():
    rev = st.read_reverse_deps(ROOT)
    # djust_core is depended on by every other crate (Cargo.toml path deps).
    assert rev["djust_core"] >= {"djust_vdom", "djust_templates", "djust_live"}


# --------------------------------------------------------------------------
# the CLI shape the hook parses
# --------------------------------------------------------------------------


def test_cli_prints_full_or_paths_and_the_reason_on_stderr():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--from", "HEAD", "--to", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    # HEAD...HEAD is an empty range -> FULL, and the reason is on stderr.
    assert r.stdout.strip() == "FULL"
    assert "select-tests:" in r.stderr


def test_cli_cargo_prints_workspace_for_an_empty_range():
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--cargo", "--from", "HEAD", "--to", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    # No crate files changed -> an empty package list; the hook then runs the
    # workspace. (`--workspace` is printed only for the Cargo.*/djust_core case.)
    assert r.stdout.strip() == ""


# Safety: a nonempty union is insufficient evidence for the whole diff.
@pytest.mark.parametrize(
    "unknown", ["docs/new-contract.md", "python/djust/new_helper.py", "tests/unit/test_gone.py"]
)
def test_mixed_mapped_and_unmapped_changes_require_full(unknown):
    sel = _select(["tests/unit/test_forms.py", unknown])
    assert sel.full
    assert unknown in sel.reason


def test_each_change_can_map_to_the_same_test():
    test = "tests/unit/test_contract.py"
    sel = _select(
        ["scripts/first.py", "scripts/second.py"],
        tests=[test],
        texts={test: 'read("first.py"); read("second.py")'},
    )
    assert not sel.full
    assert sel.tests == [test]


def test_unreadable_test_requires_full_even_when_another_test_matches():
    def read(path):
        if path == "python/tests/test_renderer_pins.py":
            raise OSError("unreadable")
        return TEXTS[path]

    sel = st.select_tests(["python/djust/forms.py"], TESTS, read)
    assert sel.full
    assert "unreadable" in sel.reason


@pytest.mark.parametrize(
    "path",
    [
        "uv.lock",
        "Cargo.lock",
        ".python-version",
        "Makefile",
        ".github/workflows/test.yml",
        "scripts/run-with-venv-python.sh",
        "tests/corpus_shards.py",
        "python/tests/differential_corpus_2723.py",
        "python/djust/live_view.py",
        "python/djust/websocket.py",
        "python/djust/state_backends/base.py",
        "python/djust/mixins/__init__.py",
    ],
)
def test_shared_harness_and_runtime_require_full_even_with_a_matching_pin(path):
    test = "tests/unit/test_pin.py"
    sel = _select([path], tests=[test], texts={test: repr(path)})
    assert sel.full
    assert path in sel.reason


@pytest.mark.parametrize(
    "consumer",
    [
        "from djust.forms import FormMixin",
        "from .forms import FormMixin",
        "from djust import forms",
        "import djust.forms",
    ],
)
def test_production_importer_requires_full_for_transitive_impact(consumer):
    texts = {**TEXTS, "python/djust/consumer.py": consumer}
    sel = st.select_tests(
        ["python/djust/forms.py"],
        TESTS,
        texts.__getitem__,
        all_modules=["python/djust/consumer.py"],
    )
    assert sel.full
    assert "consumer.py" in sel.reason


def test_leaf_module_without_production_importers_keeps_focused_selection():
    texts = {**TEXTS, "python/djust/consumer.py": "from djust.other import Other"}
    sel = st.select_tests(
        ["python/djust/forms.py"],
        TESTS,
        texts.__getitem__,
        all_modules=["python/djust/consumer.py"],
    )
    assert not sel.full
    assert sel.tests == ["tests/unit/test_forms.py"]


def test_unparseable_production_import_graph_requires_full():
    texts = {**TEXTS, "python/djust/consumer.py": "def broken("}
    sel = st.select_tests(
        ["python/djust/forms.py"],
        TESTS,
        texts.__getitem__,
        all_modules=["python/djust/consumer.py"],
    )
    assert sel.full
    assert "consumer.py" in sel.reason


@pytest.mark.parametrize(
    "path",
    [
        "examples/demo_project/demo_project/settings.py",
        "requirements-dev.txt",
        "crates/djust_components/Cargo.toml",
        ".test_collected_floor",
        "scripts/pre-push-cargo-test.sh",
    ],
)
def test_shared_dependency_and_demo_settings_require_full_even_with_a_pin(path):
    test = "tests/unit/test_pin.py"
    sel = _select([path], tests=[test], texts={test: repr(path)})
    assert sel.full
    assert path in sel.reason


# --------------------------------------------------------------------------
# DJUST_PREPUSH_TARGETED=1: direct selection instead of a blast-radius FULL
# --------------------------------------------------------------------------


def test_targeted_trades_transitive_impact_for_the_direct_selection():
    texts = {**TEXTS, "python/djust/consumer.py": "from djust.forms import FormMixin"}
    kwargs = dict(all_modules=["python/djust/consumer.py"])
    default = st.select_tests(["python/djust/forms.py"], TESTS, texts.__getitem__, **kwargs)
    targeted = st.select_tests(
        ["python/djust/forms.py"], TESTS, texts.__getitem__, targeted=True, **kwargs
    )
    assert default.full and "transitive impact" in default.reason
    assert not targeted.full
    assert targeted.tests == ["tests/unit/test_forms.py"]
    assert "DJUST_PREPUSH_TARGETED=1" in targeted.reason
    assert "consumer.py imports djust.forms (transitive impact)" in targeted.reason


@pytest.mark.parametrize(
    "path, test, text",
    [
        (
            "python/djust/live_view.py",
            "tests/unit/test_pin.py",
            "from djust.live_view import LiveView",
        ),
        ("python/djust/websocket.py", "python/djust/tests/test_websocket_mount.py", None),
        ("crates/djust_templates/src/renderer.rs", "python/tests/test_renderer_pins.py", None),
        ("python/djust/mixins/__init__.py", "tests/unit/test_pin.py", "import djust.mixins"),
        ("python/djust/state_backends/base.py", "tests/unit/test_pin.py", "'base.py'"),
    ],
)
def test_targeted_trades_a_blast_radius_path_for_its_direct_consumers(path, test, text):
    tests, texts = TESTS, TEXTS
    if text is not None:
        tests, texts = [test], {test: text}
    assert _select([path], tests=tests, texts=texts).full
    sel = st.select_tests([path], tests, texts.__getitem__, "feat/x", targeted=True)
    assert not sel.full, sel.reason
    assert test in sel.tests
    assert "direct selection instead of FULL for: %s changed" % path in sel.reason


@pytest.mark.parametrize(
    "path",
    [
        "pyproject.toml",
        "scripts/pre-push-pytest.sh",
        "scripts/select-tests.py",
        "python/tests/conftest.py",
        "tests/corpus_shards.py",
        ".github/workflows/test.yml",
    ],
)
def test_targeted_keeps_full_for_the_harness(path):
    test = "tests/unit/test_pin.py"
    sel = st.select_tests([path], [test], {test: repr(path)}.__getitem__, "feat/x", targeted=True)
    assert sel.full
    assert path in sel.reason


def test_targeted_keeps_full_for_the_branch_rule_and_unclassified_paths():
    sel = st.select_tests(
        ["python/djust/live_view.py"], TESTS, TEXTS.__getitem__, "feat/routing-flip", targeted=True
    )
    assert sel.full and "branch name" in sel.reason
    sel = st.select_tests(
        ["python/djust/websocket.py", "docs/unknown.md"],
        TESTS,
        TEXTS.__getitem__,
        "feat/x",
        targeted=True,
    )
    assert sel.full and "unclassified" in sel.reason and "docs/unknown.md" in sel.reason


def test_targeted_with_an_empty_direct_selection_is_still_full():
    sel = st.select_tests(
        ["crates/djust_core/src/value.rs"], TESTS, TEXTS.__getitem__, "feat/x", targeted=True
    )
    assert sel.full
    assert "selection is empty" in sel.reason


def test_targeted_cli_reads_the_environment_and_prints_the_reason(monkeypatch, capsys):
    monkeypatch.setenv("DJUST_PREPUSH_TARGETED", "1")
    seen = {}

    def fake_select(*args, **kwargs):
        seen.update(kwargs)
        return st.Selection(False, "1 test file(s) selected; DJUST_PREPUSH_TARGETED=1: x", ["t.py"])

    monkeypatch.setattr(st, "select_tests", fake_select)
    monkeypatch.setattr(st, "_range", lambda a, b: ("origin/main", "HEAD"))
    monkeypatch.setattr(st, "_changed_files", lambda a, b: ["python/djust/live_view.py"])
    assert st.main(["--branch", "feat/x"]) == 0
    out = capsys.readouterr()
    assert seen["targeted"] is True
    assert out.out.strip() == "t.py"
    assert "DJUST_PREPUSH_TARGETED=1" in out.err


# --------------------------------------------------------------------------
# CI mode (--ci): pull-request CI runs the selection, the merge queue runs all
# --------------------------------------------------------------------------

CI_TESTS = [*TESTS, "tests/test_ci_pins.py", "tests/test_docs_scan.py", "tests/test_gate.py"]
CI_TEXTS = {
    **TEXTS,
    "tests/test_ci_pins.py": 'yaml.safe_load(open(".github/workflows/test.yml"))\n',
    "tests/test_docs_scan.py": 'for md in DOCS.rglob("*.md"):\n',
    "tests/test_gate.py": "def test_tree():\n",
}


def _ci(changed, gates=("tests/test_gate.py",)):
    return st.select_tests(changed, CI_TESTS, CI_TEXTS.__getitem__, "feat/x", ci=True, gates=gates)


@pytest.mark.parametrize(
    "path", [".github/workflows/test.yml", ".pre-commit-config.yaml", "Makefile"]
)
def test_ci_mode_selects_the_consumers_of_ci_configuration(path):
    texts = {**CI_TEXTS, "tests/test_ci_pins.py": repr(path)}
    sel = st.select_tests([path], CI_TESTS, texts.__getitem__, "feat/x", ci=True)
    assert not sel.full, sel.reason
    assert "tests/test_ci_pins.py" in sel.tests
    # Outside CI mode the same change still runs everything.
    assert st.select_tests([path], CI_TESTS, texts.__getitem__, "feat/x", targeted=True).full


def test_ci_mode_classifies_a_doc_no_test_names_and_runs_the_doc_scanners():
    sel = _ci(["python/djust/forms.py", "docs/unknown.md"])
    assert not sel.full, sel.reason
    assert "tests/unit/test_forms.py" in sel.tests
    assert "tests/test_docs_scan.py" in sel.tests


def test_ci_mode_runs_no_doc_scanner_without_a_doc_change():
    sel = _ci(["python/djust/forms.py"])
    assert "tests/test_docs_scan.py" not in sel.tests


def test_ci_mode_adds_the_merge_gates_to_every_selection():
    sel = _ci(["python/djust/forms.py"])
    assert "tests/test_gate.py" in sel.tests
    # A gate that no longer exists is not selected.
    sel = _ci(["python/djust/forms.py"], gates=("tests/test_gone.py",))
    assert "tests/test_gone.py" not in sel.tests


def test_ci_mode_is_targeted():
    """A blast-radius path selects its direct consumers instead of everything."""
    sel = _ci(["python/djust/websocket.py"], gates=())
    assert not sel.full, sel.reason
    assert sel.tests == ["python/djust/tests/test_websocket_mount.py"]


@pytest.mark.parametrize(
    "path", ["pyproject.toml", "uv.lock", "Cargo.lock", "python/tests/conftest.py"]
)
def test_ci_mode_keeps_full_for_dependencies_and_the_test_harness(path):
    texts = {**CI_TEXTS, "tests/test_ci_pins.py": repr(path)}
    assert st.select_tests([path], CI_TESTS, texts.__getitem__, "feat/x", ci=True).full


def test_merge_gates_are_read_from_the_makefile():
    gates = st.merge_gates(ROOT)
    assert "tests/test_merge_gates_3757.py" in gates
    assert all((ROOT / g).is_file() for g in gates), gates


SUPPORT_TESTS = ["python/djust/tests/test_a.py", "python/djust/tests/test_b.py", "tests/test_c.py"]
SUPPORT_TEXTS = {
    "python/djust/tests/test_a.py": "from ._live_session import open_session\n",
    "python/djust/tests/test_b.py": "from ._matrix import ROWS\n",
    "tests/test_c.py": "def test_c(): pass\n",
    "python/djust/tests/_live_session.py": "def open_session(): ...\n",
    "python/djust/tests/_matrix.py": "from ._live_session import open_session\n",
    "tests/sweep_sample.py": "def select(): ...\n",
    "conftest.py": 'pytest_plugins = ["tests.sweep_sample"]\n',
    "pyproject.toml": "[tool.pytest.ini_options]\n",
}
SUPPORT = [
    "python/djust/tests/_live_session.py",
    "python/djust/tests/_matrix.py",
    "tests/sweep_sample.py",
]


def _ci_support(changed):
    return st.select_tests(
        changed,
        SUPPORT_TESTS,
        SUPPORT_TEXTS.__getitem__,
        "feat/x",
        ci=True,
        support=SUPPORT,
        global_files=["conftest.py", "pyproject.toml"],
    )


def test_ci_mode_selects_the_tests_that_use_a_changed_helper_directly_and_through_helpers():
    sel = _ci_support(["python/djust/tests/_live_session.py"])
    assert not sel.full, sel.reason
    # test_a imports it; test_b reaches it through _matrix.
    assert sel.tests == ["python/djust/tests/test_a.py", "python/djust/tests/test_b.py"]


def test_ci_mode_keeps_full_for_a_helper_a_conftest_names():
    sel = _ci_support(["tests/sweep_sample.py"])
    assert sel.full and "conftest.py uses test support" in sel.reason


def test_outside_ci_mode_a_helper_still_runs_everything():
    sel = st.select_tests(
        ["python/djust/tests/_matrix.py"],
        SUPPORT_TESTS,
        SUPPORT_TEXTS.__getitem__,
        "feat/x",
        targeted=True,
    )
    assert sel.full


@pytest.mark.parametrize("path", ["tests/conftest.py", "python/djust/tests/__init__.py"])
def test_ci_mode_keeps_full_for_conftest_and_package_init(path):
    assert not st.is_test_support(path)
    assert _ci_support([path]).full


@pytest.mark.parametrize(
    ("text", "refers"),
    [
        ("from ._live_session import open_session", True),
        ("from . import _live_session", True),
        ("from djust.tests import other, _live_session", True),
        ("import djust.tests._live_session as ls", True),
        ('DJANGO_SETTINGS_MODULE = "djust.tests._live_session"', True),
        ("open_live_session = _live_session_factory()", False),
        ("from tests import _live_session", False),  # another package's module of that name
    ],
)
def test_a_support_module_is_referred_to_by_import_not_by_its_bare_stem(text, refers):
    pattern = st.support_import_re("python/djust/tests/_live_session.py")
    assert bool(pattern.search(text)) is refers


# --------------------------------------------------------------------------
# #4259: a strict xfail turns red when a fix makes it pass. It pins behaviour
# through the code it renders, not by naming the module, so the selection rules
# (a)-(c) miss it; the merge queue caught one (#4257, an XPASS(strict)).
# --------------------------------------------------------------------------

XFAIL_TESTS = [*CI_TESTS, "python/tests/test_pin_strict.py", "python/tests/test_pin_loose.py"]
XFAIL_TEXTS = {
    **CI_TEXTS,
    "python/tests/test_pin_strict.py": (
        "@pytest.mark.xfail(\n    reason='renders through it (#4241)',\n    strict=True,\n)\n"
        "def test_it_prints_the_row(): ...\n"
    ),
    "python/tests/test_pin_loose.py": (
        "@pytest.mark.xfail(reason='flaky upstream')\ndef test_x(): ...\n"
        "STRICT = dict(strict=True)  # not an xfail argument\n"
    ),
}


def _xfail(changed, **kw):
    return st.select_tests(changed, XFAIL_TESTS, XFAIL_TEXTS.__getitem__, "feat/x", **kw)


def test_ci_mode_adds_every_strict_xfail_file_to_a_selection():
    sel = _xfail(["python/djust/forms.py"], ci=True, gates=())
    assert not sel.full, sel.reason
    assert "tests/unit/test_forms.py" in sel.tests
    assert "python/tests/test_pin_strict.py" in sel.tests
    # A non-strict xfail cannot go red when it passes; `strict=True` outside an xfail is not one.
    assert "python/tests/test_pin_loose.py" not in sel.tests


def test_strict_xfails_do_not_rescue_an_empty_selection_or_change_full():
    assert _xfail(["docs/nowhere.txt"], ci=True).full
    assert _xfail(["pyproject.toml"], ci=True).full


# --------------------------------------------------------------------------
# CI mode: a selection too big for its one shard runs as FULL shards
# --------------------------------------------------------------------------
# PR #4284 selected 744 files; CI ran them as one shard and the job was cancelled
# at its 45-minute timeout with no failure, where FULL's 8 shards finish in ~13 min.

BIG = ["tests/unit/test_big_%03d.py" % i for i in range(40)]
BIG_TEXTS = {**CI_TEXTS, **{t: "from djust.forms import Form\n" for t in BIG}}


def _big(tests, durations):
    return st.select_tests(
        ["python/djust/forms.py"],
        [*CI_TESTS, *tests],
        BIG_TEXTS.__getitem__,
        "feat/x",
        ci=True,
        gates=(),
        durations=durations,
    )


def _timed(tests, seconds_per_file):
    # Fast-tier tests of 0.5 s each per file, plus a slow-tier test PR CI deselects.
    out = {}
    for t in tests:
        for i in range(int(seconds_per_file * 2)):
            out["%s::test_%d" % (t, i)] = 0.5
        out[t + "::test_slow"] = 500.0
    return out


def test_ci_mode_a_selection_over_the_shard_budget_is_full():
    sel = _big(BIG, _timed(BIG, 20.0))  # 40 files x 20 s = 800 s > 600 s
    assert sel.full
    assert "one CI shard" in sel.reason


def test_ci_mode_a_selection_under_the_shard_budget_keeps_its_files():
    # The slow tier (500 s per file here) is not counted: PR CI runs -m "not slow".
    sel = _big(BIG, _timed(BIG, 10.0))  # 400 s
    assert not sel.full, sel.reason
    assert set(BIG) <= set(sel.tests)


def test_ci_mode_charges_an_unrecorded_file_the_mean_recorded_file():
    recorded = _timed(BIG[:10], 20.0)  # 10 x 20 s, mean 20 s
    assert _big(BIG[:10], recorded).tests
    assert _big(BIG, recorded).full  # 10 recorded + 30 at the 20 s mean = 800 s


def test_ci_mode_without_durations_caps_the_file_count():
    many = ["tests/unit/test_big_%03d.py" % i for i in range(st.CI_SHARD_MAX_FILES + 1)]
    texts = {**CI_TEXTS, **{t: "from djust.forms import Form\n" for t in many}}
    sel = st.select_tests(
        ["python/djust/forms.py"], [*CI_TESTS, *many], texts.__getitem__, "feat/x", ci=True
    )
    assert sel.full and str(st.CI_SHARD_MAX_FILES) in sel.reason
    assert not _big(BIG, {}).full


def test_the_shard_cap_is_what_makes_the_big_selection_full(monkeypatch):
    """Gate-off: without the cap, the same big selection would run as one shard."""
    monkeypatch.setattr(st, "CI_SHARD_BUDGET_SECONDS", float("inf"))
    sel = _big(BIG, _timed(BIG, 20.0))
    assert not sel.full, sel.reason
    assert set(BIG) <= set(sel.tests)


def test_the_shard_cap_is_ci_mode_only():
    sel = st.select_tests(
        ["tests/unit/test_big_000.py"],
        [*CI_TESTS, *BIG],
        BIG_TEXTS.__getitem__,
        "feat/x",
        durations=_timed(BIG, 100.0),
    )
    assert sel.tests == ["tests/unit/test_big_000.py"]


def test_load_durations_reads_the_committed_file():
    durations = st.load_durations(ROOT)
    assert durations and all(isinstance(v, (int, float)) for v in durations.values())
