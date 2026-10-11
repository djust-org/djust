"""UI summary reader validates the writer schema and covered file mtimes."""

import json
import os

import pytest


def test_read_ui_summary_round_trips_the_writer(tmp_path):
    from djust.audit_ast import run_ast_audit
    from djust.audit_ui import read_ui_summary, write_ui_summary

    (tmp_path / "views.py").write_text(
        'from djust import LiveView\nclass TableView(LiveView):\n    template_name = "table.html"\n'
    )
    (tmp_path / "table.html").write_text(
        "<div dj-root><table>{% for row in rows %}<tr><td>{{ row }}</td></tr>{% endfor %}</table></div>"
    )
    report = run_ast_audit(str(tmp_path))
    path, error = write_ui_summary(report, str(tmp_path))
    assert path and error is None
    state = read_ui_summary(str(tmp_path))
    assert state.status == "fresh" and state.payload["counts"]["X101"] == 1


@pytest.mark.parametrize(
    "data", ["[]", "null", "{", '{"version": 99}', '{"version": 1, "root": "wrong"}']
)
def test_reader_invalid_json_and_schema(tmp_path, data):
    from djust.audit_ui import read_ui_summary

    (tmp_path / ".djust").mkdir()
    (tmp_path / ".djust/audit-ui.json").write_text(data)
    assert read_ui_summary(str(tmp_path)).status == "invalid"


def test_reader_caps_file_size(tmp_path):
    from djust.audit_ui import read_ui_summary, _MAX_SUMMARY_BYTES

    (tmp_path / ".djust").mkdir()
    (tmp_path / ".djust/audit-ui.json").write_bytes(b" " * (_MAX_SUMMARY_BYTES + 1))
    assert read_ui_summary(str(tmp_path)).status == "invalid"


def test_reader_bounds_stale_paths_and_checks_stylesheets(tmp_path):
    from djust.audit_ui import read_ui_summary

    (tmp_path / ".djust").mkdir()
    payload = {
        "version": 1,
        "root": str(tmp_path.resolve()),
        "templates": {},
        "stylesheets": {f"{i}.css": {"mtime": 0} for i in range(10)},
        "counts": {},
        "total": 0,
    }
    (tmp_path / ".djust/audit-ui.json").write_text(json.dumps(payload))
    state = read_ui_summary(str(tmp_path))
    assert state.status == "stale" and len(state.stale_paths) == 5 and state.payload is None


def test_reader_allows_writer_relative_parent_paths(tmp_path):
    from djust.audit_ui import read_ui_summary

    root = tmp_path / "app"
    (root / ".djust").mkdir(parents=True)
    external = tmp_path / "style.css"
    external.write_text("")
    payload = {
        "version": 1,
        "root": str(root.resolve()),
        "templates": {},
        "stylesheets": {"../style.css": {"mtime": external.stat().st_mtime}},
        "counts": {},
        "total": 0,
    }
    (root / ".djust/audit-ui.json").write_text(json.dumps(payload))
    assert read_ui_summary(str(root)).status == "fresh"
    os.utime(external, (external.stat().st_atime, external.stat().st_mtime + 10))
    assert read_ui_summary(str(root)).status == "stale"
