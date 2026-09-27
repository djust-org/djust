"""`djust init` adds djust to an existing Django project."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from djust.scaffolding import init_project as init


@pytest.fixture(autouse=True)
def _no_inherited_git_env(monkeypatch):
    """The code under test runs git in temp repos. Under a git hook an
    inherited GIT_DIR would aim those commands at the real repository (#2608)."""
    from tests.git_env import GIT_EXECUTION_VARS

    for var in GIT_EXECUTION_VARS:
        monkeypatch.delenv(var, raising=False)


STOCK_ASGI = '''"""
ASGI config for mysite project.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

application = get_asgi_application()
'''


ADMIN_PROBE = """
import os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "mysite.settings"
django.setup()
from django.conf import settings
settings.ALLOWED_HOSTS = ["*"]
from django.core.management import call_command
call_command("migrate", verbosity=0)
from django.contrib.auth.models import User
from django.test import Client
user = User.objects.create_superuser("admin", "admin@example.com", "pw")
client = Client()
client.force_login(user)
for url in ("/admin/", "/admin/auth/user/", "/admin/auth/user/%d/change/" % user.pk):
    print(client.get(url).status_code)
"""


def make_project(root: Path, name: str = "mysite", asgi: str = STOCK_ASGI) -> Path:
    (root / name).mkdir(parents=True)
    (root / "manage.py").write_text(
        "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', '%s.settings')\n" % name
    )
    (root / name / "__init__.py").write_text("")
    (root / name / "settings.py").write_text(
        'INSTALLED_APPS = ["django.contrib.auth"]\nTEMPLATES = []\n'
    )
    (root / name / "asgi.py").write_text(asgi)
    return root


def test_detects_settings_from_manage_py(tmp_path):
    project = init.detect_project(make_project(tmp_path))
    assert project.settings_module == "mysite.settings"
    assert project.settings_path == tmp_path / "mysite" / "settings.py"
    assert project.asgi_path == tmp_path / "mysite" / "asgi.py"
    assert project.asgi_module == "mysite.asgi"


def test_settings_flag_overrides_manage_py(tmp_path):
    make_project(tmp_path)
    (tmp_path / "mysite" / "local.py").write_text("")
    project = init.detect_project(tmp_path, settings_module="mysite.local")
    assert project.settings_path.name == "local.py"


def test_missing_manage_py_is_refused(tmp_path):
    with pytest.raises(init.InitError, match="manage.py"):
        init.detect_project(tmp_path)


def test_settings_package_is_refused_with_snippet(tmp_path):
    make_project(tmp_path)
    (tmp_path / "mysite" / "settings.py").unlink()
    (tmp_path / "mysite" / "settings").mkdir()
    (tmp_path / "mysite" / "settings" / "__init__.py").write_text("")
    with pytest.raises(init.InitError) as exc:
        init.detect_project(tmp_path)
    assert "package" in str(exc.value)
    assert init.SETTINGS_MARKER in str(exc.value)


def test_settings_block_appended_once(tmp_path):
    project = init.detect_project(make_project(tmp_path))
    change, step = init.plan_settings(project)
    assert step.status == init.DONE
    assert change.new.startswith(change.old)
    assert change.new.count(init.SETTINGS_MARKER) == 1
    project.settings_path.write_text(change.new)
    change, step = init.plan_settings(project)
    assert change is None
    assert step.status == init.UNCHANGED


def run_block(**settings):
    namespace = dict(settings)
    exec(init.render_settings_block("mysite.asgi"), namespace)  # noqa: S102 — trusted template
    return namespace


@pytest.mark.parametrize("container", [list, tuple])
def test_settings_block_adds_apps_and_asgi(container):
    django_backend = {"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": ["t"]}
    ns = run_block(
        INSTALLED_APPS=container(["django.contrib.auth", "channels"]),
        TEMPLATES=container([django_backend]),
    )
    assert ns["INSTALLED_APPS"] == ["django.contrib.auth", "channels", "djust"]
    assert ns["ASGI_APPLICATION"] == "mysite.asgi.application"
    assert ns["CHANNEL_LAYERS"]["default"]["BACKEND"] == "channels.layers.InMemoryChannelLayer"


def test_settings_block_leaves_templates_alone():
    """LiveViews read their template source directly, so the project's template
    engines keep rendering everything else, including the admin (#2872)."""
    templates = [{"BACKEND": "django.template.backends.django.DjangoTemplates"}]
    assert run_block(INSTALLED_APPS=[], TEMPLATES=templates)["TEMPLATES"] is templates
    assert "TEMPLATES" not in init.render_settings_block("mysite.asgi")


def test_settings_block_respects_existing_configuration():
    redis = {"default": {"BACKEND": "channels_redis.core.RedisChannelLayer"}}
    ns = run_block(INSTALLED_APPS=["djust"], CHANNEL_LAYERS=redis)
    assert ns["INSTALLED_APPS"] == ["djust", "channels"]
    assert ns["CHANNEL_LAYERS"] is redis


def test_settings_block_recognizes_app_config_paths():
    apps = ["channels.apps.ChannelsConfig", "djust.apps.DjustConfig"]
    assert run_block(INSTALLED_APPS=apps)["INSTALLED_APPS"] == apps


def test_settings_block_keeps_similarly_named_apps_distinct():
    ns = run_block(INSTALLED_APPS=["djust_components", "channels_redis"])
    assert ns["INSTALLED_APPS"] == ["djust_components", "channels_redis", "channels", "djust"]


@pytest.mark.parametrize(
    "source",
    [STOCK_ASGI, STOCK_ASGI.replace("'", '"'), STOCK_ASGI.split('"""\n', 2)[2]],
)
def test_stock_asgi_is_replaced(tmp_path, source):
    project = init.detect_project(make_project(tmp_path, asgi=source))
    change, step, snippet = init.plan_asgi(project)
    assert step.status == init.DONE
    assert "LiveViewConsumer" in change.new
    assert '"DJANGO_SETTINGS_MODULE", "mysite.settings"' in change.new
    assert snippet is None


def test_customized_asgi_is_left_alone(tmp_path):
    custom = STOCK_ASGI + "\napplication = Wrapper(application)\n"
    project = init.detect_project(make_project(tmp_path, asgi=custom))
    change, step, snippet = init.plan_asgi(project)
    assert change is None
    assert step.status == init.ATTENTION
    assert "LiveViewConsumer" in snippet


def test_stock_asgi_for_another_settings_module_is_left_alone(tmp_path):
    source = STOCK_ASGI.replace("mysite.settings", "mysite.settings_prod")
    project = init.detect_project(make_project(tmp_path, asgi=source))
    change, step, snippet = init.plan_asgi(project)
    assert change is None
    assert step.status == init.ATTENTION
    assert "mysite.settings_prod" in step.detail


def test_non_string_leading_expression_is_not_a_docstring(tmp_path):
    source = "1\n" + STOCK_ASGI.split('"""\n', 2)[2]
    project = init.detect_project(make_project(tmp_path, asgi=source))
    assert init.plan_asgi(project)[1].status == init.ATTENTION


def test_nested_settings_package_is_refused(tmp_path):
    (tmp_path / "config" / "settings").mkdir(parents=True)
    (tmp_path / "manage.py").write_text(
        "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.local')\n"
    )
    (tmp_path / "config" / "__init__.py").write_text("")
    (tmp_path / "config" / "settings" / "__init__.py").write_text("")
    (tmp_path / "config" / "settings" / "local.py").write_text("INSTALLED_APPS = []\n")
    (tmp_path / "config" / "asgi.py").write_text(STOCK_ASGI)
    with pytest.raises(init.InitError, match="package"):
        init.detect_project(tmp_path)


def test_crlf_settings_keep_their_line_endings(tmp_path):
    make_project(tmp_path)
    settings = tmp_path / "mysite" / "settings.py"
    settings.write_bytes(b"INSTALLED_APPS = []\r\nTEMPLATES = []\r\n")
    project = init.detect_project(tmp_path)
    change, _ = init.plan_settings(project)
    init.apply_changes([change])
    data = settings.read_bytes()
    assert data.startswith(b"INSTALLED_APPS = []\r\nTEMPLATES = []\r\n")
    assert b"\n" not in data.replace(b"\r\n", b"")


def test_configured_asgi_is_unchanged(tmp_path):
    project = init.detect_project(
        make_project(tmp_path, asgi="from djust.websocket import LiveViewConsumer\n")
    )
    change, step, snippet = init.plan_asgi(project)
    assert (change, step.status, snippet) == (None, init.UNCHANGED, None)


def test_requirements_include_running_djust():
    from djust.scaffolding.generator import djust_requirement

    assert init.requirements() == [djust_requirement(), "channels>=4.0", "uvicorn[standard]>=0.30"]


def test_project_python_prefers_local_venv(tmp_path):
    python = tmp_path / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    assert init.find_project_python(tmp_path, {"VIRTUAL_ENV": "/elsewhere"}) == python


def test_project_python_ignores_environment_outside_project(tmp_path):
    outside = tmp_path / "other-env"
    (outside / "bin").mkdir(parents=True)
    (outside / "bin" / "python").write_text("")
    project = tmp_path / "project"
    project.mkdir()
    assert init.find_project_python(project, {"VIRTUAL_ENV": str(outside)}) is None


def test_project_python_accepts_active_environment_inside_project(tmp_path):
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    (env / "bin" / "python").write_text("")
    assert init.find_project_python(tmp_path, {"VIRTUAL_ENV": str(env)}) == env / "bin" / "python"


@pytest.mark.parametrize(
    "files, kind, runnable",
    [
        ({"uv.lock": ""}, "uv", True),
        ({"pyproject.toml": "[tool.uv]\n"}, "uv", True),
        ({"poetry.lock": ""}, "poetry", False),
        ({"requirements.txt": "django\n"}, "requirements", True),
        ({}, "none", False),
    ],
)
def test_package_action_matches_project_tooling(tmp_path, files, kind, runnable):
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    action = init.choose_package_action(tmp_path, tmp_path / ".venv/bin/python", uv_available=True)
    assert (action.kind, action.runnable) == (kind, runnable)


def test_requirements_install_targets_project_python(tmp_path):
    (tmp_path / "requirements.txt").write_text("django\n")
    python = tmp_path / ".venv/bin/python"
    action = init.choose_package_action(tmp_path, python, uv_available=True)
    assert action.command == [
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        "-r",
        "requirements.txt",
    ]
    action = init.choose_package_action(tmp_path, python, uv_available=False)
    assert action.command == [str(python), "-m", "pip", "install", "-r", "requirements.txt"]


def test_requirements_without_environment_is_not_runnable(tmp_path):
    (tmp_path / "requirements.txt").write_text("django\n")
    assert init.choose_package_action(tmp_path, None, uv_available=True).runnable is False


def test_requirements_appended_only_when_missing(tmp_path):
    (tmp_path / "requirements.txt").write_text("Django>=5.2\n# comment\nUvicorn[standard]==0.35\n")
    change = init.plan_requirements(tmp_path)
    added = change.new[len(change.old) :].splitlines()
    assert [line.split(">=")[0] for line in added] == ["djust", "channels"]
    (tmp_path / "requirements.txt").write_text(change.new)
    assert init.plan_requirements(tmp_path) is None


@pytest.mark.parametrize(
    "line",
    [
        "-e ../djust",
        "-e git+https://github.com/djust-org/djust.git#egg=djust",
        "https://example.com/djust-1.0.tar.gz",
        "djust @ git+https://github.com/djust-org/djust.git",
        "DJust==1.1",
    ],
)
def test_requirements_recognize_djust_in_any_form(tmp_path, line):
    (tmp_path / "requirements.txt").write_text("channels\nuvicorn\n%s\n" % line)
    assert init.plan_requirements(tmp_path) is None


def test_requirements_follow_included_files(tmp_path):
    (tmp_path / "base.txt").write_text("djust\nchannels\nuvicorn[standard]\n")
    (tmp_path / "requirements.txt").write_text("--requirement base.txt\n-r missing.txt\n")
    assert init.plan_requirements(tmp_path) is None


def test_similarly_named_packages_do_not_count(tmp_path):
    (tmp_path / "requirements.txt").write_text("djust-components\ndjango-channels\nuvicorn\n")
    change = init.plan_requirements(tmp_path)
    added = change.new[len(change.old) :].splitlines()
    assert [line.split(">=")[0] for line in added] == ["djust", "channels"]


def test_printed_commands_are_shell_quoted(tmp_path):
    make_project(tmp_path)
    result = init.init_project(tmp_path, dry_run=True)
    detail = next(step.detail for step in result.steps if step.name == "packages")
    assert "'djust>=" in detail
    assert "'uvicorn[standard]>=0.30'" in detail


def test_dry_run_exits_zero_even_when_asgi_needs_attention(tmp_path):
    make_project(tmp_path, asgi=STOCK_ASGI + "\napplication = Wrapper(application)\n")
    result = init.init_project(tmp_path, dry_run=True, install=False)
    assert any(step.status == init.ATTENTION for step in result.steps)
    assert result.exit_code == 0


def test_dirty_paths_are_reported_unquoted(tmp_path):
    root = tmp_path / "sub dir"
    make_project(root)
    git(tmp_path, "init", "-q")
    with pytest.raises(init.InitError) as exc:
        init.init_project(root, install=False)
    assert "sub dir/mysite/settings.py" in str(exc.value)
    assert '"' not in str(exc.value).split("Uncommitted changes in ", 1)[1].split(".", 1)[0]


def git(root, *args):
    # Under a git hook an inherited GIT_DIR would point these commands at the
    # real repository (#2608).
    from tests.git_env import isolated_git_env

    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, env=isolated_git_env()
    )


def test_uncommitted_target_files_are_refused(tmp_path):
    make_project(tmp_path)
    git(tmp_path, "init", "-q")
    with pytest.raises(init.InitError, match="--force"):
        init.init_project(tmp_path, install=False)
    assert init.SETTINGS_MARKER not in (tmp_path / "mysite/settings.py").read_text()
    result = init.init_project(tmp_path, install=False, force=True)
    assert result.exit_code == 0
    assert init.SETTINGS_MARKER in (tmp_path / "mysite/settings.py").read_text()


def test_committed_project_is_edited(tmp_path):
    make_project(tmp_path)
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", ".")
    git(tmp_path, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "init")
    assert init.init_project(tmp_path, install=False).exit_code == 0


def test_dry_run_writes_nothing(tmp_path):
    make_project(tmp_path)
    before = (tmp_path / "mysite/settings.py").read_text()
    result = init.init_project(tmp_path, dry_run=True)
    assert (tmp_path / "mysite/settings.py").read_text() == before
    output = init.format_result(result, tmp_path)
    assert "+++ b/mysite/settings.py" in output
    assert "Dry run" in output


def test_install_and_check_run_with_project_python(tmp_path, monkeypatch):
    make_project(tmp_path)
    (tmp_path / "requirements.txt").write_text("django\n")
    python = tmp_path / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("")
    monkeypatch.setenv("VIRTUAL_ENV", "/elsewhere")
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs.get("env", {})))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with (
        patch.object(init.shutil, "which", return_value=None),
        patch.object(init.subprocess, "run", side_effect=run),
    ):
        result = init.init_project(tmp_path, force=True)
    commands = [cmd for cmd, _ in calls if cmd[0] != "git"]
    assert commands == [
        [str(python), "-m", "pip", "install", "-r", "requirements.txt"],
        [str(python), "manage.py", "check"],
    ]
    assert all("VIRTUAL_ENV" not in env for cmd, env in calls if cmd[0] != "git")
    assert result.exit_code == 0
    assert result.run_command == ".venv/bin/python -m uvicorn mysite.asgi:application --reload"


def test_failed_check_needs_attention(tmp_path):
    make_project(tmp_path)
    (tmp_path / "uv.lock").write_text("")

    def run(cmd, **kwargs):
        code = 1 if "check" in cmd else 0
        return subprocess.CompletedProcess(cmd, code, "", "SystemCheckError: boom")

    with (
        patch.object(init.shutil, "which", return_value="/usr/bin/uv"),
        patch.object(init.subprocess, "run", side_effect=run),
    ):
        result = init.init_project(tmp_path, force=True)
    assert result.exit_code == 2
    assert any("boom" in note for note in result.notes)
    assert result.run_command == "uv run uvicorn mysite.asgi:application --reload"


def test_cli_init_reports_refusal(tmp_path, monkeypatch, capsys):
    import argparse

    from djust import cli

    monkeypatch.chdir(tmp_path)
    args = argparse.Namespace(settings=None, dry_run=False, no_install=True, force=False)
    assert cli.cmd_init(args) == 1
    assert "manage.py" in capsys.readouterr().out


def test_init_on_startproject_passes_django_check(tmp_path):
    import os

    subprocess.run(
        [sys.executable, "-m", "django", "startproject", "mysite", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    result = init.init_project(tmp_path, install=False)
    assert result.exit_code == 0
    env = {k: v for k, v in os.environ.items() if k != "DJANGO_SETTINGS_MODULE"}
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), *sys.path])
    check = subprocess.run(
        [sys.executable, "manage.py", "check"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    admin = subprocess.run(
        [sys.executable, "-c", ADMIN_PROBE], cwd=tmp_path, env=env, capture_output=True, text=True
    )
    assert admin.stdout.split() == ["200", "200", "200"], admin.stdout + admin.stderr
    asgi = subprocess.run(
        [sys.executable, "-c", "import mysite.asgi as a; print(type(a.application).__name__)"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert asgi.stdout.strip() == "ProtocolTypeRouter", asgi.stderr


def test_djust_init_command_is_registered(tmp_path, monkeypatch, capsys):
    from djust import cli

    make_project(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["djust", "init", "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "Dry run" in capsys.readouterr().out


def test_dry_run_reports_file_steps_as_planned_not_done(tmp_path):
    """#3172: a dry run writes nothing, so no row may say it was done."""
    make_project(tmp_path)
    result = init.init_project(tmp_path, dry_run=True)
    rows = {step.name: step for step in result.steps}
    assert rows["mysite/settings.py"].status == init.PLANNED
    assert rows["mysite/settings.py"].detail == "append djust block"
    assert rows["mysite/asgi.py"].status == init.PLANNED
    assert rows["mysite/asgi.py"].detail == "replace Django's default"
    table = init.format_result(result, tmp_path).split("Dry run: nothing was written")[1]
    assert " done " not in table
    assert "appended" not in table and "replaced" not in table
    assert "mysite/settings.py  would change  append djust block" in table


def test_dry_run_of_a_missing_asgi_says_create(tmp_path):
    make_project(tmp_path)
    (tmp_path / "mysite/asgi.py").unlink()
    result = init.init_project(tmp_path, dry_run=True, install=False)
    row = next(step for step in result.steps if step.name == "mysite/asgi.py")
    assert (row.status, row.detail) == (init.PLANNED, "create")


def test_dry_run_leaves_unchanged_and_attention_rows_alone(tmp_path):
    make_project(tmp_path, asgi=STOCK_ASGI + "\napplication = Wrapper(application)\n")
    init.init_project(tmp_path, force=True, install=False)
    result = init.init_project(tmp_path, dry_run=True, install=False)
    statuses = {step.name: step.status for step in result.steps}
    assert statuses["mysite/settings.py"] == init.UNCHANGED
    assert statuses["mysite/asgi.py"] == init.ATTENTION


def test_real_run_still_reports_file_steps_as_done(tmp_path):
    make_project(tmp_path)
    result = init.init_project(tmp_path, force=True, install=False)
    rows = {step.name: step for step in result.steps}
    assert (rows["mysite/settings.py"].status, rows["mysite/settings.py"].detail) == (
        init.DONE,
        "djust block appended",
    )


def test_check_warnings_are_reported_not_called_clean(tmp_path):
    """#3170: `manage.py check` exits 0 on warnings. Run the real check on a
    startproject tree (only the package install is stubbed) and require the
    summary to carry the warnings instead of "no issues"."""
    import os

    subprocess.run(
        [sys.executable, "-m", "django", "startproject", "mysite", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    (tmp_path / "requirements.txt").write_text("django\n")
    venv_python = tmp_path / ".venv/bin/python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("")
    real_run = subprocess.run
    pythonpath = os.pathsep.join([str(tmp_path), *sys.path])

    def run(cmd, **kwargs):
        if cmd[1:3] == ["-m", "pip"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1:] == ["manage.py", "check"]:
            env = {k: v for k, v in kwargs.pop("env").items() if k != "DJANGO_SETTINGS_MODULE"}
            env["PYTHONPATH"] = pythonpath
            return real_run([sys.executable, *cmd[1:]], env=env, **kwargs)
        return real_run(cmd, **kwargs)

    with (
        patch.object(init.shutil, "which", return_value=None),
        patch.object(init.subprocess, "run", side_effect=run),
    ):
        result = init.init_project(tmp_path, force=True)
    check = next(step for step in result.steps if step.name == "check")
    assert check.status == init.DONE
    assert check.detail != "no issues"
    assert "reported (see below)" in check.detail
    note = next(note for note in result.notes if note.startswith("manage.py check"))
    assert "WARNINGS:" in note
    assert result.exit_code == 0


@pytest.mark.parametrize(
    "output, detail",
    [
        ("System check identified no issues (0 silenced).\n", "no issues"),
        (
            "WARNINGS:\n?: (x.W1) w\n\nSystem check identified 1 issue (0 silenced).\n",
            "1 issue reported (see below)",
        ),
        (
            "WARNINGS:\n?: (x.W1) w\n\nSystem check identified 3 issues (1 silenced).\n",
            "3 issues reported (see below)",
        ),
        # #3213 review: a check message quoting the sentence mid-line must not
        # be counted instead of the real summary line.
        (
            "WARNINGS:\n?: (x.W1) said: System check identified 9 issues (0 silenced).\n\n"
            "System check identified 1 issue (0 silenced).\n",
            "1 issue reported (see below)",
        ),
    ],
)
def test_check_summary_counts_django_issues(tmp_path, output, detail):
    make_project(tmp_path)
    (tmp_path / "uv.lock").write_text("")

    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", output if "check" in cmd else "")

    with (
        patch.object(init.shutil, "which", return_value="/usr/bin/uv"),
        patch.object(init.subprocess, "run", side_effect=run),
    ):
        result = init.init_project(tmp_path, force=True)
    assert next(step for step in result.steps if step.name == "check").detail == detail
    assert any("manage.py check" in n for n in result.notes) == (detail != "no issues")
    assert result.exit_code == 0


def test_dry_run_of_a_done_step_without_planned_wording_falls_back_to_its_detail(
    tmp_path, monkeypatch
):
    """#3213 review 6: a future plan_* returning DONE without the planned
    wording must not print "would change" with an empty detail."""
    make_project(tmp_path)
    real = init.plan_settings

    def without_planned(project):
        change, step = real(project)
        return change, init.Step(step.name, step.status, "custom detail")

    monkeypatch.setattr(init, "plan_settings", without_planned)
    result = init.init_project(tmp_path, dry_run=True, install=False)
    row = next(step for step in result.steps if step.name == "mysite/settings.py")
    assert (row.status, row.detail) == (init.PLANNED, "custom detail")
