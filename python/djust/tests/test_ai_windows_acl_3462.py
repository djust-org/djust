"""Windows owner and DACL rules for the AI inventory hand-off."""

import os

import pytest

from djust import cli
from djust._windows_acl import _acl_is_trusted, windows_path_is_trusted


@pytest.mark.parametrize(
    "owner,user,aces,expected",
    [
        ("S-1-5-21-1", "S-1-5-21-1", [], True),
        ("S-1-5-21-2", "S-1-5-21-1", [], False),
        ("S-1-5-18", "S-1-5-21-1", [], False),
        ("S-1-5-21-1", "S-1-5-21-1", [(0, "S-1-1-0", 0x2)], False),
        ("S-1-5-21-1", "S-1-5-21-1", [(0, "S-1-5-11", 0x40000000)], False),
        ("S-1-5-21-1", "S-1-5-21-1", [(0, "S-1-5-32-545", 0x40000)], False),
        ("S-1-5-21-1", "S-1-5-21-1", [(0, "S-1-1-0", 0x1200A9)], True),
        ("S-1-5-21-1", "S-1-5-21-1", [(1, "S-1-1-0", 0x2)], True),
        ("S-1-5-21-1", "S-1-5-21-1", [(5, "S-1-1-0", 0x2)], False),
    ],
)
def test_acl_decision(owner, user, aces, expected):
    assert _acl_is_trusted(owner, user, aces) is expected


def test_inventory_rejects_untrusted_windows_acl_even_with_marker(tmp_path, monkeypatch, capsys):
    (tmp_path / "manage.py").touch()
    (tmp_path / "pyproject.toml").touch()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    monkeypatch.setattr(cli, "_ai_is_windows", lambda: True)
    monkeypatch.setattr(cli, "_ai_windows_path_trusted", lambda path: False)
    assert cli.cmd_ai(["inventory"]) == 2
    assert "untrusted Windows ownership or DACL" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "nt", reason="requires Windows security APIs")
def test_owned_temp_file_and_directory_are_trusted(tmp_path):
    manage = tmp_path / "manage.py"
    manage.touch()
    assert windows_path_is_trusted(str(manage))
