"""``djust_ai_context``: never replace a file silently (#3297).

#3297: the command opened its target with ``open(path, "w")`` and no check, so a
hand-written ``CLAUDE.md`` or ``.cursorrules`` was replaced by the generated one
with exit status 0. It now refuses unless ``--force`` is given.
"""

import os
from io import StringIO

import pytest
from django.core.management import call_command, load_command_class
from django.core.management.base import CommandError

FORMATS = ["claude", "cursor", "copilot"]
DEFAULT_PATHS = {
    "claude": "CLAUDE.md",
    "cursor": ".cursorrules",
    "copilot": os.path.join(".github", "copilot-instructions.md"),
}
HAND_WRITTEN = "# My rules\nAlways use tabs.\n"


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _run(*args):
    out = StringIO()
    call_command("djust_ai_context", *args, stdout=out)
    return out.getvalue()


@pytest.mark.parametrize("fmt", FORMATS)
def test_existing_default_file_is_refused(in_tmp, fmt):
    target = in_tmp / DEFAULT_PATHS[fmt]
    target.parent.mkdir(exist_ok=True)
    target.write_text(HAND_WRITTEN)
    with pytest.raises(CommandError, match="--force"):
        _run("--format", fmt)
    assert target.read_text() == HAND_WRITTEN


def test_refusal_exits_non_zero(in_tmp, capsys):
    # run_from_argv is what ``manage.py`` calls: it turns a CommandError into
    # exit status 1 and prints the message on stderr.
    (in_tmp / "CLAUDE.md").write_text(HAND_WRITTEN)
    command = load_command_class("djust", "djust_ai_context")
    with pytest.raises(SystemExit) as exc:
        command.run_from_argv(["manage.py", "djust_ai_context", "--skip-checks"])
    assert exc.value.code == 1
    assert "CLAUDE.md already exists" in capsys.readouterr().err
    assert (in_tmp / "CLAUDE.md").read_text() == HAND_WRITTEN


def test_refusal_names_the_file_and_the_alternatives(in_tmp):
    (in_tmp / "notes.md").write_text(HAND_WRITTEN)
    with pytest.raises(CommandError) as exc:
        _run("--output", "notes.md")
    message = str(exc.value)
    assert "notes.md" in message
    assert "--force" in message and "--output" in message and "--print" in message


def test_force_replaces_an_existing_file(in_tmp):
    (in_tmp / "CLAUDE.md").write_text(HAND_WRITTEN)
    assert "Wrote CLAUDE.md" in _run("--force")
    assert (in_tmp / "CLAUDE.md").read_text().startswith("# CLAUDE.md")


@pytest.mark.parametrize("fmt", FORMATS)
def test_missing_file_is_written_without_force(in_tmp, fmt):
    _run("--format", fmt)
    assert (in_tmp / DEFAULT_PATHS[fmt]).read_text().startswith("#")


def test_print_ignores_an_existing_file(in_tmp):
    (in_tmp / "CLAUDE.md").write_text(HAND_WRITTEN)
    assert "# CLAUDE.md" in _run("--print")
    assert (in_tmp / "CLAUDE.md").read_text() == HAND_WRITTEN
