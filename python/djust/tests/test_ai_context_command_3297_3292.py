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


def test_file_created_by_another_process_mid_run_is_refused(in_tmp, monkeypatch):
    # The file appears after the command decided to write but before it opens
    # the target: exclusive-create must refuse rather than truncate it.
    target = in_tmp / "CLAUDE.md"
    real_open = open

    def create_then_open(path, mode="r", *args, **kwargs):
        if os.fspath(path) == "CLAUDE.md":
            target.write_text(HAND_WRITTEN)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(
        "djust.management.commands.djust_ai_context.open", create_then_open, raising=False
    )
    with pytest.raises(CommandError, match="already exists"):
        _run()
    assert target.read_text() == HAND_WRITTEN


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
    '["myapp.views", "djust"]',
    "from djust import LoginRequiredMixin, PermissionRequiredMixin",
    "X008",
    "--force",
    # #3304: login-over-WebSocket and the HTTP fallback's get_context_data()
    "Do not call `login()` in an event handler",
    "dj-trigger-action",
    "`dj-submit`",
    "self.trigger_submit(",
    "djust_auth:login",
    "## State and `get_context_data()`",
    "super().get_context_data(**kwargs)",
    "Set state in `mount()`",
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


def _login_note():
    content = _run("--print")
    start = content.index("**Do not call `login()` in an event handler.**")
    return content[start : content.index("\n", start)]


def test_login_note_names_only_apis_that_exist_3304():
    # Parse the names out of the generated note, so removing or renaming one of
    # them breaks this test (and the note gets updated with it).
    import re

    from djust import LiveView
    from djust.auth import urls as auth_urls
    from djust.schema import DIRECTIVES

    note = _login_note()

    methods = re.findall(r"self\.(\w+)\(", note)
    assert methods == ["trigger_submit"]
    for name in methods:
        assert callable(getattr(LiveView, name, None)), name

    directives = {d["name"] for d in DIRECTIVES}
    attrs = re.findall(r"`(dj-[\w-]+)`", note)
    assert {"dj-submit", "dj-trigger-action"} <= set(attrs)
    for attr in attrs:
        assert attr in directives, attr

    for namespace, name in re.findall(r"'(\w+):(\w+)'", note):
        assert namespace == auth_urls.app_name
        assert name in {p.name for p in auth_urls.urlpatterns}, name
    assert "djust_auth:login" in note


def test_state_note_calls_a_real_method_3304():
    from djust import LiveView

    content = _run("--print")
    assert "super().get_context_data(**kwargs)" in content
    assert callable(getattr(LiveView, "get_context_data", None))
    assert callable(getattr(LiveView, "mount", None))
