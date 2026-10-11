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


@pytest.mark.parametrize("rel", ["../style.css", "/etc/passwd", "a/../../style.css"])
def test_reader_rejects_paths_outside_root(tmp_path, rel):
    from djust.audit_ui import read_ui_summary

    payload = {
        "version": 1,
        "root": str(tmp_path.resolve()),
        "templates": {rel: {"mtime": 0}},
        "stylesheets": {},
        "counts": {},
        "total": 0,
    }
    (tmp_path / ".djust").mkdir()
    (tmp_path / ".djust/audit-ui.json").write_text(json.dumps(payload))
    assert read_ui_summary(str(tmp_path)).status == "invalid"


def test_reader_rejects_excessive_records(tmp_path):
    from djust.audit_ui import read_ui_summary

    payload = {
        "version": 1,
        "root": str(tmp_path.resolve()),
        "templates": {str(i): {"mtime": 0} for i in range(10001)},
        "stylesheets": {str(i): {"mtime": 0} for i in range(10000)},
        "counts": {},
        "total": 0,
    }
    (tmp_path / ".djust").mkdir()
    (tmp_path / ".djust/audit-ui.json").write_text(json.dumps(payload))
    state = read_ui_summary(str(tmp_path))
    assert state.status == "invalid" and "record" in state.reason


def test_reader_deep_json_is_invalid(tmp_path):
    from djust.audit_ui import read_ui_summary

    (tmp_path / ".djust").mkdir()
    (tmp_path / ".djust/audit-ui.json").write_text("[" * 10000 + "0" + "]" * 10000)
    assert read_ui_summary(str(tmp_path)).status == "invalid"


@pytest.mark.parametrize("special", ["fifo", "symlink"])
def test_reader_special_file_does_not_block(tmp_path, special):
    import subprocess
    import sys

    (tmp_path / ".djust").mkdir()
    cache = tmp_path / ".djust/audit-ui.json"
    if special == "fifo":
        os.mkfifo(cache)
    else:
        cache.symlink_to("/dev/zero")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; settings.configure(BASE_DIR="
            + repr(str(tmp_path))
            + "); "
            "from djust.ai_discovery.inventory import read_project_ui_summary; "
            "assert read_project_ui_summary().status == 'invalid'",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr


def test_reader_rejects_oversize_before_reading(tmp_path, monkeypatch):
    from djust import audit_ui

    (tmp_path / ".djust").mkdir()
    (tmp_path / ".djust/audit-ui.json").write_bytes(b" " * (audit_ui._MAX_SUMMARY_BYTES + 1))
    original = os.fdopen

    class NoRead:
        def __init__(self, fd, mode):
            self.file = original(fd, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def fileno(self):
            return self.file.fileno()

        def read(self, *args):
            pytest.fail("oversize cache was read")

    monkeypatch.setattr(os, "fdopen", NoRead)
    assert audit_ui.read_ui_summary(str(tmp_path)).status == "invalid"


def test_reader_rejects_symlink_record_escaping_root(tmp_path):
    from djust.audit_ui import read_ui_summary

    (tmp_path / ".djust").mkdir()
    (tmp_path / "external").symlink_to(tmp_path.parent, target_is_directory=True)
    payload = {
        "version": 1,
        "root": str(tmp_path.resolve()),
        "templates": {"external/outside.html": {"mtime": 0}},
        "stylesheets": {},
        "counts": {},
        "total": 0,
    }
    (tmp_path / ".djust/audit-ui.json").write_text(json.dumps(payload))
    assert read_ui_summary(str(tmp_path)).status == "invalid"
