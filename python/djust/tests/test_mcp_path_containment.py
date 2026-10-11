"""MCP tools that take a filesystem path stay inside the project.

``find_handlers_for_template`` reads an absolute
path directly only when it lies inside a directory the template loaders serve
or inside ``BASE_DIR``, and then opens the resolved path; any other absolute
path gets the reply a missing template gets, so a file outside the project is
neither read nor distinguishable from a missing one. ``seed_fixtures`` keeps every fixture inside ``BASE_DIR``
and loads nothing when ``BASE_DIR`` is unset.

Both use one helper, ``_path_within``: ``os.path.realpath`` on both sides
(``..`` and symlinks are followed) and ``os.path.commonpath`` on whole path
components, so a sibling directory that shares a name prefix (``/app-data``
next to ``/app``) is outside.
"""

from __future__ import annotations

import json
import os

import pytest
from django.test import override_settings

from djust.mcp.server import _path_within


def _tool(name, **kwargs):
    from djust.mcp.server import create_server

    server = create_server()
    tool = next(t for t in server._tool_manager._tools.values() if t.name == name)
    return json.loads(tool.fn(**kwargs))


def template_handler_report(template_path):
    return _tool("find_handlers_for_template", template_path=template_path)


OUTSIDE_TEMPLATE = '<button dj-click="outside_handler">x</button>\n'


@pytest.fixture
def layout(tmp_path):
    """templates/ (served), templates-extra/ (sibling prefix), outside/."""
    templates = tmp_path / "proj" / "templates"
    templates.mkdir(parents=True)
    (templates / "inside.html").write_text('<button dj-click="inside_handler">i</button>\n')
    sibling = tmp_path / "proj" / "templates-extra"
    sibling.mkdir()
    (sibling / "sibling.html").write_text(OUTSIDE_TEMPLATE)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "outside.html").write_text(OUTSIDE_TEMPLATE)
    (templates / "link.html").symlink_to(outside / "outside.html")
    settings = [
        {
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "DIRS": [str(templates)],
            "APP_DIRS": False,
        }
    ]
    # BASE_DIR is the templates directory itself, so the sibling and outside
    # directories are outside every root.
    with override_settings(TEMPLATES=settings, BASE_DIR=str(templates)):
        yield {
            "templates": templates,
            "sibling": sibling / "sibling.html",
            "outside": outside / "outside.html",
            "link": templates / "link.html",
            "missing": outside / "missing.html",
        }


def _assert_refused(report, path):
    assert "outside_handler" not in json.dumps(report)
    assert report.get("resolved_path") is None, report
    assert "dj_handlers_in_template" not in report, report
    assert report["error"].startswith("Could not resolve template %r" % str(path))


def test_absolute_path_inside_the_template_dirs_is_read(layout):
    path = layout["templates"] / "inside.html"
    report = template_handler_report(str(path))
    assert report.get("error") is None, report
    assert report["resolved_path"] == os.path.realpath(path)
    assert report["dj_handlers_in_template"] == ["inside_handler"]


def test_the_resolved_path_is_what_is_read(layout):
    """The file opened is the one the containment check resolved."""
    (layout["templates"] / "sub").mkdir()
    dotted = os.path.join(str(layout["templates"]), "sub", "..", "inside.html")
    report = template_handler_report(dotted)
    assert report.get("error") is None, report
    assert report["resolved_path"] == os.path.realpath(layout["templates"] / "inside.html")
    assert report["dj_handlers_in_template"] == ["inside_handler"]


def test_absolute_path_outside_the_project_is_refused(layout):
    _assert_refused(template_handler_report("/etc/passwd"), "/etc/passwd")
    _assert_refused(template_handler_report(str(layout["outside"])), layout["outside"])


def test_refusal_matches_a_missing_file(layout):
    """An outside file gets the same reply as a missing file."""
    existing = template_handler_report(str(layout["outside"]))
    missing = template_handler_report(str(layout["missing"]))
    assert set(existing) == set(missing)
    assert existing["error"].replace(str(layout["outside"]), "X") == missing["error"].replace(
        str(layout["missing"]), "X"
    )


def test_sibling_directory_sharing_a_prefix_is_refused(layout):
    _assert_refused(template_handler_report(str(layout["sibling"])), layout["sibling"])


def test_dotdot_segments_are_resolved_before_the_check(layout):
    dotted = os.path.join(str(layout["templates"]), "..", "..", "outside", "outside.html")
    _assert_refused(template_handler_report(dotted), dotted)


def test_symlink_out_of_the_template_dirs_is_refused(layout):
    _assert_refused(template_handler_report(str(layout["link"])), layout["link"])


def test_logical_names_still_resolve(layout):
    report = template_handler_report("inside.html")
    assert report["dj_handlers_in_template"] == ["inside_handler"]


# --------------------------------------------------------------------------- #
# The shared helper
# --------------------------------------------------------------------------- #
def test_path_within(tmp_path):
    root = tmp_path / "app"
    (root / "sub").mkdir(parents=True)
    (tmp_path / "app-data").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (root / "elsewhere-link").symlink_to(tmp_path / "elsewhere")

    assert _path_within(str(root / "sub" / "f.json"), [root])
    assert _path_within(str(root), [root])
    assert not _path_within(str(tmp_path / "app-data" / "f.json"), [root])
    assert not _path_within(str(root / ".." / "elsewhere" / "f.json"), [root])
    assert not _path_within(str(root / "elsewhere-link" / "f.json"), [root])
    assert not _path_within(str(root / "sub" / "f.json"), [])
    assert not _path_within(str(root / "sub" / "f.json"), [None, ""])


# --------------------------------------------------------------------------- #
# seed_fixtures
# --------------------------------------------------------------------------- #
def _seed(paths):
    return _tool("seed_fixtures", fixture_paths=paths)


@pytest.fixture
def no_subprocess(monkeypatch):
    import subprocess

    def _refuse(*args, **kwargs):  # pragma: no cover - reached only on a regression
        raise AssertionError("loaddata ran for a path that should have been refused")

    monkeypatch.setattr(subprocess, "run", _refuse)


@pytest.mark.usefixtures("no_subprocess")
def test_seed_fixtures_refuses_a_sibling_prefix_directory(tmp_path):
    base = tmp_path / "app"
    base.mkdir()
    sibling = tmp_path / "app-other"
    sibling.mkdir()
    (sibling / "users.json").write_text("[]")
    with override_settings(BASE_DIR=str(base)):
        result = _seed([str(sibling / "users.json")])
    assert "outside the project directory" in result["error"]


@pytest.mark.usefixtures("no_subprocess")
def test_seed_fixtures_refuses_a_relative_path_outside_base_dir(tmp_path):
    base = tmp_path / "app"
    base.mkdir()
    with override_settings(BASE_DIR=str(base)):
        result = _seed(["../outside.json"])
    assert "outside the project directory" in result["error"]


@pytest.mark.usefixtures("no_subprocess")
def test_seed_fixtures_refuses_a_symlink_outside_base_dir(tmp_path):
    base = tmp_path / "app"
    base.mkdir()
    (tmp_path / "outside.json").write_text("[]")
    (base / "link.json").symlink_to(tmp_path / "outside.json")
    with override_settings(BASE_DIR=str(base)):
        result = _seed(["link.json"])
    assert "outside the project directory" in result["error"]


@pytest.mark.usefixtures("no_subprocess")
def test_seed_fixtures_requires_base_dir(tmp_path):
    (tmp_path / "users.json").write_text("[]")
    with override_settings(BASE_DIR=None):
        result = _seed([str(tmp_path / "users.json")])
    assert "BASE_DIR" in result["error"]
