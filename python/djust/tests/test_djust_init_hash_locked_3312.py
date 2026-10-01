"""``djust init`` does not edit a hash-locked requirements file (#3312, item 2).

``uv pip install --require-hashes`` / ``pip --require-hashes`` need every
requirement to carry its ``--hash=``; adding ``[standard]`` to a hashed line
(or appending an unhashed one) makes the file uninstallable. ``init`` reports
what the project needs instead of editing it.
"""

import subprocess
from unittest.mock import patch

from djust.scaffolding import init_project as init
from djust.tests.test_djust_init import make_project

HASHED = (
    "django==5.2.17 \\\n"
    "    --hash=sha256:aaaa \\\n"
    "    --hash=sha256:bbbb\n"
    "uvicorn==0.35.0 \\\n"
    "    --hash=sha256:cccc\n"
)


def _write(tmp_path, text):
    (tmp_path / "requirements.txt").write_text(text)


def test_a_hashed_requirement_line_is_not_rewritten(tmp_path):
    _write(tmp_path, HASHED)
    assert init.plan_requirements(tmp_path) is None


def test_missing_packages_are_not_appended_to_a_hash_locked_file(tmp_path):
    _write(tmp_path, "django==5.2.17 --hash=sha256:aaaa\n")
    assert init.plan_requirements(tmp_path) is None


def test_hashes_in_an_included_file_lock_the_whole_tree(tmp_path):
    (tmp_path / "base.txt").write_text(HASHED)
    _write(tmp_path, "-r base.txt\n")
    assert init.plan_requirements(tmp_path) is None


def test_the_report_names_what_to_add_and_the_extra(tmp_path):
    _write(tmp_path, HASHED)
    assert init.hash_locked_requirements(tmp_path) == init.requirements()
    assert "uvicorn[standard]>=0.30" in init.requirements()


def test_no_report_for_an_ordinary_file(tmp_path):
    _write(tmp_path, "django\n")
    assert init.hash_locked_requirements(tmp_path) is None


def test_no_report_when_a_hash_locked_file_already_has_everything(tmp_path):
    _write(
        tmp_path,
        "djust==1.2.2 \\\n    --hash=sha256:a\nchannels==4.2 \\\n    --hash=sha256:b\n"
        "uvicorn[standard]==0.35 \\\n    --hash=sha256:c\n",
    )
    assert init.plan_requirements(tmp_path) is None
    assert init.hash_locked_requirements(tmp_path) is None


def test_a_comment_mentioning_hashes_is_not_a_lock(tmp_path):
    _write(tmp_path, "# regenerate with --generate-hashes someday\ndjango\n")
    assert init.plan_requirements(tmp_path) is not None


def test_init_leaves_the_file_alone_and_says_so(tmp_path):
    make_project(tmp_path)
    _write(tmp_path, HASHED)

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch.object(init.shutil, "which", return_value=None),
        patch.object(init.subprocess, "run", side_effect=run),
    ):
        result = init.init_project(tmp_path, force=True)
    assert (tmp_path / "requirements.txt").read_text() == HASHED
    assert all(change.path.name != "requirements.txt" for change in result.changes)
    step = next(s for s in result.steps if s.name == "requirements.txt")
    assert step.status == init.ATTENTION and "hash" in step.detail
    note = next(n for n in result.notes if "uvicorn[standard]" in n)
    assert "--generate-hashes" in note
    assert result.exit_code == 2


def test_dry_run_reports_it_too(tmp_path):
    make_project(tmp_path)
    _write(tmp_path, HASHED)
    result = init.init_project(tmp_path, dry_run=True)
    assert any(s.name == "requirements.txt" and s.status == init.ATTENTION for s in result.steps)
