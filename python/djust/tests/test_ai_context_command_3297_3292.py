"""``djust_ai_context``: never replace a file silently (#3297) and carry security guidance (#3292).

#3297: the command opened its target with ``open(path, "w")`` and no check, so a
hand-written ``CLAUDE.md`` or ``.cursorrules`` was replaced by the generated one
with exit status 0. It now refuses unless ``--force`` is given.

#3292: the generated text said nothing about XSS (``|safe`` / ``mark_safe``),
``manage.py check`` or ``djust_audit --ast``, although the tooling that catches
the commonest mistake exists. The lines are pinned per format so they cannot
drop out unnoticed.
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
    assert "## Security" in _run("--print")
    assert (in_tmp / "CLAUDE.md").read_text() == HAND_WRITTEN


# --- the security section ---------------------------------------------------

REQUIRED_LINES = [
    "## Security",
    "python manage.py check",
    "python manage.py djust_audit --ast",
    "|safe",
    "mark_safe()",
    "X006",
    "login_required",
    "permission_required",
    "{% csrf_token %}",
]


@pytest.mark.parametrize("fmt", FORMATS)
def test_every_format_carries_the_security_section(fmt):
    content = _run("--format", fmt, "--print")
    for needle in REQUIRED_LINES:
        assert needle in content, "%s output lost %r" % (fmt, needle)


def test_security_section_makes_no_comparative_claim():
    content = _run("--print").lower()
    assert "more secure" not in content
    assert "secure by default" not in content
    assert "do not make generated code secure on their own" in content


def test_audit_codes_named_in_the_context_exist():
    # Every X-code the generated text cites must be one djust_audit --ast emits.
    import re

    from djust.audit_ast import AST_FINDING_CODES

    cited = set(re.findall(r"\bX\d{3}\b", _run("--print")))
    assert cited, "the security section should cite audit codes"
    for code in cited:
        assert code in AST_FINDING_CODES
