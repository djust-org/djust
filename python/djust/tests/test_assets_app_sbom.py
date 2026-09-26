from __future__ import annotations

import json

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from djust.assets.registry import get_registry
from djust.assets.sbom import served_directory_containing, write_app_sbom
from djust.checks.sbom import (
    check_sbom_collectstatic_order,
    check_sbom_configured,
    check_sbom_current,
    check_sbom_path,
)
from djust.tests._asset_fixtures import write_asset

APPS_DJUST_FIRST = [
    "djust",
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.staticfiles",
]


def ids(messages):
    return sorted(m.id for m in messages)


def test_writes_to_the_configured_path(tmp_path):
    static_dir, manifest = write_asset(tmp_path)
    out = tmp_path / "sbom" / "app.cdx.json"
    with override_settings(
        DJUST_ASSET_MANIFESTS=[str(manifest)], STATICFILES_DIRS=[str(static_dir)]
    ):
        write_app_sbom(out)
        doc = json.loads(out.read_text())
        assert any(c["name"] == "test-lib" for c in doc["components"])
        assert doc["metadata"]["properties"][0]["value"] == get_registry().digest()


@pytest.mark.parametrize("setting", ["STATIC_ROOT", "MEDIA_ROOT"])
def test_refuses_served_directories(tmp_path, setting):
    served = tmp_path / "served"
    with override_settings(**{setting: str(served)}):
        assert served_directory_containing(served / "x" / "app.cdx.json") == served
        with pytest.raises(CommandError, match="served"):
            write_app_sbom(served / "app.cdx.json")


def test_collectstatic_writes_nothing_without_the_setting(tmp_path):
    root = tmp_path / "collected"
    with override_settings(STATIC_ROOT=str(root), DJUST_SBOM_PATH=None):
        call_command("collectstatic", interactive=False, verbosity=0)
    assert not list(tmp_path.rglob("*.cdx.json"))


def test_collectstatic_writes_the_sbom_when_configured(tmp_path):
    out = tmp_path / "private" / "djust-assets.cdx.json"
    with override_settings(
        STATIC_ROOT=str(tmp_path / "collected"),
        DJUST_SBOM_PATH=str(out),
        INSTALLED_APPS=APPS_DJUST_FIRST,
    ):
        call_command("collectstatic", interactive=False, verbosity=0)
    assert out.is_file()


def test_existing_app_without_sbom_path_gets_only_b011(settings):
    # Demo settings list "djust" after staticfiles, like most apps.
    settings.DJUST_SBOM_PATH = None
    found = (
        check_sbom_configured(None)
        + check_sbom_path(None)
        + check_sbom_collectstatic_order(None)
        + check_sbom_current(None)
    )
    assert ids(found) == ["djust.B011"]


def test_b012_path_inside_static_root(tmp_path):
    with override_settings(STATIC_ROOT=str(tmp_path), DJUST_SBOM_PATH=str(tmp_path / "a.cdx.json")):
        assert "djust.B012" in ids(check_sbom_path(None))


def test_b012_hint_notes_the_cdx_json_suffix_only_when_missing(tmp_path):
    with override_settings(STATIC_ROOT=str(tmp_path), DJUST_SBOM_PATH=str(tmp_path / "a.json")):
        (b012,) = check_sbom_path(None)
    assert ".cdx.json" in b012.hint
    with override_settings(STATIC_ROOT=str(tmp_path), DJUST_SBOM_PATH=str(tmp_path / "a.cdx.json")):
        (b012,) = check_sbom_path(None)
    assert ".cdx.json" not in b012.hint


@pytest.mark.parametrize("value", [42, ["a.cdx.json"], object()])
def test_non_path_sbom_setting_is_a_b012_error_not_a_crash(value):
    with override_settings(DJUST_SBOM_PATH=value):
        (b012,) = check_sbom_path(None)
        assert b012.id == "djust.B012" and "str or os.PathLike" in b012.msg
        # The other SBOM checks leave it to B012 instead of raising TypeError.
        assert check_sbom_configured(None) == []
        assert check_sbom_collectstatic_order(None) == []
        assert check_sbom_current(None) == []


def test_directory_at_the_sbom_path_is_a_command_error(tmp_path):
    target = tmp_path / "a.cdx.json"
    target.mkdir()
    with pytest.raises(CommandError, match="directory"):
        write_app_sbom(target)


def test_collectstatic_dry_run_writes_no_sbom(tmp_path):
    out = tmp_path / "private" / "djust-assets.cdx.json"
    with override_settings(
        STATIC_ROOT=str(tmp_path / "collected"),
        DJUST_SBOM_PATH=str(out),
        INSTALLED_APPS=APPS_DJUST_FIRST,
    ):
        call_command("collectstatic", interactive=False, verbosity=0, dry_run=True)
    assert not out.exists()


def test_b013_follows_the_resolved_collectstatic_command(tmp_path, monkeypatch):
    """B013 asks Django which app's collectstatic wins, not just where the
    literal "djust" sits: a third app overriding it also skips the SBOM."""
    out = str(tmp_path / "a.cdx.json")
    with override_settings(INSTALLED_APPS=APPS_DJUST_FIRST, DJUST_SBOM_PATH=out):
        assert check_sbom_collectstatic_order(None) == []
        with monkeypatch.context() as patch:
            patch.setattr(
                "django.core.management.get_commands",
                lambda: {"collectstatic": "someapp.storage"},
            )
            (b013,) = check_sbom_collectstatic_order(None)
    assert b013.id == "djust.B013" and "someapp.storage" in b013.msg


def test_b013_only_when_sbom_path_is_set(tmp_path):
    apps_wrong = [
        "django.contrib.contenttypes",
        "django.contrib.auth",
        "django.contrib.staticfiles",
        "djust",
    ]
    with override_settings(INSTALLED_APPS=apps_wrong, DJUST_SBOM_PATH=None):
        assert check_sbom_collectstatic_order(None) == []
    with override_settings(INSTALLED_APPS=apps_wrong, DJUST_SBOM_PATH=str(tmp_path / "a.cdx.json")):
        (b013,) = check_sbom_collectstatic_order(None)
    assert b013.id == "djust.B013" and "django.contrib.staticfiles" in b013.msg


def test_b014_missing_or_stale(tmp_path):
    out = tmp_path / "a.cdx.json"
    with override_settings(DJUST_SBOM_PATH=str(out)):
        assert ids(check_sbom_current(None)) == ["djust.B014"]
        write_app_sbom(out)
        assert check_sbom_current(None) == []
        out.write_text(out.read_text().replace(get_registry().digest(), "0" * 64))
        assert ids(check_sbom_current(None)) == ["djust.B014"]


def test_b014_non_object_json_does_not_raise(tmp_path):
    out = tmp_path / "a.cdx.json"
    out.write_text("[1, 2]")
    with override_settings(DJUST_SBOM_PATH=str(out)):
        assert ids(check_sbom_current(None)) == ["djust.B014"]


def test_djust_sbom_command_prints_the_document(capsys):
    call_command("djust_sbom")
    assert json.loads(capsys.readouterr().out)["bomFormat"] == "CycloneDX"


def test_app_static_directory_is_served(tmp_path):
    """AppDirectoriesFinder serves every installed app's static/ directory,
    so an SBOM there would be published by collectstatic too."""
    from pathlib import Path

    from django.apps import apps

    app_static = Path(apps.get_app_config("djust").path) / "static"
    assert app_static.is_dir()
    target = app_static / "sbom" / "app.cdx.json"
    with override_settings(STATIC_ROOT=str(tmp_path / "root"), DJUST_SBOM_PATH=str(target)):
        assert served_directory_containing(target) == app_static.resolve()
        assert "djust.B012" in ids(check_sbom_path(None))
        with pytest.raises(CommandError, match="served"):
            write_app_sbom(target)
    assert not target.exists()
