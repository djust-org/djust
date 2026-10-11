"""Agent discovery hints and cached UI replacement advice."""

import io
import json
import os
import re
import sys

import pytest
from django.core.management import call_command
from django.test import override_settings


@pytest.fixture
def reset_i001(monkeypatch):
    from djust.checks import ai_discovery as checks
    from djust.ai_discovery.agents import AGENT_ENV_VARS

    monkeypatch.setitem(checks._I001_STATE, "emitted", False)
    monkeypatch.delenv("DJUST_AI_HINTS", raising=False)
    for name in AGENT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["manage.py", "check"])
    return checks


def test_i001_fires_for_check_run_by_claude_code(reset_i001, monkeypatch):
    from djust.ai_discovery.agents import available_discovery_commands
    from django.core.checks import Info

    monkeypatch.setenv("CLAUDECODE", "1")
    result = reset_i001.check_ai_discovery_hint(None)
    assert len(result) == 1 and isinstance(result[0], Info) and result[0].id == "djust.I001"
    assert all(c.invocation in result[0].hint for c in available_discovery_commands())


@pytest.mark.parametrize(
    "name,value",
    [
        ("DJUST_AGENT", "1"),
        ("CURSOR_AGENT", "1"),
        ("CODEX_THREAD_ID", "x"),
        ("CODEX_SANDBOX", "seatbelt"),
        ("CODEX_SANDBOX_NETWORK_DISABLED", "1"),
    ],
)
def test_i001_agent_env_vars(reset_i001, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    assert reset_i001.check_ai_discovery_hint(None)[0].id == "djust.I001"


def test_i001_ignores_codex_config_vars(reset_i001, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", "/tmp/example")
    monkeypatch.setenv("CODEX_API_KEY", "test-value")
    assert reset_i001.check_ai_discovery_hint(None) == []


@pytest.mark.parametrize("value", ["0", "", "FALSE", "No", "off"])
def test_i001_falsy_values_do_not_count(reset_i001, monkeypatch, value):
    monkeypatch.setenv("DJUST_AGENT", value)
    monkeypatch.setenv("CLAUDECODE", "")
    assert reset_i001.check_ai_discovery_hint(None) == []


@pytest.mark.parametrize("command", ["runserver", "test", "migrate", "djust_check", "djust_mcp"])
def test_i001_only_for_the_check_command(reset_i001, monkeypatch, command):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr(sys, "argv", ["manage.py", command])
    assert reset_i001.check_ai_discovery_hint(None) == []


def test_i001_silenced_by_djust_ai_hints_0(reset_i001, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("DJUST_AI_HINTS", "0")
    assert reset_i001.check_ai_discovery_hint(None) == []


def test_i001_silenced_by_suppress_checks(reset_i001, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    with override_settings(DJUST_CONFIG={"suppress_checks": ["I001"]}):
        assert reset_i001.check_ai_discovery_hint(None) == []


def test_i001_silenced_by_silenced_system_checks(reset_i001, monkeypatch):
    from django.core.checks import run_checks

    monkeypatch.setenv("CLAUDECODE", "1")
    with override_settings(SILENCED_SYSTEM_CHECKS=["djust.I001"]):
        assert not any(
            c.id == "djust.I001" and not c.is_silenced() for c in run_checks(tags=["djust"])
        )


def test_i001_once_per_process(reset_i001, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    assert reset_i001.check_ai_discovery_hint(None)
    assert reset_i001.check_ai_discovery_hint(None) == []


def test_i001_only_lists_existing_commands(reset_i001, monkeypatch):
    from django.core import management

    original = management.get_commands()
    monkeypatch.setattr(
        management, "get_commands", lambda: {k: v for k, v in original.items() if k != "djust_ai"}
    )
    monkeypatch.setenv("CLAUDECODE", "1")
    assert "djust_ai " not in reset_i001.check_ai_discovery_hint(None)[0].hint


def test_i001_end_to_end_manage_py_check(reset_i001, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    out, err = io.StringIO(), io.StringIO()
    call_command("check", stdout=out, stderr=err, fail_level="CRITICAL")
    assert "djust.I001" in out.getvalue() + err.getvalue()


def test_i002_fires_when_liveviews_exist_without_theming(monkeypatch):
    from django.apps import apps
    from djust.checks.ai_discovery import check_theming_not_installed

    original = apps.is_installed
    monkeypatch.setattr(apps, "is_installed", lambda app: app != "djust.theming" and original(app))
    result = check_theming_not_installed(None)
    assert result and result[0].id == "djust.I002"


def test_i002_silent_when_theming_installed():
    from djust.checks.ai_discovery import check_theming_not_installed

    assert check_theming_not_installed(None) == []


def test_i002_silent_without_user_liveviews(monkeypatch):
    from djust.checks import ai_discovery as checks

    monkeypatch.setattr("django.apps.apps.is_installed", lambda app: False)
    monkeypatch.setattr(checks, "_user_liveview_count", lambda: 0)
    assert checks.check_theming_not_installed(None) == []


def test_i002_suppressible(monkeypatch):
    from djust.checks.ai_discovery import check_theming_not_installed

    monkeypatch.setattr("django.apps.apps.is_installed", lambda app: False)
    with override_settings(DJUST_CONFIG={"suppress_checks": ["I002"]}):
        assert check_theming_not_installed(None) == []


@pytest.fixture
def cache_project(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    monkeypatch.chdir(tmp_path)
    template = tmp_path / "example.html"
    template.write_text("<table></table>")
    (tmp_path / ".djust").mkdir()
    payload = {
        "version": 1,
        "root": str(tmp_path.resolve()),
        "templates": {"example.html": {"mtime": template.stat().st_mtime}},
        "stylesheets": {},
        "counts": {"X101": 2},
        "total": 2,
        "generated_at": "today",
    }

    def write(**changes):
        (tmp_path / ".djust/audit-ui.json").write_text(json.dumps(payload | changes))

    return tmp_path, template, write


def check_ui():
    from djust.checks.ai_discovery import check_hand_rolled_ui

    return check_hand_rolled_ui(None)


def test_i003_silent_without_cache(cache_project):
    assert check_ui() == []


def test_i003_silent_on_invalid_json(cache_project):
    root, _, _ = cache_project
    (root / ".djust/audit-ui.json").write_text("{")
    assert check_ui() == []


@pytest.mark.parametrize(
    "change",
    [
        {"version": 42},
        {"root": "elsewhere"},
        {"counts": []},
        {"templates": []},
        {"counts": {"X101": "bad"}},
    ],
)
def test_i003_silent_on_invalid_payload(cache_project, change):
    cache_project[2](**change)
    assert check_ui() == []


def test_i003_silent_when_a_covered_template_is_newer(cache_project):
    _, template, write = cache_project
    write()
    os.utime(template, (template.stat().st_atime, template.stat().st_mtime + 10))
    assert check_ui() == []


def test_i003_silent_when_a_covered_file_is_gone(cache_project):
    _, template, write = cache_project
    write()
    template.unlink()
    assert check_ui() == []


def test_i003_fires_on_fresh_x1xx_counts(cache_project):
    cache_project[2]()
    result = check_ui()
    assert result[0].id == "djust.I003" and "X101" in result[0].msg
    assert "data_table" in result[0].hint


def test_i003_ignores_x105_only(cache_project):
    cache_project[2](counts={"X105": 3})
    assert check_ui() == []


def test_i003_hint_when_components_not_installed(cache_project, monkeypatch):
    cache_project[2]()
    monkeypatch.setattr("django.apps.apps.is_installed", lambda app: False)
    assert "INSTALLED_APPS" in check_ui()[0].hint


def test_i003_hint_when_components_unavailable(cache_project, monkeypatch):
    cache_project[2]()
    monkeypatch.setattr("django.apps.apps.is_installed", lambda app: False)
    monkeypatch.setattr("importlib.util.find_spec", lambda module: None)
    assert 'pip install "djust[components]"' in check_ui()[0].hint


def test_i003_reads_cwd_when_base_dir_has_no_cache(cache_project, settings):
    root, _, write = cache_project
    settings.BASE_DIR = root / "other"
    write()
    assert check_ui()


def test_i003_never_scans_templates(cache_project, monkeypatch):
    cache_project[2]()

    def forbidden(*args, **kwargs):
        raise AssertionError("scan called")

    monkeypatch.setattr("djust.audit_ui.scan_ui", forbidden)
    monkeypatch.setattr("djust.audit_ast.run_ast_audit", forbidden)
    assert check_ui()


def test_i003_missing_cache_does_not_import_audit_ui(cache_project, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "djust.audit_ui":
            raise AssertionError("audit_ui imported for missing cache")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    assert check_ui() == []


@pytest.mark.skipif(sys.platform == "win32", reason="symlink support")
def test_i003_symlinked_cache_dir_is_ignored(cache_project):
    root, _, write = cache_project
    write()
    (root / ".djust").rename(root / "actual-cache")
    (root / ".djust").symlink_to(root / "actual-cache", target_is_directory=True)
    assert check_ui() == []


def test_i003_replacement_map_matches_audit_details():
    from djust import audit_ui
    from djust.checks.ai_discovery import _UI_REPLACEMENTS

    for code, names in _UI_REPLACEMENTS.items():
        parsed = {
            n
            for n in re.findall(r"{%\s*(\w+)", getattr(audit_ui, "_" + code + "_DETAILS"))
            if not n.startswith("end")
        }
        assert set(names) == parsed


def test_i003_suppressible(cache_project):
    cache_project[2]()
    with override_settings(DJUST_CONFIG={"suppress_checks": ["I003"]}):
        assert check_ui() == []
