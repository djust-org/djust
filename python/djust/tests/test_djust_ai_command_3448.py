"""Discovery command output and cache integration contracts."""

import io
import json
import os

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


def invoke(*args):
    out = io.StringIO()
    call_command("djust_ai", *args, stdout=out)
    return out.getvalue()


def test_inventory_json_shape():
    result = json.loads(invoke("inventory", "--json"))
    assert list(result) == [
        "version",
        "kind",
        "djust_version",
        "apps",
        "theming",
        "components",
        "project",
        "ui_audit",
        "discovery",
    ]
    assert result["version"] == 1 and result["kind"] == "inventory"
    apps = {a["app"]: a for a in result["apps"]}
    assert set(apps) == {"djust.components", "djust.theming", "djust.auth", "djust.admin_ext"}
    assert apps["djust.components"]["enabled"] and apps["djust.theming"]["enabled"]


def test_inventory_reports_available_not_enabled(monkeypatch):
    from django.apps import apps

    original = apps.is_installed
    monkeypatch.setattr(
        apps, "is_installed", lambda app: app != "djust.components" and original(app)
    )
    item = next(
        a
        for a in json.loads(invoke("inventory", "--json"))["apps"]
        if a["app"] == "djust.components"
    )
    assert item["available"] and not item["enabled"]
    assert "available, not enabled" in invoke("inventory")


def test_inventory_component_count_matches_catalog():
    from djust.ai_discovery.catalog import load_catalog

    assert json.loads(invoke("inventory", "--json"))["components"]["count"] == len(load_catalog())


def test_inventory_ui_audit_missing(settings, tmp_path, monkeypatch):
    settings.BASE_DIR = tmp_path
    monkeypatch.chdir(tmp_path)
    result = json.loads(invoke("inventory", "--json"))["ui_audit"]
    assert result["status"] == "missing" and result["counts"] is None


def test_inventory_ui_audit_fresh_then_stale(settings, tmp_path, monkeypatch):
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
    (tmp_path / ".djust/audit-ui.json").write_text(json.dumps(payload))
    result = json.loads(invoke("inventory", "--json"))["ui_audit"]
    assert result["status"] == "fresh" and result["counts"]["X101"] == 2
    os.utime(template, (template.stat().st_atime, template.stat().st_mtime + 10))
    result = json.loads(invoke("inventory", "--json"))["ui_audit"]
    assert result["status"] == "stale" and result["stale_paths"] == ["example.html"]


def test_suggest_json_shape():
    result = json.loads(invoke("suggest", "data table", "--json"))
    assert (
        result["version"] == 1 and result["kind"] == "suggest" and result["intent"] == "data table"
    )
    first = result["results"][0]
    assert first["name"] == "data_table" and first["score"] > 0
    assert set(first["required_props"]) >= {"rows", "columns"}
    assert first["props_source"] == "signature" and "variants" not in first


def test_suggest_text_output_has_snippet_and_load_line():
    text = invoke("suggest", "data table")
    assert "{% load djust_components %}" in text and "{% data_table" in text
    assert "required: rows, columns" in text


def test_suggest_no_match_exits_zero_with_empty_results():
    assert json.loads(invoke("suggest", "zzzzzzzz", "--json"))["results"] == []
    assert "No component matched" in invoke("suggest", "zzzzzzzz")


def test_suggest_empty_intent_is_a_command_error():
    with pytest.raises(CommandError, match="intent is empty"):
        invoke("suggest", "")


def test_manifest_prints_markdown():
    text = invoke("manifest")
    assert text.startswith("# djust ") and "## UI components" in text


def test_djust_ai_skips_system_checks():
    from djust.management.commands.djust_ai import Command

    assert Command.requires_system_checks == []


def test_inventory_reports_invalid_cache_directory_before_cwd_fallback(
    settings, tmp_path, monkeypatch
):
    base = tmp_path / "base"
    cwd = tmp_path / "cwd"
    base.mkdir()
    cwd.mkdir()
    (base / ".djust").write_text("not a directory")
    settings.BASE_DIR = base
    monkeypatch.chdir(cwd)
    result = json.loads(invoke("inventory", "--json"))["ui_audit"]
    assert result["status"] == "invalid"
    assert result["path"] == str(base / ".djust/audit-ui.json")


def test_inventory_imports_url_only_liveview_in_subprocess(tmp_path):
    import os
    import subprocess
    import sys

    (tmp_path / "settings.py").write_text(
        "SECRET_KEY='test'\nINSTALLED_APPS=['djust']\nROOT_URLCONF='urls'\n"
        "LIVEVIEW_CONFIG={'hot_reload_auto_enable': False}\n"
    )
    (tmp_path / "views.py").write_text(
        "from djust import LiveView\nclass UrlOnlyView(LiveView):\n    template_name='only.html'\n"
    )
    (tmp_path / "urls.py").write_text(
        "from django.urls import path\nfrom views import UrlOnlyView\n"
        "urlpatterns=[path('only/', UrlOnlyView.as_view())]\n"
    )
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="settings")
    result = subprocess.run(
        [sys.executable, "-m", "djust", "ai", "inventory", "--json"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert any(
        view["name"] == "UrlOnlyView" for view in json.loads(result.stdout)["project"]["views"]
    )


def test_inventory_text_strips_all_derived_controls():
    from djust.ai_discovery.inventory import build_inventory, render_inventory

    data = build_inventory()
    control = "\x1b]0;PWN\x07\n\x85"
    data["project"]["views"] = [{"name": control + "View"}]
    data["theming"]["error"] = control + "theme"
    data["ui_audit"].update(status="stale", reason=control + "reason")
    output = render_inventory(data)
    assert "PWN" in output
    assert all(ord(char) >= 32 or char == "\n" for char in output)
    assert not any(127 <= ord(char) <= 159 for char in output)
    assert "\nView" not in output


def test_inventory_text_retains_discovery_command_lines():
    from djust.ai_discovery.agents import DISCOVERY_COMMANDS
    from djust.ai_discovery.inventory import build_inventory, render_inventory

    output = render_inventory(build_inventory())
    lines = output.splitlines()
    assert sum("#" in line and "python manage.py" in line for line in lines) == len(
        DISCOVERY_COMMANDS
    )
