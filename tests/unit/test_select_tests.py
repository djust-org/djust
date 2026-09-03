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
    assert sel.tests == ["tests/unit/test_forms.py"]


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


def test_reader_errors_are_treated_as_no_text():
    def boom(_p):
        raise OSError("unreadable")

    sel = st.select_tests(["crates/djust_components/src/renderer.rs"], TESTS, boom, "x")
    assert sel.full and "empty" in sel.reason


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
    assert rev["djust_core"] >= {"djust_vdom", "djust_templates", "djust_live", "djust_components"}


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
