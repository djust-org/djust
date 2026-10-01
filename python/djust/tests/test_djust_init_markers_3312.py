"""``djust init`` and requirements that carry environment markers or live in ``-r`` files (#3312, items 3 and 4).

``uv add 'uvicorn[standard]'`` on a package declared with a marker appends a
second, unconditional line instead of keeping the specifier, so init reports
the extra instead of running it. An extra that only a marked line carries is
not treated as present (a marked pair must not add up to "satisfied"), and an
extra missing only in an included file is reported rather than skipped silently.
"""

import subprocess
from unittest.mock import patch

import pytest

from djust.scaffolding import init_project as init
from djust.tests.test_djust_init import make_project, uv_project

BASE = '"djust==1.2.2", "channels>=4.2"'


def _uv(tmp_path, uvicorn_lines):
    uv_project(tmp_path, "%s, %s" % (BASE, uvicorn_lines))
    return init.choose_package_action(tmp_path, None, uv_available=True)


# --- uv: marker-declared package ----------------------------------------------


def test_marker_declared_package_is_not_passed_to_uv_add(tmp_path):
    action = _uv(tmp_path, "\"uvicorn>=0.30; python_version>'3.9'\"")
    assert action.command == []  # `uv add 'uvicorn[standard]'` would append a 2nd line
    assert action.manual == ["uvicorn[standard]"]


def test_other_packages_still_go_to_uv_add_beside_a_manual_one(tmp_path):
    uv_project(tmp_path, '"djust==1.2.2", "uvicorn>=0.30; sys_platform != \'win32\'"')
    action = init.choose_package_action(tmp_path, None, uv_available=True)
    assert action.command == ["uv", "add", "channels>=4.0"]
    assert action.manual == ["uvicorn[standard]"]


def test_a_marked_declaration_with_the_extra_does_not_count_as_satisfied(tmp_path):
    action = _uv(tmp_path, "\"uvicorn[standard]>=0.30; sys_platform == 'win32'\"")
    assert action.manual == ["uvicorn[standard]"]


def test_a_marked_pair_is_not_satisfied_by_adding_up_its_extras(tmp_path):
    action = _uv(
        tmp_path,
        "\"uvicorn[standard]>=0.30; sys_platform == 'win32'\", "
        "\"uvicorn>=0.30; sys_platform != 'win32'\"",
    )
    assert action.command == []
    assert action.manual == ["uvicorn[standard]"]


def test_an_unmarked_declaration_with_the_extra_satisfies_it(tmp_path):
    action = _uv(
        tmp_path,
        '"uvicorn[standard]>=0.30", "uvicorn>=0.30; python_version < \'3.9\'"',
    )
    assert action.command == []
    assert action.manual == []


def test_unmarked_declaration_without_the_extra_still_uses_uv_add(tmp_path):
    action = _uv(tmp_path, '"uvicorn>=0.30,<0.40"')
    assert action.command == ["uv", "add", "uvicorn[standard]"]
    assert action.manual == []


@pytest.mark.parametrize(
    "requirement, expected",
    [
        ("uvicorn>=0.30", False),
        ("uvicorn>=0.30; python_version>'3.9'", True),
        ("uvicorn[standard];sys_platform=='win32'", True),
        ("uvicorn @ https://example.com/u.whl", False),
        ("uvicorn @ https://example.com/u.whl;x=1", False),  # ';' inside the URL
        ("uvicorn @ https://example.com/u.whl ; python_version>'3.9'", True),
    ],
)
def test_marker_detection(requirement, expected):
    assert init._has_marker(requirement) is expected


def _run_init(tmp_path):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch.object(init.shutil, "which", return_value="/usr/bin/uv"),
        patch.object(init.subprocess, "run", side_effect=run),
    ):
        result = init.init_project(tmp_path, force=True)
    return result, calls


def test_init_reports_a_marker_declared_extra_instead_of_running_uv_add(tmp_path):
    make_project(tmp_path)
    uv_project(tmp_path, "%s, \"uvicorn>=0.30; python_version>'3.9'\"" % BASE)
    result, calls = _run_init(tmp_path)
    assert not any(cmd[:2] == ["uv", "add"] for cmd in calls)
    step = next(s for s in result.steps if s.name == "pyproject.toml")
    assert step.status == init.ATTENTION
    note = "\n".join(result.notes)
    assert "uvicorn[standard]" in note
    assert "environment marker" in note
    assert "uvicorn" not in (tmp_path / "pyproject.toml").read_text().replace(
        "uvicorn>=0.30; python_version>'3.9'", ""
    )
    assert "ATTENTION" in init.format_result(result, tmp_path)


def test_init_dry_run_reports_it_too(tmp_path):
    make_project(tmp_path)
    uv_project(tmp_path, "%s, \"uvicorn>=0.30; python_version>'3.9'\"" % BASE)
    result = init.init_project(tmp_path, dry_run=True)
    assert any(s.name == "pyproject.toml" and s.status == init.ATTENTION for s in result.steps)


def test_nothing_is_reported_when_every_extra_is_present(tmp_path):
    make_project(tmp_path)
    uv_project(tmp_path, '%s, "uvicorn[standard]>=0.30"' % BASE)
    result, _ = _run_init(tmp_path)
    assert not any(s.name == "pyproject.toml" for s in result.steps)


# --- requirements.txt: duplicate lines and markers ----------------------------

SATISFIED = "djust==1.2.2\nchannels>=4.2\n"


def _requirements(tmp_path, text):
    (tmp_path / "requirements.txt").write_text(text)


def test_an_extra_only_on_a_marked_line_is_reported_not_counted(tmp_path):
    _requirements(tmp_path, SATISFIED + "uvicorn[standard]>=0.30; sys_platform == 'win32'\n")
    assert init.plan_requirements(tmp_path) is None  # nothing safe to write
    by_hand = init.requirements_by_hand(tmp_path)
    assert len(by_hand) == 1
    assert "uvicorn" in by_hand[0] and "uvicorn[standard]" in by_hand[0]


def test_a_marked_pair_does_not_add_up_to_satisfied(tmp_path):
    _requirements(
        tmp_path,
        SATISFIED
        + "uvicorn[standard]>=0.30; sys_platform == 'win32'\n"
        + "uvicorn>=0.30; sys_platform != 'win32'\n",
    )
    assert init.plan_requirements(tmp_path) is None
    assert init.requirements_by_hand(tmp_path)


def test_the_unmarked_line_gets_the_extra_and_the_marked_one_is_left_alone(tmp_path):
    text = SATISFIED + "uvicorn>=0.30; python_version < '3.9'\nuvicorn>=0.31\n"
    _requirements(tmp_path, text)
    change = init.plan_requirements(tmp_path)
    assert (
        change.new == SATISFIED + "uvicorn>=0.30; python_version < '3.9'\nuvicorn[standard]>=0.31\n"
    )
    assert init.requirements_by_hand(tmp_path) == []


def test_an_unmarked_line_with_the_extra_satisfies_it(tmp_path):
    _requirements(
        tmp_path, SATISFIED + "uvicorn[standard]>=0.30\nuvicorn>=0.30; python_version<'3.9'\n"
    )
    assert init.plan_requirements(tmp_path) is None
    assert init.requirements_by_hand(tmp_path) == []


def test_a_marked_line_alone_is_not_rewritten_nor_duplicated(tmp_path):
    _requirements(tmp_path, SATISFIED + "uvicorn>=0.30; python_version>'3.9'\n")
    assert init.plan_requirements(tmp_path) is None
    assert init.requirements_by_hand(tmp_path)


# --- requirements.txt: the extra is missing only in an included file ----------


def test_an_extra_missing_only_in_an_included_file_is_reported(tmp_path):
    (tmp_path / "base.txt").write_text("djust\nchannels\nuvicorn\n")
    _requirements(tmp_path, "-r base.txt\n")
    assert init.plan_requirements(tmp_path) is None  # the included file is the user's
    by_hand = init.requirements_by_hand(tmp_path)
    assert len(by_hand) == 1
    assert "base.txt" in by_hand[0]
    assert "uvicorn[standard]" in by_hand[0]
    assert (tmp_path / "base.txt").read_text() == "djust\nchannels\nuvicorn\n"


def test_an_included_file_that_has_the_extra_is_not_reported(tmp_path):
    (tmp_path / "base.txt").write_text("djust\nchannels\nuvicorn[standard]\n")
    _requirements(tmp_path, "-r base.txt\n")
    assert init.requirements_by_hand(tmp_path) == []


def test_a_line_in_this_file_is_rewritten_even_when_an_included_file_names_it(tmp_path):
    (tmp_path / "base.txt").write_text("djust\nchannels\n")
    _requirements(tmp_path, "-r base.txt\nuvicorn>=0.30\n")
    change = init.plan_requirements(tmp_path)
    assert "uvicorn[standard]>=0.30" in change.new
    assert init.requirements_by_hand(tmp_path) == []


def test_init_says_so_for_an_included_file(tmp_path):
    make_project(tmp_path)
    (tmp_path / "base.txt").write_text("djust\nchannels\nuvicorn\n")
    _requirements(tmp_path, "-r base.txt\n")
    with patch.object(init.shutil, "which", return_value=None):
        result = init.init_project(tmp_path, install=True, force=True, dry_run=True)
    step = next(s for s in result.steps if s.name == "requirements.txt")
    assert step.status == init.ATTENTION
    assert "base.txt" in "\n".join(result.notes)


def test_hash_locked_report_includes_a_marker_only_extra(tmp_path):
    _requirements(
        tmp_path,
        "djust==1.2.2 \\\n    --hash=sha256:a\nchannels==4.2 \\\n    --hash=sha256:b\n"
        "uvicorn[standard]==0.35; sys_platform == 'win32' \\\n    --hash=sha256:c\n",
    )
    assert init.hash_locked_requirements(tmp_path) == ["uvicorn[standard]>=0.30"]


# --- review of #3321 ------------------------------------------------------------


def test_marker_declared_packages_that_need_no_extra_are_not_reported(tmp_path):
    # djust and channels need no extra: a marker on them is not for init to report.
    action = _uv(tmp_path, '"uvicorn[standard]>=0.30"')  # baseline: nothing to do
    assert action.command == [] and action.manual == []
    uv_project(
        tmp_path,
        "\"djust==1.2.2; python_version>'3.9'\", \"channels>=4.2; sys_platform != 'win32'\", "
        '"uvicorn[standard]>=0.30"',
    )
    action = init.choose_package_action(tmp_path, None, uv_available=True)
    assert action.command == []
    assert action.manual == []


def test_rerunning_init_on_marker_declared_packages_is_clean(tmp_path):
    make_project(tmp_path)
    uv_project(
        tmp_path,
        "\"djust==1.2.2; python_version>'3.9'\", \"channels>=4.2; sys_platform != 'win32'\", "
        '"uvicorn[standard]>=0.30"',
    )
    result, _ = _run_init(tmp_path)
    again, _ = _run_init(tmp_path)
    for run in (result, again):
        assert run.exit_code == 0
        assert not any(s.status == init.ATTENTION for s in run.steps)
        assert not any(s.name == "pyproject.toml" for s in run.steps)


def test_attention_and_already_declared_are_not_both_printed(tmp_path):
    make_project(tmp_path)
    uv_project(tmp_path, "%s, \"uvicorn>=0.30; python_version>'3.9'\"" % BASE)
    result, _ = _run_init(tmp_path)
    packages = next(s for s in result.steps if s.name == "packages")
    assert "already declared" not in packages.detail
    assert "pyproject.toml" in packages.detail
    assert any(s.name == "pyproject.toml" and s.status == init.ATTENTION for s in result.steps)


def test_init_reports_a_marked_only_requirements_extra(tmp_path):
    make_project(tmp_path)
    _requirements(tmp_path, SATISFIED + "uvicorn[standard]>=0.30; sys_platform == 'win32'\n")
    with patch.object(init.shutil, "which", return_value=None):
        result = init.init_project(tmp_path, install=True, force=True, dry_run=True)
    step = next(s for s in result.steps if s.name == "requirements.txt")
    assert step.status == init.ATTENTION
    assert "environment marker" in "\n".join(result.notes)
    assert (tmp_path / "requirements.txt").read_text().endswith("sys_platform == 'win32'\n")


def test_a_hash_locked_tree_is_reported_once_not_twice(tmp_path):
    # requirements_by_hand defers to hash_locked_requirements for a locked file,
    # so an included file lacking the extra does not add a second requirements.txt step.
    make_project(tmp_path)
    (tmp_path / "base.txt").write_text(
        "djust==1.2.2 \\\n    --hash=sha256:a\nchannels==4.2 \\\n    --hash=sha256:b\n"
        "uvicorn==0.35 \\\n    --hash=sha256:c\n"
    )
    _requirements(tmp_path, "-r base.txt\n")
    assert init.requirements_by_hand(tmp_path) == []
    with patch.object(init.shutil, "which", return_value=None):
        result = init.init_project(tmp_path, install=True, force=True, dry_run=True)
    steps = [s for s in result.steps if s.name == "requirements.txt"]
    assert len(steps) == 1
    assert "hash-locked" in steps[0].detail


# --- a `-r` cycle spelled with `..` (pre-existing) -----------------------------


def test_an_include_cycle_spelled_with_dotdot_terminates(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "requirements.txt").write_text("-r sub/../requirements.txt\ndjust\nchannels\n")
    assert init.plan_requirements(tmp_path) is not None  # uvicorn is missing, and no crash
    (tmp_path / "sub" / "a.txt").write_text("-r ../sub/a.txt\n")
    (tmp_path / "requirements.txt").write_text("-r sub/a.txt\ndjust\nchannels\nuvicorn[standard]\n")
    assert init.plan_requirements(tmp_path) is None
    assert init.hash_locked_requirements(tmp_path) is None
    (tmp_path / "requirements.txt").write_text("-r sub/../sub/a.txt\ndjust\n")
    assert init.requirements_by_hand(tmp_path) == []
